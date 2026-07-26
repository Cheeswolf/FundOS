import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.services.deployment_preflight import run_deployment_preflight  # noqa: E402
from fundos.storage import Database, database_from_url  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate FundOS production deployment gates"
    )
    parser.add_argument("--mode", choices=("production", "cutover"), required=True)
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "data" / "fundos.sqlite3",
    )
    parser.add_argument("--database-url", default=os.environ.get("FUNDOS_DATABASE_URL"))
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--maximum-backup-age-hours", type=float, default=24)
    arguments = parser.parse_args()

    database = (
        database_from_url(arguments.database_url)
        if arguments.database_url
        else Database(arguments.database)
    )
    report = run_deployment_preflight(
        database,
        environment=os.environ,
        mode=arguments.mode,
        backup_manifest=arguments.backup_manifest,
        maximum_backup_age_hours=arguments.maximum_backup_age_hours,
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if not report.ready:
        raise SystemExit("Deployment preflight blocked")


if __name__ == "__main__":
    main()
