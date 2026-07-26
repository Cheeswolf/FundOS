import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.analytics import (  # noqa: E402
    DatedPrice,
    align_prices_asof,
    calculate_nav,
    calculate_portfolio_returns,
    prices_to_returns,
)


def run_benchmark(*, years: int = 20, asset_count: int = 6) -> dict[str, float | int]:
    if years < 1 or asset_count < 2:
        raise ValueError("benchmark requires at least one year and two assets")
    observations = years * 252
    start = date(2000, 1, 3)
    prices: list[DatedPrice] = []
    symbols = [f"ASSET_{index + 1}" for index in range(asset_count)]
    for asset_index, symbol in enumerate(symbols):
        for index in range(observations):
            if index not in {0, observations - 1} and (index + asset_index) % 11 == 0:
                continue
            trade_date = start + timedelta(days=index)
            trend = 1 + index * (0.0001 + asset_index * 0.00001)
            cycle = 1 + ((index + asset_index * 3) % 17 - 8) * 0.0005
            prices.append(DatedPrice(symbol, trade_date, 100 * trend * cycle))

    started = perf_counter()
    dates, aligned, quality = align_prices_asof(
        prices,
        symbols,
        maximum_age_days=3,
    )
    asset_returns = prices_to_returns(aligned)
    weights = {symbol: 1 / asset_count for symbol in symbols}
    portfolio_returns = calculate_portfolio_returns(asset_returns, weights)
    nav = calculate_nav(portfolio_returns)
    elapsed = perf_counter() - started
    return {
        "years": years,
        "assets": asset_count,
        "source_rows": len(prices),
        "valuation_dates": len(dates),
        "carried_values": quality.carried_values,
        "final_nav": nav[-1],
        "elapsed_seconds": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark long-history portfolio analytics")
    parser.add_argument("--years", type=int, default=20)
    parser.add_argument("--assets", type=int, default=6)
    parser.add_argument("--max-seconds", type=float, default=5.0)
    arguments = parser.parse_args()
    result = run_benchmark(years=arguments.years, asset_count=arguments.assets)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["elapsed_seconds"] > arguments.max_seconds:
        raise SystemExit(
            f"Performance regression: {result['elapsed_seconds']:.3f}s "
            f"> {arguments.max_seconds:.3f}s"
        )


if __name__ == "__main__":
    main()
