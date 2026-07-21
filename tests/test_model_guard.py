import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundos.agents import (  # noqa: E402
    GuardedLanguageModel, ModelCallBlocked, ModelCompletion,
)
from fundos.storage import Database  # noqa: E402


class CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system_prompt, user_prompt):
        self.calls += 1
        return ModelCompletion("{}")


class ModelGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Path(self.temporary.name) / "guard.sqlite3")
        self.database.initialize()
        self.model = CountingModel()

    def guard(self, **overrides):
        settings = {
            "model": self.model, "database": self.database,
            "provider_name": "fixture", "model_name": "model-a",
            "circuit_failure_threshold": 3,
        }
        settings.update(overrides)
        return GuardedLanguageModel(**settings)

    def insert_call(self, call_id, status, tokens=0, cost=0):
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_calls (
                    call_id, purpose, provider, model, status, attempts, latency_ms,
                    input_tokens, output_tokens, estimated_cost_usd, prompt_sha256,
                    error_message, created_at
                ) VALUES (?, 'research', 'fixture', 'model-a', ?, 1, 1, ?, 0, ?, 'hash', NULL, ?)
                """,
                (call_id, status, tokens, cost, datetime.now(timezone.utc).isoformat()),
            )

    def test_allows_call_below_limits(self) -> None:
        result = self.guard(max_daily_tokens=100).complete(system_prompt="s", user_prompt="u")
        self.assertEqual(result.content, "{}")
        self.assertEqual(self.model.calls, 1)

    def test_blocks_at_daily_token_budget_and_creates_one_alert(self) -> None:
        self.insert_call("C1", "succeeded", tokens=100)
        guard = self.guard(max_daily_tokens=100)
        for _ in range(2):
            with self.assertRaisesRegex(ModelCallBlocked, "token budget"):
                guard.complete(system_prompt="s", user_prompt="u")
        self.assertEqual(self.model.calls, 0)
        alerts = self.database.fetch_all("SELECT * FROM alert_events")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "warning")

    def test_opens_circuit_after_consecutive_failures(self) -> None:
        for index in range(3):
            self.insert_call(f"F{index}", "failed")
        with self.assertRaisesRegex(ModelCallBlocked, "circuit opened"):
            self.guard().complete(system_prompt="s", user_prompt="u")
        self.assertEqual(self.model.calls, 0)
        alert = self.database.fetch_all("SELECT * FROM alert_events")[0]
        self.assertEqual(alert["severity"], "critical")

    def test_blocks_at_daily_cost_budget(self) -> None:
        self.insert_call("COST1", "succeeded", tokens=10, cost=1.25)
        with self.assertRaisesRegex(ModelCallBlocked, "cost budget"):
            self.guard(max_daily_cost_usd=1.25).complete(system_prompt="s", user_prompt="u")
        self.assertEqual(self.model.calls, 0)

    def test_success_resets_consecutive_failure_window(self) -> None:
        self.insert_call("F1", "failed")
        self.insert_call("F2", "failed")
        self.insert_call("S1", "succeeded")
        self.guard().complete(system_prompt="s", user_prompt="u")
        self.assertEqual(self.model.calls, 1)
