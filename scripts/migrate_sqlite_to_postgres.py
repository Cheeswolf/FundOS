import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage import Database, PostgresDatabase  # noqa: E402
from fundos.storage.data_migration import migrate_sqlite_to_postgres  # noqa: E402
from fundos.storage.postgres import check_postgres_readiness  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate a stopped FundOS SQLite database into empty PostgreSQL"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "data" / "fundos.sqlite3",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("FUNDOS_POSTGRES_URL"),
        help="Target URL; defaults to FUNDOS_POSTGRES_URL",
    )
    parser.add_argument(
        "--confirm-writes-stopped",
        action="store_true",
        help="Required confirmation that API and scheduled writers are stopped",
    )
    arguments = parser.parse_args()
    if not arguments.confirm_writes_stopped:
        parser.error("--confirm-writes-stopped is required")
    if not arguments.source.is_file():
        parser.error(f"SQLite source does not exist: {arguments.source}")
    if not arguments.database_url:
        parser.error("--database-url or FUNDOS_POSTGRES_URL is required")

    readiness = check_postgres_readiness(arguments.database_url)
    if not readiness.ready:
        raise SystemExit("PostgreSQL migration refused: target preflight is not ready")
    target = PostgresDatabase(arguments.database_url)
    target.initialize()
    report = migrate_sqlite_to_postgres(
        Database(arguments.source),
        target,
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
