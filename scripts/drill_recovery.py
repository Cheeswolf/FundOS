from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.storage import (  # noqa: E402
    create_database_backup, restore_database_backup, verify_database_backup,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a non-destructive FundOS recovery drill")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    parser.add_argument("--backup-directory", type=Path, default=PROJECT_ROOT / "backups")
    arguments = parser.parse_args()
    backup, manifest = create_database_backup(arguments.database, arguments.backup_directory, label="drill")
    source = verify_database_backup(backup, manifest)
    with tempfile.TemporaryDirectory(prefix="fundos-recovery-drill-") as directory:
        restored = Path(directory) / "restored.sqlite3"
        restore_database_backup(backup, restored, manifest_path=manifest)
        drill_backup, drill_manifest = create_database_backup(restored, Path(directory), label="verified")
        recovered = verify_database_backup(drill_backup, drill_manifest)
        if source.schema_version != recovered.schema_version or source.table_counts != recovered.table_counts:
            raise SystemExit("recovery drill failed: restored database differs from backup")
    print(json.dumps({"status": "passed", "backup": str(backup), "manifest": str(manifest)}))


if __name__ == "__main__":
    main()
