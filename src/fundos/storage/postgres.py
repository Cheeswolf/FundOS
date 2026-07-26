from dataclasses import asdict, dataclass
from contextlib import contextmanager
from typing import Any, Callable

from fundos.storage.database import Database
from fundos.storage.dialects import PostgresDialect


@dataclass(frozen=True, slots=True)
class PostgresReadinessReport:
    server_version: int
    database_name: str
    user_name: str
    ssl_enabled: bool
    can_connect: bool
    can_create_in_database: bool
    can_use_public_schema: bool
    can_create_in_public_schema: bool

    @property
    def ready(self) -> bool:
        return (
            self.server_version >= 160000
            and self.can_connect
            and self.can_create_in_database
            and self.can_use_public_schema
            and self.can_create_in_public_schema
        )

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ready": self.ready}


def check_postgres_readiness(
    database_url: str,
    *,
    connector: Callable[[str], Any] | None = None,
) -> PostgresReadinessReport:
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("database URL must use postgresql:// or postgres://")
    if connector is None:
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                'PostgreSQL checks require: python -m pip install -e ".[postgres]"'
            ) from error
        connector = psycopg.connect

    with connector(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    current_setting('server_version_num')::integer,
                    current_database(),
                    current_user,
                    COALESCE((
                        SELECT ssl
                        FROM pg_stat_ssl
                        WHERE pid = pg_backend_pid()
                    ), false),
                    has_database_privilege(current_user, current_database(), 'CONNECT'),
                    has_database_privilege(current_user, current_database(), 'CREATE'),
                    has_schema_privilege(current_user, 'public', 'USAGE'),
                    has_schema_privilege(current_user, 'public', 'CREATE')
                """
            )
            row = cursor.fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL readiness query returned no result")
    return PostgresReadinessReport(
        server_version=int(row[0]),
        database_name=str(row[1]),
        user_name=str(row[2]),
        ssl_enabled=bool(row[3]),
        can_connect=bool(row[4]),
        can_create_in_database=bool(row[5]),
        can_use_public_schema=bool(row[6]),
        can_create_in_public_schema=bool(row[7]),
    )


class PostgresConnectionAdapter:
    """Expose the subset of DB-API used by FundOS with qmark SQL compatibility."""

    def __init__(self, connection: Any) -> None:
        self.raw_connection = connection
        self.dialect = PostgresDialect()

    def execute(
        self,
        query: str,
        parameters: tuple[Any, ...] = (),
    ) -> Any:
        return self.raw_connection.execute(
            self.dialect.prepare(query),
            parameters,
        )

    def executemany(
        self,
        query: str,
        parameters: list[tuple[Any, ...]],
    ) -> Any:
        with self.raw_connection.cursor() as cursor:
            cursor.executemany(
                self.dialect.prepare(query),
                parameters,
            )
        return None


class PostgresDatabase(Database):
    """PostgreSQL connection adapter; schema initialization is added next."""

    def __init__(
        self,
        database_url: str,
        *,
        connector: Callable[..., Any] | None = None,
    ) -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("database URL must use postgresql:// or postgres://")
        self.path = database_url
        self.dialect = PostgresDialect()
        self._connector = connector

    @contextmanager
    def connect(self):
        connector = self._connector
        connect_options: dict[str, Any] = {}
        if connector is None:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:
                raise RuntimeError(
                    'PostgreSQL support requires: python -m pip install -e ".[postgres]"'
                ) from error
            connector = psycopg.connect
            connect_options["row_factory"] = dict_row
        raw_connection = connector(self.path, **connect_options)
        connection = PostgresConnectionAdapter(raw_connection)
        try:
            yield connection
            raw_connection.commit()
        except Exception:
            raw_connection.rollback()
            raise
        finally:
            raw_connection.close()

    def initialize(self) -> None:
        from fundos.storage.database import SCHEMA
        from fundos.storage.postgres_migrations import apply_postgres_migrations

        with self.connect() as connection:
            apply_postgres_migrations(connection, SCHEMA)

    def begin_idempotent_write(
        self,
        connection: PostgresConnectionAdapter,
        key: str,
    ) -> None:
        self.dialect.begin_idempotent_write(connection, key)

    def table_columns(self, table_name: str) -> list[str]:
        rows = self.fetch_all(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return [str(row["column_name"]) for row in rows]
