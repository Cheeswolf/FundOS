import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import Asset  # noqa: E402
from fundos.storage import Database  # noqa: E402


class DatabaseTests(unittest.TestCase):
    def test_initializes_and_upserts_market_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.upsert_assets([Asset("CSI300", "沪深300", "equity")])
            inserted = database.upsert_prices(
                [("fixture", "CSI300", date(2026, 7, 21), 4012.5)]
            )
            self.assertEqual(inserted, 1)
            rows = database.fetch_all("SELECT * FROM market_prices")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "CSI300")

    def test_rejects_prices_for_unknown_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            with self.assertRaises(Exception):
                database.upsert_prices([("fixture", "UNKNOWN", date(2026, 7, 21), 1.0)])


if __name__ == "__main__":
    unittest.main()
