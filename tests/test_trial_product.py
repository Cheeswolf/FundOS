import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import Asset  # noqa: E402
from fundos.services import initialize_trial_product  # noqa: E402
from fundos.storage import Database  # noqa: E402


class TrialProductTests(unittest.TestCase):
    def test_initializes_and_recalculates_trial_product_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.upsert_assets([
                Asset("EQUITY", "Equity Fund", "equity"),
                Asset("CASH", "Cash", "cash"),
                Asset("BM", "Benchmark", "benchmark"),
            ])
            dates = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
            values = {
                "EQUITY": [1.0, 1.1, 1.21],
                "CASH": [1.0, 1.0, 1.0],
                "BM": [1.0, 1.05, 1.1025],
            }
            database.upsert_prices(
                ("trial", symbol, trade_date, close)
                for symbol, closes in values.items()
                for trade_date, close in zip(dates, closes, strict=True)
            )
            configuration = {
                "product": {
                    "product_id": "trial-product",
                    "name": "Trial Product",
                    "objective": "Controlled historical simulation",
                    "risk_level": "medium",
                },
                "constraints": {
                    "maximum_single_asset_weight": 0.8,
                    "minimum_cash_weight": 0.2,
                    "maximum_turnover_per_rebalance": 0.2,
                    "maximum_data_age_days": 3,
                    "maximum_stress_loss": 0.2,
                },
                "strategic_allocation": [
                    {"symbol": "EQUITY", "target": 0.8},
                    {"symbol": "CASH", "target": 0.2},
                ],
                "benchmark": {"symbol": "BM"},
            }

            first = initialize_trial_product(
                database,
                configuration=configuration,
                provider="trial",
            )
            second = initialize_trial_product(
                database,
                configuration=configuration,
                provider="trial",
            )
            self.assertTrue(first.created_product)
            self.assertTrue(first.created_version)
            self.assertFalse(second.created_product)
            self.assertFalse(second.created_version)
            self.assertEqual(first.effective_date, "2026-07-01")
            self.assertAlmostEqual(second.performance.portfolio_nav, 1.1664)
            self.assertEqual(
                len(database.fetch_all(
                    "SELECT * FROM portfolio_versions WHERE product_id = ?",
                    ("trial-product",),
                )),
                1,
            )
            self.assertEqual(
                len(database.fetch_all(
                    "SELECT * FROM rebalance_records WHERE product_id = ?",
                    ("trial-product",),
                )),
                1,
            )


if __name__ == "__main__":
    unittest.main()
