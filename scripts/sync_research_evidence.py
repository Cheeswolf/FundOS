from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.data_providers import OfficialResearchCollector  # noqa: E402
from fundos.services import register_research_sources, run_evidence_collection  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect official PBC/NBS research pages into the pending evidence store"
    )
    parser.add_argument("--source", action="append", help="Source ID; may be repeated")
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument(
        "--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3"
    )
    parser.add_argument(
        "--collectors",
        type=Path,
        default=PROJECT_ROOT / "config" / "official_research_collectors.json",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=PROJECT_ROOT / "config" / "research_sources.json",
    )
    arguments = parser.parse_args()
    if arguments.max_items < 1 or arguments.max_items > 50:
        raise SystemExit("--max-items must be between 1 and 50")
    collector_config = json.loads(arguments.collectors.read_text(encoding="utf-8"))
    source_config = json.loads(arguments.sources.read_text(encoding="utf-8"))
    selected = set(arguments.source or [])
    configurations = [
        item for item in collector_config["collectors"]
        if not selected or item["source_id"] in selected
    ]
    missing = selected - {item["source_id"] for item in configurations}
    if missing:
        raise SystemExit(f"Unknown collector sources: {', '.join(sorted(missing))}")
    database = Database(arguments.database)
    database.initialize()
    register_research_sources(database, source_config["sources"])
    failures = 0
    for configuration in configurations:
        result = run_evidence_collection(
            database,
            collector=OfficialResearchCollector.from_mapping(configuration),
            max_items=arguments.max_items,
        )
        print(
            f"{result.source_id}: {result.status}; discovered {result.discovered_count}, "
            f"imported {result.imported_count}, duplicates {result.duplicate_count}"
        )
        if result.error_message:
            print(f"ERROR: {result.error_message}")
        failures += int(result.status == "failed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
