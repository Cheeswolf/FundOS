from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.services import build_approved_research_request  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a ResearchAgent input using approved evidence only"
    )
    parser.add_argument("--product-id", default="fundos-index-allocation-trial")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3"
    )
    arguments = parser.parse_args()
    database = Database(arguments.database)
    database.initialize()
    request = build_approved_research_request(
        database,
        product_id=arguments.product_id,
        report_id=arguments.report_id,
        as_of_date=arguments.as_of,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Built {arguments.output}: {len(request['evidence'])} approved evidence items "
        f"covering {len(request['asset_symbols'])} assets"
    )


if __name__ == "__main__":
    main()
