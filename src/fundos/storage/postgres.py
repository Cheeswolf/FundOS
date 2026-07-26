from dataclasses import asdict, dataclass
from typing import Any, Callable


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
