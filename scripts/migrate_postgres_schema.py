import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage import PostgresDatabase  # noqa: E402
from fundos.storage.postgres import check_postgres_readiness  # noqa: E402
from fundos.storage.versions import CURRENT_SCHEMA_VERSION  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upgrade or roll back the versioned FundOS PostgreSQL schema"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("FUNDOS_DATABASE_URL"),
    )
    parser.add_argument(
        "--target-version",
        type=int,
        default=CURRENT_SCHEMA_VERSION,
    )
    parser.add_argument(
        "--confirm-rollback",
        action="store_true",
        help="Required when target version is below the currently applied version",
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        parser.error("--database-url or FUNDOS_DATABASE_URL is required")
    readiness = check_postgres_readiness(arguments.database_url)
    if not readiness.ready:
        raise SystemExit("PostgreSQL migration refused: preflight is not ready")
    database = PostgresDatabase(arguments.database_url)
    try:
        current = database.get_schema_version()
    except Exception:
        current = 0
    if arguments.target_version < current and not arguments.confirm_rollback:
        parser.error("--confirm-rollback is required for a downgrade")
    applied = database.migrate_to_version(arguments.target_version)
    print(json.dumps({
        "previous_version": current,
        "target_version": arguments.target_version,
        "applied_version": applied,
        "direction": (
            "upgrade" if applied > current
            else "rollback" if applied < current
            else "unchanged"
        ),
    }))


if __name__ == "__main__":
    main()
