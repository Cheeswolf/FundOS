from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.services import review_raw_research_evidence  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve or reject pending research evidence")
    parser.add_argument("raw_evidence_id")
    decision = parser.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument(
        "--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3"
    )
    arguments = parser.parse_args()
    database = Database(arguments.database)
    database.initialize()
    result = review_raw_research_evidence(
        database,
        raw_evidence_id=arguments.raw_evidence_id,
        approved=arguments.approve,
        reviewed_by=arguments.reviewed_by,
        note=arguments.note,
    )
    print(
        f"{result.raw_evidence_id}: {result.review_status} by "
        f"{result.reviewed_by} at {result.reviewed_at}"
    )


if __name__ == "__main__":
    main()
