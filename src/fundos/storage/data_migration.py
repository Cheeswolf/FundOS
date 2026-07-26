from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Any, Iterable

from fundos.storage import Database, PostgresDatabase


EXCLUDED_TABLES = {"schema_migrations", "sqlite_sequence"}


@dataclass(frozen=True, slots=True)
class MigratedTable:
    table_name: str
    row_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DatabaseMigrationReport:
    source_schema_version: int
    target_schema_version: int
    tables: tuple[MigratedTable, ...]

    @property
    def total_rows(self) -> int:
        return sum(table.row_count for table in self.tables)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_schema_version": self.source_schema_version,
            "target_schema_version": self.target_schema_version,
            "total_rows": self.total_rows,
            "tables": [asdict(table) for table in self.tables],
        }


def dependency_order(dependencies: dict[str, set[str]]) -> tuple[str, ...]:
    remaining = {table: set(values) & dependencies.keys() for table, values in dependencies.items()}
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            table for table, values in remaining.items()
            if values.issubset(ordered)
        )
        if not ready:
            raise ValueError(
                "database contains cyclic or unresolved foreign-key dependencies"
            )
        for table in ready:
            ordered.append(table)
            del remaining[table]
    return tuple(ordered)


def rows_digest(rows: Iterable[dict[str, Any]]) -> str:
    canonical = sorted(
        json.dumps(
            row,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_default,
        )
        for row in rows
    )
    return sha256("\n".join(canonical).encode("utf-8")).hexdigest()


def migrate_sqlite_to_postgres(
    source: Database,
    target: PostgresDatabase,
) -> DatabaseMigrationReport:
    source_version = source.get_schema_version()
    target_version = target.get_schema_version()
    if source_version != target_version:
        raise ValueError(
            f"schema versions differ: SQLite={source_version}, PostgreSQL={target_version}"
        )

    with source.connect() as source_connection:
        source_connection.execute("BEGIN IMMEDIATE")
        tables = _sqlite_tables(source_connection)
        dependencies = {
            table: _sqlite_dependencies(source_connection, table) & set(tables)
            for table in tables
        }
        ordered_tables = dependency_order(dependencies)
        source_rows = {
            table: [
                dict(row)
                for row in source_connection.execute(
                    f'SELECT * FROM "{table}"'
                ).fetchall()
            ]
            for table in ordered_tables
        }

        with target.connect() as target_connection:
            for table in ordered_tables:
                count = target_connection.execute(
                    f'SELECT COUNT(*) AS count FROM "{table}"'
                ).fetchone()["count"]
                if count:
                    raise ValueError(
                        f"PostgreSQL target table is not empty: {table}"
                    )

            migrated: list[MigratedTable] = []
            for table in ordered_tables:
                columns = _sqlite_columns(source_connection, table)
                rows = source_rows[table]
                if rows:
                    names = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    values = [
                        tuple(row[column] for column in columns)
                        for row in rows
                    ]
                    target_connection.executemany(
                        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                        values,
                    )
                _reset_identity(target_connection, table)
                target_rows = [
                    dict(row)
                    for row in target_connection.execute(
                        f'SELECT * FROM "{table}"'
                    ).fetchall()
                ]
                source_hash = rows_digest(rows)
                target_hash = rows_digest(target_rows)
                if len(target_rows) != len(rows) or target_hash != source_hash:
                    raise ValueError(
                        f"migration verification failed for table {table}"
                    )
                migrated.append(MigratedTable(table, len(rows), source_hash))

    return DatabaseMigrationReport(
        source_version,
        target_version,
        tuple(migrated),
    )


def _sqlite_tables(connection: Any) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' ORDER BY name
        """
    ).fetchall()
    return tuple(
        str(row["name"])
        for row in rows
        if row["name"] not in EXCLUDED_TABLES
    )


def _sqlite_columns(connection: Any, table: str) -> tuple[str, ...]:
    _validate_identifier(table)
    return tuple(
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    )


def _sqlite_dependencies(connection: Any, table: str) -> set[str]:
    _validate_identifier(table)
    return {
        str(row["table"])
        for row in connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    }


def _reset_identity(connection: Any, table: str) -> None:
    identity_rows = connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ?
          AND is_identity = 'YES'
        """,
        (table,),
    ).fetchall()
    for row in identity_rows:
        column = str(row["column_name"])
        _validate_identifier(column)
        connection.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence(?, ?),
                COALESCE(MAX("{column}"), 1),
                COUNT(*) > 0
            )
            FROM "{table}"
            """,
            (table, column),
        )


def _validate_identifier(value: str) -> None:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError(f"unsafe database identifier: {value!r}")


def _json_default(value: Any) -> str:
    if isinstance(value, bytes):
        return value.hex()
    return str(value)
