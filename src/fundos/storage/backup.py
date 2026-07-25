from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CRITICAL_TABLES = (
    "portfolio_products", "portfolio_versions", "research_reports", "workflow_runs",
    "portfolio_nav", "api_audit_events",
)


@dataclass(frozen=True, slots=True)
class BackupVerification:
    valid: bool
    sha256: str
    size_bytes: int
    schema_version: int
    table_counts: dict[str, int]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect(path: Path) -> tuple[int, dict[str, int]]:
    with closing(sqlite3.connect(path)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError(f"SQLite integrity check failed: {result}")
        version = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
        existing = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in CRITICAL_TABLES if table in existing
        }
    return int(version), counts


def create_database_backup(
    source_path: str | Path, destination_directory: str | Path, *, label: str = "fundos"
) -> tuple[Path, Path]:
    source = Path(source_path).resolve()
    destination = Path(destination_directory).resolve()
    if not source.is_file():
        raise ValueError("source database does not exist")
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = destination / f"{label}-{timestamp}.sqlite3"
    if backup_path == source:
        raise ValueError("backup path cannot equal source database")
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(backup_path)) as target_connection:
            source_connection.backup(target_connection)
    version, counts = _inspect(backup_path)
    manifest_path = backup_path.with_suffix(".manifest.json")
    manifest = {
        "format_version": 1,
        "backup_file": backup_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": _sha256(backup_path),
        "size_bytes": backup_path.stat().st_size,
        "schema_version": version,
        "table_counts": counts,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return backup_path, manifest_path


def verify_database_backup(
    backup_path: str | Path, manifest_path: str | Path | None = None
) -> BackupVerification:
    backup = Path(backup_path).resolve()
    manifest_file = Path(manifest_path).resolve() if manifest_path else backup.with_suffix(".manifest.json")
    if not backup.is_file() or not manifest_file.is_file():
        raise ValueError("backup database and manifest are required")
    manifest: dict[str, Any] = json.loads(manifest_file.read_text(encoding="utf-8"))
    digest = _sha256(backup)
    size = backup.stat().st_size
    if manifest.get("backup_file") != backup.name:
        raise ValueError("backup filename does not match manifest")
    if manifest.get("sha256") != digest or manifest.get("size_bytes") != size:
        raise ValueError("backup checksum or size does not match manifest")
    version, counts = _inspect(backup)
    if manifest.get("schema_version") != version or manifest.get("table_counts") != counts:
        raise ValueError("backup database contents do not match manifest")
    return BackupVerification(True, digest, size, version, counts)


def restore_database_backup(
    backup_path: str | Path,
    target_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    replace_existing: bool = False,
) -> Path | None:
    backup = Path(backup_path).resolve()
    target = Path(target_path).resolve()
    verify_database_backup(backup, manifest_path)
    if backup == target:
        raise ValueError("backup and restore target cannot be the same file")
    preserved: Path | None = None
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not replace_existing:
            raise FileExistsError("restore target exists; explicit replacement is required")
        preserved, _ = create_database_backup(target, target.parent, label=f"{target.stem}-pre-restore")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".restore", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(backup)) as source_connection:
            with closing(sqlite3.connect(temporary)) as target_connection:
                source_connection.backup(target_connection)
        _inspect(temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return preserved
