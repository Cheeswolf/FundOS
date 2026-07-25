from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

from fundos.storage import Database


def validate_research_agent_request(
    database: Database,
    configuration: Mapping[str, Any],
) -> None:
    """Fail before a paid model call when a controlled research request is unsafe."""
    required = ("report_id", "product_id", "as_of_date", "asset_symbols", "evidence")
    missing = [key for key in required if key not in configuration]
    if missing:
        raise ValueError(f"research configuration is missing: {', '.join(missing)}")

    report_id = str(configuration["report_id"]).strip()
    product_id = str(configuration["product_id"]).strip()
    as_of_date = date.fromisoformat(str(configuration["as_of_date"]))
    symbols = tuple(str(item).strip() for item in configuration["asset_symbols"])
    evidence = configuration["evidence"]
    if not report_id or not product_id:
        raise ValueError("report and product IDs are required")
    if not symbols or any(not symbol for symbol in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("asset symbols must be non-empty and unique")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("at least one trusted evidence item is required")

    products = database.fetch_all(
        "SELECT 1 FROM portfolio_products WHERE product_id = ?", (product_id,)
    )
    if not products:
        raise ValueError(f"portfolio product does not exist: {product_id}")
    if database.fetch_all("SELECT 1 FROM research_reports WHERE report_id = ?", (report_id,)):
        raise ValueError(f"research report already exists: {report_id}")

    published = database.fetch_all(
        """
        SELECT version_id FROM portfolio_versions
        WHERE product_id = ? AND status = 'published'
        ORDER BY effective_date DESC, version_number DESC LIMIT 1
        """,
        (product_id,),
    )
    if not published:
        raise ValueError("product has no published portfolio version")
    portfolio_symbols = {
        row["asset_symbol"]
        for row in database.fetch_all(
            "SELECT asset_symbol FROM portfolio_version_weights WHERE version_id = ?",
            (published[0]["version_id"],),
        )
    }
    if set(symbols) != portfolio_symbols:
        raise ValueError("research assets must exactly match the current published portfolio")

    evidence_ids: list[str] = []
    for item in evidence:
        if not isinstance(item, Mapping):
            raise ValueError("each evidence item must be an object")
        evidence_id = str(item.get("evidence_id", "")).strip()
        content = str(item.get("content", "")).strip()
        if not evidence_id:
            raise ValueError("each evidence item requires an evidence_id")
        if not content:
            raise ValueError(f"evidence content is required: {evidence_id}")
        if len(content) > 12000:
            raise ValueError(f"evidence content exceeds 12000 characters: {evidence_id}")
        published_at = datetime.fromisoformat(str(item.get("published_at", "")))
        if published_at.date() > as_of_date:
            raise ValueError(f"evidence is newer than the report date: {evidence_id}")
        evidence_ids.append(evidence_id)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("research evidence IDs must be unique")
