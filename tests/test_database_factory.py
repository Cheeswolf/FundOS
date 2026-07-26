import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.storage import Database, PostgresDatabase  # noqa: E402
from fundos.storage.factory import database_from_url, redact_database_url  # noqa: E402
from fundos.api import create_app  # noqa: E402


class DatabaseFactoryTests(unittest.TestCase):
    def test_creates_sqlite_database_from_url(self) -> None:
        database = database_from_url("sqlite:///data/test%20database.sqlite3")

        self.assertIsInstance(database, Database)
        self.assertEqual(database.path, str(Path("data/test database.sqlite3")))

    def test_creates_postgres_database_without_exposing_credentials(self) -> None:
        url = "postgresql://fundos:very-secret@db.internal:5432/fundos"

        database = database_from_url(url)

        self.assertIsInstance(database, PostgresDatabase)
        self.assertEqual(
            redact_database_url(url),
            "postgresql://fundos:***@db.internal:5432/fundos",
        )
        self.assertNotIn("very-secret", redact_database_url(url))

    def test_rejects_unsupported_or_empty_database_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            database_from_url("mysql://localhost/fundos")
        with self.assertRaisesRegex(ValueError, "include a path"):
            database_from_url("sqlite:///")

    def test_api_accepts_explicit_sqlite_database_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "url-config.sqlite3"

            app = create_app(
                database_url=f"sqlite:///{path.as_posix()}",
                api_key="test-key",
            )

            self.assertEqual(app.state.database.path, str(path))
            self.assertEqual(app.state.database.get_schema_version(), 15)

    def test_api_rejects_conflicting_path_and_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot both"):
            create_app(
                Path("data/path.sqlite3"),
                database_url="sqlite:///data/url.sqlite3",
            )


if __name__ == "__main__":
    unittest.main()
