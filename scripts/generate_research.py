from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.agents import (  # noqa: E402
    AuditedLanguageModel,
    GuardedLanguageModel,
    OpenAICompatibleProvider,
    ResearchAgent,
)
from fundos.domain import ResearchEvidence  # noqa: E402
from fundos.services import create_research_report, validate_research_agent_request  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and persist a validated AI research draft")
    parser.add_argument("input", type=Path, help="JSON file containing report metadata and trusted evidence")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    arguments = parser.parse_args()

    configuration = json.loads(arguments.input.read_text(encoding="utf-8"))
    required_environment = ("FUNDOS_LLM_API_KEY",)
    missing = [name for name in required_environment if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit(f"Missing model configuration: {', '.join(missing)}")
    database = Database(arguments.database)
    database.initialize()
    try:
        validate_research_agent_request(database, configuration)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"Research preflight failed: {error}") from error
    provider = OpenAICompatibleProvider(
        api_key=os.environ.get("FUNDOS_LLM_API_KEY", ""),
        model=os.environ.get("FUNDOS_LLM_MODEL", "deepseek-v4-flash"),
        base_url=os.environ.get("FUNDOS_LLM_BASE_URL", "https://api.deepseek.com"),
    )
    evidence = tuple(
        ResearchEvidence(
            evidence_id=item["evidence_id"],
            title=item["title"],
            source=item["source"],
            url=item["url"],
            published_at=datetime.fromisoformat(item["published_at"]),
            content=item.get("content", ""),
        )
        for item in configuration["evidence"]
    )
    audited_provider = AuditedLanguageModel(
        model=provider,
        database=database,
        call_id=f"research:{configuration['report_id']}",
        purpose="research_draft",
        provider_name=os.environ.get("FUNDOS_LLM_PROVIDER", "deepseek"),
        model_name=os.environ.get("FUNDOS_LLM_MODEL", "deepseek-v4-flash"),
        input_cost_per_million=float(os.environ.get("FUNDOS_LLM_INPUT_COST_PER_MILLION", "0.14")),
        output_cost_per_million=float(os.environ.get("FUNDOS_LLM_OUTPUT_COST_PER_MILLION", "0.28")),
    )
    guarded_provider = GuardedLanguageModel(
        model=audited_provider,
        database=database,
        provider_name=os.environ.get("FUNDOS_LLM_PROVIDER", "deepseek"),
        model_name=os.environ.get("FUNDOS_LLM_MODEL", "deepseek-v4-flash"),
        max_daily_cost_usd=float(os.environ.get("FUNDOS_LLM_MAX_DAILY_COST_USD", "1")),
        max_daily_tokens=int(os.environ.get("FUNDOS_LLM_MAX_DAILY_TOKENS", "100000")),
        circuit_failure_threshold=int(os.environ.get("FUNDOS_LLM_CIRCUIT_FAILURE_THRESHOLD", "3")),
    )
    report = ResearchAgent(guarded_provider).draft_report(
        report_id=configuration["report_id"],
        product_id=configuration["product_id"],
        as_of_date=date.fromisoformat(configuration["as_of_date"]),
        asset_symbols=tuple(configuration["asset_symbols"]),
        evidence=evidence,
    )
    create_research_report(database, report)
    print(json.dumps({"report_id": report.report_id, "status": "draft"}))


if __name__ == "__main__":
    main()
