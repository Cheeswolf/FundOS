import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.services import run_operations_cycle  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one FundOS daily operations cycle")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "operations.json")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    arguments = parser.parse_args()
    database = Database(arguments.database)
    database.initialize()
    configuration = json.loads(arguments.config.read_text(encoding="utf-8"))
    for item in configuration["portfolios"]:
        result = run_operations_cycle(database, as_of_date=arguments.as_of, **item)
        print(f"{result.product_id}: {result.status} — {result.message}")


if __name__ == "__main__":
    main()

