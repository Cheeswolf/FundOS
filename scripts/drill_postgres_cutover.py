import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage import Database, PostgresDatabase  # noqa: E402
from fundos.storage.cutover import drill_cutover, probe_candidate_api  # noqa: E402
from fundos.storage.postgres import check_postgres_readiness  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a non-destructive FundOS PostgreSQL cutover drill"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "data" / "fundos.sqlite3",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("FUNDOS_POSTGRES_URL"),
    )
    parser.add_argument(
        "--confirm-writes-stopped",
        action="store_true",
        help="Required confirmation that API and scheduled writers are stopped",
    )
    parser.add_argument(
        "--candidate-api-url",
        help="Optional candidate API URL for read-only HTTP smoke checks",
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
        raise SystemExit("Cutover drill refused: PostgreSQL preflight is not ready")
    report = drill_cutover(
        Database(arguments.source),
        PostgresDatabase(arguments.database_url),
    )
    payload = report.as_dict()
    api_smoke = (
        probe_candidate_api(arguments.candidate_api_url)
        if arguments.candidate_api_url
        else {}
    )
    payload["candidate_api_smoke"] = api_smoke
    ready = report.ready and all(api_smoke.values())
    payload["ready"] = ready
    payload["decision"] = (
        "ready_for_manual_cutover" if ready else "remain_on_sqlite"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not ready:
        raise SystemExit("Cutover drill failed; remain on SQLite")


if __name__ == "__main__":
    main()
