from pathlib import Path
from urllib.parse import unquote

from fundos.storage.database import Database
from fundos.storage.postgres import PostgresDatabase


def database_from_url(database_url: str) -> Database:
    normalized = database_url.strip()
    if normalized.startswith(("postgresql://", "postgres://")):
        return PostgresDatabase(normalized)
    if normalized.startswith("sqlite:///"):
        value = unquote(normalized.removeprefix("sqlite:///"))
        if not value:
            raise ValueError("SQLite database URL must include a path")
        return Database(Path(value))
    raise ValueError(
        "unsupported database URL; use sqlite:///path or postgresql://..."
    )


def redact_database_url(database_url: str) -> str:
    if "://" not in database_url:
        return database_url
    scheme, remainder = database_url.split("://", 1)
    if "@" not in remainder:
        return database_url
    credentials, location = remainder.rsplit("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{location}"
