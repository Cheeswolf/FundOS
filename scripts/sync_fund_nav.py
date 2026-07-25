import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.data_providers import (  # noqa: E402
    TushareFundError,
    TushareFundNavProvider,
    TushareHttpClient,
    fund_nav_rows_to_prices,
    validate_nav_history,
)
from fundos.domain import Asset  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and synchronize selected off-exchange fund NAVs from Tushare"
    )
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "product.real_trial.json",
    )
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--minimum-history-days", type=int, default=1095)
    parser.add_argument("--maximum-gap-days", type=int, default=14)
    parser.add_argument("--maximum-absolute-return", type=float, default=0.35)
    arguments = parser.parse_args()

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required")
    configuration = json.loads(arguments.config.read_text(encoding="utf-8"))
    instruments = {
        item["symbol"]: item["selected_instrument"]
        for item in configuration["data_policy"]["instruments"]
        if item["selected_instrument"]["type"] == "off_exchange_fund"
    }
    selected = arguments.symbol or list(instruments)
    unknown = set(selected) - set(instruments)
    if unknown:
        raise SystemExit(f"Unknown or non-fund symbols: {', '.join(sorted(unknown))}")

    provider = TushareFundNavProvider(TushareHttpClient(token))
    database = Database(arguments.database)
    database.initialize()
    assets = json.loads((PROJECT_ROOT / "config" / "assets.json").read_text(encoding="utf-8"))
    database.upsert_assets(Asset(**item) for item in assets["assets"])

    total = 0
    for symbol in selected:
        instrument = instruments[symbol]
        provider_code = instrument["provider_code"]
        try:
            provider.validate_fund(
                provider_code,
                expected_name=instrument["metadata_name_contains"],
            )
            nav_rows = provider.get_fund_nav(
                provider_code,
                internal_symbol=symbol,
                start_date=arguments.start,
                end_date=arguments.end,
            )
            validate_nav_history(
                nav_rows,
                minimum_history_days=arguments.minimum_history_days,
                maximum_gap_days=arguments.maximum_gap_days,
                maximum_absolute_return=arguments.maximum_absolute_return,
            )
            price_rows = fund_nav_rows_to_prices(nav_rows)
        except TushareFundError as error:
            raise SystemExit(f"{symbol}/{provider_code}: {error}") from error
        database.upsert_market_data_observations(
            (
                "tushare-fund-nav",
                provider_code,
                row.symbol,
                row.nav_date,
                row.announced_date,
                row.available_date,
                row.value_field,
                row.close,
                row.close,
            )
            for row in nav_rows
        )
        count = database.upsert_prices(
            ("tushare-fund-nav", row.symbol, row.trade_date, row.close)
            for row in price_rows
        )
        total += count
        print(
            f"{symbol} <- {instrument['name']} ({provider_code}): "
            f"{count} rows, NAV field {nav_rows[0].value_field}"
        )
    print(f"Synchronized {total} point-in-time-safe fund NAV rows")


if __name__ == "__main__":
    main()
