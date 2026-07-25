import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.services import build_trial_valuation_series  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the unified trial fund, cash, and composite benchmark series"
    )
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "product.real_trial.json",
    )
    parser.add_argument("--source-provider", default="tushare-fund-nav")
    parser.add_argument("--target-provider", default="fundos-trial-normalized")
    arguments = parser.parse_args()

    configuration = json.loads(arguments.config.read_text(encoding="utf-8"))
    instruments = configuration["data_policy"]["instruments"]
    fund_symbols = [
        item["symbol"]
        for item in instruments
        if item["selected_instrument"]["type"] == "off_exchange_fund"
    ]
    cash_symbols = [
        item["symbol"]
        for item in instruments
        if item["selected_instrument"]["type"] == "cash_ledger"
    ]
    if len(cash_symbols) != 1:
        raise SystemExit("trial configuration must contain exactly one cash ledger")
    benchmark = configuration["benchmark"]
    result = build_trial_valuation_series(
        Database(arguments.database),
        source_provider=arguments.source_provider,
        target_provider=arguments.target_provider,
        fund_symbols=fund_symbols,
        cash_symbol=cash_symbols[0],
        benchmark_symbol=benchmark["symbol"],
        benchmark_weights={
            item["symbol"]: item["weight"]
            for item in benchmark["components"]
        },
    )
    print(
        f"Built {arguments.target_provider}: {result.source_rows} fund rows, "
        f"{result.cash_rows} cash rows, {result.benchmark_rows} benchmark rows"
    )
    print(f"Coverage: {result.first_date} to {result.last_date}")


if __name__ == "__main__":
    main()
