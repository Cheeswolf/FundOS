import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import Asset, PortfolioProduct  # noqa: E402
from fundos.storage import Database  # noqa: E402
from fundos.storage.cutover import (  # noqa: E402
    drill_cutover,
    probe_candidate_api,
    snapshot_database,
)


class CutoverDrillTests(unittest.TestCase):
    def create_database(self, path: Path) -> Database:
        database = Database(path)
        database.initialize()
        database.upsert_assets([Asset("CASH", "Cash", "cash")])
        database.create_product(
            PortfolioProduct(
                "P1",
                "Portfolio",
                "CASH",
                datetime(2026, 7, 26),
            )
        )
        return database

    def test_matching_databases_are_ready_for_manual_cutover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.create_database(Path(directory) / "source.sqlite3")
            target = self.create_database(Path(directory) / "target.sqlite3")

            report = drill_cutover(source, target)

        self.assertTrue(report.ready)
        self.assertEqual(report.decision, "ready_for_manual_cutover")
        self.assertTrue(all(report.checks.values()))
        self.assertIn("No runtime configuration was changed", report.rollback)

    def test_difference_keeps_runtime_on_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.create_database(Path(directory) / "source.sqlite3")
            target = self.create_database(Path(directory) / "target.sqlite3")
            target.upsert_assets([Asset("BOND", "Bond", "fixed_income")])

            report = drill_cutover(source, target)

        self.assertFalse(report.ready)
        self.assertEqual(report.decision, "remain_on_sqlite")
        self.assertFalse(report.checks["row_counts_match"])
        self.assertFalse(report.checks["content_digests_match"])

    def test_snapshot_excludes_backend_metadata_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = self.create_database(Path(directory) / "source.sqlite3")

            snapshot = snapshot_database(database)

        names = {table.table_name for table in snapshot.tables}
        self.assertNotIn("schema_migrations", names)
        self.assertNotIn("sqlite_sequence", names)
        self.assertIn("portfolio_products", names)

    def test_candidate_api_smoke_uses_read_only_endpoints(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return b"ok"

        with patch(
            "fundos.storage.cutover.urlopen",
            return_value=Response(),
        ) as mocked:
            checks = probe_candidate_api("http://127.0.0.1:9000")

        self.assertEqual(
            checks,
            {"/health": True, "/products": True, "/dashboard": True},
        )
        requested = [call.args[0] for call in mocked.call_args_list]
        self.assertEqual(
            requested,
            [
                "http://127.0.0.1:9000/health",
                "http://127.0.0.1:9000/products",
                "http://127.0.0.1:9000/dashboard",
            ],
        )


if __name__ == "__main__":
    unittest.main()
