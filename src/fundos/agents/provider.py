from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelProviderError(RuntimeError):
    """Raised when a language model cannot return usable content."""

    def __init__(self, message: str, *, retryable: bool = False, attempts: int = 1) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class ModelCompletion:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    attempts: int = 1


class LanguageModel(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelCompletion: ...


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
    max_attempts: int = 3
    retry_backoff_seconds: float = 1
    transport: HttpTransport = _default_transport
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("model API key is required")
        if not self.model.strip():
            raise ValueError("model name is required")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("model base URL must be HTTP or HTTPS")
        if self.max_attempts < 1:
            raise ValueError("model max attempts must be positive")
        if self.retry_backoff_seconds < 0:
            raise ValueError("model retry backoff cannot be negative")

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelCompletion:
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
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self.transport(request, self.timeout_seconds)
                response = json.loads(raw)
                content = response["choices"][0]["message"]["content"]
                usage = response.get("usage") or {}
                if not isinstance(content, str) or not content.strip():
                    raise ModelProviderError("model provider returned empty content", attempts=attempt)
                return ModelCompletion(
                    content=content,
                    input_tokens=self._token_count(usage.get("prompt_tokens")),
                    output_tokens=self._token_count(usage.get("completion_tokens")),
                    attempts=attempt,
                )
            except HTTPError as error:
                retryable = error.code in {408, 409, 429} or error.code >= 500
                detail = error.read().decode("utf-8", errors="replace")[:500]
                failure = ModelProviderError(
                    f"model provider returned HTTP {error.code}: {detail}",
                    retryable=retryable, attempts=attempt,
                )
            except URLError as error:
                failure = ModelProviderError(
                    f"model provider connection failed: {error.reason}", retryable=True, attempts=attempt,
                )
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
                raise ModelProviderError("model provider returned an invalid response", attempts=attempt) from error
            if not failure.retryable or attempt == self.max_attempts:
                raise failure
            self.sleeper(self.retry_backoff_seconds * (2 ** (attempt - 1)))
        raise AssertionError("unreachable")

    @staticmethod
    def _token_count(value: object) -> int | None:
        return value if isinstance(value, int) and value >= 0 else None
