import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage.postgres_backup import restore_postgres_backup  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore a FundOS backup into an explicitly provided empty PostgreSQL database"
    )
    parser.add_argument("backup", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--target-database-url",
        default=os.environ.get("FUNDOS_RESTORE_DATABASE_URL"),
    )
    parser.add_argument(
        "--confirm-empty-target",
        action="store_true",
        help="Required confirmation; the tool also verifies that no user tables exist",
    )
    arguments = parser.parse_args()
    if not arguments.confirm_empty_target:
        parser.error("--confirm-empty-target is required")
    if not arguments.target_database_url:
        parser.error(
            "--target-database-url or FUNDOS_RESTORE_DATABASE_URL is required"
        )
    report = restore_postgres_backup(
        arguments.backup,
        arguments.target_database_url,
        manifest_path=arguments.manifest,
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
