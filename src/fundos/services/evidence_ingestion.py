from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class EvidenceImportResult:
    raw_evidence_id: str
    created: bool
    content_sha256: str
    review_status: str


def register_research_sources(
    database: Database,
    sources: Iterable[Mapping[str, Any]],
) -> int:
    rows = list(sources)
    if not rows:
        raise ValueError("at least one research evidence source is required")
    registered = 0
    with database.connect() as connection:
        for item in rows:
            source_id = str(item.get("source_id", "")).strip()
            name = str(item.get("name", "")).strip()
            source_type = str(item.get("source_type", "")).strip()
            domains = _clean_unique(item.get("allowed_domains"), "allowed domains")
            symbols = _clean_unique(item.get("asset_symbols"), "asset symbols")
            license_note = str(item.get("license_note", "")).strip()
            if not source_id or not name or not license_note:
                raise ValueError("source ID, name and license note are required")
            if source_type not in {"official", "licensed", "internal"}:
                raise ValueError(f"invalid research source type: {source_type}")
            unknown_assets = [
                symbol for symbol in symbols
                if connection.execute(
                    "SELECT 1 FROM assets WHERE symbol = ?", (symbol,)
                ).fetchone() is None
            ]
            if unknown_assets:
                raise ValueError(
                    f"research source contains unknown assets: {', '.join(unknown_assets)}"
                )
            connection.execute(
                """
                INSERT INTO research_evidence_sources (
                    source_id, name, source_type, allowed_domains, asset_symbols,
                    license_note, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    name = excluded.name,
                    source_type = excluded.source_type,
                    allowed_domains = excluded.allowed_domains,
                    asset_symbols = excluded.asset_symbols,
                    license_note = excluded.license_note,
                    enabled = excluded.enabled,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source_id, name, source_type,
                    json.dumps(domains, ensure_ascii=False),
                    json.dumps(symbols, ensure_ascii=False),
                    license_note, int(bool(item.get("enabled", True))),
                ),
            )
            registered += 1
    return registered


def import_raw_research_evidence(
    database: Database,
    evidence: Mapping[str, Any],
    *,
    retrieved_at: datetime | None = None,
) -> EvidenceImportResult:
    source_id = str(evidence.get("source_id", "")).strip()
    title = str(evidence.get("title", "")).strip()
    url = str(evidence.get("url", "")).strip()
    content = str(evidence.get("content", "")).strip()
    symbols = _clean_unique(evidence.get("asset_symbols"), "asset symbols")
    if not source_id or not title or not url or not content:
        raise ValueError("source ID, title, URL and evidence content are required")
    if len(content) > 12000:
        raise ValueError("raw research evidence content cannot exceed 12000 characters")
    published_at = _aware_datetime(evidence.get("published_at"), "published_at")
    retrieved = retrieved_at or datetime.now(timezone.utc)
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    if published_at > retrieved:
        raise ValueError("research evidence cannot be retrieved before it is published")

    source_rows = database.fetch_all(
        "SELECT * FROM research_evidence_sources WHERE source_id = ?", (source_id,)
    )
    if not source_rows:
        raise ValueError(f"research evidence source is not registered: {source_id}")
    source = source_rows[0]
    if not source["enabled"]:
        raise ValueError(f"research evidence source is disabled: {source_id}")
    domains = tuple(json.loads(source["allowed_domains"]))
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    if not hostname or not any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in domains
    ):
        raise ValueError(f"evidence URL is outside the source allowlist: {hostname or url}")
    allowed_symbols = set(json.loads(source["asset_symbols"]))
    if not set(symbols) <= allowed_symbols:
        raise ValueError("evidence assets are outside the registered source coverage")

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    identity = f"{source_id}\n{url}\n{published_at.isoformat()}\n{content_hash}"
    raw_id = f"raw-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
    existing = database.fetch_all(
        """
        SELECT raw_evidence_id, review_status FROM raw_research_evidence
        WHERE source_id = ? AND url = ? AND content_sha256 = ?
        """,
        (source_id, url, content_hash),
    )
    if existing:
        return EvidenceImportResult(
            existing[0]["raw_evidence_id"], False, content_hash, existing[0]["review_status"]
        )
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO raw_research_evidence (
                raw_evidence_id, source_id, title, url, published_at, retrieved_at,
                content, content_sha256, asset_symbols
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_id, source_id, title, url, published_at.isoformat(),
                retrieved.astimezone(timezone.utc).isoformat(), content, content_hash,
                json.dumps(symbols, ensure_ascii=False),
            ),
        )
    return EvidenceImportResult(raw_id, True, content_hash, "pending")


def _clean_unique(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a non-empty list")
    cleaned = tuple(str(item).strip().lower() if label == "allowed domains" else str(item).strip() for item in value)
    if not cleaned or any(not item for item in cleaned) or len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{label} must be non-empty and unique")
    return cleaned


def _aware_datetime(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError(f"{label} must be a valid ISO 8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed
