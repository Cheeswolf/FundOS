import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import (  # noqa: E402
    Asset, AssetView, PortfolioProduct, ResearchEvidence, ResearchReport,
)
from fundos.services import create_research_report, finalize_research_report  # noqa: E402
from fundos.storage import Database  # noqa: E402


class ResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "test.sqlite3")
        self.database.initialize()
        self.database.upsert_assets([Asset("EQUITY", "Equity", "equity")])
        self.database.create_product(PortfolioProduct("P1", "Portfolio", "BM", datetime.now()))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def report(self) -> ResearchReport:
        return ResearchReport(
            "R1", "P1", date(2026, 7, 1), "risk_on", "Growth conditions remain supportive.", Decimal("0.75"),
            (
                ResearchEvidence(
                    "E1", "Macro release", "official-source", "https://example.test/macro",
                    datetime(2026, 6, 30, tzinfo=timezone.utc),
                ),
            ),
            (
                AssetView(
                    "EQUITY", "positive", Decimal("0.80"),
                    "Improving growth supports equities.", ("E1",),
                ),
            ),
        )

    def test_persists_and_finalizes_structured_research(self) -> None:
        create_research_report(self.database, self.report())
        finalize_research_report(self.database, report_id="R1")
        report = self.database.fetch_all("SELECT * FROM research_reports WHERE report_id = 'R1'")[0]
        self.assertEqual(report["status"], "final")
        self.assertEqual(len(self.database.fetch_all("SELECT * FROM research_evidence")), 1)
        self.assertEqual(len(self.database.fetch_all("SELECT * FROM asset_views")), 1)
        self.assertEqual(len(self.database.fetch_all("SELECT * FROM asset_view_evidence")), 1)

    def test_rejects_future_evidence(self) -> None:
        report = self.report()
        future = ResearchReport(
            report.report_id, report.product_id, report.as_of_date, report.market_regime,
            report.summary, report.confidence,
            (ResearchEvidence("E1", "Future", "source", "https://example.test/future", datetime(2026, 7, 2, tzinfo=timezone.utc)),),
            report.asset_views,
        )
        with self.assertRaisesRegex(ValueError, "after the report date"):
            create_research_report(self.database, future)

    def test_rejects_unknown_evidence_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            ResearchReport(
                "R1", "P1", date(2026, 7, 1), "neutral", "Summary", Decimal("0.5"),
                (),
                (AssetView("EQUITY", "neutral", Decimal("0.5"), "Thesis", ("missing",)),),
            )


if __name__ == "__main__":
    unittest.main()
