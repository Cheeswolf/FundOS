from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.services import import_raw_research_evidence  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import raw evidence from a registered source")
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3"
    )
    arguments = parser.parse_args()
    payload = json.loads(arguments.input.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else payload.get("evidence", [payload])
    if not isinstance(items, list) or not items:
        raise SystemExit("Evidence input must contain at least one item")
    database = Database(arguments.database)
    database.initialize()
    created = 0
    for item in items:
        result = import_raw_research_evidence(database, item)
        created += int(result.created)
        print(
            f"{result.raw_evidence_id}: "
            f"{'created' if result.created else 'duplicate'}, {result.review_status}"
        )
    print(f"Imported {created} new raw evidence items; {len(items) - created} duplicates")


if __name__ == "__main__":
    main()
