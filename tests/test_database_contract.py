import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import (  # noqa: E402
    Asset,
    AssetView,
    InvestmentMandate,
    PortfolioProduct,
    PortfolioVersion,
    PositionWeight,
    ResearchEvidence,
    ResearchReport,
)
from fundos.services import (  # noqa: E402
    calculate_and_store_versioned_performance,
    create_proposal,
    create_research_report,
    finalize_research_report,
    publish_approved_workflow,
    record_committee_decision,
    record_committee_opinion,
    run_risk_review,
    run_scheduled_job,
)
from fundos.services.audit_log import record_audit_event, verify_audit_chain  # noqa: E402
from fundos.storage import Database, PostgresDatabase  # noqa: E402


POSTGRES_URL = os.environ.get("FUNDOS_TEST_POSTGRES_URL", "").strip()


def exercise_database_contract(database, namespace: str) -> None:
    equity = f"{namespace}EQ"
    cash = f"{namespace}CS"
    benchmark = f"{namespace}BM"
    product_id = f"{namespace}-product"
    report_id = f"{namespace}-research"
    evidence_id = f"{namespace}-evidence"
    version_id = f"{namespace}-v1"
    run_id = f"{namespace}-run"
    database.upsert_assets(
        [
            Asset(equity, "Contract Equity", "equity"),
            Asset(cash, "Contract Cash", "cash"),
            Asset(benchmark, "Contract Benchmark", "benchmark"),
        ]
    )
    database.create_product(
        PortfolioProduct(product_id, "Contract Portfolio", benchmark, datetime.now())
    )
    database.upsert_investment_mandate(
        InvestmentMandate(
            product_id,
            "Cross-database contract",
            "medium",
            Decimal("0.80"),
            Decimal("0.10"),
            Decimal("1"),
            maximum_stress_loss=Decimal("0.40"),
        )
    )
    dates = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
    series = {
        equity: [100.0, 102.0, 104.0],
        cash: [1.0, 1.0, 1.0],
        benchmark: [100.0, 101.0, 102.0],
    }
    database.upsert_prices(
        [
            ("contract", symbol, nav_date, value)
            for symbol, values in series.items()
            for nav_date, value in zip(dates, values, strict=True)
        ]
    )
    research = ResearchReport(
        report_id,
        product_id,
        dates[0],
        "balanced",
        "Cross-database research.",
        Decimal("0.80"),
        (
            ResearchEvidence(
                evidence_id,
                "Contract evidence",
                "fixture",
                f"https://example.test/{namespace}",
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                "Deterministic evidence snapshot.",
            ),
        ),
        (
            AssetView(
                equity,
                "positive",
                Decimal("0.70"),
                "Expected measured growth.",
                (evidence_id,),
            ),
        ),
    )
    create_research_report(database, research)
    finalize_research_report(database, report_id=report_id)
    created_run = create_proposal(
        database,
        version=PortfolioVersion(
            version_id,
            product_id,
            1,
            dates[0],
            (
                PositionWeight(equity, Decimal("0.70")),
                PositionWeight(cash, Decimal("0.30")),
            ),
        ),
        rationale="Initial contract allocation",
        created_by="contract-test",
        research_report_id=report_id,
        run_id=run_id,
    )
    assert created_run == run_id
    risk = run_risk_review(
        database,
        run_id=run_id,
        provider="contract",
        as_of_date=dates[-1],
        stress_scenarios={
            "equity_selloff": {equity: -0.20, cash: 0.0},
        },
    )
    assert risk.passed
    record_committee_opinion(
        database,
        run_id=run_id,
        member_role="risk",
        recommendation="approve",
        rationale="Contract limits passed.",
        submitted_by="contract-risk",
    )
    decision = record_committee_decision(
        database,
        run_id=run_id,
        approved=True,
        rationale="Approved by contract.",
        decided_by="contract-committee",
        minimum_opinions=1,
    )
    assert decision == "approved"
    published = publish_approved_workflow(database, run_id=run_id)
    assert published.status == "published"

    performance = calculate_and_store_versioned_performance(
        database,
        product_id=product_id,
        provider="contract",
        benchmark_symbol=benchmark,
    )
    assert performance.portfolio_nav > 1
    assert len(database.fetch_all(
        "SELECT * FROM portfolio_nav WHERE product_id = ?",
        (product_id,),
    )) == 3
    assert len(database.fetch_all(
        "SELECT * FROM benchmark_nav WHERE product_id = ?",
        (product_id,),
    )) == 3

    scheduled = run_scheduled_job(
        database,
        job_name=f"{namespace}-daily",
        task=lambda: "completed",
        now=lambda: datetime(2026, 7, 26, tzinfo=timezone.utc),
    )
    assert scheduled.status == "succeeded"
    assert database.fetch_all(
        "SELECT * FROM scheduled_job_locks WHERE job_name = ?",
        (f"{namespace}-daily",),
    ) == []

    audit_id = f"{namespace}-audit"
    record_audit_event(
        database,
        {
            "audit_id": audit_id,
            "request_id": f"{namespace}-request",
            "method": "POST",
            "path": "/contract",
            "actor_id": "contract",
            "actor_role": "admin",
            "outcome": "succeeded",
            "status_code": 200,
            "client_ip": "127.0.0.1",
            "created_at": datetime(2026, 7, 26, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert verify_audit_chain(database)["valid"]


class SQLiteDatabaseContractTests(unittest.TestCase):
    def test_complete_business_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "contract.sqlite3")
            database.initialize()
            exercise_database_contract(database, "sqlite")


@unittest.skipUnless(
    POSTGRES_URL,
    "set FUNDOS_TEST_POSTGRES_URL to run PostgreSQL contract tests",
)
class PostgresDatabaseContractTests(unittest.TestCase):
    def test_complete_business_contract(self) -> None:
        database = PostgresDatabase(POSTGRES_URL)
        database.initialize()
        exercise_database_contract(database, f"pg{uuid4().hex[:8]}")


if __name__ == "__main__":
    unittest.main()
