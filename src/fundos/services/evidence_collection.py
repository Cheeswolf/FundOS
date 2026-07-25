from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from fundos.data_providers import (
    OfficialResearchCollectionError,
    OfficialResearchCollector,
)
from fundos.storage import Database

from .evidence_ingestion import import_raw_research_evidence


@dataclass(frozen=True, slots=True)
class EvidenceCollectionResult:
    run_id: str
    source_id: str
    status: str
    discovered_count: int
    imported_count: int
    duplicate_count: int
    error_message: str | None


def run_evidence_collection(
    database: Database,
    *,
    collector: OfficialResearchCollector,
    max_items: int = 5,
) -> EvidenceCollectionResult:
    if not database.fetch_all(
        "SELECT 1 FROM research_evidence_sources WHERE source_id = ? AND enabled = 1",
        (collector.source_id,),
    ):
        raise ValueError(f"collector source is not registered or enabled: {collector.source_id}")
    run_id = str(uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO evidence_collection_runs (run_id, source_id, status, started_at)
            VALUES (?, ?, 'running', ?)
            """,
            (run_id, collector.source_id, started_at),
        )
    discovered = imported = duplicates = 0
    error_message: str | None = None
    try:
        pages = collector.collect(max_items=max_items)
        discovered = len(pages)
        for page in pages:
            result = import_raw_research_evidence(
                database,
                {
                    "source_id": page.source_id,
                    "title": page.title,
                    "url": page.url,
                    "published_at": page.published_at,
                    "asset_symbols": list(page.asset_symbols),
                    "content": page.content,
                },
            )
            if result.created:
                imported += 1
            else:
                duplicates += 1
        status = "succeeded"
    except (OfficialResearchCollectionError, OSError, ValueError) as error:
        status = "failed"
        error_message = str(error)[:2000]
    completed_at = datetime.now(timezone.utc).isoformat()
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE evidence_collection_runs SET
                status = ?, discovered_count = ?, imported_count = ?,
                duplicate_count = ?, error_message = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (
                status, discovered, imported, duplicates, error_message,
                completed_at, run_id,
            ),
        )
    return EvidenceCollectionResult(
        run_id, collector.source_id, status, discovered, imported, duplicates, error_message
    )
