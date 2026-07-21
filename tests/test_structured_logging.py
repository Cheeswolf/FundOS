import json
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.core import JsonFormatter  # noqa: E402


class StructuredLoggingTests(unittest.TestCase):
    def test_formats_operational_context_as_json(self) -> None:
        record = logging.LogRecord(
            "fundos.pipeline", logging.INFO, __file__, 1,
            "pipeline completed", (), None,
        )
        record.event = "pipeline_completed"
        record.run_id = "RUN1"
        record.status = "succeeded"
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["event"], "pipeline_completed")
        self.assertEqual(payload["run_id"], "RUN1")
        self.assertEqual(payload["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()

