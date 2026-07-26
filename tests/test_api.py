import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from fundos.api import create_app  # noqa: E402
from fundos.domain import Asset, PortfolioProduct  # noqa: E402
from fundos.services import (  # noqa: E402
    import_raw_research_evidence,
    register_research_sources,
    run_scheduled_job,
)


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temporary_directory.name) / "api.sqlite3", api_key="test-secret")
        self.client = TestClient(self.app)
        self.write_headers = {"X-API-Key": "test-secret"}

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_dashboard_is_served(self) -> None:
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("FundOS 投资组合运营台", response.text)
        self.assertIn("组合总览", response.text)
        self.assertIn("fundos-index-allocation-trial", response.text)
        self.assertIn("历史模拟业绩", response.text)
        self.assertIn("运行监控", response.text)
        self.assertIn("/scheduled-jobs/runs", response.text)
        self.assertIn("决策操作", response.text)
        self.assertIn("proposalForm", response.text)
        self.assertIn("/committee-decision", response.text)
        self.assertIn("Idempotency-Key", response.text)

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
        self.assertEqual(self.client.get("/products/missing/operations").json(), [])
        self.assertEqual(self.client.get("/pipeline-runs").json(), [])
        self.assertEqual(self.client.get("/alerts").json(), [])
        self.assertEqual(self.client.get("/scheduled-jobs/runs").json(), [])
        self.assertEqual(self.client.get("/scheduled-jobs/locks").json(), [])
        self.assertEqual(self.client.get("/model-calls").json(), [])
        self.assertIsNone(self.client.get("/model-calls/summary").json()["success_rate"])

    def test_lists_scheduled_job_runs_and_active_locks_without_owner_id(self) -> None:
        run_scheduled_job(
            self.app.state.database,
            job_name="daily-production",
            task=lambda: "done",
        )
        runs = self.client.get("/scheduled-jobs/runs?job_name=daily-production").json()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "succeeded")
        self.assertNotIn("owner_id", runs[0])

        now = datetime.now(timezone.utc)
        with self.app.state.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_job_locks
                    (job_name, owner_id, acquired_at, lease_until)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "weekly-research", "private-owner", now.isoformat(),
                    (now + timedelta(minutes=10)).isoformat(),
                ),
            )
        locks = self.client.get("/scheduled-jobs/locks").json()
        self.assertEqual(locks[0]["job_name"], "weekly-research")
        self.assertTrue(locks[0]["active"])
        self.assertNotIn("owner_id", locks[0])

    def test_rejects_invalid_scheduled_job_status_filter(self) -> None:
        response = self.client.get("/scheduled-jobs/runs?status=unknown")
        self.assertEqual(response.status_code, 422)

    def test_admin_reviews_raw_research_evidence(self) -> None:
        self.app.state.database.upsert_assets([Asset("BOND", "Bond", "bond")])
        register_research_sources(
            self.app.state.database,
            [{
                "source_id": "official",
                "name": "Official Source",
                "source_type": "official",
                "allowed_domains": ["official.example"],
                "asset_symbols": ["BOND"],
                "license_note": "Verified official source.",
            }],
        )
        imported = import_raw_research_evidence(
            self.app.state.database,
            {
                "source_id": "official",
                "title": "Release",
                "url": "https://official.example/release",
                "published_at": "2026-07-24T08:00:00+08:00",
                "asset_symbols": ["BOND"],
                "content": "Verified factual excerpt.",
            },
            retrieved_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        )
        listing = self.client.get(
            "/research-evidence?status=pending", headers=self.write_headers
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["asset_symbols"], ["BOND"])
        reviewed = self.client.post(
            f"/research-evidence/{imported.raw_evidence_id}/review",
            headers=self.write_headers,
            json={
                "approved": True,
                "reviewed_by": "governance-owner",
                "note": "Source and content verified.",
            },
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json()["review_status"], "approved")

    def test_reports_model_call_usage_and_hides_prompt_hash(self) -> None:
        with self.app.state.database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO model_calls (
                    call_id, purpose, provider, model, status, attempts, latency_ms,
                    input_tokens, output_tokens, estimated_cost_usd, prompt_sha256,
                    error_message, created_at
                ) VALUES (?, 'research_draft', 'fixture', 'model-a', ?, ?, ?, ?, ?, ?, 'hash', ?, ?)
                """,
                [
                    ("C1", "succeeded", 1, 100, 20, 5, 0.00008, None, datetime.now().astimezone().isoformat()),
                    ("C2", "failed", 3, 300, None, None, None, "timeout", datetime.now().astimezone().isoformat()),
                ],
            )

        summary = self.client.get("/model-calls/summary?days=7").json()
        self.assertEqual((summary["total_calls"], summary["total_attempts"]), (2, 4))
        self.assertEqual(summary["success_rate"], 0.5)
        self.assertEqual((summary["input_tokens"], summary["output_tokens"]), (20, 5))
        calls = self.client.get("/model-calls?status=failed").json()
        self.assertEqual(calls[0]["call_id"], "C2")
        self.assertNotIn("prompt_sha256", calls[0])
        self.assertEqual(self.client.get("/model-calls?status=unknown").status_code, 422)

    def test_admin_controls_model_circuit_and_alert_lifecycle(self) -> None:
        for index in range(3):
            with self.app.state.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO model_calls (
                        call_id, purpose, provider, model, status, attempts, latency_ms,
                        prompt_sha256, created_at
                    ) VALUES (?, 'research', 'fixture', 'model-a', 'failed', 1, 1, 'hash', ?)
                    """,
                    (f"FAIL{index}", datetime.now().astimezone().isoformat()),
                )
        status = self.client.get("/model-policy/status?provider=fixture&model=model-a")
        self.assertEqual(status.json()["state"], "open")
        self.assertEqual(self.client.post("/model-policy/circuit-reset", json={
            "provider": "fixture", "model": "model-a", "reset_by": "admin", "reason": "recovered",
        }).status_code, 401)
        reset = self.client.post("/model-policy/circuit-reset", headers=self.write_headers, json={
            "provider": "fixture", "model": "model-a", "reset_by": "admin", "reason": "recovered",
        })
        self.assertEqual(reset.json()["state"], "closed")
        self.assertEqual(
            self.client.get("/model-policy/status?provider=fixture&model=model-a").json()["state"],
            "closed",
        )

        from fundos.services import create_alert
        alert_id = create_alert(
            self.app.state.database, source_type="model_policy", source_id="test-alert",
            severity="warning", title="Budget reached", message="Review usage",
        )
        acknowledged = self.client.post(
            f"/alerts/{alert_id}/acknowledge", headers=self.write_headers,
            json={"updated_by": "admin", "note": "reviewing"},
        )
        self.assertEqual(acknowledged.json()["lifecycle_state"], "acknowledged")
        resolved = self.client.post(
            f"/alerts/{alert_id}/resolve", headers=self.write_headers,
            json={"updated_by": "admin", "note": "usage approved"},
        )
        self.assertEqual(resolved.json()["lifecycle_state"], "resolved")
        listed = self.client.get("/alerts").json()
        self.assertEqual(listed[0]["lifecycle_state"], "resolved")

    def test_write_endpoints_require_api_key(self) -> None:
        response = self.client.post("/assets", json=[
            {"symbol": "A", "name": "Asset", "asset_class": "equity"}
        ])
        self.assertEqual(response.status_code, 401)

    def test_role_scoped_keys_separate_operations_from_admin_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            role_app = create_app(
                Path(directory) / "roles.sqlite3",
                api_keys={"operator-secret": "operator", "admin-secret": "admin"},
            )
            with TestClient(role_app) as client:
                operator = {"X-API-Key": "operator-secret"}
                admin = {"X-API-Key": "admin-secret"}
                created = client.post("/assets", headers=operator, json=[
                    {"symbol": "A", "name": "Asset", "asset_class": "equity"}
                ])
                self.assertEqual(created.status_code, 201)
                self.assertEqual(client.get("/products").status_code, 200)
                decision_payload = {
                    "approved": True, "rationale": "Approved", "decided_by": "committee",
                }
                self.assertEqual(
                    client.post("/workflows/missing/committee-decision", headers=operator, json=decision_payload).status_code,
                    403,
                )
                self.assertNotEqual(
                    client.post("/workflows/missing/committee-decision", headers=admin, json=decision_payload).status_code,
                    403,
                )
                reset_payload = {
                    "provider": "fixture", "model": "model-a",
                    "reset_by": "operator", "reason": "test",
                }
                self.assertEqual(
                    client.post("/model-policy/circuit-reset", headers=operator, json=reset_payload).status_code,
                    403,
                )
                self.assertEqual(
                    client.post("/model-policy/circuit-reset", headers=admin, json=reset_payload).status_code,
                    200,
                )

    def test_rejects_invalid_role_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "roles"):
                create_app(Path(directory) / "invalid.sqlite3", api_keys={"key": "viewer"})

    def test_audits_successful_and_rejected_writes_without_exposing_keys(self) -> None:
        successful = self.client.post(
            "/assets", headers={**self.write_headers, "X-Request-ID": "request-success"},
            json=[{"symbol": "AUDIT", "name": "Audit Asset", "asset_class": "cash"}],
        )
        self.assertEqual(successful.status_code, 201)
        rejected = self.client.post(
            "/assets", headers={"X-API-Key": "wrong-secret", "X-Request-ID": "request-rejected"},
            json=[{"symbol": "NO", "name": "Rejected", "asset_class": "cash"}],
        )
        self.assertEqual(rejected.status_code, 401)

        response = self.client.get("/audit-events", headers=self.write_headers)
        self.assertEqual(response.status_code, 200)
        events = {item["request_id"]: item for item in response.json()}
        self.assertEqual(events["request-success"]["outcome"], "succeeded")
        self.assertEqual(events["request-success"]["actor_role"], "admin")
        self.assertEqual(events["request-rejected"]["outcome"], "rejected")
        self.assertEqual(events["request-rejected"]["actor_role"], "unknown")
        serialized = str(events)
        self.assertNotIn("test-secret", serialized)
        self.assertNotIn("wrong-secret", serialized)

    def test_audit_log_requires_admin_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            role_app = create_app(
                Path(directory) / "audit-roles.sqlite3",
                api_keys={"operator-secret": "operator", "admin-secret": "admin"},
            )
            with TestClient(role_app) as client:
                self.assertEqual(
                    client.get("/audit-events", headers={"X-API-Key": "operator-secret"}).status_code,
                    403,
                )
                self.assertEqual(
                    client.get("/audit-events", headers={"X-API-Key": "admin-secret"}).status_code,
                    200,
                )

    def test_admin_can_verify_export_and_apply_audit_retention(self) -> None:
        self.client.post(
            "/assets", headers=self.write_headers,
            json=[{"symbol": "EXPORT", "name": "Export Asset", "asset_class": "cash"}],
        )
        integrity = self.client.get("/audit-events/integrity", headers=self.write_headers)
        self.assertEqual(integrity.status_code, 200)
        self.assertTrue(integrity.json()["valid"])
        exported = self.client.get("/audit-events/export.csv", headers=self.write_headers)
        self.assertEqual(exported.status_code, 200)
        self.assertIn("text/csv", exported.headers["content-type"])
        self.assertIn("audit_id,request_id", exported.text)
        retention = self.client.post(
            "/audit-events/retention?days=365", headers=self.write_headers,
        )
        self.assertEqual(retention.status_code, 200)
        self.assertEqual(retention.json()["retention_days"], 365)

    def test_complete_write_workflow(self) -> None:
        assets = self.client.post("/assets", headers=self.write_headers, json=[
            {"symbol": "EQUITY", "name": "Equity", "asset_class": "equity"},
            {"symbol": "CASH", "name": "Cash", "asset_class": "cash"},
        ])
        self.assertEqual(assets.status_code, 201)
        product_headers = {**self.write_headers, "Idempotency-Key": "create-product-P1"}
        product_payload = {
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
        }
        product = self.client.post("/products", headers=product_headers, json=product_payload)
        self.assertEqual(product.status_code, 201)
        repeated_product = self.client.post("/products", headers=product_headers, json=product_payload)
        self.assertEqual(repeated_product.status_code, 201)
        self.assertEqual(len(self.client.get("/products").json()), 1)
        prices = self.client.post("/market-prices", headers=self.write_headers, json={
            "provider": "fixture",
            "prices": [
                {"symbol": "EQUITY", "trade_date": "2026-07-01", "close": 100},
                {"symbol": "CASH", "trade_date": "2026-07-01", "close": 1},
            ],
        })
        self.assertEqual(prices.status_code, 201)
        research = self.client.post("/research", headers=self.write_headers, json={
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
        proposal = self.client.post("/proposals", headers=self.write_headers, json={
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
        risk = self.client.post("/workflows/RUN1/risk-review", headers=self.write_headers, json={
            "provider": "fixture",
            "as_of_date": "2026-07-01",
            "stress_scenarios": {
                "equity_selloff": {"EQUITY": -0.2, "CASH": 0.0}
            },
        })
        self.assertEqual(risk.status_code, 200)
        self.assertTrue(risk.json()["passed"])
        decision = self.client.post("/workflows/RUN1/committee-decision", headers=self.write_headers, json={
            "approved": True,
            "rationale": "All constraints passed.",
            "decided_by": "investment-committee",
        })
        self.assertEqual(decision.status_code, 200)
        publish_headers = {**self.write_headers, "Idempotency-Key": "publish-RUN1"}
        published = self.client.post("/workflows/RUN1/publish", headers=publish_headers)
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.json()["status"], "published")
        repeated_publish = self.client.post("/workflows/RUN1/publish", headers=publish_headers)
        self.assertEqual(repeated_publish.status_code, 200)
        self.assertEqual(repeated_publish.json(), published.json())
        workflow = self.client.get("/workflows/RUN1").json()
        self.assertEqual(workflow["run"]["state"], "published")

    def test_operator_can_finalize_draft_research(self) -> None:
        self.client.post("/assets", headers=self.write_headers, json=[
            {"symbol": "EQUITY", "name": "Equity", "asset_class": "equity"},
        ])
        self.app.state.database.create_product(
            PortfolioProduct("P1", "Portfolio", "BM", datetime.now())
        )
        created = self.client.post("/research", headers=self.write_headers, json={
            "report_id": "DRAFT1",
            "product_id": "P1",
            "as_of_date": "2026-07-01",
            "market_regime": "neutral",
            "summary": "Draft research.",
            "confidence": 0.7,
            "evidence": [{
                "evidence_id": "E1",
                "title": "Evidence",
                "source": "fixture",
                "url": "https://example.test/evidence",
                "published_at": "2026-07-01T00:00:00Z",
            }],
            "asset_views": [{
                "asset_symbol": "EQUITY",
                "direction": "neutral",
                "confidence": 0.7,
                "thesis": "Balanced.",
                "evidence_ids": ["E1"],
            }],
            "finalize": False,
        })
        self.assertEqual(created.status_code, 201)
        finalized = self.client.post(
            "/research/DRAFT1/finalize", headers=self.write_headers,
        )
        self.assertEqual(finalized.status_code, 200)
        self.assertEqual(finalized.json()["status"], "final")


if __name__ == "__main__":
    unittest.main()
