import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage.postgres_backup import (  # noqa: E402
    create_postgres_backup,
    verify_postgres_backup,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create and verify a native PostgreSQL custom-format backup"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("FUNDOS_DATABASE_URL"),
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=PROJECT_ROOT / "backups" / "postgresql",
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        parser.error("--database-url or FUNDOS_DATABASE_URL is required")
    backup, manifest = create_postgres_backup(
        arguments.database_url,
        arguments.destination,
    )
    verification = verify_postgres_backup(backup, manifest)
    print(json.dumps({
        "backup": str(backup),
        "manifest": str(manifest),
        "verified": verification.valid,
        "schema_version": verification.schema_version,
    }))


if __name__ == "__main__":
    main()
