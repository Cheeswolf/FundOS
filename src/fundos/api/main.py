import os
import csv
import io
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from fundos.api.schemas import (
    AssetInput,
    ApprovedResearchRequestInput,
    ApiErrorResponse,
    AlertLifecycleInput,
    CircuitResetInput,
    CommitteeDecisionInput,
    CommitteeOpinionInput,
    EvidenceReviewInput,
    HealthResponse,
    PriceBatchInput,
    ProductCreate,
    ProposalCreate,
    ResearchCreate,
    RiskReviewInput,
    ScheduledJobLockResponse,
    ScheduledJobRunResponse,
)
from fundos.domain import (
    Asset,
    AssetView,
    InvestmentMandate,
    PortfolioProduct,
    PortfolioVersion,
    PositionWeight,
    ResearchEvidence,
    ResearchReport,
)
from fundos.core import MetricsRegistry, configure_opentelemetry, trace_request
from fundos.services import (
    build_approved_research_request,
    create_proposal,
    create_research_report,
    finalize_research_report,
    get_model_circuit_status,
    publish_approved_workflow,
    purge_audit_events,
    record_audit_event,
    record_committee_decision,
    record_committee_opinion,
    reset_model_circuit,
    run_risk_review,
    update_alert_lifecycle,
    review_raw_research_evidence,
    verify_audit_chain,
)
from fundos.storage import Database


PAGINATED_RESPONSE_DOCS = {
    200: {
        "description": "Successful array response with compatible pagination headers.",
        "headers": {
            "X-Total-Count": {
                "description": "Total number of matching records.",
                "schema": {"type": "integer"},
            },
            "X-Limit": {
                "description": "Maximum records returned.",
                "schema": {"type": "integer"},
            },
            "X-Offset": {
                "description": "Number of matching records skipped.",
                "schema": {"type": "integer"},
            },
        },
    }
}


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _committee_opinions(database: Database, run_id: str) -> list[dict[str, Any]]:
    opinions = _rows(database.fetch_all(
        """
        SELECT opinion_id, run_id, member_role, recommendation, rationale,
               alternative_weights, conditions, submitted_by, created_at
        FROM committee_opinions WHERE run_id = ? ORDER BY created_at, member_role
        """,
        (run_id,),
    ))
    for opinion in opinions:
        opinion["alternative_weights"] = json.loads(opinion["alternative_weights"])
    return opinions


def get_database(request: Request) -> Database:
    return request.app.state.database


def _domain_error(error: Exception) -> HTTPException:
    if isinstance(error, sqlite3.IntegrityError):
        return HTTPException(
            status_code=409,
            detail=str(error),
            headers={"X-FundOS-Error-Code": "RESOURCE_CONFLICT"},
        )
    return HTTPException(
        status_code=422,
        detail=str(error),
        headers={"X-FundOS-Error-Code": "DOMAIN_RULE_VIOLATION"},
    )


def _pagination_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    offset: int,
) -> None:
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Offset"] = str(offset)


def _idempotent(
    database: Database,
    *,
    key: str | None,
    operation: str,
    payload: Any,
    action,
) -> dict[str, Any]:
    if not key:
        return action()
    request_hash = sha256(
        json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    existing = database.fetch_all(
        "SELECT * FROM idempotency_records WHERE idempotency_key = ?", (key,)
    )
    if existing:
        if existing[0]["operation"] != operation or existing[0]["request_hash"] != request_hash:
            raise HTTPException(status_code=409, detail="idempotency key was already used for another request")
        return json.loads(existing[0]["response_json"])
    result = action()
    try:
        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_records
                    (idempotency_key, operation, request_hash, response_json)
                VALUES (?, ?, ?, ?)
                """,
                (key, operation, request_hash, json.dumps(result, ensure_ascii=False)),
            )
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="concurrent idempotent request conflict") from error
    return result


def create_app(
    database_path: str | Path | None = None,
    *,
    api_key: str | None = None,
    api_keys: dict[str, str] | None = None,
) -> FastAPI:
    resolved_path = Path(database_path or os.environ.get("FUNDOS_DB_PATH", "data/fundos.sqlite3"))
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(resolved_path)
    database.initialize()

    app = FastAPI(
        title="FundOS API",
        version="0.1.0",
        description="Investment research and portfolio operating system API",
        responses={
            status: {"model": ApiErrorResponse}
            for status in (401, 403, 404, 409, 422, 500)
        },
    )
    app.state.database = database
    app.state.metrics = MetricsRegistry()
    app.state.opentelemetry_enabled = configure_opentelemetry()
    configured_keys = dict(api_keys or {})
    if api_keys is None:
        serialized_keys = os.environ.get("FUNDOS_API_KEYS_JSON", "").strip()
        if serialized_keys:
            try:
                parsed_keys = json.loads(serialized_keys)
            except json.JSONDecodeError as error:
                raise ValueError("FUNDOS_API_KEYS_JSON must be valid JSON") from error
            if not isinstance(parsed_keys, dict):
                raise ValueError("FUNDOS_API_KEYS_JSON must be a key-to-role object")
            configured_keys.update({str(key): str(role) for key, role in parsed_keys.items()})
    legacy_key = api_key if api_key is not None else os.environ.get("FUNDOS_API_KEY")
    if legacy_key:
        configured_keys[legacy_key] = "admin"
    invalid_roles = set(configured_keys.values()) - {"operator", "admin"}
    if invalid_roles or any(not key for key in configured_keys):
        raise ValueError("API keys must be non-empty and roles must be operator or admin")
    app.state.api_keys = configured_keys

    @app.exception_handler(HTTPException)
    async def http_error_response(request: Request, error: HTTPException) -> JSONResponse:
        default_codes = {
            401: "AUTHENTICATION_REQUIRED",
            403: "PERMISSION_DENIED",
            404: "RESOURCE_NOT_FOUND",
            409: "RESOURCE_CONFLICT",
            422: "INVALID_REQUEST",
        }
        headers = dict(error.headers or {})
        code = headers.pop(
            "X-FundOS-Error-Code",
            default_codes.get(error.status_code, "HTTP_ERROR"),
        )
        message = str(error.detail)
        request_id = getattr(request.state, "request_id", str(uuid4()))
        return JSONResponse(
            status_code=error.status_code,
            headers=headers,
            content={
                "detail": message,
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request_id,
                    "issues": [],
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        return JSONResponse(
            status_code=422,
            content={
                "detail": "request validation failed",
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "request validation failed",
                    "request_id": request_id,
                    "issues": error.errors(),
                },
            },
        )

    @app.middleware("http")
    async def observe_http_requests(request: Request, call_next):
        request_id = getattr(request.state, "request_id", None)
        if not request_id:
            request_id = request.headers.get("X-Request-ID", "").strip()[:128] or str(uuid4())
            request.state.request_id = request_id
        started = perf_counter()
        status_code = 500
        with trace_request(
            method=request.method,
            path=request.url.path,
            request_id=request_id,
        ) as trace_id:
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                duration = perf_counter() - started
                route = request.scope.get("route")
                route_path = getattr(route, "path", "__unmatched__")
                app.state.metrics.observe(
                    method=request.method,
                    path=route_path,
                    status_code=status_code,
                    duration_seconds=duration,
                )
                if "response" in locals():
                    response.headers["X-Trace-ID"] = trace_id
                    response.headers["Server-Timing"] = f"app;dur={duration * 1000:.3f}"

    @app.middleware("http")
    async def audit_mutating_requests(request: Request, call_next):
        request_id = getattr(request.state, "request_id", None)
        if not request_id:
            request_id = request.headers.get("X-Request-ID", "").strip()[:128] or str(uuid4())
        request.state.request_id = request_id
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        supplied_key = request.headers.get("X-API-Key")
        request.state.actor_id = (
            f"key:{sha256(supplied_key.encode('utf-8')).hexdigest()[:12]}"
            if supplied_key else "anonymous"
        )
        request.state.actor_role = "unknown" if configured_keys else "development-admin"
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            outcome = "succeeded" if status_code < 400 else "rejected" if status_code < 500 else "failed"
            record_audit_event(database, {
                "audit_id": str(uuid4()), "request_id": request_id,
                "method": request.method, "path": request.url.path,
                "actor_id": request.state.actor_id, "actor_role": request.state.actor_role,
                "outcome": outcome, "status_code": status_code,
                "client_ip": request.client.host if request.client else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

    def require_role(minimum_role: str):
        required_level = {"operator": 1, "admin": 2}[minimum_role]

        def dependency(
            request: Request,
            supplied_key: str | None = Header(default=None, alias="X-API-Key"),
        ) -> None:
            keys = request.app.state.api_keys
            if not keys:
                return
            matched_role = next(
                (role for key, role in keys.items() if supplied_key and compare_digest(supplied_key, key)),
                None,
            )
            if matched_role is None:
                raise HTTPException(status_code=401, detail="invalid or missing API key")
            request.state.actor_role = matched_role
            if {"operator": 1, "admin": 2}[matched_role] < required_level:
                raise HTTPException(status_code=403, detail="insufficient API key role")

        return dependency

    require_operator = require_role("operator")
    require_admin = require_role("admin")

    @app.get("/health", tags=["system"], response_model=HealthResponse)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/metrics",
        tags=["system"],
        dependencies=[Depends(require_operator)],
        response_class=Response,
    )
    def prometheus_metrics(request: Request) -> Response:
        return Response(
            content=request.app.state.metrics.prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get(
        "/operations/metrics",
        tags=["operations"],
        dependencies=[Depends(require_operator)],
    )
    def operations_metrics(request: Request) -> dict[str, Any]:
        metrics = request.app.state.metrics.snapshot()
        total = sum(item.count for item in metrics)
        errors = sum(item.count for item in metrics if item.status_code >= 400)
        failures = sum(item.count for item in metrics if item.status_code >= 500)
        return {
            "opentelemetry_enabled": request.app.state.opentelemetry_enabled,
            "request_count": total,
            "error_count": errors,
            "error_rate": errors / total if total else 0.0,
            "server_error_count": failures,
            "server_error_rate": failures / total if total else 0.0,
            "series": [
                {
                    "method": item.method,
                    "path": item.path,
                    "status_code": item.status_code,
                    "count": item.count,
                    "duration_seconds_sum": item.duration_seconds_sum,
                    "duration_seconds_max": item.duration_seconds_max,
                }
                for item in metrics
            ],
        }

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> str:
        return (Path(__file__).with_name("dashboard.html")).read_text(encoding="utf-8")

    @app.get("/products", tags=["portfolios"])
    def list_products(db: Database = Depends(get_database)) -> list[dict[str, Any]]:
        return _rows(db.fetch_all("SELECT * FROM portfolio_products ORDER BY created_at"))

    @app.post("/assets", status_code=201, tags=["setup"], dependencies=[Depends(require_operator)])
    def upsert_assets(payload: list[AssetInput], db: Database = Depends(get_database)) -> dict[str, int]:
        if not payload:
            raise HTTPException(status_code=422, detail="at least one asset is required")
        try:
            db.upsert_assets(Asset(item.symbol, item.name, item.asset_class) for item in payload)
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"upserted": len(payload)}

    @app.post("/products", status_code=201, tags=["portfolios"], dependencies=[Depends(require_operator)])
    def create_product(
        payload: ProductCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        db: Database = Depends(get_database),
    ) -> dict[str, str]:
        try:
            product = PortfolioProduct(payload.product_id, payload.name, payload.benchmark_symbol, datetime.now())
            mandate = InvestmentMandate(
                    payload.product_id, payload.objective, payload.risk_level,
                    payload.max_single_asset_weight, payload.min_cash_weight,
                    payload.max_turnover, payload.maximum_data_age_days,
                    payload.maximum_stress_loss,
            )
            return _idempotent(
                db,
                key=idempotency_key,
                operation="create_product",
                payload=payload.dict(),
                action=lambda: (
                    db.create_product_with_mandate(product, mandate)
                    or {"product_id": payload.product_id}
                ),
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error

    @app.post("/market-prices", status_code=201, tags=["market-data"], dependencies=[Depends(require_operator)])
    def import_market_prices(payload: PriceBatchInput, db: Database = Depends(get_database)) -> dict[str, int]:
        try:
            count = db.upsert_prices(
                (payload.provider, item.symbol, item.trade_date, item.close) for item in payload.prices
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"upserted": count}

    @app.post("/research", status_code=201, tags=["research"], dependencies=[Depends(require_operator)])
    def create_research(payload: ResearchCreate, db: Database = Depends(get_database)) -> dict[str, str]:
        try:
            report = ResearchReport(
                payload.report_id, payload.product_id, payload.as_of_date,
                payload.market_regime, payload.summary, payload.confidence,
                tuple(
                    ResearchEvidence(
                        item.evidence_id, item.title, item.source, item.url,
                        item.published_at, item.content,
                    )
                    for item in payload.evidence
                ),
                tuple(
                    AssetView(
                        item.asset_symbol, item.direction, item.confidence,
                        item.thesis, tuple(item.evidence_ids),
                    )
                    for item in payload.asset_views
                ),
            )
            create_research_report(db, report)
            if payload.finalize:
                finalize_research_report(db, report_id=payload.report_id)
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"report_id": payload.report_id, "status": "final" if payload.finalize else "draft"}

    @app.post(
        "/research/{report_id}/finalize",
        tags=["research"],
        dependencies=[Depends(require_operator)],
    )
    def finalize_research(
        report_id: str,
        db: Database = Depends(get_database),
    ) -> dict[str, str]:
        try:
            finalize_research_report(db, report_id=report_id)
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"report_id": report_id, "status": "final"}

    @app.post("/proposals", status_code=201, tags=["workflow"], dependencies=[Depends(require_operator)])
    def create_portfolio_proposal(payload: ProposalCreate, db: Database = Depends(get_database)) -> dict[str, str]:
        try:
            run_id = create_proposal(
                db,
                version=PortfolioVersion(
                    payload.version_id, payload.product_id, payload.version_number,
                    payload.effective_date,
                    tuple(PositionWeight(item.asset_symbol, item.weight) for item in payload.weights),
                ),
                rationale=payload.rationale,
                created_by=payload.created_by,
                research_report_id=payload.research_report_id,
                run_id=payload.run_id,
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"run_id": run_id, "state": "proposed"}

    @app.post("/workflows/{run_id}/risk-review", tags=["workflow"], dependencies=[Depends(require_operator)])
    def review_workflow_risk(
        run_id: str, payload: RiskReviewInput, db: Database = Depends(get_database)
    ) -> dict[str, Any]:
        try:
            report = run_risk_review(
                db, run_id=run_id, provider=payload.provider,
                as_of_date=payload.as_of_date, stress_scenarios=payload.stress_scenarios,
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {
            "run_id": report.run_id,
            "passed": report.passed,
            "hard_failure_count": report.hard_failure_count,
            "checks": [asdict(item) for item in report.checks],
        }

    @app.post("/workflows/{run_id}/committee-decision", tags=["workflow"], dependencies=[Depends(require_admin)])
    def decide_workflow(
        run_id: str, payload: CommitteeDecisionInput, db: Database = Depends(get_database)
    ) -> dict[str, str]:
        try:
            decision = record_committee_decision(
                db, run_id=run_id, approved=payload.approved,
                rationale=payload.rationale, decided_by=payload.decided_by,
                minimum_opinions=payload.minimum_opinions,
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"run_id": run_id, "decision": decision}

    @app.post(
        "/workflows/{run_id}/committee-opinions",
        status_code=201,
        tags=["workflow"],
        dependencies=[Depends(require_admin)],
    )
    def submit_committee_opinion(
        run_id: str,
        payload: CommitteeOpinionInput,
        db: Database = Depends(get_database),
    ) -> dict[str, str]:
        try:
            opinion_id = record_committee_opinion(
                db,
                run_id=run_id,
                member_role=payload.member_role,
                recommendation=payload.recommendation,
                rationale=payload.rationale,
                alternative_weights={
                    item.asset_symbol: float(item.weight)
                    for item in payload.alternative_weights
                },
                conditions=payload.conditions,
                submitted_by=payload.submitted_by,
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"opinion_id": opinion_id, "run_id": run_id}

    @app.post("/workflows/{run_id}/publish", tags=["workflow"], dependencies=[Depends(require_admin)])
    def publish_workflow(
        run_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        db: Database = Depends(get_database),
    ) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            result = publish_approved_workflow(db, run_id=run_id)
            return {
                "run_id": run_id,
                "version_id": result.version_id,
                "previous_version_id": result.previous_version_id,
                "turnover": result.turnover,
                "status": result.status,
            }

        try:
            return _idempotent(
                db,
                key=idempotency_key,
                operation=f"publish_workflow:{run_id}",
                payload={"run_id": run_id},
                action=action,
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error

    @app.get("/products/{product_id}", tags=["portfolios"])
    def get_product(product_id: str, db: Database = Depends(get_database)) -> dict[str, Any]:
        products = db.fetch_all("SELECT * FROM portfolio_products WHERE product_id = ?", (product_id,))
        if not products:
            raise HTTPException(status_code=404, detail="portfolio product not found")
        mandate = db.fetch_all("SELECT * FROM investment_mandates WHERE product_id = ?", (product_id,))
        return {"product": dict(products[0]), "investment_mandate": dict(mandate[0]) if mandate else None}

    @app.get("/products/{product_id}/versions", tags=["portfolios"])
    def list_versions(product_id: str, db: Database = Depends(get_database)) -> list[dict[str, Any]]:
        versions = _rows(db.fetch_all(
            "SELECT * FROM portfolio_versions WHERE product_id = ? ORDER BY version_number", (product_id,)
        ))
        for version in versions:
            version["weights"] = _rows(db.fetch_all(
                "SELECT asset_symbol, weight FROM portfolio_version_weights WHERE version_id = ? ORDER BY asset_symbol",
                (version["version_id"],),
            ))
        return versions

    @app.get("/products/{product_id}/performance", tags=["performance"])
    def get_performance(
        product_id: str,
        limit: int = Query(default=500, ge=1, le=5000),
        db: Database = Depends(get_database),
    ) -> dict[str, Any]:
        nav = _rows(db.fetch_all(
            "SELECT nav_date, nav FROM portfolio_nav WHERE product_id = ? ORDER BY nav_date DESC LIMIT ?",
            (product_id, limit),
        ))
        nav.reverse()
        benchmark_nav = _rows(db.fetch_all(
            """
            SELECT benchmark_symbol, nav_date, nav
            FROM benchmark_nav
            WHERE product_id = ?
            ORDER BY nav_date DESC LIMIT ?
            """,
            (product_id, limit),
        ))
        benchmark_nav.reverse()
        snapshots = _rows(db.fetch_all(
            "SELECT * FROM performance_snapshots WHERE product_id = ? ORDER BY as_of_date DESC",
            (product_id,),
        ))
        return {
            "nav": nav,
            "benchmark_nav": benchmark_nav,
            "snapshots": snapshots,
        }

    @app.get(
        "/products/{product_id}/operations",
        tags=["operations"],
        responses=PAGINATED_RESPONSE_DOCS,
    )
    def list_operations_cycles(
        product_id: str,
        response: Response,
        limit: int = Query(default=30, ge=1, le=365),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        total = db.fetch_all(
            "SELECT COUNT(*) AS count FROM operations_cycles WHERE product_id = ?",
            (product_id,),
        )[0]["count"]
        _pagination_headers(response, total=total, limit=limit, offset=offset)
        return _rows(db.fetch_all(
            """
            SELECT * FROM operations_cycles
            WHERE product_id = ? ORDER BY as_of_date DESC LIMIT ? OFFSET ?
            """,
            (product_id, limit, offset),
        ))

    @app.get("/pipeline-runs", tags=["operations"], responses=PAGINATED_RESPONSE_DOCS)
    def list_pipeline_runs(
        response: Response,
        limit: int = Query(default=20, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        total = db.fetch_all("SELECT COUNT(*) AS count FROM pipeline_runs")[0]["count"]
        _pagination_headers(response, total=total, limit=limit, offset=offset)
        runs = _rows(db.fetch_all(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ))
        for run in runs:
            run["steps"] = _rows(db.fetch_all(
                "SELECT * FROM pipeline_steps WHERE run_id = ? ORDER BY step_id",
                (run["run_id"],),
            ))
        return runs

    @app.get(
        "/scheduled-jobs/runs",
        tags=["operations"],
        responses=PAGINATED_RESPONSE_DOCS,
        response_model=list[ScheduledJobRunResponse],
    )
    def list_scheduled_job_runs(
        response: Response,
        job_name: str | None = Query(default=None, min_length=1),
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        allowed_statuses = {"running", "succeeded", "failed", "skipped", "abandoned"}
        if status is not None and status not in allowed_statuses:
            raise HTTPException(status_code=422, detail="invalid scheduled job status")
        conditions: list[str] = []
        parameters: list[Any] = []
        if job_name is not None:
            conditions.append("job_name = ?")
            parameters.append(job_name)
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        count_parameters = tuple(parameters)
        total = db.fetch_all(
            f"SELECT COUNT(*) AS count FROM scheduled_job_runs {where}",
            count_parameters,
        )[0]["count"]
        _pagination_headers(response, total=total, limit=limit, offset=offset)
        parameters.extend((limit, offset))
        return _rows(db.fetch_all(
            f"""
            SELECT run_id, job_name, status, started_at, completed_at,
                   lease_until, message
            FROM scheduled_job_runs
            {where}
            ORDER BY started_at DESC LIMIT ? OFFSET ?
            """,
            tuple(parameters),
        ))

    @app.get(
        "/scheduled-jobs/locks",
        tags=["operations"],
        response_model=list[ScheduledJobLockResponse],
    )
    def list_scheduled_job_locks(
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        locks = _rows(db.fetch_all(
            """
            SELECT job_name, acquired_at, lease_until
            FROM scheduled_job_locks ORDER BY job_name
            """
        ))
        for item in locks:
            item["active"] = datetime.fromisoformat(item["lease_until"]) > now
        return locks

    @app.get("/alerts", tags=["operations"], responses=PAGINATED_RESPONSE_DOCS)
    def list_alerts(
        response: Response,
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        if status is not None and status not in {"pending", "delivered", "failed"}:
            raise HTTPException(status_code=422, detail="invalid alert status")
        if status:
            total = db.fetch_all(
                "SELECT COUNT(*) AS count FROM alert_events WHERE status = ?",
                (status,),
            )[0]["count"]
            _pagination_headers(response, total=total, limit=limit, offset=offset)
            return _rows(db.fetch_all(
                """SELECT a.*, l.state AS lifecycle_state, l.updated_by, l.note, l.updated_at
                FROM alert_events a LEFT JOIN alert_lifecycle l ON l.alert_id = a.alert_id
                WHERE a.status = ? ORDER BY a.created_at DESC LIMIT ? OFFSET ?""",
                (status, limit, offset),
            ))
        total = db.fetch_all("SELECT COUNT(*) AS count FROM alert_events")[0]["count"]
        _pagination_headers(response, total=total, limit=limit, offset=offset)
        return _rows(db.fetch_all(
            """SELECT a.*, l.state AS lifecycle_state, l.updated_by, l.note, l.updated_at
            FROM alert_events a LEFT JOIN alert_lifecycle l ON l.alert_id = a.alert_id
            ORDER BY a.created_at DESC LIMIT ? OFFSET ?""", (limit, offset)
        ))

    @app.post(
        "/alerts/{alert_id}/acknowledge", tags=["operations"],
        dependencies=[Depends(require_admin)],
    )
    def acknowledge_alert(
        alert_id: str, payload: AlertLifecycleInput, db: Database = Depends(get_database)
    ) -> dict[str, str]:
        try:
            state = update_alert_lifecycle(
                db, alert_id=alert_id, state="acknowledged",
                updated_by=payload.updated_by, note=payload.note,
            )
        except ValueError as error:
            raise _domain_error(error) from error
        return {"alert_id": alert_id, "lifecycle_state": state}

    @app.post(
        "/alerts/{alert_id}/resolve", tags=["operations"],
        dependencies=[Depends(require_admin)],
    )
    def resolve_alert(
        alert_id: str, payload: AlertLifecycleInput, db: Database = Depends(get_database)
    ) -> dict[str, str]:
        try:
            state = update_alert_lifecycle(
                db, alert_id=alert_id, state="resolved",
                updated_by=payload.updated_by, note=payload.note,
            )
        except ValueError as error:
            raise _domain_error(error) from error
        return {"alert_id": alert_id, "lifecycle_state": state}

    @app.get("/model-policy/status", tags=["ai-operations"])
    def model_policy_status(
        provider: str = Query(min_length=1), model: str = Query(min_length=1),
        db: Database = Depends(get_database),
    ) -> dict[str, Any]:
        threshold = int(os.environ.get("FUNDOS_LLM_CIRCUIT_FAILURE_THRESHOLD", "3"))
        return get_model_circuit_status(
            db, provider=provider, model=model, failure_threshold=max(1, threshold),
        )

    @app.post(
        "/model-policy/circuit-reset", tags=["ai-operations"],
        dependencies=[Depends(require_admin)],
    )
    def reset_circuit(
        payload: CircuitResetInput, db: Database = Depends(get_database)
    ) -> dict[str, Any]:
        try:
            reset_id = reset_model_circuit(
                db, provider=payload.provider, model=payload.model,
                reset_by=payload.reset_by, reason=payload.reason,
            )
        except ValueError as error:
            raise _domain_error(error) from error
        return {"reset_id": reset_id, "state": "closed"}

    @app.get("/model-calls/summary", tags=["ai-operations"])
    def model_call_summary(
        days: int = Query(default=30, ge=1, le=365),
        db: Database = Depends(get_database),
    ) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        row = db.fetch_all(
            """
            SELECT
                COUNT(*) AS total_calls,
                SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_calls,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_calls,
                COALESCE(SUM(attempts), 0) AS total_attempts,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                COALESCE(AVG(latency_ms), 0) AS average_latency_ms
            FROM model_calls WHERE created_at >= ?
            """,
            (since,),
        )[0]
        result = dict(row)
        total = result["total_calls"]
        result["success_rate"] = result["succeeded_calls"] / total if total else None
        result["period_days"] = days
        return result

    @app.get("/model-calls", tags=["ai-operations"], responses=PAGINATED_RESPONSE_DOCS)
    def list_model_calls(
        response: Response,
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        if status is not None and status not in {"succeeded", "failed"}:
            raise HTTPException(status_code=422, detail="invalid model call status")
        columns = """
            call_id, purpose, provider, model, status, attempts, latency_ms,
            input_tokens, output_tokens, estimated_cost_usd, error_message, created_at
        """
        if status:
            total = db.fetch_all(
                "SELECT COUNT(*) AS count FROM model_calls WHERE status = ?",
                (status,),
            )[0]["count"]
            _pagination_headers(response, total=total, limit=limit, offset=offset)
            return _rows(db.fetch_all(
                f"SELECT {columns} FROM model_calls WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ))
        total = db.fetch_all("SELECT COUNT(*) AS count FROM model_calls")[0]["count"]
        _pagination_headers(response, total=total, limit=limit, offset=offset)
        return _rows(db.fetch_all(
            f"SELECT {columns} FROM model_calls ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ))

    @app.get(
        "/audit-events",
        tags=["security"],
        dependencies=[Depends(require_admin)],
        responses=PAGINATED_RESPONSE_DOCS,
    )
    def list_audit_events(
        response: Response,
        outcome: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        if outcome is not None and outcome not in {"succeeded", "rejected", "failed"}:
            raise HTTPException(status_code=422, detail="invalid audit outcome")
        if outcome:
            total = db.fetch_all(
                "SELECT COUNT(*) AS count FROM api_audit_events WHERE outcome = ?",
                (outcome,),
            )[0]["count"]
            _pagination_headers(response, total=total, limit=limit, offset=offset)
            return _rows(db.fetch_all(
                "SELECT * FROM api_audit_events WHERE outcome = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (outcome, limit, offset),
            ))
        total = db.fetch_all("SELECT COUNT(*) AS count FROM api_audit_events")[0]["count"]
        _pagination_headers(response, total=total, limit=limit, offset=offset)
        return _rows(db.fetch_all(
            "SELECT * FROM api_audit_events ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ))

    @app.get("/audit-events/integrity", tags=["security"], dependencies=[Depends(require_admin)])
    def audit_integrity(db: Database = Depends(get_database)) -> dict[str, Any]:
        return verify_audit_chain(db)

    @app.get("/audit-events/export.csv", tags=["security"], dependencies=[Depends(require_admin)])
    def export_audit_events(db: Database = Depends(get_database)) -> StreamingResponse:
        rows = db.fetch_all("SELECT * FROM api_audit_events ORDER BY created_at, audit_id")
        output = io.StringIO()
        columns = [item[1] for item in db.fetch_all("PRAGMA table_info(api_audit_events)")]
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
        return StreamingResponse(
            iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=fundos-audit-events.csv"},
        )

    @app.post(
        "/audit-events/retention", tags=["security"], dependencies=[Depends(require_admin)],
    )
    def apply_audit_retention(
        days: int = Query(default=365, ge=30, le=3650),
        db: Database = Depends(get_database),
    ) -> dict[str, int]:
        return {"retention_days": days, "deleted_events": purge_audit_events(db, retention_days=days)}

    @app.get("/products/{product_id}/research", tags=["research"])
    def list_research(product_id: str, db: Database = Depends(get_database)) -> list[dict[str, Any]]:
        reports = _rows(db.fetch_all(
            "SELECT * FROM research_reports WHERE product_id = ? ORDER BY as_of_date DESC", (product_id,)
        ))
        for report in reports:
            report["evidence"] = _rows(db.fetch_all(
                "SELECT * FROM research_evidence WHERE report_id = ? ORDER BY published_at",
                (report["report_id"],),
            ))
            report["asset_views"] = _rows(db.fetch_all(
                "SELECT asset_symbol, direction, confidence, thesis FROM asset_views WHERE report_id = ? ORDER BY asset_symbol",
                (report["report_id"],),
            ))
        return reports

    @app.get(
        "/research-evidence",
        tags=["research"],
        dependencies=[Depends(require_admin)],
        responses=PAGINATED_RESPONSE_DOCS,
    )
    def list_raw_research_evidence(
        response: Response,
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        if status is not None and status not in {"pending", "approved", "rejected"}:
            raise HTTPException(status_code=422, detail="invalid evidence review status")
        query = """
            SELECT e.*, s.name AS source_name, s.source_type
            FROM raw_research_evidence e
            JOIN research_evidence_sources s ON s.source_id = e.source_id
        """
        parameters: list[Any] = []
        count_query = "SELECT COUNT(*) AS count FROM raw_research_evidence e"
        if status is not None:
            query += " WHERE e.review_status = ?"
            count_query += " WHERE e.review_status = ?"
            parameters.append(status)
        total = db.fetch_all(count_query, tuple(parameters))[0]["count"]
        _pagination_headers(response, total=total, limit=limit, offset=offset)
        query += " ORDER BY e.published_at DESC, e.raw_evidence_id LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        rows = _rows(db.fetch_all(query, tuple(parameters)))
        for row in rows:
            row["asset_symbols"] = json.loads(row["asset_symbols"])
        return rows

    @app.get(
        "/evidence-collection-runs",
        tags=["research"],
        dependencies=[Depends(require_admin)],
        responses=PAGINATED_RESPONSE_DOCS,
    )
    def list_evidence_collection_runs(
        response: Response,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        total = db.fetch_all(
            "SELECT COUNT(*) AS count FROM evidence_collection_runs"
        )[0]["count"]
        _pagination_headers(response, total=total, limit=limit, offset=offset)
        return _rows(db.fetch_all(
            """
            SELECT * FROM evidence_collection_runs
            ORDER BY started_at DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ))

    @app.post(
        "/research-evidence/{raw_evidence_id}/review",
        tags=["research"],
        dependencies=[Depends(require_admin)],
    )
    def review_research_evidence(
        raw_evidence_id: str,
        payload: EvidenceReviewInput,
        db: Database = Depends(get_database),
    ) -> dict[str, Any]:
        try:
            return asdict(review_raw_research_evidence(
                db,
                raw_evidence_id=raw_evidence_id,
                approved=payload.approved,
                reviewed_by=payload.reviewed_by,
                note=payload.note,
            ))
        except ValueError as error:
            raise _domain_error(error) from error

    @app.post(
        "/products/{product_id}/approved-research-input",
        tags=["research"],
        dependencies=[Depends(require_admin)],
    )
    def create_approved_research_input(
        product_id: str,
        payload: ApprovedResearchRequestInput,
        db: Database = Depends(get_database),
    ) -> dict[str, Any]:
        try:
            return build_approved_research_request(
                db,
                product_id=product_id,
                report_id=payload.report_id,
                as_of_date=payload.as_of_date.isoformat(),
            )
        except ValueError as error:
            raise _domain_error(error) from error

    @app.get("/workflows/{run_id}", tags=["workflow"])
    def get_workflow(run_id: str, db: Database = Depends(get_database)) -> dict[str, Any]:
        runs = db.fetch_all("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,))
        if not runs:
            raise HTTPException(status_code=404, detail="workflow run not found")
        proposal = db.fetch_all("SELECT * FROM portfolio_proposals WHERE run_id = ?", (run_id,))
        checks = _rows(db.fetch_all("SELECT * FROM risk_checks WHERE run_id = ? ORDER BY check_id", (run_id,)))
        decision = db.fetch_all("SELECT * FROM committee_decisions WHERE run_id = ?", (run_id,))
        opinions = _committee_opinions(db, run_id)
        return {
            "run": dict(runs[0]),
            "proposal": dict(proposal[0]) if proposal else None,
            "risk_checks": checks,
            "committee_opinions": opinions,
            "committee_decision": dict(decision[0]) if decision else None,
        }

    @app.get("/products/{product_id}/workflows", tags=["workflow"])
    def list_product_workflows(product_id: str, db: Database = Depends(get_database)) -> list[dict[str, Any]]:
        runs = db.fetch_all(
            "SELECT * FROM workflow_runs WHERE product_id = ? ORDER BY created_at DESC",
            (product_id,),
        )
        result = []
        for run in runs:
            run_id = run["run_id"]
            proposal = db.fetch_all("SELECT * FROM portfolio_proposals WHERE run_id = ?", (run_id,))
            decision = db.fetch_all("SELECT * FROM committee_decisions WHERE run_id = ?", (run_id,))
            result.append({
                "run": dict(run),
                "proposal": dict(proposal[0]) if proposal else None,
                "risk_checks": _rows(db.fetch_all(
                    "SELECT * FROM risk_checks WHERE run_id = ? ORDER BY check_id", (run_id,)
                )),
                "committee_opinions": _committee_opinions(db, run_id),
                "committee_decision": dict(decision[0]) if decision else None,
            })
        return result

    @app.get("/products/{product_id}/reviews", tags=["reviews"])
    def list_reviews(product_id: str, db: Database = Depends(get_database)) -> list[dict[str, Any]]:
        reviews = _rows(db.fetch_all(
            "SELECT * FROM review_reports WHERE product_id = ? ORDER BY period_end DESC", (product_id,)
        ))
        for review in reviews:
            review["contributions"] = _rows(db.fetch_all(
                "SELECT asset_symbol, contribution FROM review_contributions WHERE review_id = ? ORDER BY asset_symbol",
                (review["review_id"],),
            ))
            review["view_outcomes"] = _rows(db.fetch_all(
                "SELECT asset_symbol, direction, realized_return, was_correct FROM research_view_outcomes WHERE review_id = ? ORDER BY asset_symbol",
                (review["review_id"],),
            ))
        return reviews

    return app


app = create_app()


def run() -> None:
    uvicorn.run("fundos.api.main:app", host="127.0.0.1", port=8000, reload=False)
