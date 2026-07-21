import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundos.agents import (  # noqa: E402
    AuditedLanguageModel, ModelCompletion, ModelProviderError,
)
from fundos.storage import Database  # noqa: E402


class SuccessfulModel:
    def complete(self, *, system_prompt, user_prompt):
        return ModelCompletion("{}", input_tokens=20, output_tokens=5, attempts=2)


class FailedModel:
    def complete(self, *, system_prompt, user_prompt):
        raise ModelProviderError("provider unavailable", retryable=True, attempts=3)


class ModelAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Path(self.temporary.name) / "audit.sqlite3")
        self.database.initialize()

    def audited(self, model, call_id):
        return AuditedLanguageModel(
            model=model, database=self.database, call_id=call_id,
            purpose="research_draft", provider_name="fixture", model_name="fixture-model",
            input_cost_per_million=2, output_cost_per_million=8,
        )

    def test_records_success_usage_without_prompt_content(self) -> None:
        self.audited(SuccessfulModel(), "call-1").complete(
            system_prompt="private system", user_prompt="private evidence",
        )
        row = self.database.fetch_all("SELECT * FROM model_calls WHERE call_id = 'call-1'")[0]
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual((row["attempts"], row["input_tokens"], row["output_tokens"]), (2, 20, 5))
        self.assertAlmostEqual(row["estimated_cost_usd"], 0.00008)
        self.assertNotIn("private", row["prompt_sha256"])

    def test_records_terminal_failure(self) -> None:
        with self.assertRaises(ModelProviderError):
            self.audited(FailedModel(), "call-2").complete(system_prompt="s", user_prompt="u")
        row = self.database.fetch_all("SELECT * FROM model_calls WHERE call_id = 'call-2'")[0]
        self.assertEqual((row["status"], row["attempts"]), ("failed", 3))
        self.assertIn("unavailable", row["error_message"])
