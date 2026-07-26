import time
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from fundos.data_providers import AlphaVantageError, PriceRow
from fundos.services.operations import run_operations_cycle
from fundos.services.alerts import create_alert
from fundos.storage import Database


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DailyPriceProvider(Protocol):
    def get_daily_prices(self, provider_symbol: str, **kwargs) -> list[PriceRow]: ...


@dataclass(frozen=True, slots=True)
class PipelineResult:
    run_id: str
    status: str
    price_rows_written: int
    successful_steps: int
    failed_steps: int
    errors: tuple[str, ...]


def run_production_pipeline(
    database: Database,
    *,
    market_provider: DailyPriceProvider,
    provider_name: str,
    symbol_mappings: Mapping[str, str],
    portfolios: Sequence[Mapping[str, Any]],
    as_of_date: date,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
    output_size: str = "compact",
    sleep: Callable[[float], None] = time.sleep,
) -> PipelineResult:
    if max_attempts < 1 or retry_delay_seconds < 0:
        raise ValueError("retry policy is invalid")
    if not symbol_mappings:
        raise ValueError("at least one market symbol mapping is required")
    run_id = str(uuid4())
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO pipeline_runs (run_id, as_of_date, provider, status) VALUES (?, ?, ?, 'running')",
            (run_id, as_of_date.isoformat(), provider_name),
        )
    logger.info("production pipeline started", extra={"event": "pipeline_started", "run_id": run_id})

    price_rows_written = 0
    successful_steps = 0
    failed_steps = 0
    errors: list[str] = []

    def record_step(
        step_name: str,
        status: str,
        attempts: int,
        rows_written: int,
        message: str,
    ) -> None:
        nonlocal successful_steps, failed_steps
        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO pipeline_steps
                    (run_id, step_name, status, attempts, rows_written, message)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, step_name, status, attempts, rows_written, message),
            )
        if status == "succeeded":
            successful_steps += 1
        else:
            failed_steps += 1

    for internal_symbol, provider_symbol in symbol_mappings.items():
        step_name = f"market-data:{internal_symbol}"
        for attempt in range(1, max_attempts + 1):
            try:
                rows = market_provider.get_daily_prices(
                    provider_symbol,
                    internal_symbol=internal_symbol,
                    end_date=as_of_date,
                    output_size=output_size,
                )
                count = database.upsert_prices(
                    (provider_name, row.symbol, row.trade_date, row.close) for row in rows
                )
                price_rows_written += count
                record_step(step_name, "succeeded", attempt, count, f"同步 {count} 条行情")
                logger.info(
                    "market data step succeeded",
                    extra={
                        "event": "pipeline_step", "run_id": run_id,
                        "step_name": step_name, "status": "succeeded",
                        "attempts": attempt, "rows_written": count,
                    },
                )
                break
            except (AlphaVantageError, OSError, ValueError) as error:
                if attempt == max_attempts:
                    message = f"{internal_symbol}/{provider_symbol}: {error}"
                    errors.append(message)
                    record_step(step_name, "failed", attempt, 0, message)
                    logger.error(
                        "market data step failed",
                        extra={
                            "event": "pipeline_step", "run_id": run_id,
                            "step_name": step_name, "status": "failed",
                            "attempts": attempt, "error": str(error),
                        },
                    )
                else:
                    sleep(retry_delay_seconds * (2 ** (attempt - 1)))

    for portfolio in portfolios:
        product_id = portfolio["product_id"]
        step_name = f"operations:{product_id}"
        try:
            result = run_operations_cycle(
                database,
                product_id=product_id,
                provider=provider_name,
                benchmark_symbol=portfolio["benchmark_symbol"],
                as_of_date=as_of_date,
                transaction_cost_rate=float(portfolio.get("transaction_cost_rate", 0.0)),
                charge_initial_allocation=bool(portfolio.get("charge_initial_allocation", False)),
            )
            if result.status == "blocked":
                errors.append(f"{product_id}: {result.message}")
                record_step(step_name, "blocked", 1, 0, result.message)
            else:
                record_step(step_name, "succeeded", 1, 0, result.message)
        except (ValueError, RuntimeError) as error:
            message = f"{product_id}: {error}"
            errors.append(message)
            record_step(step_name, "failed", 1, 0, message)

    if failed_steps == 0:
        status = "succeeded"
    elif successful_steps == 0:
        status = "failed"
    else:
        status = "partial"
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE pipeline_runs SET
                status = ?, price_rows_written = ?, successful_steps = ?,
                failed_steps = ?, error_summary = ?, completed_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (
                status, price_rows_written, successful_steps, failed_steps,
                " | ".join(errors) if errors else None, run_id,
            ),
        )
    if status != "succeeded":
        create_alert(
            database,
            source_type="pipeline_run",
            source_id=run_id,
            severity="critical" if status == "failed" else "warning",
            title=f"FundOS 生产管道{status}",
            message=" | ".join(errors) if errors else "生产管道存在失败步骤",
        )
    logger.info(
        "production pipeline completed",
        extra={
            "event": "pipeline_completed", "run_id": run_id,
            "status": status, "rows_written": price_rows_written,
        },
    )
    return PipelineResult(
        run_id, status, price_rows_written, successful_steps,
        failed_steps, tuple(errors),
    )
