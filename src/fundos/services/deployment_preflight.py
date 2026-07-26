from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping

from fundos.services.audit_log import verify_audit_chain
from fundos.storage import Database, verify_database_backup
from fundos.storage.postgres_backup import verify_postgres_backup


@dataclass(frozen=True, slots=True)
class DeploymentCheck:
    code: str
    passed: bool
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class DeploymentPreflightReport:
    ready: bool
    mode: str
    checked_at: str
    checks: tuple[DeploymentCheck, ...]

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "mode": self.mode,
            "checked_at": self.checked_at,
            "blocking_failures": sum(
                not check.passed and check.severity == "blocking"
                for check in self.checks
            ),
            "warnings": sum(
                not check.passed and check.severity == "warning"
                for check in self.checks
            ),
            "checks": [asdict(check) for check in self.checks],
        }


def run_deployment_preflight(
    database: Database,
    *,
    environment: Mapping[str, str],
    mode: str,
    backup_manifest: Path | None,
    maximum_backup_age_hours: float = 24,
    now: datetime | None = None,
) -> DeploymentPreflightReport:
    if mode not in {"production", "cutover"}:
        raise ValueError("deployment mode must be production or cutover")
    if maximum_backup_age_hours <= 0:
        raise ValueError("maximum backup age must be positive")
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    checks: list[DeploymentCheck] = []

    try:
        schema_version = database.get_schema_version()
    except Exception as error:
        checks.append(_check("database", False, "blocking", f"database unavailable: {error}"))
    else:
        checks.append(_check(
            "schema_version",
            schema_version == 14,
            "blocking",
            f"schema version is {schema_version}; expected 14",
        ))
        audit = verify_audit_chain(database)
        checks.append(_check(
            "audit_chain",
            bool(audit["valid"]),
            "blocking",
            f"audit chain checked {audit['checked_events']} events",
        ))
        products = _count(database, "portfolio_products")
        versions = _count(database, "portfolio_versions", "status = 'published'")
        nav_rows = _count(database, "portfolio_nav")
        checks.extend([
            _check("products", products > 0, "blocking", f"{products} products"),
            _check(
                "published_versions",
                versions > 0,
                "blocking",
                f"{versions} published versions",
            ),
            _check("portfolio_nav", nav_rows > 0, "blocking", f"{nav_rows} NAV rows"),
        ])

    keys = _configured_api_keys(environment)
    strong_keys = bool(keys) and all(_strong_secret(key) for key in keys)
    checks.append(_check(
        "api_keys",
        strong_keys,
        "blocking",
        (
            f"{len(keys)} non-empty API keys meet minimum strength"
            if strong_keys
            else "API keys are missing, weak, or still contain placeholder text"
        ),
    ))

    read_only = environment.get("FUNDOS_READ_ONLY", "").strip().lower() in {"1", "true"}
    expected_read_only = mode == "cutover"
    checks.append(_check(
        "read_only_mode",
        read_only == expected_read_only,
        "blocking",
        (
            "read-only state matches deployment mode"
            if read_only == expected_read_only
            else f"{mode} mode requires FUNDOS_READ_ONLY={str(expected_read_only).lower()}"
        ),
    ))
    checks.append(_backup_check(
        backup_manifest,
        checked_at,
        maximum_backup_age_hours,
        expected_backend=(
            "sqlite" if mode == "cutover" else database.dialect.name
        ),
    ))
    checks.extend([
        _check(
            "alert_webhook",
            bool(environment.get("FUNDOS_ALERT_WEBHOOK_URL", "").strip()),
            "warning",
            (
                "alert webhook is configured"
                if environment.get("FUNDOS_ALERT_WEBHOOK_URL", "").strip()
                else "alert webhook is not configured"
            ),
        ),
        _check(
            "opentelemetry",
            bool(environment.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()),
            "warning",
            (
                "OpenTelemetry exporter is configured"
                if environment.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
                else "OpenTelemetry exporter is not configured"
            ),
        ),
    ])
    ready = not any(
        not check.passed and check.severity == "blocking"
        for check in checks
    )
    return DeploymentPreflightReport(
        ready,
        mode,
        checked_at.isoformat(),
        tuple(checks),
    )


def _backup_check(
    manifest_path: Path | None,
    checked_at: datetime,
    maximum_age_hours: float,
    *,
    expected_backend: str,
) -> DeploymentCheck:
    if manifest_path is None or not manifest_path.is_file():
        return _check("backup", False, "blocking", "verified backup manifest is required")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        backend = str(manifest.get("backend", "sqlite"))
        if backend != expected_backend:
            raise ValueError(
                f"backup backend is {backend}; expected {expected_backend}"
            )
        backup = manifest_path.parent / str(manifest["backup_file"])
        verification = (
            verify_postgres_backup(backup, manifest_path)
            if backend == "postgresql"
            else verify_database_backup(backup, manifest_path)
        )
        created_at = datetime.fromisoformat(str(manifest["created_at"]))
        if created_at.tzinfo is None:
            raise ValueError("backup created_at must include timezone")
        age_hours = (
            checked_at - created_at.astimezone(timezone.utc)
        ).total_seconds() / 3600
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        return _check("backup", False, "blocking", f"backup verification failed: {error}")
    valid = verification.valid and 0 <= age_hours <= maximum_age_hours
    return _check(
        "backup",
        valid,
        "blocking",
        f"verified backup age is {age_hours:.2f} hours",
    )


def _configured_api_keys(environment: Mapping[str, str]) -> tuple[str, ...]:
    keys: list[str] = []
    legacy = environment.get("FUNDOS_API_KEY", "").strip()
    if legacy:
        keys.append(legacy)
    serialized = environment.get("FUNDOS_API_KEYS_JSON", "").strip()
    if serialized:
        try:
            parsed = json.loads(serialized)
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed, dict):
            return ()
        keys.extend(str(key).strip() for key in parsed)
    return tuple(key for key in keys if key)


def _strong_secret(secret: str) -> bool:
    lowered = secret.lower()
    placeholders = ("replace", "change-me", "example", "password", "secret")
    return len(secret) >= 24 and not any(value in lowered for value in placeholders)


def _count(database: Database, table: str, condition: str | None = None) -> int:
    query = f'SELECT COUNT(*) AS count FROM "{table}"'
    if condition:
        query += f" WHERE {condition}"
    return int(database.fetch_all(query)[0]["count"])


def _check(code: str, passed: bool, severity: str, message: str) -> DeploymentCheck:
    return DeploymentCheck(code, passed, severity, message)
