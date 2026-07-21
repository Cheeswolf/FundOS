import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.storage import Database  # noqa: E402


class MigrationTests(unittest.TestCase):
    def test_migrations_are_versioned_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "migration.sqlite3")
            database.initialize()
            self.assertEqual(database.get_schema_version(), 6)
            database.initialize()
            migrations = database.fetch_all("SELECT * FROM schema_migrations ORDER BY version")
            self.assertEqual([row["version"] for row in migrations], [1, 2, 3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()
