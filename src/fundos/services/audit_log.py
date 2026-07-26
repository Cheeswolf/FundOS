from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fundos.storage import Database


AUDIT_FIELDS = (
    "audit_id", "request_id", "method", "path", "actor_id", "actor_role",
    "outcome", "status_code", "client_ip", "created_at",
)


def event_hash(event: dict[str, Any], previous_hash: str) -> str:
    canonical = json.dumps(
        {field: event.get(field) for field in AUDIT_FIELDS},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(f"{previous_hash}\n{canonical}".encode("utf-8")).hexdigest()


def record_audit_event(database: Database, event: dict[str, Any]) -> str:
    with database.connect() as connection:
        database.begin_idempotent_write(connection, "audit-chain")
        previous = connection.execute(
            "SELECT event_hash FROM api_audit_events ORDER BY created_at DESC, audit_id DESC LIMIT 1"
        ).fetchone()
        if previous:
            previous_hash = previous["event_hash"]
        else:
            anchor = connection.execute(
                "SELECT anchor_hash FROM audit_retention_anchors ORDER BY anchor_id DESC LIMIT 1"
            ).fetchone()
            previous_hash = anchor["anchor_hash"] if anchor else ""
        digest = event_hash(event, previous_hash)
        connection.execute(
            """
            INSERT INTO api_audit_events (
                audit_id, request_id, method, path, actor_id, actor_role, outcome,
                status_code, client_ip, created_at, previous_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(event.get(field) for field in AUDIT_FIELDS) + (previous_hash, digest),
        )
    return digest


def verify_audit_chain(database: Database) -> dict[str, Any]:
    rows = database.fetch_all("SELECT * FROM api_audit_events ORDER BY created_at, audit_id")
    if rows:
        expected_previous = rows[0]["previous_hash"]
        if expected_previous:
            anchors = database.fetch_all(
                "SELECT 1 FROM audit_retention_anchors WHERE anchor_hash = ?", (expected_previous,)
            )
            if not anchors:
                return {"valid": False, "checked_events": 0, "broken_audit_id": rows[0]["audit_id"]}
    else:
        expected_previous = ""
    for index, row in enumerate(rows):
        item = dict(row)
        if item["previous_hash"] != expected_previous or item["event_hash"] != event_hash(item, expected_previous):
            return {"valid": False, "checked_events": index, "broken_audit_id": item["audit_id"]}
        expected_previous = item["event_hash"]
    return {"valid": True, "checked_events": len(rows), "broken_audit_id": None}


def purge_audit_events(database: Database, *, retention_days: int) -> int:
    if retention_days < 30:
        raise ValueError("audit retention must be at least 30 days")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with database.connect() as connection:
        database.begin_idempotent_write(connection, "audit-retention")
        last = connection.execute(
            """
            SELECT audit_id, event_hash FROM api_audit_events
            WHERE created_at < ? ORDER BY created_at DESC, audit_id DESC LIMIT 1
            """,
            (cutoff,),
        ).fetchone()
        if last is None:
            return 0
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM api_audit_events WHERE created_at < ?", (cutoff,)
        ).fetchone()["count"]
        connection.execute(
            """
            INSERT INTO audit_retention_anchors
                (cutoff_at, anchor_audit_id, anchor_hash, deleted_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cutoff, last["audit_id"], last["event_hash"], count, datetime.now(timezone.utc).isoformat()),
        )
        connection.execute("DELETE FROM api_audit_events WHERE created_at < ?", (cutoff,))
    return int(count)
