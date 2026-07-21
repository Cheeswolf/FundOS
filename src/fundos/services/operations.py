from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from fundos.services.versioned_performance import calculate_and_store_versioned_performance
from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class OperationsCycleResult:
    cycle_id: str
    product_id: str
    as_of_date: date
    status: str
    latest_data_date: date | None
    data_age_days: int | None
    performance_updated: bool
    weekly_decision_due: bool
    monthly_review_due: bool
    message: str


def run_operations_cycle(
    database: Database,
    *,
    product_id: str,
    provider: str,
    benchmark_symbol: str,
    as_of_date: date,
) -> OperationsCycleResult:
    mandate_rows = database.fetch_all(
        "SELECT * FROM investment_mandates WHERE product_id = ?", (product_id,)
    )
    if not mandate_rows:
        raise ValueError("investment mandate is required for operations cycle")
    versions = database.get_portfolio_versions(product_id, published_only=True)
    if not versions:
        raise ValueError("at least one published version is required for operations cycle")
    symbols = sorted({position.asset_symbol for version in versions for position in version.weights} | {benchmark_symbol})
    placeholders = ", ".join("?" for _ in symbols)
    latest_rows = database.fetch_all(
        f"""
        SELECT symbol, MAX(trade_date) AS latest_date
        FROM market_prices
        WHERE provider = ? AND symbol IN ({placeholders}) AND trade_date <= ?
        GROUP BY symbol
        """,
        (provider, *symbols, as_of_date.isoformat()),
    )
    latest_by_symbol = {
        row["symbol"]: date.fromisoformat(row["latest_date"])
        for row in latest_rows
    }
    missing = set(symbols) - set(latest_by_symbol)
    latest_data_date = min(latest_by_symbol.values()) if latest_by_symbol else None
    data_age_days = (as_of_date - latest_data_date).days if latest_data_date else None
    data_is_fresh = (
        not missing
        and data_age_days is not None
        and data_age_days <= mandate_rows[0]["maximum_data_age_days"]
    )

    performance_updated = False
    if data_is_fresh:
        calculate_and_store_versioned_performance(
            database,
            product_id=product_id,
            provider=provider,
            benchmark_symbol=benchmark_symbol,
        )
        performance_updated = True

    latest_version_date = max(version.effective_date for version in versions)
    weekly_decision_due = as_of_date.weekday() == 4 and (as_of_date - latest_version_date).days >= 7
    monthly_review_rows = database.fetch_all(
        """
        SELECT 1 FROM review_reports
        WHERE product_id = ? AND substr(period_end, 1, 7) = ? LIMIT 1
        """,
        (product_id, as_of_date.strftime("%Y-%m")),
    )
    monthly_review_due = as_of_date.day >= 28 and not monthly_review_rows

    if not data_is_fresh:
        status = "blocked"
        if missing:
            message = f"缺少行情数据: {', '.join(sorted(missing))}"
        else:
            message = f"行情已过期 {data_age_days} 天，暂停更新业绩与决策流程"
    elif weekly_decision_due or monthly_review_due:
        status = "attention_required"
        due = []
        if weekly_decision_due:
            due.append("周度投研与投委会")
        if monthly_review_due:
            due.append("月度复盘")
        message = "待执行：" + "、".join(due)
    else:
        status = "healthy"
        message = "数据与运营节奏正常"

    existing = database.fetch_all(
        "SELECT cycle_id FROM operations_cycles WHERE product_id = ? AND as_of_date = ?",
        (product_id, as_of_date.isoformat()),
    )
    cycle_id = existing[0]["cycle_id"] if existing else str(uuid4())
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO operations_cycles (
                cycle_id, product_id, as_of_date, status, latest_data_date,
                data_age_days, performance_updated, weekly_decision_due,
                monthly_review_due, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, as_of_date) DO UPDATE SET
                status = excluded.status,
                latest_data_date = excluded.latest_data_date,
                data_age_days = excluded.data_age_days,
                performance_updated = excluded.performance_updated,
                weekly_decision_due = excluded.weekly_decision_due,
                monthly_review_due = excluded.monthly_review_due,
                message = excluded.message,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                cycle_id, product_id, as_of_date.isoformat(), status,
                latest_data_date.isoformat() if latest_data_date else None,
                data_age_days, int(performance_updated), int(weekly_decision_due),
                int(monthly_review_due), message,
            ),
        )
    return OperationsCycleResult(
        cycle_id, product_id, as_of_date, status, latest_data_date,
        data_age_days, performance_updated, weekly_decision_due,
        monthly_review_due, message,
    )

