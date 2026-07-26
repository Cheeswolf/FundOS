import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.storage.dialects import (  # noqa: E402
    PostgresDialect,
    SQLiteDialect,
    qmark_to_postgres,
)
from fundos.storage.errors import INTEGRITY_ERRORS  # noqa: E402


class RecordingConnection:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, query, parameters=()):
        self.calls.append((query, parameters))


class DatabaseDialectTests(unittest.TestCase):
    def test_translates_qmark_parameters_and_preserves_quoted_text(self) -> None:
        query = """
            SELECT * FROM evidence
            WHERE source_id = ?
              AND title = 'Why?'
              AND identifier = "question?"
              AND note = 'investor''s ?'
        """

        translated = qmark_to_postgres(query)

        self.assertIn("source_id = %s", translated)
        self.assertIn("'Why?'", translated)
        self.assertIn('"question?"', translated)
        self.assertIn("'investor''s ?'", translated)

    def test_rejects_unterminated_sql_quote(self) -> None:
        with self.assertRaisesRegex(ValueError, "unterminated"):
            qmark_to_postgres("SELECT 'broken ?")

    def test_sqlite_uses_immediate_write_transaction(self) -> None:
        connection = RecordingConnection()

        SQLiteDialect().begin_idempotent_write(connection, "request-key")

        self.assertEqual(connection.calls, [("BEGIN IMMEDIATE", ())])

    def test_postgres_uses_keyed_transaction_advisory_lock(self) -> None:
        connection = RecordingConnection()

        PostgresDialect().begin_idempotent_write(connection, "request-key")

        self.assertEqual(
            connection.calls,
            [
                (
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("request-key",),
                )
            ],
        )

    def test_timestamp_expressions_are_backend_specific(self) -> None:
        self.assertEqual(
            SQLiteDialect().timestamp_expression("created_at"),
            "datetime(created_at)",
        )
        self.assertEqual(
            PostgresDialect().timestamp_expression("created_at"),
            "CAST(created_at AS TIMESTAMPTZ)",
        )

    def test_sqlite_integrity_error_remains_supported(self) -> None:
        self.assertTrue(issubclass(sqlite3.IntegrityError, INTEGRITY_ERRORS))


if __name__ == "__main__":
    unittest.main()
