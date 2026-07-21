import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.data_providers import AlphaVantageDailyProvider, AlphaVantageError  # noqa: E402
from fundos.domain import Asset  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize real daily market data from Alpha Vantage")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "market_data.alpha_vantage.json",
    )
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--symbol", action="append", help="Internal symbol to sync; repeat as needed")
    parser.add_argument("--compact", action="store_true", help="Request the provider's compact recent history")
    arguments = parser.parse_args()

    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        raise SystemExit("ALPHA_VANTAGE_API_KEY is required")
    configuration = json.loads(arguments.config.read_text(encoding="utf-8"))
    mappings = configuration["symbols"]
    selected = arguments.symbol or list(mappings)
    unknown = set(selected) - set(mappings)
    if unknown:
        raise SystemExit(f"Unknown internal symbols: {', '.join(sorted(unknown))}")

    database = Database(arguments.database)
    database.initialize()
    asset_configuration = json.loads((PROJECT_ROOT / "config" / "assets.json").read_text(encoding="utf-8"))
    database.upsert_assets(Asset(**item) for item in asset_configuration["assets"])
    provider = AlphaVantageDailyProvider(api_key)
    total = 0
    for internal_symbol in selected:
        provider_symbol = mappings[internal_symbol]
        try:
            rows = provider.get_daily_prices(
                provider_symbol,
                internal_symbol=internal_symbol,
                start_date=arguments.start,
                end_date=arguments.end,
                output_size="compact" if arguments.compact else "full",
            )
        except AlphaVantageError as error:
            raise SystemExit(f"{internal_symbol}/{provider_symbol}: {error}") from error
        inserted = database.upsert_prices(
            (
                configuration["provider_name"],
                row.symbol,
                row.trade_date,
                row.close,
            )
            for row in rows
        )
        total += inserted
        print(f"{internal_symbol} <- {provider_symbol}: {inserted} rows")
    print(f"Synchronized {total} real market price rows")


if __name__ == "__main__":
    main()
