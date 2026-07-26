from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fundos.storage import Database


def get_model_circuit_status(
    database: Database, *, provider: str, model: str, failure_threshold: int
) -> dict[str, Any]:
    if not provider.strip() or not model.strip():
        raise ValueError("provider and model are required")
    if failure_threshold < 1:
        raise ValueError("failure threshold must be positive")
    resets = database.fetch_all(
        """
        SELECT * FROM model_circuit_resets
        WHERE provider = ? AND model = ? ORDER BY reset_at DESC LIMIT 1
        """,
        (provider, model),
    )
    reset_at = resets[0]["reset_at"] if resets else None
    timestamp = database.dialect.timestamp_expression
    rows = database.fetch_all(
        f"""
        SELECT status, created_at FROM model_calls
        WHERE provider = ? AND model = ?
          AND {timestamp("created_at")} >
              COALESCE({timestamp("?")}, {timestamp("'0001-01-01 00:00:00'")})
        ORDER BY {timestamp("created_at")} DESC LIMIT ?
        """,
        (provider, model, reset_at or "", failure_threshold),
    )
    consecutive_failures = 0
    for row in rows:
        if row["status"] != "failed":
            break
        consecutive_failures += 1
    return {
        "provider": provider,
        "model": model,
        "state": "open" if consecutive_failures >= failure_threshold else "closed",
        "consecutive_failures": consecutive_failures,
        "failure_threshold": failure_threshold,
        "last_reset_at": reset_at,
        "last_reset_by": resets[0]["reset_by"] if resets else None,
    }


def reset_model_circuit(
    database: Database, *, provider: str, model: str, reset_by: str, reason: str
) -> int:
    if not all(value.strip() for value in (provider, model, reset_by, reason)):
        raise ValueError("provider, model, reset actor and reason are required")
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO model_circuit_resets (provider, model, reset_by, reason, reset_at)
            VALUES (?, ?, ?, ?, ?)
            RETURNING reset_id
            """,
            (provider.strip(), model.strip(), reset_by.strip(), reason.strip(), datetime.now(timezone.utc).isoformat()),
        )
        row = cursor.fetchone()
    return int(row["reset_id"])


def update_alert_lifecycle(
    database: Database, *, alert_id: str, state: str, updated_by: str, note: str
) -> str:
    if state not in {"acknowledged", "resolved"}:
        raise ValueError("alert lifecycle state is invalid")
    if not updated_by.strip() or not note.strip():
        raise ValueError("alert actor and note are required")
    alerts = database.fetch_all("SELECT 1 FROM alert_events WHERE alert_id = ?", (alert_id,))
    if not alerts:
        raise ValueError("alert does not exist")
    existing = database.fetch_all("SELECT state FROM alert_lifecycle WHERE alert_id = ?", (alert_id,))
    if existing and existing[0]["state"] == "resolved" and state == "acknowledged":
        raise ValueError("a resolved alert cannot return to acknowledged")
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO alert_lifecycle (alert_id, state, updated_by, note, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alert_id) DO UPDATE SET
                state = excluded.state, updated_by = excluded.updated_by,
                note = excluded.note, updated_at = excluded.updated_at
            """,
            (alert_id, state, updated_by.strip(), note.strip(), datetime.now(timezone.utc).isoformat()),
        )
    return state
