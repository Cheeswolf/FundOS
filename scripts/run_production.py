import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.data_providers import AlphaVantageDailyProvider  # noqa: E402
from fundos.core import configure_logging  # noqa: E402
from fundos.domain import Asset  # noqa: E402
from fundos.services import (  # noqa: E402
    deliver_pending_alerts,
    run_production_pipeline,
    run_scheduled_job,
)
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize prices and run FundOS operations")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--job-name", default="daily-production-pipeline")
    parser.add_argument("--lease-seconds", type=int, default=3600)
    arguments = parser.parse_args()
    configure_logging(arguments.log_level)
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        raise SystemExit("ALPHA_VANTAGE_API_KEY is required")

    market_config = json.loads(
        (PROJECT_ROOT / "config" / "market_data.alpha_vantage.json").read_text(encoding="utf-8")
    )
    operations_config = json.loads(
        (PROJECT_ROOT / "config" / "operations.json").read_text(encoding="utf-8")
    )
    assets_config = json.loads((PROJECT_ROOT / "config" / "assets.json").read_text(encoding="utf-8"))
    database = Database(arguments.database)
    database.initialize()
    database.upsert_assets(Asset(**item) for item in assets_config["assets"])
    scheduled = run_scheduled_job(
        database,
        job_name=arguments.job_name,
        lease_seconds=arguments.lease_seconds,
        task=lambda: run_production_pipeline(
            database,
            market_provider=AlphaVantageDailyProvider(api_key),
            provider_name=market_config["provider_name"],
            symbol_mappings=market_config["symbols"],
            portfolios=operations_config["portfolios"],
            as_of_date=arguments.as_of,
            max_attempts=arguments.attempts,
            retry_delay_seconds=arguments.retry_delay,
        ),
        is_successful=lambda pipeline: pipeline.status == "succeeded",
    )
    if scheduled.status == "skipped":
        print(f"Scheduled job skipped: {scheduled.message}")
        return
    result = scheduled.value
    if result is None:
        raise RuntimeError("scheduled production job completed without a result")
    print(
        f"Pipeline {result.status}: {result.price_rows_written} prices, "
        f"{result.successful_steps} successful steps, {result.failed_steps} failed steps"
    )
    for error in result.errors:
        print(f"ERROR: {error}")
    webhook_url = os.environ.get("FUNDOS_ALERT_WEBHOOK_URL", "")
    if webhook_url and result.status != "succeeded":
        delivery = deliver_pending_alerts(database, webhook_url=webhook_url)
        print(f"Alerts delivered: {delivery.delivered}; failed: {delivery.failed}")
    if result.status != "succeeded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
