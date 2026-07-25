import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import (  # noqa: E402
    Asset, PortfolioProduct, PortfolioVersion, PositionWeight,
)
from fundos.services import validate_research_agent_request  # noqa: E402
from fundos.storage import Database  # noqa: E402


class AgentResearchPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "agent.sqlite3")
        self.database.initialize()
        self.database.upsert_assets(
            [Asset("EQUITY", "Equity", "equity"), Asset("CASH", "Cash", "cash")]
        )
        self.database.create_product(PortfolioProduct("P1", "Portfolio", "BM", datetime.now()))
        version = PortfolioVersion(
            "V1", "P1", 1, date(2026, 7, 1),
            (
                PositionWeight("EQUITY", Decimal("0.8")),
                PositionWeight("CASH", Decimal("0.2")),
            ),
        )
        self.database.create_version(version)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE portfolio_versions SET status = 'published' WHERE version_id = 'V1'"
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def request(self) -> dict:
        return {
            "report_id": "R1",
            "product_id": "P1",
            "as_of_date": "2026-07-20",
            "asset_symbols": ["EQUITY", "CASH"],
            "evidence": [
                {
                    "evidence_id": "E1",
                    "published_at": "2026-07-19T08:00:00+08:00",
                    "content": "Official data used for the controlled analysis.",
                }
            ],
        }

    def test_accepts_complete_controlled_request(self) -> None:
        validate_research_agent_request(self.database, self.request())

    def test_rejects_metadata_only_evidence(self) -> None:
        request = self.request()
        request["evidence"][0]["content"] = ""
        with self.assertRaisesRegex(ValueError, "content is required"):
            validate_research_agent_request(self.database, request)

    def test_rejects_assets_outside_current_portfolio(self) -> None:
        request = self.request()
        request["asset_symbols"] = ["EQUITY"]
        with self.assertRaisesRegex(ValueError, "exactly match"):
            validate_research_agent_request(self.database, request)


if __name__ == "__main__":
    unittest.main()
