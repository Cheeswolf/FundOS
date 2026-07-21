import json
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundos.agents import ModelCompletion, ResearchAgent, ResearchAgentError  # noqa: E402
from fundos.domain import ResearchEvidence  # noqa: E402


class FakeModel:
    def __init__(self, response: object) -> None:
        self.response = json.dumps(response)
        self.prompts: list[tuple[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return ModelCompletion(self.response)


class ResearchAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = (
            ResearchEvidence(
                evidence_id="E1",
                title="Observed market data",
                source="licensed-source",
                url="https://example.test/e1",
                published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            ),
        )

    def payload(self) -> dict:
        return {
            "market_regime": "balanced",
            "summary": "Evidence supports a balanced stance.",
            "confidence": 0.7,
            "asset_views": [
                {
                    "asset_symbol": "EQUITY",
                    "direction": "positive",
                    "confidence": 0.7,
                    "thesis": "The supplied evidence is constructive.",
                    "evidence_ids": ["E1"],
                },
                {
                    "asset_symbol": "CASH",
                    "direction": "neutral",
                    "confidence": 0.6,
                    "thesis": "Cash remains a portfolio stabilizer.",
                    "evidence_ids": ["E1"],
                },
            ],
        }

    def test_generates_validated_report_from_trusted_evidence(self) -> None:
        model = FakeModel(self.payload())
        report = ResearchAgent(model).draft_report(
            report_id="R-AI-1",
            product_id="P1",
            as_of_date=date(2026, 7, 21),
            asset_symbols=("EQUITY", "CASH"),
            evidence=self.evidence,
        )

        self.assertEqual(report.report_id, "R-AI-1")
        self.assertEqual(len(report.asset_views), 2)
        supplied = json.loads(model.prompts[0][1])
        self.assertEqual(supplied["evidence"][0]["evidence_id"], "E1")

    def test_rejects_unknown_evidence_citation(self) -> None:
        payload = self.payload()
        payload["asset_views"][0]["evidence_ids"] = ["INVENTED"]
        with self.assertRaisesRegex(ResearchAgentError, "unknown evidence"):
            ResearchAgent(FakeModel(payload)).draft_report(
                report_id="R-AI-2", product_id="P1", as_of_date=date(2026, 7, 21),
                asset_symbols=("EQUITY", "CASH"), evidence=self.evidence,
            )

    def test_rejects_missing_or_duplicate_asset_coverage(self) -> None:
        payload = self.payload()
        payload["asset_views"][1]["asset_symbol"] = "EQUITY"
        with self.assertRaisesRegex(ResearchAgentError, "exactly once"):
            ResearchAgent(FakeModel(payload)).draft_report(
                report_id="R-AI-3", product_id="P1", as_of_date=date(2026, 7, 21),
                asset_symbols=("EQUITY", "CASH"), evidence=self.evidence,
            )

    def test_rejects_non_json_output(self) -> None:
        model = FakeModel({})
        model.response = "not json"
        with self.assertRaisesRegex(ResearchAgentError, "not valid JSON"):
            ResearchAgent(model).draft_report(
                report_id="R-AI-4", product_id="P1", as_of_date=date(2026, 7, 21),
                asset_symbols=("EQUITY",), evidence=self.evidence,
            )

    def test_rejects_invalid_direction(self) -> None:
        payload = self.payload()
        payload["asset_views"][0]["direction"] = "strong-buy"
        with self.assertRaisesRegex(ResearchAgentError, "direction is invalid"):
            ResearchAgent(FakeModel(payload)).draft_report(
                report_id="R-AI-5", product_id="P1", as_of_date=date(2026, 7, 21),
                asset_symbols=("EQUITY", "CASH"), evidence=self.evidence,
            )

    def test_rejects_future_evidence_before_model_call(self) -> None:
        future_evidence = (
            ResearchEvidence(
                evidence_id="F1", title="Future item", source="source",
                url="https://example.test/future",
                published_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
            ),
        )
        model = FakeModel(self.payload())
        with self.assertRaisesRegex(ResearchAgentError, "after the report date"):
            ResearchAgent(model).draft_report(
                report_id="R-AI-6", product_id="P1", as_of_date=date(2026, 7, 21),
                asset_symbols=("EQUITY", "CASH"), evidence=future_evidence,
            )
        self.assertEqual(model.prompts, [])
