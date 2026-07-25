from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fundos.storage import Database

from .provider import LanguageModel, ModelCompletion, ModelProviderError


@dataclass(slots=True)
class AuditedLanguageModel:
    model: LanguageModel
    database: Database
    call_id: str
    purpose: str
    provider_name: str
    model_name: str
    input_cost_per_million: float = 0
    output_cost_per_million: float = 0

    def __post_init__(self) -> None:
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise ValueError("model token prices cannot be negative")

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelCompletion:
        prompt_hash = hashlib.sha256(
            f"{system_prompt}\n---\n{user_prompt}".encode("utf-8")
        ).hexdigest()
        started = time.monotonic()
        try:
            completion = self.model.complete(system_prompt=system_prompt, user_prompt=user_prompt)
        except ModelProviderError as error:
            self._record(
                status="failed", attempts=error.attempts,
                latency_ms=self._latency(started), prompt_hash=prompt_hash,
                error_message=str(error)[:1000],
            )
            raise
        self._record(
            status="succeeded", attempts=completion.attempts,
            latency_ms=self._latency(started), prompt_hash=prompt_hash,
            input_tokens=completion.input_tokens, output_tokens=completion.output_tokens,
            estimated_cost_usd=self._estimate_cost(completion),
        )
        return completion

    @staticmethod
    def _latency(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    def _record(
        self, *, status: str, attempts: int, latency_ms: int, prompt_hash: str,
        input_tokens: int | None = None, output_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_calls (
                    call_id, purpose, provider, model, status, attempts, latency_ms,
                    input_tokens, output_tokens, estimated_cost_usd, prompt_sha256,
                    error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    purpose = excluded.purpose,
                    provider = excluded.provider,
                    model = excluded.model,
                    status = excluded.status,
                    attempts = excluded.attempts,
                    latency_ms = excluded.latency_ms,
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    estimated_cost_usd = excluded.estimated_cost_usd,
                    prompt_sha256 = excluded.prompt_sha256,
                    error_message = excluded.error_message,
                    created_at = excluded.created_at
                """,
                (
                    self.call_id, self.purpose, self.provider_name, self.model_name, status,
                    attempts, latency_ms, input_tokens, output_tokens, estimated_cost_usd, prompt_hash,
                    error_message, datetime.now(timezone.utc).isoformat(),
                ),
            )

    def _estimate_cost(self, completion: ModelCompletion) -> float | None:
        if completion.input_tokens is None or completion.output_tokens is None:
            return None
        return round(
            completion.input_tokens * self.input_cost_per_million / 1_000_000
            + completion.output_tokens * self.output_cost_per_million / 1_000_000,
            8,
        )
