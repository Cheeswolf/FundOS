import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.data_providers import CsvPriceProvider  # noqa: E402
from fundos.domain import Asset  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import validated market prices into FundOS")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument("--provider", default="csv")
    arguments = parser.parse_args()

    prices = CsvPriceProvider().load(arguments.csv_path)
    database = Database(arguments.database)
    database.initialize()
    asset_config = json.loads((PROJECT_ROOT / "config" / "assets.json").read_text(encoding="utf-8"))
    database.upsert_assets(Asset(**item) for item in asset_config["assets"])
    inserted = database.upsert_prices(
        (arguments.provider, item.symbol, item.trade_date, item.close) for item in prices
    )
    print(f"Imported {inserted} price rows from {arguments.csv_path}")


if __name__ == "__main__":
    main()
