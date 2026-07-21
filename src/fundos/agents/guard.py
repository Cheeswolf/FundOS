from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fundos.services.alerts import create_alert
from fundos.storage import Database

from .provider import LanguageModel, ModelCompletion


class ModelCallBlocked(RuntimeError):
    """Raised before a provider call when an operational policy is open."""


@dataclass(slots=True)
class GuardedLanguageModel:
    model: LanguageModel
    database: Database
    provider_name: str
    model_name: str
    max_daily_cost_usd: float = 0
    max_daily_tokens: int = 0
    circuit_failure_threshold: int = 3

    def __post_init__(self) -> None:
        if self.max_daily_cost_usd < 0 or self.max_daily_tokens < 0:
            raise ValueError("model budgets cannot be negative")
        if self.circuit_failure_threshold < 0:
            raise ValueError("circuit failure threshold cannot be negative")

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelCompletion:
        today = datetime.now(timezone.utc).date().isoformat()
        usage = self.database.fetch_all(
            """
            SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS cost
            FROM model_calls
            WHERE status = 'succeeded' AND substr(created_at, 1, 10) = ?
            """,
            (today,),
        )[0]
        if self.max_daily_cost_usd and usage["cost"] >= self.max_daily_cost_usd:
            self._block(
                policy="daily-cost", severity="warning",
                message=f"Daily model cost budget reached: ${usage['cost']:.6f} / ${self.max_daily_cost_usd:.6f}",
            )
        if self.max_daily_tokens and usage["tokens"] >= self.max_daily_tokens:
            self._block(
                policy="daily-tokens", severity="warning",
                message=f"Daily model token budget reached: {usage['tokens']} / {self.max_daily_tokens}",
            )
        if self.circuit_failure_threshold:
            resets = self.database.fetch_all(
                """
                SELECT reset_at FROM model_circuit_resets
                WHERE provider = ? AND model = ? ORDER BY reset_at DESC LIMIT 1
                """,
                (self.provider_name, self.model_name),
            )
            reset_at = resets[0]["reset_at"] if resets else ""
            recent = self.database.fetch_all(
                """
                SELECT status FROM model_calls
                WHERE provider = ? AND model = ?
                  AND datetime(created_at) > COALESCE(datetime(?), '0001-01-01 00:00:00')
                ORDER BY datetime(created_at) DESC LIMIT ?
                """,
                (self.provider_name, self.model_name, reset_at, self.circuit_failure_threshold),
            )
            if len(recent) == self.circuit_failure_threshold and all(
                row["status"] == "failed" for row in recent
            ):
                self._block(
                    policy="circuit", severity="critical",
                    message=f"Model circuit opened after {self.circuit_failure_threshold} consecutive failures",
                )
        return self.model.complete(system_prompt=system_prompt, user_prompt=user_prompt)

    def _block(self, *, policy: str, severity: str, message: str) -> None:
        alert_day = datetime.now(timezone.utc).date().isoformat()
        create_alert(
            self.database,
            source_type="model_policy",
            source_id=f"{policy}:{self.provider_name}:{self.model_name}:{alert_day}",
            severity=severity,
            title=f"Model call blocked: {policy}",
            message=message,
        )
        raise ModelCallBlocked(message)
