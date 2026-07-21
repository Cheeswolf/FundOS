import sqlite3
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def apply_migrations(connection: sqlite3.Connection, schema: str) -> int:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    def create_current_schema(target: sqlite3.Connection) -> None:
        target.executescript(schema)

    def upgrade_legacy_columns(target: sqlite3.Connection) -> None:
        version_columns = {row[1] for row in target.execute("PRAGMA table_info(portfolio_versions)")}
        if "status" not in version_columns:
            target.execute("ALTER TABLE portfolio_versions ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
        if "published_at" not in version_columns:
            target.execute("ALTER TABLE portfolio_versions ADD COLUMN published_at TEXT")
        mandate_columns = {row[1] for row in target.execute("PRAGMA table_info(investment_mandates)")}
        if "maximum_data_age_days" not in mandate_columns:
            target.execute(
                "ALTER TABLE investment_mandates ADD COLUMN maximum_data_age_days INTEGER NOT NULL DEFAULT 3"
            )
        if "maximum_stress_loss" not in mandate_columns:
            target.execute(
                "ALTER TABLE investment_mandates ADD COLUMN maximum_stress_loss REAL NOT NULL DEFAULT 0.20"
            )
        proposal_columns = {row[1] for row in target.execute("PRAGMA table_info(portfolio_proposals)")}
        if "research_report_id" not in proposal_columns:
            target.execute("ALTER TABLE portfolio_proposals ADD COLUMN research_report_id TEXT")

    def add_model_call_audit(target: sqlite3.Connection) -> None:
        target.execute(
            """
            CREATE TABLE IF NOT EXISTS model_calls (
                call_id TEXT PRIMARY KEY,
                purpose TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
                attempts INTEGER NOT NULL CHECK (attempts > 0),
                latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
                input_tokens INTEGER,
                output_tokens INTEGER,
                estimated_cost_usd REAL CHECK (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0),
                prompt_sha256 TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

    def add_model_operations_control(target: sqlite3.Connection) -> None:
        target.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_circuit_resets (
                reset_id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                reset_by TEXT NOT NULL,
                reason TEXT NOT NULL,
                reset_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_lifecycle (
                alert_id TEXT PRIMARY KEY REFERENCES alert_events(alert_id),
                state TEXT NOT NULL CHECK (state IN ('acknowledged', 'resolved')),
                updated_by TEXT NOT NULL,
                note TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    migrations = (
        Migration(1, "create_current_schema", create_current_schema),
        Migration(2, "upgrade_legacy_columns", upgrade_legacy_columns),
        Migration(3, "add_model_call_audit", add_model_call_audit),
        Migration(4, "add_model_operations_control", add_model_operations_control),
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for migration in migrations:
        if migration.version in applied:
            continue
        migration.apply(connection)
        connection.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (migration.version, migration.name),
        )
    return migrations[-1].version
