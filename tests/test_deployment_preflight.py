import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import PortfolioProduct  # noqa: E402
from fundos.services.deployment_preflight import run_deployment_preflight  # noqa: E402
from fundos.storage import Database, create_database_backup  # noqa: E402


class DeploymentPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.database = Database(root / "fundos.sqlite3")
        self.database.initialize()
        self.database.create_product(
            PortfolioProduct(
                "P1",
                "Deployment Portfolio",
                "BM",
                datetime(2026, 7, 26),
            )
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_versions (
                    version_id, product_id, version_number,
                    effective_date, status, published_at
                ) VALUES (
                    'V1', 'P1', 1, '2026-07-01',
                    'published', '2026-07-01T00:00:00+00:00'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO portfolio_nav (product_id, nav_date, nav)
                VALUES ('P1', '2026-07-01', 1.0)
                """
            )
        _, self.manifest = create_database_backup(
            self.database.path,
            root / "backups",
        )
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.backup_time = datetime.fromisoformat(manifest["created_at"])
        self.environment = {
            "FUNDOS_API_KEY": "a9F3kLm7Qp2Rz8Vx4Nc6Tw1Y",
            "FUNDOS_READ_ONLY": "false",
            "FUNDOS_ALERT_WEBHOOK_URL": "https://alerts.example.test/fundos",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example.test",
        }

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_ready_production_report_contains_no_credentials(self) -> None:
        report = run_deployment_preflight(
            self.database,
            environment=self.environment,
            mode="production",
            backup_manifest=self.manifest,
            now=self.backup_time + timedelta(hours=1),
        )

        self.assertTrue(report.ready)
        payload = report.as_dict()
        self.assertEqual(payload["blocking_failures"], 0)
        self.assertNotIn(self.environment["FUNDOS_API_KEY"], json.dumps(payload))

    def test_cutover_requires_read_only_mode(self) -> None:
        report = run_deployment_preflight(
            self.database,
            environment=self.environment,
            mode="cutover",
            backup_manifest=self.manifest,
            now=self.backup_time + timedelta(hours=1),
        )

        self.assertFalse(report.ready)
        read_only = next(
            check for check in report.checks if check.code == "read_only_mode"
        )
        self.assertFalse(read_only.passed)
        self.assertEqual(read_only.severity, "blocking")

    def test_weak_key_and_stale_backup_block_deployment(self) -> None:
        environment = {
            **self.environment,
            "FUNDOS_API_KEY": "replace-with-secret",
        }

        report = run_deployment_preflight(
            self.database,
            environment=environment,
            mode="production",
            backup_manifest=self.manifest,
            maximum_backup_age_hours=24,
            now=self.backup_time + timedelta(hours=25),
        )

        self.assertFalse(report.ready)
        failed = {
            check.code for check in report.checks
            if not check.passed and check.severity == "blocking"
        }
        self.assertEqual(failed, {"api_keys", "backup"})


if __name__ == "__main__":
    unittest.main()
