import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.benchmark_history import run_benchmark  # noqa: E402


class HistoryPerformanceTests(unittest.TestCase):
    def test_twenty_year_six_asset_history_meets_baseline(self) -> None:
        result = run_benchmark(years=20, asset_count=6)
        self.assertEqual(result["valuation_dates"], 20 * 252)
        self.assertGreater(result["source_rows"], 25_000)
        self.assertGreater(result["carried_values"], 0)
        self.assertGreater(result["final_nav"], 0)
        self.assertLess(result["elapsed_seconds"], 5.0)


if __name__ == "__main__":
    unittest.main()
