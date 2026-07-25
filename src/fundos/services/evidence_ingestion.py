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


@dataclass(frozen=True, slots=True)
class EvidenceReviewResult:
    raw_evidence_id: str
    review_status: str
    reviewed_by: str
    reviewed_at: str


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


def review_raw_research_evidence(
    database: Database,
    *,
    raw_evidence_id: str,
    approved: bool,
    reviewed_by: str,
    note: str,
) -> EvidenceReviewResult:
    evidence_id = raw_evidence_id.strip()
    reviewer = reviewed_by.strip()
    review_note = note.strip()
    if not evidence_id or not reviewer or not review_note:
        raise ValueError("evidence ID, reviewer and review note are required")
    rows = database.fetch_all(
        """
        SELECT e.*, s.enabled FROM raw_research_evidence e
        JOIN research_evidence_sources s ON s.source_id = e.source_id
        WHERE e.raw_evidence_id = ?
        """,
        (evidence_id,),
    )
    if not rows:
        raise ValueError("raw research evidence does not exist")
    evidence = rows[0]
    if evidence["review_status"] != "pending":
        raise ValueError("only pending research evidence can be reviewed")
    if approved and not evidence["enabled"]:
        raise ValueError("evidence from a disabled source cannot be approved")
    actual_hash = hashlib.sha256(evidence["content"].encode("utf-8")).hexdigest()
    if actual_hash != evidence["content_sha256"]:
        raise ValueError("research evidence content integrity check failed")
    status = "approved" if approved else "rejected"
    reviewed_at = datetime.now(timezone.utc).isoformat()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE raw_research_evidence SET
                review_status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ?
            WHERE raw_evidence_id = ? AND review_status = 'pending'
            """,
            (status, reviewer, reviewed_at, review_note, evidence_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("research evidence was reviewed concurrently")
    return EvidenceReviewResult(evidence_id, status, reviewer, reviewed_at)


def build_approved_research_request(
    database: Database,
    *,
    product_id: str,
    report_id: str,
    as_of_date: str,
) -> dict[str, Any]:
    product = product_id.strip()
    report = report_id.strip()
    if not product or not report:
        raise ValueError("product and report IDs are required")
    cutoff = _aware_date(as_of_date)
    if database.fetch_all("SELECT 1 FROM research_reports WHERE report_id = ?", (report,)):
        raise ValueError(f"research report already exists: {report}")
    versions = database.fetch_all(
        """
        SELECT version_id FROM portfolio_versions
        WHERE product_id = ? AND status = 'published' AND effective_date <= ?
        ORDER BY effective_date DESC, version_number DESC LIMIT 1
        """,
        (product, cutoff),
    )
    if not versions:
        raise ValueError("product has no published portfolio version by the report date")
    symbols = [
        row["asset_symbol"]
        for row in database.fetch_all(
            """
            SELECT asset_symbol FROM portfolio_version_weights
            WHERE version_id = ? ORDER BY asset_symbol
            """,
            (versions[0]["version_id"],),
        )
    ]
    rows = database.fetch_all(
        """
        SELECT e.*, s.name AS source_name
        FROM raw_research_evidence e
        JOIN research_evidence_sources s ON s.source_id = e.source_id
        WHERE e.review_status = 'approved' AND s.enabled = 1
          AND substr(e.published_at, 1, 10) <= ?
        ORDER BY e.published_at, e.raw_evidence_id
        """,
        (cutoff,),
    )
    selected = [
        row for row in rows if set(json.loads(row["asset_symbols"])) & set(symbols)
    ]
    covered = {
        symbol
        for row in selected
        for symbol in json.loads(row["asset_symbols"])
        if symbol in symbols
    }
    missing = sorted(set(symbols) - covered)
    if missing:
        raise ValueError(
            f"approved research evidence does not cover assets: {', '.join(missing)}"
        )
    for row in selected:
        actual_hash = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
        if actual_hash != row["content_sha256"]:
            raise ValueError(
                f"approved research evidence integrity check failed: {row['raw_evidence_id']}"
            )
    return {
        "report_id": report,
        "product_id": product,
        "as_of_date": cutoff,
        "asset_symbols": symbols,
        "evidence": [
            {
                "evidence_id": row["raw_evidence_id"],
                "title": row["title"],
                "source": row["source_name"],
                "url": row["url"],
                "published_at": row["published_at"],
                "content": row["content"],
            }
            for row in selected
        ],
    }


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


def _aware_date(value: object) -> str:
    from datetime import date

    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as error:
        raise ValueError("as_of_date must be a valid ISO date") from error
