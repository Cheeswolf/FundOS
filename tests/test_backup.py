import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundos.domain import PortfolioProduct  # noqa: E402
from fundos.storage import (  # noqa: E402
    Database, create_database_backup, restore_database_backup, verify_database_backup,
)


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "source.sqlite3"
        database = Database(self.source_path)
        database.initialize()
        database.create_product(PortfolioProduct("P1", "Backup Portfolio", "BM", datetime.now()))

    def test_creates_verifies_and_restores_backup(self) -> None:
        backup, manifest = create_database_backup(self.source_path, self.root / "backups")
        verification = verify_database_backup(backup, manifest)
        self.assertTrue(verification.valid)
        self.assertEqual(verification.schema_version, 14)
        self.assertEqual(verification.table_counts["portfolio_products"], 1)

        restored = self.root / "restored.sqlite3"
        self.assertIsNone(restore_database_backup(backup, restored, manifest_path=manifest))
        products = Database(restored).fetch_all("SELECT * FROM portfolio_products")
        self.assertEqual(products[0]["name"], "Backup Portfolio")

    def test_rejects_checksum_mismatch(self) -> None:
        backup, manifest = create_database_backup(self.source_path, self.root / "backups")
        with backup.open("ab") as target:
            target.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum or size"):
            verify_database_backup(backup, manifest)

    def test_requires_explicit_replace_and_preserves_existing_database(self) -> None:
        backup, manifest = create_database_backup(self.source_path, self.root / "backups")
        target = self.root / "target.sqlite3"
        existing = Database(target)
        existing.initialize()
        existing.create_product(PortfolioProduct("OLD", "Existing", "BM", datetime.now()))
        with self.assertRaises(FileExistsError):
            restore_database_backup(backup, target, manifest_path=manifest)

        preserved = restore_database_backup(
            backup, target, manifest_path=manifest, replace_existing=True,
        )
        self.assertIsNotNone(preserved)
        self.assertTrue(preserved.is_file())
        self.assertEqual(
            Database(preserved).fetch_all("SELECT name FROM portfolio_products")[0]["name"],
            "Existing",
        )
        self.assertEqual(
            Database(target).fetch_all("SELECT name FROM portfolio_products")[0]["name"],
            "Backup Portfolio",
        )
