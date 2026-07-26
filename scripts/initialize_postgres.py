import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage import PostgresDatabase  # noqa: E402
from fundos.storage.postgres import check_postgres_readiness  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize the versioned FundOS PostgreSQL schema"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("FUNDOS_POSTGRES_URL"),
        help="PostgreSQL URL; defaults to FUNDOS_POSTGRES_URL",
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        parser.error("--database-url or FUNDOS_POSTGRES_URL is required")

    report = check_postgres_readiness(arguments.database_url)
    if not report.ready:
        raise SystemExit("PostgreSQL initialization refused: preflight is not ready")
    database = PostgresDatabase(arguments.database_url)
    database.initialize()
    print(f"PostgreSQL schema initialized at version {database.get_schema_version()}")


if __name__ == "__main__":
    main()
