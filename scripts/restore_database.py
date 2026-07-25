from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage import restore_database_backup, verify_database_backup  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and restore a FundOS SQLite backup")
    parser.add_argument("backup", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--replace", action="store_true", help="Replace target after preserving a pre-restore backup")
    arguments = parser.parse_args()
    verification = verify_database_backup(arguments.backup, arguments.manifest)
    preserved = restore_database_backup(
        arguments.backup, arguments.target, manifest_path=arguments.manifest,
        replace_existing=arguments.replace,
    )
    print(json.dumps({
        "restored": str(arguments.target), "schema_version": verification.schema_version,
        "preserved_previous": str(preserved) if preserved else None,
    }))


if __name__ == "__main__":
    main()
