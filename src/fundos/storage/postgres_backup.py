from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from fundos.storage.postgres import PostgresDatabase


Runner = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PostgresBackupVerification:
    valid: bool
    sha256: str
    size_bytes: int
    schema_version: int
    table_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class PostgresRestoreReport:
    valid: bool
    schema_version: int
    table_counts: dict[str, int]
    restored_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_postgres_backup(
    database_url: str,
    destination_directory: str | Path,
    *,
    label: str = "fundos-postgres",
    database: PostgresDatabase | None = None,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    target = Path(destination_directory).resolve()
    target.mkdir(parents=True, exist_ok=True)
    created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
    backup = target / f"{label}-{stamp}.dump"
    manifest = backup.with_suffix(".manifest.json")
    temporary = backup.with_suffix(".dump.partial")
    if temporary.exists():
        temporary.unlink()
    connection = database or PostgresDatabase(database_url)
    schema_version = connection.get_schema_version()
    table_counts = _table_counts(connection)
    command, environment, database_name = _postgres_command(
        "pg_dump",
        database_url,
        extra=(
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={temporary}",
        ),
    )
    try:
        version = runner(
            ["pg_dump", "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        runner(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("pg_dump did not create a non-empty backup")
        os.replace(temporary, backup)
    finally:
        if temporary.exists():
            temporary.unlink()
    payload = {
        "format_version": 1,
        "backend": "postgresql",
        "backup_file": backup.name,
        "created_at": created_at.isoformat(),
        "sha256": _file_sha256(backup),
        "size_bytes": backup.stat().st_size,
        "schema_version": schema_version,
        "table_counts": table_counts,
        "database_name": database_name,
        "pg_dump_version": version,
    }
    manifest.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return backup, manifest


def verify_postgres_backup(
    backup_path: str | Path,
    manifest_path: str | Path | None = None,
) -> PostgresBackupVerification:
    backup = Path(backup_path).resolve()
    manifest = (
        Path(manifest_path).resolve()
        if manifest_path
        else backup.with_suffix(".manifest.json")
    )
    if not backup.is_file() or not manifest.is_file():
        raise ValueError("PostgreSQL backup and manifest are required")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    digest = _file_sha256(backup)
    size = backup.stat().st_size
    if payload.get("backend") != "postgresql":
        raise ValueError("backup manifest is not PostgreSQL")
    if payload.get("backup_file") != backup.name:
        raise ValueError("backup filename does not match manifest")
    if payload.get("sha256") != digest or payload.get("size_bytes") != size:
        raise ValueError("backup checksum or size does not match manifest")
    return PostgresBackupVerification(
        True,
        digest,
        size,
        int(payload["schema_version"]),
        {str(key): int(value) for key, value in payload["table_counts"].items()},
    )


def restore_postgres_backup(
    backup_path: str | Path,
    target_database_url: str,
    *,
    manifest_path: str | Path | None = None,
    target_database: PostgresDatabase | None = None,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> PostgresRestoreReport:
    backup = Path(backup_path).resolve()
    verification = verify_postgres_backup(backup, manifest_path)
    target = target_database or PostgresDatabase(target_database_url)
    existing = target.list_tables()
    if existing:
        raise ValueError(
            "PostgreSQL restore target must be an empty database"
        )
    command, environment, _ = _postgres_command(
        "pg_restore",
        target_database_url,
        extra=(
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            str(backup),
        ),
    )
    runner(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    schema_version = target.get_schema_version()
    counts = _table_counts(target)
    if (
        schema_version != verification.schema_version
        or counts != verification.table_counts
    ):
        raise ValueError(
            "restored PostgreSQL schema or table counts do not match backup manifest"
        )
    restored_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return PostgresRestoreReport(
        True,
        schema_version,
        counts,
        restored_at.isoformat(),
    )


def _postgres_command(
    executable: str,
    database_url: str,
    *,
    extra: tuple[str, ...],
) -> tuple[list[str], dict[str, str], str]:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ValueError("database URL must use postgresql:// or postgres://")
    database_name = unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not parsed.username or not database_name:
        raise ValueError("PostgreSQL URL must include host, user, and database")
    command = [
        executable,
        f"--host={parsed.hostname}",
        f"--port={parsed.port or 5432}",
        f"--username={unquote(parsed.username)}",
        f"--dbname={database_name}",
        *extra,
    ]
    environment = dict(os.environ)
    if parsed.password is not None:
        environment["PGPASSWORD"] = unquote(parsed.password)
    sslmode = parse_qs(parsed.query).get("sslmode", [])
    if sslmode:
        environment["PGSSLMODE"] = sslmode[-1]
    return command, environment, database_name


def _table_counts(database: PostgresDatabase) -> dict[str, int]:
    return {
        table: int(
            database.fetch_all(
                f'SELECT COUNT(*) AS count FROM "{table}"'
            )[0]["count"]
        )
        for table in database.list_tables()
    }


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
