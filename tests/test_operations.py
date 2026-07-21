import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.services import run_operations_cycle  # noqa: E402
from fundos.storage import Database  # noqa: E402


class OperationsCycleTests(unittest.TestCase):
    def test_runs_idempotent_daily_cycle_and_marks_weekly_work_due(self) -> None:
        project_root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "operations.sqlite3"
            subprocess.run(
                [sys.executable, str(project_root / "scripts" / "seed_demo.py"), "--database", str(database_path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                check=True,
            )
            database = Database(database_path)
            first = run_operations_cycle(
                database,
                product_id="fundos-demo-balanced",
                provider="demo-synthetic",
                benchmark_symbol="BALANCED_BENCHMARK",
                as_of_date=date(2026, 7, 10),
            )
            second = run_operations_cycle(
                database,
                product_id="fundos-demo-balanced",
                provider="demo-synthetic",
                benchmark_symbol="BALANCED_BENCHMARK",
                as_of_date=date(2026, 7, 10),
            )
            self.assertEqual(first.status, "attention_required")
            self.assertTrue(first.performance_updated)
            self.assertTrue(first.weekly_decision_due)
            self.assertFalse(first.monthly_review_due)
            self.assertEqual(first.cycle_id, second.cycle_id)
            self.assertEqual(len(database.fetch_all("SELECT * FROM operations_cycles")), 1)

    def test_blocks_cycle_when_market_data_is_stale(self) -> None:
        project_root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "operations.sqlite3"
            subprocess.run(
                [sys.executable, str(project_root / "scripts" / "seed_demo.py"), "--database", str(database_path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                check=True,
            )
            result = run_operations_cycle(
                Database(database_path),
                product_id="fundos-demo-balanced",
                provider="demo-synthetic",
                benchmark_symbol="BALANCED_BENCHMARK",
                as_of_date=date(2026, 7, 20),
            )
            self.assertEqual(result.status, "blocked")
            self.assertFalse(result.performance_updated)
            self.assertGreater(result.data_age_days, 3)


if __name__ == "__main__":
    unittest.main()

