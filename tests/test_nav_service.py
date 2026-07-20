import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import Asset, PortfolioProduct  # noqa: E402
from fundos.services import calculate_and_store_nav  # noqa: E402
from fundos.storage import Database  # noqa: E402


class NavServiceTests(unittest.TestCase):
    def test_calculates_and_persists_nav(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.upsert_assets([Asset("A", "Asset A", "equity"), Asset("B", "Asset B", "bond")])
            database.create_product(PortfolioProduct("P1", "Portfolio", "BM", datetime.now()))
            database.upsert_prices(
                [
                    ("fixture", "A", date(2026, 7, 1), 100),
                    ("fixture", "A", date(2026, 7, 2), 110),
                    ("fixture", "B", date(2026, 7, 1), 200),
                    ("fixture", "B", date(2026, 7, 2), 200),
                ]
            )
            nav = calculate_and_store_nav(
                database,
                product_id="P1",
                provider="fixture",
                weights={"A": 0.5, "B": 0.5},
            )
            self.assertEqual(len(nav), 2)
            self.assertAlmostEqual(nav[-1].nav, 1.05)
            stored = database.fetch_all("SELECT * FROM portfolio_nav ORDER BY nav_date")
            self.assertEqual(len(stored), 2)
            self.assertAlmostEqual(stored[-1]["nav"], 1.05)


if __name__ == "__main__":
    unittest.main()
