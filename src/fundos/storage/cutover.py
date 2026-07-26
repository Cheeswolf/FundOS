from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin
from urllib.request import urlopen

from fundos.services.audit_log import verify_audit_chain
from fundos.storage import Database
from fundos.storage.data_migration import rows_digest


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    table_name: str
    row_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    schema_version: int
    tables: tuple[TableSnapshot, ...]

    @property
    def total_rows(self) -> int:
        return sum(table.row_count for table in self.tables)


@dataclass(frozen=True, slots=True)
class CutoverDrillReport:
    ready: bool
    decision: str
    source: DatabaseSnapshot
    target: DatabaseSnapshot
    checks: dict[str, bool]
    probes: dict[str, int | bool]
    rollback: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "decision": self.decision,
            "source": {
                **asdict(self.source),
                "total_rows": self.source.total_rows,
            },
            "target": {
                **asdict(self.target),
                "total_rows": self.target.total_rows,
            },
            "checks": self.checks,
            "probes": self.probes,
            "rollback": self.rollback,
        }


def snapshot_database(database: Database) -> DatabaseSnapshot:
    snapshots: list[TableSnapshot] = []
    for table in database.list_tables():
        rows = [
            dict(row)
            for row in database.fetch_all(f'SELECT * FROM "{table}"')
        ]
        snapshots.append(
            TableSnapshot(table, len(rows), rows_digest(rows))
        )
    return DatabaseSnapshot(
        database.get_schema_version(),
        tuple(snapshots),
    )


def drill_cutover(
    source: Database,
    target: Database,
) -> CutoverDrillReport:
    source_snapshot = snapshot_database(source)
    target_snapshot = snapshot_database(target)
    source_tables = {
        table.table_name: table for table in source_snapshot.tables
    }
    target_tables = {
        table.table_name: table for table in target_snapshot.tables
    }
    table_sets_match = source_tables.keys() == target_tables.keys()
    counts_match = table_sets_match and all(
        source_tables[name].row_count == target_tables[name].row_count
        for name in source_tables
    )
    digests_match = table_sets_match and all(
        source_tables[name].sha256 == target_tables[name].sha256
        for name in source_tables
    )
    checks = {
        "schema_versions_match": (
            source_snapshot.schema_version == target_snapshot.schema_version
        ),
        "table_sets_match": table_sets_match,
        "row_counts_match": counts_match,
        "content_digests_match": digests_match,
        "audit_chain_valid": bool(verify_audit_chain(target)["valid"]),
    }
    probes = {
        "products": _count(target, "portfolio_products"),
        "published_versions": _count(
            target,
            "portfolio_versions",
            "status = 'published'",
        ),
        "portfolio_nav_rows": _count(target, "portfolio_nav"),
        "benchmark_nav_rows": _count(target, "benchmark_nav"),
        "scheduled_job_runs": _count(target, "scheduled_job_runs"),
        "pipeline_runs": _count(target, "pipeline_runs"),
        "audit_events": _count(target, "api_audit_events"),
        "database_readable": True,
    }
    ready = all(checks.values())
    return CutoverDrillReport(
        ready=ready,
        decision=(
            "ready_for_manual_cutover"
            if ready
            else "remain_on_sqlite"
        ),
        source=source_snapshot,
        target=target_snapshot,
        checks=checks,
        probes=probes,
        rollback=(
            "No runtime configuration was changed; keep FUNDOS_DATABASE_URL "
            "unset and continue using FUNDOS_DB_PATH."
        ),
    )


def probe_candidate_api(
    base_url: str,
    *,
    timeout_seconds: float = 5,
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    for path in ("/health", "/products", "/dashboard"):
        with urlopen(
            urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
            timeout=timeout_seconds,
        ) as response:
            body = response.read()
            checks[path] = response.status == 200 and bool(body)
    return checks


def _count(
    database: Database,
    table: str,
    condition: str | None = None,
) -> int:
    query = f'SELECT COUNT(*) AS count FROM "{table}"'
    if condition:
        query += f" WHERE {condition}"
    return int(database.fetch_all(query)[0]["count"])
