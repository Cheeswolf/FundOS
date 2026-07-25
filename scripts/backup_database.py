from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage import create_database_backup, verify_database_backup  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify an online FundOS SQLite backup")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument("--destination", type=Path, default=PROJECT_ROOT / "backups")
    arguments = parser.parse_args()
    backup, manifest = create_database_backup(arguments.database, arguments.destination)
    verification = verify_database_backup(backup, manifest)
    print(json.dumps({"backup": str(backup), "manifest": str(manifest), "verified": verification.valid}))


if __name__ == "__main__":
    main()
