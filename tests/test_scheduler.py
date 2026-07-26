import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.services import run_scheduled_job  # noqa: E402
from fundos.storage import Database  # noqa: E402


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.directory.name) / "scheduler.sqlite3")
        self.database.initialize()
        self.started_at = datetime(2026, 7, 26, 1, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_records_success_and_releases_lease(self) -> None:
        result = run_scheduled_job(
            self.database,
            job_name="daily-production",
            task=lambda: 42,
            now=lambda: self.started_at,
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.value, 42)
        self.assertEqual(self.database.fetch_all("SELECT * FROM scheduled_job_locks"), [])
        runs = self.database.fetch_all("SELECT * FROM scheduled_job_runs")
        self.assertEqual(runs[0]["status"], "succeeded")

    def test_skips_when_another_owner_has_active_lease(self) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_job_locks
                    (job_name, owner_id, acquired_at, lease_until)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "daily-production", "other", self.started_at.isoformat(),
                    (self.started_at + timedelta(minutes=10)).isoformat(),
                ),
            )
        called = False

        def task() -> None:
            nonlocal called
            called = True

        result = run_scheduled_job(
            self.database,
            job_name="daily-production",
            task=task,
            now=lambda: self.started_at,
        )
        self.assertEqual(result.status, "skipped")
        self.assertFalse(called)

    def test_expired_lease_is_recovered_and_prior_run_is_abandoned(self) -> None:
        expired = self.started_at - timedelta(seconds=1)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_job_locks
                    (job_name, owner_id, acquired_at, lease_until)
                VALUES ('daily-production', 'old-owner', ?, ?)
                """,
                ((expired - timedelta(hours=1)).isoformat(), expired.isoformat()),
            )
            connection.execute(
                """
                INSERT INTO scheduled_job_runs (
                    run_id, job_name, owner_id, status, started_at, lease_until
                ) VALUES ('old-run', 'daily-production', 'old-owner', 'running', ?, ?)
                """,
                ((expired - timedelta(hours=1)).isoformat(), expired.isoformat()),
            )
        result = run_scheduled_job(
            self.database,
            job_name="daily-production",
            task=lambda: "recovered",
            now=lambda: self.started_at,
        )
        self.assertEqual(result.status, "succeeded")
        old = self.database.fetch_all(
            "SELECT * FROM scheduled_job_runs WHERE run_id = 'old-run'"
        )[0]
        self.assertEqual(old["status"], "abandoned")

    def test_failure_is_recorded_and_lease_is_released(self) -> None:
        def fail() -> None:
            raise RuntimeError("pipeline failed")

        with self.assertRaisesRegex(RuntimeError, "pipeline failed"):
            run_scheduled_job(
                self.database,
                job_name="daily-production",
                task=fail,
                now=lambda: self.started_at,
            )
        run = self.database.fetch_all("SELECT * FROM scheduled_job_runs")[0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["message"], "pipeline failed")
        self.assertEqual(self.database.fetch_all("SELECT * FROM scheduled_job_locks"), [])

    def test_unsuccessful_result_is_recorded_without_losing_value(self) -> None:
        result = run_scheduled_job(
            self.database,
            job_name="daily-production",
            task=lambda: {"status": "partial"},
            is_successful=lambda value: value["status"] == "succeeded",
            now=lambda: self.started_at,
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.value, {"status": "partial"})
        run = self.database.fetch_all("SELECT * FROM scheduled_job_runs")[0]
        self.assertEqual(run["status"], "failed")


if __name__ == "__main__":
    unittest.main()
