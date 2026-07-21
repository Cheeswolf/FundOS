import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundos.services import (  # noqa: E402
    create_alert, get_model_circuit_status, reset_model_circuit, update_alert_lifecycle,
)
from fundos.storage import Database  # noqa: E402


class ModelOperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Path(self.temporary.name) / "operations.sqlite3")
        self.database.initialize()

    def insert_failure(self, call_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_calls (
                    call_id, purpose, provider, model, status, attempts, latency_ms,
                    prompt_sha256, created_at
                ) VALUES (?, 'research', 'fixture', 'model-a', 'failed', 1, 1, 'hash', ?)
                """,
                (call_id, datetime.now(timezone.utc).isoformat()),
            )

    def test_manual_reset_closes_open_circuit(self) -> None:
        for index in range(3):
            self.insert_failure(f"F{index}")
        before = get_model_circuit_status(
            self.database, provider="fixture", model="model-a", failure_threshold=3,
        )
        self.assertEqual(before["state"], "open")

        reset_model_circuit(
            self.database, provider="fixture", model="model-a",
            reset_by="operator", reason="Provider recovered and smoke test passed",
        )
        after = get_model_circuit_status(
            self.database, provider="fixture", model="model-a", failure_threshold=3,
        )
        self.assertEqual(after["state"], "closed")
        self.assertEqual(after["last_reset_by"], "operator")

    def test_alert_can_be_acknowledged_then_resolved_but_not_reopened(self) -> None:
        alert_id = create_alert(
            self.database, source_type="model_policy", source_id="circuit:test",
            severity="critical", title="Circuit open", message="Three failures",
        )
        self.assertEqual(update_alert_lifecycle(
            self.database, alert_id=alert_id, state="acknowledged",
            updated_by="operator", note="Investigating provider",
        ), "acknowledged")
        self.assertEqual(update_alert_lifecycle(
            self.database, alert_id=alert_id, state="resolved",
            updated_by="operator", note="Provider restored",
        ), "resolved")
        with self.assertRaisesRegex(ValueError, "cannot return"):
            update_alert_lifecycle(
                self.database, alert_id=alert_id, state="acknowledged",
                updated_by="operator", note="Reopen",
            )
