import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import Asset, InvestmentMandate, PortfolioProduct, PortfolioVersion, PositionWeight  # noqa: E402
from fundos.services import publish_portfolio_version  # noqa: E402
from fundos.storage import Database  # noqa: E402


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "test.sqlite3")
        self.database.initialize()
        self.database.upsert_assets([
            Asset("EQUITY", "Equity", "equity"),
            Asset("BOND", "Bond", "fixed_income"),
            Asset("CASH", "Cash", "cash"),
        ])
        self.database.create_product(PortfolioProduct("P1", "Portfolio", "BM", datetime.now()))
        self.database.upsert_investment_mandate(
            InvestmentMandate(
                "P1", "Balanced growth", "medium",
                Decimal("0.60"), Decimal("0.05"), Decimal("0.30"),
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_version(self, version_id: str, number: int, effective_date: date, equity: str, bond: str, cash: str) -> None:
        self.database.create_version(
            PortfolioVersion(
                version_id, "P1", number, effective_date,
                (
                    PositionWeight("EQUITY", Decimal(equity)),
                    PositionWeight("BOND", Decimal(bond)),
                    PositionWeight("CASH", Decimal(cash)),
                ),
            )
        )

    def test_publishes_version_and_records_rebalance(self) -> None:
        self.create_version("V1", 1, date(2026, 7, 1), "0.50", "0.45", "0.05")
        first = publish_portfolio_version(self.database, version_id="V1", reason="Initial allocation", approved_by="committee")
        self.assertEqual(first.status, "published")
        self.assertEqual(first.turnover, 0)

        self.create_version("V2", 2, date(2026, 7, 8), "0.40", "0.50", "0.10")
        second = publish_portfolio_version(self.database, version_id="V2", reason="Reduce equity risk", approved_by="committee")
        self.assertAlmostEqual(second.turnover, 0.10)
        records = self.database.fetch_all("SELECT * FROM rebalance_records ORDER BY rebalance_id")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["previous_version_id"], "V1")

    def test_rejects_single_asset_limit_violation(self) -> None:
        self.create_version("V1", 1, date(2026, 7, 1), "0.70", "0.25", "0.05")
        with self.assertRaisesRegex(ValueError, "single asset"):
            publish_portfolio_version(self.database, version_id="V1", reason="Invalid", approved_by="committee")
        status = self.database.fetch_all("SELECT status FROM portfolio_versions WHERE version_id = 'V1'")[0]
        self.assertEqual(status["status"], "draft")

    def test_rejects_turnover_above_limit(self) -> None:
        self.create_version("V1", 1, date(2026, 7, 1), "0.50", "0.45", "0.05")
        publish_portfolio_version(self.database, version_id="V1", reason="Initial", approved_by="committee")
        self.create_version("V2", 2, date(2026, 7, 8), "0.10", "0.60", "0.30")
        with self.assertRaisesRegex(ValueError, "turnover"):
            publish_portfolio_version(self.database, version_id="V2", reason="Too large", approved_by="committee")


if __name__ == "__main__":
    unittest.main()
