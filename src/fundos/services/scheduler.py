from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Generic, TypeVar
from uuid import uuid4

from fundos.storage import Database


ResultT = TypeVar("ResultT")


@dataclass(frozen=True, slots=True)
class ScheduledJobResult(Generic[ResultT]):
    run_id: str
    job_name: str
    status: str
    value: ResultT | None = None
    message: str = ""


def run_scheduled_job(
    database: Database,
    *,
    job_name: str,
    task: Callable[[], ResultT],
    lease_seconds: int = 3600,
    now: Callable[[], datetime] | None = None,
    is_successful: Callable[[ResultT], bool] | None = None,
) -> ScheduledJobResult[ResultT]:
    if not job_name.strip():
        raise ValueError("scheduled job name is required")
    if lease_seconds < 1:
        raise ValueError("scheduled job lease must be positive")
    clock = now or (lambda: datetime.now(timezone.utc))
    started_at = _utc(clock())
    lease_until = started_at + timedelta(seconds=lease_seconds)
    owner_id = str(uuid4())
    run_id = str(uuid4())

    acquired = _acquire_lease(
        database,
        job_name=job_name,
        owner_id=owner_id,
        run_id=run_id,
        started_at=started_at,
        lease_until=lease_until,
    )
    if not acquired:
        message = "another instance holds an active lease"
        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_job_runs (
                    run_id, job_name, owner_id, status, started_at,
                    completed_at, lease_until, message
                ) VALUES (?, ?, ?, 'skipped', ?, ?, ?, ?)
                """,
                (
                    run_id, job_name, owner_id, started_at.isoformat(),
                    started_at.isoformat(), lease_until.isoformat(), message,
                ),
            )
        return ScheduledJobResult(run_id, job_name, "skipped", message=message)

    try:
        value = task()
    except Exception as error:
        _finish_run(
            database,
            run_id=run_id,
            job_name=job_name,
            owner_id=owner_id,
            status="failed",
            completed_at=_utc(clock()),
            message=str(error)[:2000],
        )
        raise
    succeeded = is_successful(value) if is_successful is not None else True
    status = "succeeded" if succeeded else "failed"
    message = "" if succeeded else "task completed with an unsuccessful result"
    _finish_run(
        database,
        run_id=run_id,
        job_name=job_name,
        owner_id=owner_id,
        status=status,
        completed_at=_utc(clock()),
        message=message or None,
    )
    return ScheduledJobResult(run_id, job_name, status, value=value, message=message)


def _acquire_lease(
    database: Database,
    *,
    job_name: str,
    owner_id: str,
    run_id: str,
    started_at: datetime,
    lease_until: datetime,
) -> bool:
    with database.connect() as connection:
        database.begin_idempotent_write(
            connection,
            f"scheduled-job:{job_name}",
        )
        existing = connection.execute(
            "SELECT * FROM scheduled_job_locks WHERE job_name = ?",
            (job_name,),
        ).fetchone()
        if existing is not None and existing["lease_until"] > started_at.isoformat():
            return False
        if existing is not None:
            connection.execute(
                """
                UPDATE scheduled_job_runs
                SET status = 'abandoned', completed_at = ?,
                    message = 'lease expired before completion'
                WHERE job_name = ? AND owner_id = ? AND status = 'running'
                """,
                (started_at.isoformat(), job_name, existing["owner_id"]),
            )
        connection.execute(
            """
            INSERT INTO scheduled_job_locks (job_name, owner_id, acquired_at, lease_until)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_name) DO UPDATE SET
                owner_id = excluded.owner_id,
                acquired_at = excluded.acquired_at,
                lease_until = excluded.lease_until
            """,
            (job_name, owner_id, started_at.isoformat(), lease_until.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO scheduled_job_runs (
                run_id, job_name, owner_id, status, started_at, lease_until
            ) VALUES (?, ?, ?, 'running', ?, ?)
            """,
            (run_id, job_name, owner_id, started_at.isoformat(), lease_until.isoformat()),
        )
    return True


def _finish_run(
    database: Database,
    *,
    run_id: str,
    job_name: str,
    owner_id: str,
    status: str,
    completed_at: datetime,
    message: str | None = None,
) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE scheduled_job_runs
            SET status = ?, completed_at = ?, message = ?
            WHERE run_id = ? AND owner_id = ? AND status = 'running'
            """,
            (status, completed_at.isoformat(), message, run_id, owner_id),
        )
        connection.execute(
            "DELETE FROM scheduled_job_locks WHERE job_name = ? AND owner_id = ?",
            (job_name, owner_id),
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("scheduler clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)
