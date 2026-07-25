from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.services import register_research_sources  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Register controlled research evidence sources")
    parser.add_argument(
        "--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3"
    )
    parser.add_argument(
        "--config", type=Path, default=PROJECT_ROOT / "config" / "research_sources.json"
    )
    arguments = parser.parse_args()
    configuration = json.loads(arguments.config.read_text(encoding="utf-8"))
    database = Database(arguments.database)
    database.initialize()
    count = register_research_sources(database, configuration["sources"])
    print(f"Registered {count} controlled research evidence sources")


if __name__ == "__main__":
    main()
