import sqlite3
import hashlib
import json
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

    def add_api_audit_log(target: sqlite3.Connection) -> None:
        target.execute(
            """
            CREATE TABLE IF NOT EXISTS api_audit_events (
                audit_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'rejected', 'failed')),
                status_code INTEGER NOT NULL,
                client_ip TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

    def add_audit_integrity_chain(target: sqlite3.Connection) -> None:
        columns = {row[1] for row in target.execute("PRAGMA table_info(api_audit_events)")}
        if "previous_hash" not in columns:
            target.execute("ALTER TABLE api_audit_events ADD COLUMN previous_hash TEXT")
        if "event_hash" not in columns:
            target.execute("ALTER TABLE api_audit_events ADD COLUMN event_hash TEXT")
        fields = (
            "audit_id", "request_id", "method", "path", "actor_id", "actor_role",
            "outcome", "status_code", "client_ip", "created_at",
        )
        previous_hash = ""
        for row in target.execute("SELECT * FROM api_audit_events ORDER BY created_at, audit_id").fetchall():
            item = dict(row)
            canonical = json.dumps(
                {field: item.get(field) for field in fields},
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )
            digest = hashlib.sha256(f"{previous_hash}\n{canonical}".encode("utf-8")).hexdigest()
            target.execute(
                "UPDATE api_audit_events SET previous_hash = ?, event_hash = ? WHERE audit_id = ?",
                (previous_hash, digest, item["audit_id"]),
            )
            previous_hash = digest
        target.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_retention_anchors (
                anchor_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cutoff_at TEXT NOT NULL,
                anchor_audit_id TEXT NOT NULL,
                anchor_hash TEXT NOT NULL,
                deleted_count INTEGER NOT NULL CHECK (deleted_count > 0),
                created_at TEXT NOT NULL
            )
            """
        )

    def ensure_model_call_cost_column(target: sqlite3.Connection) -> None:
        columns = {row[1] for row in target.execute("PRAGMA table_info(model_calls)")}
        if "estimated_cost_usd" not in columns:
            target.execute(
                """
                ALTER TABLE model_calls ADD COLUMN estimated_cost_usd REAL
                CHECK (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0)
                """
            )

    def add_market_data_observations(target: sqlite3.Connection) -> None:
        target.execute(
            """
            CREATE TABLE IF NOT EXISTS market_data_observations (
                provider TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                symbol TEXT NOT NULL REFERENCES assets(symbol),
                valuation_date TEXT NOT NULL,
                announced_date TEXT NOT NULL,
                available_date TEXT NOT NULL,
                value_field TEXT NOT NULL,
                raw_value REAL NOT NULL CHECK (raw_value > 0),
                normalized_value REAL NOT NULL CHECK (normalized_value > 0),
                revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                retrieved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, provider_symbol, valuation_date)
            )
            """
        )

    def add_research_evidence_content(target: sqlite3.Connection) -> None:
        if target.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'research_evidence'"
        ).fetchone() is None:
            return
        columns = {row[1] for row in target.execute("PRAGMA table_info(research_evidence)")}
        if "content" not in columns:
            target.execute(
                "ALTER TABLE research_evidence ADD COLUMN content TEXT NOT NULL DEFAULT ''"
            )

    def add_raw_research_evidence_store(target: sqlite3.Connection) -> None:
        target.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_evidence_sources (
                source_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_type TEXT NOT NULL
                    CHECK (source_type IN ('official', 'licensed', 'internal')),
                allowed_domains TEXT NOT NULL,
                asset_symbols TEXT NOT NULL,
                license_note TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS raw_research_evidence (
                raw_evidence_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES research_evidence_sources(source_id),
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                content TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                asset_symbols TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (review_status IN ('pending', 'approved', 'rejected')),
                reviewed_by TEXT,
                reviewed_at TEXT,
                review_note TEXT,
                UNIQUE (source_id, url, content_sha256)
            );
            """
        )

    def add_evidence_collection_runs(target: sqlite3.Connection) -> None:
        target.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_collection_runs (
                run_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES research_evidence_sources(source_id),
                status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
                discovered_count INTEGER NOT NULL DEFAULT 0,
                imported_count INTEGER NOT NULL DEFAULT 0,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )

    migrations = (
        Migration(1, "create_current_schema", create_current_schema),
        Migration(2, "upgrade_legacy_columns", upgrade_legacy_columns),
        Migration(3, "add_model_call_audit", add_model_call_audit),
        Migration(4, "add_model_operations_control", add_model_operations_control),
        Migration(5, "add_api_audit_log", add_api_audit_log),
        Migration(6, "add_audit_integrity_chain", add_audit_integrity_chain),
        Migration(7, "ensure_model_call_cost_column", ensure_model_call_cost_column),
        Migration(8, "add_market_data_observations", add_market_data_observations),
        Migration(9, "add_research_evidence_content", add_research_evidence_content),
        Migration(10, "add_raw_research_evidence_store", add_raw_research_evidence_store),
        Migration(11, "add_evidence_collection_runs", add_evidence_collection_runs),
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
