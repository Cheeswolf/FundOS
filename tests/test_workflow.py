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
    create_proposal,
    create_research_report,
    finalize_research_report,
    publish_approved_workflow,
    record_committee_decision,
    run_risk_review,
)
from fundos.storage import Database  # noqa: E402


class WorkflowTests(unittest.TestCase):
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
            InvestmentMandate("P1", "Balanced", "medium", Decimal("0.60"), Decimal("0.05"), Decimal("0.30"))
        )
        self.database.upsert_prices([
            ("fixture", "EQUITY", date(2026, 7, 1), 100.0),
            ("fixture", "BOND", date(2026, 7, 1), 100.0),
            ("fixture", "CASH", date(2026, 7, 1), 1.0),
        ])
        report = ResearchReport(
            "R1", "P1", date(2026, 7, 1), "balanced", "Markets are stable.", Decimal("0.80"),
            (ResearchEvidence("E1", "Market note", "fixture", "https://example.test/e1", datetime(2026, 7, 1, tzinfo=timezone.utc)),),
            (AssetView("EQUITY", "neutral", Decimal("0.70"), "Valuation is balanced.", ("E1",)),),
        )
        create_research_report(self.database, report)
        finalize_research_report(self.database, report_id="R1")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def version(self, equity: str = "0.50", bond: str = "0.45", cash: str = "0.05") -> PortfolioVersion:
        return PortfolioVersion(
            "V1", "P1", 1, date(2026, 7, 1),
            (
                PositionWeight("EQUITY", Decimal(equity)),
                PositionWeight("BOND", Decimal(bond)),
                PositionWeight("CASH", Decimal(cash)),
            ),
        )

    def state(self, run_id: str) -> str:
        return self.database.fetch_all("SELECT state FROM workflow_runs WHERE run_id = ?", (run_id,))[0]["state"]

    def review(self, run_id: str, *, as_of_date: date = date(2026, 7, 1), equity_shock: float = -0.20):
        return run_risk_review(
            self.database,
            run_id=run_id,
            provider="fixture",
            as_of_date=as_of_date,
            stress_scenarios={
                "equity_selloff": {"EQUITY": equity_shock, "BOND": 0.02, "CASH": 0.0},
                "rate_shock": {"EQUITY": -0.05, "BOND": -0.08, "CASH": 0.0},
            },
        )

    def test_complete_workflow_publishes_version(self) -> None:
        run_id = create_proposal(
            self.database,
            version=self.version(),
            rationale="Initial balanced allocation",
            created_by="portfolio-manager",
            research_report_id="R1",
        )
        self.assertEqual(self.state(run_id), "proposed")
        report = self.review(run_id)
        self.assertTrue(report.passed)
        self.assertTrue(all(check.passed for check in report))
        self.assertEqual(self.state(run_id), "risk_passed")
        decision = record_committee_decision(
            self.database,
            run_id=run_id,
            approved=True,
            rationale="Constraints satisfied",
            decided_by="investment-committee",
        )
        self.assertEqual(decision, "approved")
        publish_approved_workflow(self.database, run_id=run_id)
        self.assertEqual(self.state(run_id), "published")
        version = self.database.fetch_all("SELECT status FROM portfolio_versions WHERE version_id = 'V1'")[0]
        self.assertEqual(version["status"], "published")

    def test_failed_risk_review_blocks_committee(self) -> None:
        run_id = create_proposal(
            self.database,
            version=self.version("0.70", "0.25", "0.05"),
            rationale="Concentrated proposal",
            created_by="portfolio-manager",
            research_report_id="R1",
        )
        report = self.review(run_id)
        failed_codes = {check.rule_code for check in report if not check.passed}
        self.assertIn("SINGLE_ASSET_LIMIT", failed_codes)
        self.assertEqual(self.state(run_id), "rejected")
        with self.assertRaisesRegex(ValueError, "passed risk review"):
            record_committee_decision(
                self.database,
                run_id=run_id,
                approved=True,
                rationale="Should not pass",
                decided_by="committee",
            )

    def test_cannot_publish_before_approval(self) -> None:
        run_id = create_proposal(
            self.database,
            version=self.version(),
            rationale="Initial proposal",
            created_by="portfolio-manager",
            research_report_id="R1",
        )
        with self.assertRaisesRegex(ValueError, "committee approval"):
            publish_approved_workflow(self.database, run_id=run_id)

    def test_stale_market_data_rejects_proposal(self) -> None:
        run_id = create_proposal(
            self.database,
            version=self.version(),
            rationale="Proposal with stale inputs",
            created_by="portfolio-manager",
            research_report_id="R1",
        )
        report = self.review(run_id, as_of_date=date(2026, 7, 10))
        failed = {check.rule_code for check in report if not check.passed}
        self.assertIn("DATA_FRESHNESS", failed)
        self.assertEqual(report.hard_failure_count, 1)
        self.assertEqual(self.state(run_id), "rejected")

    def test_excessive_stress_loss_rejects_proposal(self) -> None:
        run_id = create_proposal(
            self.database,
            version=self.version(),
            rationale="Proposal under stress",
            created_by="portfolio-manager",
            research_report_id="R1",
        )
        report = self.review(run_id, equity_shock=-0.60)
        failed = {check.rule_code for check in report if not check.passed}
        self.assertIn("STRESS_LOSS", failed)
        self.assertEqual(self.state(run_id), "rejected")


if __name__ == "__main__":
    unittest.main()
