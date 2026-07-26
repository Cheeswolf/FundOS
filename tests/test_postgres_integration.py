import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from fundos.api import create_app  # noqa: E402
from fundos.api.main import _idempotent  # noqa: E402
from fundos.domain import Asset, PortfolioProduct  # noqa: E402
from fundos.storage import Database  # noqa: E402
from fundos.storage.data_migration import migrate_sqlite_to_postgres  # noqa: E402
from fundos.storage.cutover import drill_cutover  # noqa: E402


POSTGRES_URL = os.environ.get("FUNDOS_TEST_POSTGRES_URL", "").strip()


@unittest.skipUnless(
    POSTGRES_URL,
    "set FUNDOS_TEST_POSTGRES_URL to run PostgreSQL integration tests",
)
class PostgresApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app(database_url=POSTGRES_URL, api_key="postgres-test-key")
        cls.client = TestClient(cls.app)
        cls.headers = {"X-API-Key": "postgres-test-key"}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_00_migrates_sqlite_data_and_verifies_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Database(Path(directory) / "source.sqlite3")
            source.initialize()
            source.upsert_assets([Asset("MIGCASH", "Migration Cash", "cash")])
            source.create_product(
                PortfolioProduct(
                    "migrated-product",
                    "Migrated Product",
                    "MIGCASH",
                    datetime(2026, 7, 26),
                )
            )

            report = migrate_sqlite_to_postgres(
                source,
                self.app.state.database,
            )
            drill = drill_cutover(source, self.app.state.database)

        self.assertEqual(report.source_schema_version, 14)
        self.assertEqual(report.target_schema_version, 14)
        self.assertGreater(report.total_rows, 0)
        self.assertTrue(drill.ready)
        migrated = self.client.get("/products/migrated-product")
        self.assertEqual(migrated.status_code, 200, migrated.text)
        self.assertEqual(
            migrated.json()["product"]["name"],
            "Migrated Product",
        )

    def test_health_and_idempotent_product_write(self) -> None:
        suffix = uuid4().hex[:12]
        asset_symbol = f"PG{suffix}"
        product_id = f"postgres-{suffix}"
        asset = self.client.post(
            "/assets",
            headers=self.headers,
            json=[
                {
                    "symbol": asset_symbol,
                    "name": "PostgreSQL integration asset",
                    "asset_class": "cash",
                }
            ],
        )
        self.assertEqual(asset.status_code, 201, asset.text)
        payload = {
            "product_id": product_id,
            "name": "PostgreSQL integration portfolio",
            "benchmark_symbol": asset_symbol,
            "objective": "Verify PostgreSQL API persistence",
            "risk_level": "low",
            "max_single_asset_weight": 1,
            "min_cash_weight": 0,
            "max_turnover": 1,
            "maximum_data_age_days": 5,
            "maximum_stress_loss": 1,
        }
        headers = {
            **self.headers,
            "Idempotency-Key": f"create-{product_id}",
        }

        created = self.client.post("/products", headers=headers, json=payload)
        repeated = self.client.post("/products", headers=headers, json=payload)

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(repeated.status_code, 201, repeated.text)
        self.assertEqual(repeated.json(), created.json())
        detail = self.client.get(f"/products/{product_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["product"]["product_id"], product_id)

    def test_concurrent_idempotent_requests_execute_once(self) -> None:
        suffix = uuid4().hex[:12]
        product_id = f"concurrent-{suffix}"
        payload = {
            "product_id": product_id,
            "name": "Concurrent PostgreSQL portfolio",
            "benchmark_symbol": "MIGCASH",
            "objective": "Verify concurrent idempotency",
            "risk_level": "low",
            "max_single_asset_weight": 1,
            "min_cash_weight": 0,
            "max_turnover": 1,
            "maximum_data_age_days": 5,
            "maximum_stress_loss": 1,
        }
        headers = {
            **self.headers,
            "Idempotency-Key": f"concurrent-{product_id}",
        }

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda _: self.client.post(
                        "/products",
                        headers=headers,
                        json=payload,
                    ),
                    range(2),
                )
            )

        self.assertEqual([response.status_code for response in responses], [201, 201])
        self.assertEqual(responses[0].json(), responses[1].json())
        rows = self.app.state.database.fetch_all(
            "SELECT * FROM portfolio_products WHERE product_id = ?",
            (product_id,),
        )
        self.assertEqual(len(rows), 1)

    def test_failed_idempotent_action_rolls_back_business_write(self) -> None:
        product_id = f"rollback-{uuid4().hex[:12]}"

        def action(connection):
            connection.execute(
                """
                INSERT INTO portfolio_products (
                    product_id, name, benchmark_symbol, created_at
                ) VALUES (?, 'Rollback Portfolio', 'MIGCASH', ?)
                """,
                (product_id, datetime.now(timezone.utc).isoformat()),
            )
            raise RuntimeError("intentional rollback")

        with self.assertRaisesRegex(RuntimeError, "intentional rollback"):
            _idempotent(
                self.app.state.database,
                key=f"rollback-{product_id}",
                operation="rollback-contract",
                payload={"product_id": product_id},
                action=action,
            )

        self.assertEqual(
            self.app.state.database.fetch_all(
                "SELECT * FROM portfolio_products WHERE product_id = ?",
                (product_id,),
            ),
            [],
        )
