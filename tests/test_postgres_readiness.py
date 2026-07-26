import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.storage.postgres import (  # noqa: E402
    PostgresConnectionAdapter,
    PostgresDatabase,
    check_postgres_readiness,
)


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, query):
        self.query = query

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.cursor_instance = FakeCursor(row)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def cursor(self):
        return self.cursor_instance


class PostgresReadinessTests(unittest.TestCase):
    def test_reports_ready_postgres_16_target(self) -> None:
        connection = FakeConnection(
            (160004, "fundos", "fundos", True, True, True, True, True)
        )

        report = check_postgres_readiness(
            "postgresql://fundos:secret@localhost/fundos",
            connector=lambda _: connection,
        )

        self.assertTrue(report.ready)
        self.assertEqual(report.database_name, "fundos")
        self.assertNotIn("secret", str(report.as_dict()))
        self.assertIn("server_version_num", connection.cursor_instance.query)

    def test_reports_insufficient_version_or_permissions(self) -> None:
        report = check_postgres_readiness(
            "postgres://localhost/fundos",
            connector=lambda _: FakeConnection(
                (150012, "fundos", "readonly", False, True, False, True, False)
            ),
        )

        self.assertFalse(report.ready)
        self.assertFalse(report.can_create_in_database)

    def test_rejects_non_postgres_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "postgresql"):
            check_postgres_readiness(
                "sqlite:///data/fundos.sqlite3",
                connector=lambda _: None,
            )

    def test_connection_adapter_translates_queries_and_transactions(self) -> None:
        class RawConnection:
            def __init__(self):
                self.calls = []
                self.committed = False
                self.closed = False

            def execute(self, query, parameters):
                self.calls.append((query, parameters))
                return self

            def fetchall(self):
                return [{"product_id": "P1"}]

            def commit(self):
                self.committed = True

            def rollback(self):
                raise AssertionError("rollback was not expected")

            def close(self):
                self.closed = True

        raw = RawConnection()
        database = PostgresDatabase(
            "postgresql://localhost/fundos",
            connector=lambda *_args, **_kwargs: raw,
        )

        rows = database.fetch_all(
            "SELECT * FROM portfolio_products WHERE product_id = ?",
            ("P1",),
        )

        self.assertEqual(rows, [{"product_id": "P1"}])
        self.assertEqual(
            raw.calls,
            [
                (
                    "SELECT * FROM portfolio_products WHERE product_id = %s",
                    ("P1",),
                )
            ],
        )
        self.assertTrue(raw.committed)
        self.assertTrue(raw.closed)

    def test_adapter_translates_batch_queries(self) -> None:
        class RawConnection:
            def executemany(self, query, parameters):
                self.call = (query, parameters)
                return "ok"

        raw = RawConnection()

        result = PostgresConnectionAdapter(raw).executemany(
            "INSERT INTO assets VALUES (?, ?)",
            [("A", "Asset")],
        )

        self.assertEqual(result, "ok")
        self.assertEqual(
            raw.call,
            ("INSERT INTO assets VALUES (%s, %s)", [("A", "Asset")]),
        )


if __name__ == "__main__":
    unittest.main()
