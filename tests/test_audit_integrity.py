import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundos.services import purge_audit_events, record_audit_event, verify_audit_chain  # noqa: E402
from fundos.storage import Database  # noqa: E402


class AuditIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Path(self.temporary.name) / "audit.sqlite3")
        self.database.initialize()

    def record(self, audit_id: str, created_at: datetime) -> None:
        record_audit_event(self.database, {
            "audit_id": audit_id, "request_id": f"request-{audit_id}",
            "method": "POST", "path": "/assets", "actor_id": "key:fixture",
            "actor_role": "admin", "outcome": "succeeded", "status_code": 201,
            "client_ip": "127.0.0.1", "created_at": created_at.isoformat(),
        })

    def test_detects_a_tampered_event(self) -> None:
        self.record("A1", datetime.now(timezone.utc))
        self.assertTrue(verify_audit_chain(self.database)["valid"])
        with self.database.connect() as connection:
            connection.execute("UPDATE api_audit_events SET path = '/tampered' WHERE audit_id = 'A1'")
        result = verify_audit_chain(self.database)
        self.assertFalse(result["valid"])
        self.assertEqual(result["broken_audit_id"], "A1")

    def test_retention_keeps_anchor_for_remaining_chain(self) -> None:
        now = datetime.now(timezone.utc)
        self.record("OLD", now - timedelta(days=400))
        self.record("CURRENT", now)
        deleted = purge_audit_events(self.database, retention_days=365)
        self.assertEqual(deleted, 1)
        self.assertEqual(
            [row["audit_id"] for row in self.database.fetch_all("SELECT * FROM api_audit_events")],
            ["CURRENT"],
        )
        self.assertEqual(len(self.database.fetch_all("SELECT * FROM audit_retention_anchors")), 1)
        self.assertTrue(verify_audit_chain(self.database)["valid"])

    def test_retention_refuses_unsafe_short_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 30 days"):
            purge_audit_events(self.database, retention_days=7)
