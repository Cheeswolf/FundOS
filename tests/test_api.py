import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from fundos.api import create_app  # noqa: E402
from fundos.domain import PortfolioProduct  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temporary_directory.name) / "api.sqlite3")
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_lists_and_gets_product(self) -> None:
        self.app.state.database.create_product(
            PortfolioProduct("P1", "Test Portfolio", "BM", datetime.now())
        )
        listing = self.client.get("/products")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["product_id"], "P1")
        detail = self.client.get("/products/P1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["product"]["name"], "Test Portfolio")

    def test_missing_resources_return_404(self) -> None:
        self.assertEqual(self.client.get("/products/missing").status_code, 404)
        self.assertEqual(self.client.get("/workflows/missing").status_code, 404)

    def test_complete_write_workflow(self) -> None:
        assets = self.client.post("/assets", json=[
            {"symbol": "EQUITY", "name": "Equity", "asset_class": "equity"},
            {"symbol": "CASH", "name": "Cash", "asset_class": "cash"},
        ])
        self.assertEqual(assets.status_code, 201)
        product = self.client.post("/products", json={
            "product_id": "P1",
            "name": "API Portfolio",
            "benchmark_symbol": "BM",
            "objective": "Balanced growth",
            "risk_level": "medium",
            "max_single_asset_weight": 0.8,
            "min_cash_weight": 0.1,
            "max_turnover": 0.5,
            "maximum_data_age_days": 3,
            "maximum_stress_loss": 0.3,
        })
        self.assertEqual(product.status_code, 201)
        prices = self.client.post("/market-prices", json={
            "provider": "fixture",
            "prices": [
                {"symbol": "EQUITY", "trade_date": "2026-07-01", "close": 100},
                {"symbol": "CASH", "trade_date": "2026-07-01", "close": 1},
            ],
        })
        self.assertEqual(prices.status_code, 201)
        research = self.client.post("/research", json={
            "report_id": "R1",
            "product_id": "P1",
            "as_of_date": "2026-07-01",
            "market_regime": "neutral",
            "summary": "Markets remain balanced.",
            "confidence": 0.75,
            "evidence": [{
                "evidence_id": "E1",
                "title": "Market report",
                "source": "fixture",
                "url": "https://example.test/report",
                "published_at": "2026-07-01T00:00:00Z",
            }],
            "asset_views": [{
                "asset_symbol": "EQUITY",
                "direction": "neutral",
                "confidence": 0.7,
                "thesis": "Valuation is balanced.",
                "evidence_ids": ["E1"],
            }],
            "finalize": True,
        })
        self.assertEqual(research.status_code, 201)
        proposal = self.client.post("/proposals", json={
            "version_id": "V1",
            "product_id": "P1",
            "version_number": 1,
            "effective_date": "2026-07-01",
            "weights": [
                {"asset_symbol": "EQUITY", "weight": 0.8},
                {"asset_symbol": "CASH", "weight": 0.2},
            ],
            "research_report_id": "R1",
            "rationale": "Initial allocation",
            "created_by": "portfolio-manager",
            "run_id": "RUN1",
        })
        self.assertEqual(proposal.status_code, 201)
        risk = self.client.post("/workflows/RUN1/risk-review", json={
            "provider": "fixture",
            "as_of_date": "2026-07-01",
            "stress_scenarios": {
                "equity_selloff": {"EQUITY": -0.2, "CASH": 0.0}
            },
        })
        self.assertEqual(risk.status_code, 200)
        self.assertTrue(risk.json()["passed"])
        decision = self.client.post("/workflows/RUN1/committee-decision", json={
            "approved": True,
            "rationale": "All constraints passed.",
            "decided_by": "investment-committee",
        })
        self.assertEqual(decision.status_code, 200)
        published = self.client.post("/workflows/RUN1/publish")
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.json()["status"], "published")
        workflow = self.client.get("/workflows/RUN1").json()
        self.assertEqual(workflow["run"]["state"], "published")


if __name__ == "__main__":
    unittest.main()
