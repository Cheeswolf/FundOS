import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.services import create_alert, deliver_pending_alerts  # noqa: E402
from fundos.storage import Database  # noqa: E402


class WebhookResponse(io.BytesIO):
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AlertTests(unittest.TestCase):
    def test_alerts_are_idempotent_and_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "alerts.sqlite3")
            database.initialize()
            first = create_alert(
                database,
                source_type="pipeline_run",
                source_id="RUN1",
                severity="warning",
                title="Pipeline partial",
                message="One symbol failed",
            )
            second = create_alert(
                database,
                source_type="pipeline_run",
                source_id="RUN1",
                severity="warning",
                title="Pipeline partial",
                message="One symbol failed",
            )
            self.assertEqual(first, second)
            self.assertEqual(len(database.fetch_all("SELECT * FROM alert_events")), 1)

            failed = deliver_pending_alerts(
                database,
                webhook_url="https://example.test/webhook",
                opener=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("network down")),
            )
            self.assertEqual(failed.failed, 1)
            payloads = []

            def successful_opener(request, timeout):
                payloads.append(json.loads(request.data.decode("utf-8")))
                return WebhookResponse()

            delivered = deliver_pending_alerts(
                database,
                webhook_url="https://example.test/webhook",
                opener=successful_opener,
            )
            self.assertEqual(delivered.delivered, 1)
            self.assertEqual(payloads[0]["source_id"], "RUN1")
            alert = database.fetch_all("SELECT * FROM alert_events")[0]
            self.assertEqual(alert["status"], "delivered")
            self.assertEqual(alert["delivery_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
