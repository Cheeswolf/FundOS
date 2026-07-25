import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.services import initialize_trial_product  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize the controlled trial product and calculate gross historical simulation"
    )
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "product.real_trial.json",
    )
    parser.add_argument("--provider", default="fundos-trial-normalized")
    arguments = parser.parse_args()

    configuration = json.loads(arguments.config.read_text(encoding="utf-8"))
    disclosure = configuration["performance_policy"]["required_disclosure"]
    result = initialize_trial_product(
        Database(arguments.database),
        configuration=configuration,
        provider=arguments.provider,
    )
    print(
        f"Trial product {result.product_id}, version {result.version_id}, "
        f"effective {result.effective_date}"
    )
    print(
        f"Portfolio NAV {result.performance.portfolio_nav:.6f}; "
        f"benchmark NAV {result.performance.benchmark_nav:.6f}; "
        f"excess return {result.performance.excess_return:.2%}"
    )
    print(f"Disclosure: {disclosure}")


if __name__ == "__main__":
    main()
