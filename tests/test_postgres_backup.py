import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.storage.postgres_backup import (  # noqa: E402
    create_postgres_backup,
    restore_postgres_backup,
    verify_postgres_backup,
)


DATABASE_URL = (
    "postgresql://fundos:highly-sensitive-value@db.internal:5433/fundos"
    "?sslmode=require"
)


class FakeDatabase:
    def __init__(self, *, empty=False):
        self.empty = empty
        self.restored = False

    def get_schema_version(self):
        return 14

    def list_tables(self):
        if self.empty and not self.restored:
            return []
        return ["assets"]

    def fetch_all(self, query):
        if 'FROM "assets"' in query:
            return [{"count": 2}]
        raise AssertionError(query)


class FakeRunner:
    def __init__(self, restore_target=None):
        self.calls = []
        self.restore_target = restore_target

    def __call__(self, command, **options):
        self.calls.append((command, options))
        if command[:2] == ["pg_dump", "--version"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="pg_dump (PostgreSQL) 16.4\n",
                stderr="",
            )
        if command[0] == "pg_dump":
            output = next(
                value.removeprefix("--file=")
                for value in command
                if value.startswith("--file=")
            )
            Path(output).write_bytes(b"PGDMP deterministic fixture")
        if command[0] == "pg_restore" and self.restore_target is not None:
            self.restore_target.restored = True
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class PostgresBackupTests(unittest.TestCase):
    def create_backup(self, directory, runner=None):
        return create_postgres_backup(
            DATABASE_URL,
            directory,
            database=FakeDatabase(),
            runner=runner or FakeRunner(),
            now=datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

    def test_creates_verifiable_backup_without_password_in_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            backup, manifest = self.create_backup(directory, runner)

            verification = verify_postgres_backup(backup, manifest)
            payload = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertTrue(verification.valid)
        self.assertEqual(verification.schema_version, 14)
        dump_command, options = runner.calls[1]
        self.assertNotIn("highly-sensitive-value", " ".join(dump_command))
        self.assertEqual(options["env"]["PGPASSWORD"], "highly-sensitive-value")
        self.assertEqual(options["env"]["PGSSLMODE"], "require")
        self.assertEqual(payload["backend"], "postgresql")
        self.assertNotIn("highly-sensitive-value", json.dumps(payload))

    def test_verification_rejects_tampered_dump(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup, manifest = self.create_backup(directory)
            backup.write_bytes(b"tampered")

            with self.assertRaisesRegex(ValueError, "checksum or size"):
                verify_postgres_backup(backup, manifest)

    def test_restore_refuses_nonempty_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup, manifest = self.create_backup(directory)

            with self.assertRaisesRegex(ValueError, "empty database"):
                restore_postgres_backup(
                    backup,
                    DATABASE_URL,
                    manifest_path=manifest,
                    target_database=FakeDatabase(empty=False),
                    runner=FakeRunner(),
                )

    def test_restores_and_verifies_empty_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup, manifest = self.create_backup(directory)
            target = FakeDatabase(empty=True)
            runner = FakeRunner(restore_target=target)

            report = restore_postgres_backup(
                backup,
                DATABASE_URL,
                manifest_path=manifest,
                target_database=target,
                runner=runner,
                now=datetime(2026, 7, 26, 1, tzinfo=timezone.utc),
            )

        self.assertTrue(report.valid)
        self.assertEqual(report.table_counts, {"assets": 2})
        restore_command = runner.calls[-1][0]
        self.assertEqual(restore_command[0], "pg_restore")
        self.assertNotIn("--clean", restore_command)


if __name__ == "__main__":
    unittest.main()
