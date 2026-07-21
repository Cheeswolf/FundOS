import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundos.agents import ModelProviderError, OpenAICompatibleProvider  # noqa: E402


class ModelProviderTests(unittest.TestCase):
    def test_calls_chat_completions_with_json_contract(self) -> None:
        captured = {}

        def transport(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            return json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode()

        provider = OpenAICompatibleProvider(
            api_key="secret", model="test-model", base_url="https://models.example/v1",
            timeout_seconds=12, transport=transport,
        )
        result = provider.complete(system_prompt="system", user_prompt="user")

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(captured["url"], "https://models.example/v1/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer secret")
        self.assertEqual(captured["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(captured["timeout"], 12)

    def test_rejects_malformed_provider_response(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="secret", model="test-model", base_url="https://models.example/v1",
            transport=lambda request, timeout: b"{}",
        )
        with self.assertRaisesRegex(ModelProviderError, "invalid response"):
            provider.complete(system_prompt="system", user_prompt="user")
