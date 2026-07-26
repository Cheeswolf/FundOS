import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage.postgres import check_postgres_readiness  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check the target PostgreSQL server before FundOS migration"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("FUNDOS_POSTGRES_URL"),
        help="PostgreSQL URL; defaults to FUNDOS_POSTGRES_URL",
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        parser.error("--database-url or FUNDOS_POSTGRES_URL is required")

    try:
        report = check_postgres_readiness(arguments.database_url)
    except (RuntimeError, ValueError, OSError) as error:
        raise SystemExit(f"PostgreSQL preflight failed: {error}") from error
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if not report.ready:
        raise SystemExit("PostgreSQL preflight failed: target permissions or version are insufficient")


if __name__ == "__main__":
    main()
