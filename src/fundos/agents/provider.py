from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelProviderError(RuntimeError):
    """Raised when a language model cannot return usable content."""


class LanguageModel(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> str: ...


HttpTransport = Callable[[Request, float], bytes]


def _default_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is operator configured
        return response.read()


@dataclass(frozen=True, slots=True)
class OpenAICompatibleProvider:
    """Minimal adapter for providers implementing the chat-completions contract."""

    api_key: str
    model: str
    base_url: str
    timeout_seconds: float = 60
    transport: HttpTransport = _default_transport

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("model API key is required")
        if not self.model.strip():
            raise ValueError("model name is required")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("model base URL must be HTTP or HTTPS")

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            raw = self.transport(request, self.timeout_seconds)
            response = json.loads(raw)
            content = response["choices"][0]["message"]["content"]
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise ModelProviderError(f"model provider returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise ModelProviderError(f"model provider connection failed: {error.reason}") from error
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ModelProviderError("model provider returned an invalid response") from error
        if not isinstance(content, str) or not content.strip():
            raise ModelProviderError("model provider returned empty content")
        return content
