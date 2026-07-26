import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from fundos.api import create_app  # noqa: E402
from fundos.domain import Asset, PortfolioProduct  # noqa: E402
from fundos.storage import Database  # noqa: E402
from fundos.storage.data_migration import migrate_sqlite_to_postgres  # noqa: E402


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

        self.assertEqual(report.source_schema_version, 14)
        self.assertEqual(report.target_schema_version, 14)
        self.assertGreater(report.total_rows, 0)
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
