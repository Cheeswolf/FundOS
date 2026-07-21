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

    migrations = (
        Migration(1, "create_current_schema", create_current_schema),
        Migration(2, "upgrade_legacy_columns", upgrade_legacy_columns),
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

