import json
import ssl
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundos.agents import ModelProviderError, OpenAICompatibleProvider  # noqa: E402


class ModelProviderTests(unittest.TestCase):
    def test_default_transport_uses_certifi_trust_store(self) -> None:
        from unittest.mock import patch

        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with patch("fundos.agents.provider.urlopen", return_value=response) as opener:
            from fundos.agents.provider import _default_transport

            _default_transport(unittest.mock.MagicMock(), 10)

        context = opener.call_args.kwargs["context"]
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_calls_chat_completions_with_json_contract(self) -> None:
        captured = {}

        def transport(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            return json.dumps({
                "choices": [{"message": {"content": "{\"ok\": true}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }).encode()

        provider = OpenAICompatibleProvider(
            api_key="secret", model="test-model", base_url="https://models.example/v1",
            timeout_seconds=12, transport=transport,
        )
        result = provider.complete(system_prompt="system", user_prompt="user")

        self.assertEqual(result.content, '{"ok": true}')
        self.assertEqual((result.input_tokens, result.output_tokens), (12, 4))
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

    def test_retries_transient_connection_failure(self) -> None:
        attempts = []
        delays = []

        def transport(request, timeout):
            from urllib.error import URLError
            attempts.append(1)
            if len(attempts) < 3:
                raise URLError("temporary")
            return b'{"choices":[{"message":{"content":"{}"}}]}'

        provider = OpenAICompatibleProvider(
            api_key="secret", model="test-model", base_url="https://models.example/v1",
            transport=transport, sleeper=delays.append,
        )
        result = provider.complete(system_prompt="system", user_prompt="user")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(delays, [1, 2])
