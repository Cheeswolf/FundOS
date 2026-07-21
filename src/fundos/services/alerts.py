import json
from dataclasses import dataclass
from typing import Callable
from urllib.request import Request, urlopen
from uuid import uuid4

from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class AlertDeliveryResult:
    delivered: int
    failed: int


def create_alert(
    database: Database,
    *,
    source_type: str,
    source_id: str,
    severity: str,
    title: str,
    message: str,
) -> str:
    if severity not in {"warning", "critical"}:
        raise ValueError("alert severity must be warning or critical")
    if not title.strip() or not message.strip():
        raise ValueError("alert title and message are required")
    existing = database.fetch_all(
        "SELECT alert_id FROM alert_events WHERE source_type = ? AND source_id = ?",
        (source_type, source_id),
    )
    if existing:
        return existing[0]["alert_id"]
    alert_id = str(uuid4())
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO alert_events
                (alert_id, source_type, source_id, severity, title, message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (alert_id, source_type, source_id, severity, title.strip(), message.strip()),
        )
    return alert_id


def deliver_pending_alerts(
    database: Database,
    *,
    webhook_url: str,
    opener: Callable = urlopen,
    timeout_seconds: int = 15,
) -> AlertDeliveryResult:
    if not webhook_url.strip():
        raise ValueError("webhook URL is required")
    alerts = database.fetch_all(
        "SELECT * FROM alert_events WHERE status IN ('pending', 'failed') ORDER BY created_at"
    )
    delivered = 0
    failed = 0
    for alert in alerts:
        payload = json.dumps({
            "alert_id": alert["alert_id"],
            "severity": alert["severity"],
            "title": alert["title"],
            "message": alert["message"],
            "source_type": alert["source_type"],
            "source_id": alert["source_id"],
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "FundOS/0.1 alert-client"},
            method="POST",
        )
        try:
            with opener(request, timeout=timeout_seconds) as response:
                status_code = getattr(response, "status", 200)
                if not 200 <= status_code < 300:
                    raise OSError(f"webhook returned HTTP {status_code}")
            with database.connect() as connection:
                connection.execute(
                    """
                    UPDATE alert_events SET status = 'delivered',
                        delivery_attempts = delivery_attempts + 1,
                        last_error = NULL, delivered_at = CURRENT_TIMESTAMP
                    WHERE alert_id = ?
                    """,
                    (alert["alert_id"],),
                )
            delivered += 1
        except OSError as error:
            with database.connect() as connection:
                connection.execute(
                    """
                    UPDATE alert_events SET status = 'failed',
                        delivery_attempts = delivery_attempts + 1, last_error = ?
                    WHERE alert_id = ?
                    """,
                    (str(error), alert["alert_id"]),
                )
            failed += 1
    return AlertDeliveryResult(delivered, failed)

