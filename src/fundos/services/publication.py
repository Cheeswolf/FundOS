from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class PublicationResult:
    version_id: str
    previous_version_id: str | None
    turnover: float
    status: str


def publish_portfolio_version(
    database: Database,
    *,
    version_id: str,
    reason: str,
    approved_by: str,
    connection: Any | None = None,
) -> PublicationResult:
    if not reason.strip() or not approved_by.strip():
        raise ValueError("publication reason and approver are required")

    if connection is None:
        with database.connect() as owned_connection:
            return publish_portfolio_version(
                database,
                version_id=version_id,
                reason=reason,
                approved_by=approved_by,
                connection=owned_connection,
            )
    else:
        version = connection.execute(
            "SELECT * FROM portfolio_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if version is None:
            raise ValueError("portfolio version does not exist")
        if version["status"] != "draft":
            raise ValueError("only a draft version can be published")

        mandate = connection.execute(
            "SELECT * FROM investment_mandates WHERE product_id = ?",
            (version["product_id"],),
        ).fetchone()
        if mandate is None:
            raise ValueError("investment mandate is required before publication")

        weight_rows = connection.execute(
            """
            SELECT w.asset_symbol, w.weight, a.asset_class
            FROM portfolio_version_weights w
            JOIN assets a ON a.symbol = w.asset_symbol
            WHERE w.version_id = ?
            """,
            (version_id,),
        ).fetchall()
        if not weight_rows:
            raise ValueError("portfolio version has no weights")
        if max(row["weight"] for row in weight_rows) > mandate["max_single_asset_weight"] + 1e-12:
            raise ValueError("single asset weight exceeds investment mandate")
        cash_weight = sum(row["weight"] for row in weight_rows if row["asset_class"] == "cash")
        if cash_weight + 1e-12 < mandate["min_cash_weight"]:
            raise ValueError("cash weight is below investment mandate")

        previous = connection.execute(
            """
            SELECT * FROM portfolio_versions
            WHERE product_id = ? AND status = 'published'
            ORDER BY version_number DESC LIMIT 1
            """,
            (version["product_id"],),
        ).fetchone()
        expected_number = 1 if previous is None else previous["version_number"] + 1
        if version["version_number"] != expected_number:
            raise ValueError("version number must follow the latest published version")
        if previous is not None and version["effective_date"] <= previous["effective_date"]:
            raise ValueError("effective date must be after the latest published version")

        current_weights = {row["asset_symbol"]: row["weight"] for row in weight_rows}
        previous_weights: dict[str, float] = {}
        if previous is not None:
            previous_weights = {
                row["asset_symbol"]: row["weight"]
                for row in connection.execute(
                    "SELECT asset_symbol, weight FROM portfolio_version_weights WHERE version_id = ?",
                    (previous["version_id"],),
                ).fetchall()
            }
        all_symbols = set(current_weights) | set(previous_weights)
        turnover = 0.0 if previous is None else sum(
            abs(current_weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0))
            for symbol in all_symbols
        ) / 2
        if turnover > mandate["max_turnover"] + 1e-12:
            raise ValueError("portfolio turnover exceeds investment mandate")

        connection.execute(
            "UPDATE portfolio_versions SET status = 'published', published_at = ? WHERE version_id = ?",
            (datetime.now(timezone.utc).isoformat(), version_id),
        )
        connection.execute(
            """
            INSERT INTO rebalance_records (
                product_id, previous_version_id, new_version_id, effective_date,
                turnover, reason, approved_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version["product_id"], previous["version_id"] if previous else None,
                version_id, version["effective_date"], turnover, reason.strip(), approved_by.strip(),
            ),
        )
        return PublicationResult(
            version_id,
            previous["version_id"] if previous else None,
            turnover,
            "published",
        )
