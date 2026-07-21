import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import (  # noqa: E402
    Asset, AssetView, InvestmentMandate, PortfolioProduct, PortfolioVersion,
    PositionWeight, ResearchEvidence, ResearchReport,
)
from fundos.services import (  # noqa: E402
    create_research_report,
    finalize_research_report,
    generate_review_report,
    publish_portfolio_version,
)
from fundos.storage import Database  # noqa: E402


class ReviewTests(unittest.TestCase):
    def test_generates_attribution_rebalance_effect_and_view_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.upsert_assets([Asset("A", "Asset A", "equity"), Asset("B", "Asset B", "bond")])
            database.create_product(PortfolioProduct("P1", "Portfolio", "BM", datetime.now()))
            database.upsert_investment_mandate(
                InvestmentMandate("P1", "Test", "medium", Decimal("1"), Decimal("0"), Decimal("1"))
            )
            versions = [
                PortfolioVersion("V1", "P1", 1, date(2026, 7, 1), (PositionWeight("A", Decimal("1")),)),
                PortfolioVersion("V2", "P1", 2, date(2026, 7, 2), (PositionWeight("B", Decimal("1")),)),
            ]
            for version in versions:
                database.create_version(version)
                publish_portfolio_version(
                    database, version_id=version.version_id, reason="Test", approved_by="committee"
                )
            evidence = ResearchEvidence(
                "E1", "Research", "fixture", "https://example.test/research",
                datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
            report = ResearchReport(
                "R1", "P1", date(2026, 7, 1), "transition", "Expect bonds to outperform.", Decimal("0.8"),
                (evidence,),
                (
                    AssetView("A", "negative", Decimal("0.7"), "Equity risk is elevated.", ("E1",)),
                    AssetView("B", "positive", Decimal("0.8"), "Bond outlook is positive.", ("E1",)),
                ),
            )
            create_research_report(database, report)
            finalize_research_report(database, report_id="R1")
            dates = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
            series = {"A": [100, 110, 110], "B": [100, 100, 120]}
            database.upsert_prices([
                ("fixture", symbol, nav_date, value)
                for symbol, values in series.items()
                for nav_date, value in zip(dates, values, strict=True)
            ])

            result = generate_review_report(
                database,
                product_id="P1",
                research_report_id="R1",
                provider="fixture",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 3),
                summary="The rebalance improved performance.",
                lessons="Retain evidence-based risk controls.",
            )
            self.assertAlmostEqual(result.actual_return, 0.32)
            self.assertAlmostEqual(result.counterfactual_return, 0.10)
            self.assertAlmostEqual(result.rebalance_effect, 0.22)
            self.assertFalse(result.view_outcomes["A"])
            self.assertTrue(result.view_outcomes["B"])
            self.assertEqual(len(database.fetch_all("SELECT * FROM review_reports")), 1)
            self.assertEqual(len(database.fetch_all("SELECT * FROM research_view_outcomes")), 2)


if __name__ == "__main__":
    unittest.main()

