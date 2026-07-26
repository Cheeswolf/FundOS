from dataclasses import dataclass
from typing import Any, Protocol


class ExecutableConnection(Protocol):
    def execute(
        self,
        query: str,
        parameters: tuple[Any, ...] = (),
    ) -> Any: ...


def qmark_to_postgres(query: str) -> str:
    """Translate DB-API qmark placeholders without changing quoted question marks."""
    translated: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(query):
        character = query[index]
        if quote is not None:
            translated.append(character)
            if character == quote:
                if index + 1 < len(query) and query[index + 1] == quote:
                    translated.append(query[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in ("'", '"'):
            quote = character
            translated.append(character)
        elif character == "?":
            translated.append("%s")
        else:
            translated.append(character)
        index += 1
    if quote is not None:
        raise ValueError("SQL contains an unterminated quoted string")
    return "".join(translated)


@dataclass(frozen=True, slots=True)
class SQLiteDialect:
    name: str = "sqlite"

    def prepare(self, query: str) -> str:
        return query

    def timestamp_expression(self, expression: str) -> str:
        return f"datetime({expression})"

    def begin_idempotent_write(
        self,
        connection: ExecutableConnection,
        key: str,
    ) -> None:
        del key
        connection.execute("BEGIN IMMEDIATE")


@dataclass(frozen=True, slots=True)
class PostgresDialect:
    name: str = "postgresql"

    def prepare(self, query: str) -> str:
        return qmark_to_postgres(query)

    def timestamp_expression(self, expression: str) -> str:
        return f"CAST({expression} AS TIMESTAMPTZ)"

    def begin_idempotent_write(
        self,
        connection: ExecutableConnection,
        key: str,
    ) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (key,),
        )
