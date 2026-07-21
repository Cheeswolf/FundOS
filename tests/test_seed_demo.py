import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SeedDemoTests(unittest.TestCase):
    def test_seeds_complete_demo_idempotently(self) -> None:
        project_root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "demo.sqlite3"
            command = [sys.executable, str(project_root / "scripts" / "seed_demo.py"), "--database", str(database_path)]
            first = subprocess.run(command, cwd=project_root, capture_output=True, text=True, check=True)
            second = subprocess.run(command, cwd=project_root, capture_output=True, text=True, check=True)
            self.assertIn("Demo product: fundos-demo-balanced", first.stdout)
            self.assertIn("Demo product: fundos-demo-balanced", second.stdout)
            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM portfolio_versions").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_reports").fetchone()[0], 1)
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM portfolio_nav").fetchone()[0], 20)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()

