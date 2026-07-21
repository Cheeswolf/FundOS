from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.agents import OpenAICompatibleProvider, ResearchAgent  # noqa: E402
from fundos.domain import ResearchEvidence  # noqa: E402
from fundos.services import create_research_report  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and persist a validated AI research draft")
    parser.add_argument("input", type=Path, help="JSON file containing report metadata and trusted evidence")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "fundos.db")
    arguments = parser.parse_args()

    configuration = json.loads(arguments.input.read_text(encoding="utf-8"))
    provider = OpenAICompatibleProvider(
        api_key=os.environ.get("FUNDOS_LLM_API_KEY", ""),
        model=os.environ.get("FUNDOS_LLM_MODEL", ""),
        base_url=os.environ.get("FUNDOS_LLM_BASE_URL", "https://api.openai.com/v1"),
    )
    evidence = tuple(
        ResearchEvidence(
            evidence_id=item["evidence_id"],
            title=item["title"],
            source=item["source"],
            url=item["url"],
            published_at=datetime.fromisoformat(item["published_at"]),
        )
        for item in configuration["evidence"]
    )
    report = ResearchAgent(provider).draft_report(
        report_id=configuration["report_id"],
        product_id=configuration["product_id"],
        as_of_date=date.fromisoformat(configuration["as_of_date"]),
        asset_symbols=tuple(configuration["asset_symbols"]),
        evidence=evidence,
    )
    database = Database(arguments.database)
    database.initialize()
    create_research_report(database, report)
    print(json.dumps({"report_id": report.report_id, "status": "draft"}))


if __name__ == "__main__":
    main()
