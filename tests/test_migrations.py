import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.storage import Database  # noqa: E402


class MigrationTests(unittest.TestCase):
    def test_migrations_are_versioned_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "migration.sqlite3")
            database.initialize()
            self.assertEqual(database.get_schema_version(), 10)
            database.initialize()
            migrations = database.fetch_all("SELECT * FROM schema_migrations ORDER BY version")
            self.assertEqual(
                [row["version"] for row in migrations],
                [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            )

    def test_upgrades_model_call_table_created_before_cost_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO schema_migrations (version, name) VALUES
                        (1, 'legacy'), (2, 'legacy'), (3, 'legacy'),
                        (4, 'legacy'), (5, 'legacy'), (6, 'legacy');
                    CREATE TABLE model_calls (
                        call_id TEXT PRIMARY KEY,
                        purpose TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL,
                        latency_ms INTEGER NOT NULL,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        prompt_sha256 TEXT NOT NULL,
                        error_message TEXT,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                connection.commit()
            database = Database(path)
            database.initialize()
            columns = {
                row["name"] for row in database.fetch_all("PRAGMA table_info(model_calls)")
            }
            self.assertIn("estimated_cost_usd", columns)
            self.assertEqual(database.get_schema_version(), 10)


if __name__ == "__main__":
    unittest.main()
