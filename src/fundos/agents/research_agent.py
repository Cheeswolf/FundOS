from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from fundos.domain import AssetView, ResearchEvidence, ResearchReport

from .provider import LanguageModel, ModelProviderError


class ResearchAgentError(ValueError):
    """Raised when generated research fails the controlled output contract."""


SYSTEM_PROMPT = """You are an investment research drafting agent.
Use only the supplied evidence. Do not invent facts, URLs, assets, or evidence IDs.
Return one JSON object with keys: market_regime, summary, confidence, asset_views.
Each asset view must contain asset_symbol, direction, confidence, thesis, evidence_ids.
direction must be negative, neutral, or positive. confidence must be between 0 and 1.
Every requested asset must appear exactly once and cite at least one supplied evidence ID.
The output is a research draft, not investment advice or an authorization to trade."""


class ResearchAgent:
    def __init__(self, model: LanguageModel) -> None:
        self.model = model

    def draft_report(
        self,
        *,
        report_id: str,
        product_id: str,
        as_of_date: date,
        asset_symbols: tuple[str, ...],
        evidence: tuple[ResearchEvidence, ...],
    ) -> ResearchReport:
        if not asset_symbols or len(set(asset_symbols)) != len(asset_symbols):
            raise ResearchAgentError("asset symbols must be non-empty and unique")
        if not evidence:
            raise ResearchAgentError("research evidence is required")
        if any(item.published_at.date() > as_of_date for item in evidence):
            raise ResearchAgentError("research evidence cannot be published after the report date")
        user_prompt = json.dumps(
            {
                "as_of_date": as_of_date.isoformat(),
                "asset_symbols": asset_symbols,
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "title": item.title,
                        "source": item.source,
                        "url": item.url,
                        "published_at": item.published_at.isoformat(),
                    }
                    for item in evidence
                ],
            },
            ensure_ascii=False,
        )
        try:
            completion = self.model.complete(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)
            payload = self._parse_object(completion.content)
            views = tuple(self._parse_view(item) for item in payload["asset_views"])
            report = ResearchReport(
                report_id=report_id,
                product_id=product_id,
                as_of_date=as_of_date,
                market_regime=str(payload["market_regime"]),
                summary=str(payload["summary"]),
                confidence=Decimal(str(payload["confidence"])),
                evidence=evidence,
                asset_views=views,
            )
        except ModelProviderError:
            raise
        except (KeyError, TypeError, InvalidOperation, ValueError) as error:
            raise ResearchAgentError(f"generated research violates the output contract: {error}") from error

        expected = set(asset_symbols)
        actual = [view.asset_symbol for view in report.asset_views]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ResearchAgentError("generated research must cover every requested asset exactly once")
        return report

    @staticmethod
    def _parse_object(content: str) -> dict:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1])
                if text.lstrip().startswith("json"):
                    text = text.lstrip()[4:].lstrip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ResearchAgentError("model output is not valid JSON") from error
        if not isinstance(payload, dict):
            raise ResearchAgentError("model output must be a JSON object")
        return payload

    @staticmethod
    def _parse_view(payload: object) -> AssetView:
        if not isinstance(payload, dict):
            raise ResearchAgentError("each asset view must be an object")
        evidence_ids = payload["evidence_ids"]
        if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
            raise ResearchAgentError("asset view evidence_ids must be a string list")
        direction = payload["direction"]
        if direction not in {"negative", "neutral", "positive"}:
            raise ResearchAgentError("asset view direction is invalid")
        return AssetView(
            asset_symbol=str(payload["asset_symbol"]),
            direction=direction,
            confidence=Decimal(str(payload["confidence"])),
            thesis=str(payload["thesis"]),
            evidence_ids=tuple(evidence_ids),
        )
