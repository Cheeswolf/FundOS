import os
import sqlite3
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from fundos.api.schemas import (
    AssetInput,
    CommitteeDecisionInput,
    PriceBatchInput,
    ProductCreate,
    ProposalCreate,
    ResearchCreate,
    RiskReviewInput,
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
from fundos.services import (
    create_proposal,
    create_research_report,
    finalize_research_report,
    publish_approved_workflow,
    record_committee_decision,
    run_risk_review,
)
from fundos.storage import Database


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def get_database(request: Request) -> Database:
    return request.app.state.database


def _domain_error(error: Exception) -> HTTPException:
    if isinstance(error, sqlite3.IntegrityError):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))


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


def create_app(database_path: str | Path | None = None, *, api_key: str | None = None) -> FastAPI:
    resolved_path = Path(database_path or os.environ.get("FUNDOS_DB_PATH", "data/fundos.sqlite3"))
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(resolved_path)
    database.initialize()

    app = FastAPI(
        title="FundOS API",
        version="0.1.0",
        description="Investment research and portfolio operating system API",
    )
    app.state.database = database
    app.state.api_key = api_key if api_key is not None else os.environ.get("FUNDOS_API_KEY")

    def require_write_access(
        request: Request,
        supplied_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> None:
        expected_key = request.app.state.api_key
        if expected_key and supplied_key != expected_key:
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> str:
        return (Path(__file__).with_name("dashboard.html")).read_text(encoding="utf-8")

    @app.get("/products", tags=["portfolios"])
    def list_products(db: Database = Depends(get_database)) -> list[dict[str, Any]]:
        return _rows(db.fetch_all("SELECT * FROM portfolio_products ORDER BY created_at"))

    @app.post("/assets", status_code=201, tags=["setup"], dependencies=[Depends(require_write_access)])
    def upsert_assets(payload: list[AssetInput], db: Database = Depends(get_database)) -> dict[str, int]:
        if not payload:
            raise HTTPException(status_code=422, detail="at least one asset is required")
        try:
            db.upsert_assets(Asset(item.symbol, item.name, item.asset_class) for item in payload)
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"upserted": len(payload)}

    @app.post("/products", status_code=201, tags=["portfolios"], dependencies=[Depends(require_write_access)])
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

    @app.post("/market-prices", status_code=201, tags=["market-data"], dependencies=[Depends(require_write_access)])
    def import_market_prices(payload: PriceBatchInput, db: Database = Depends(get_database)) -> dict[str, int]:
        try:
            count = db.upsert_prices(
                (payload.provider, item.symbol, item.trade_date, item.close) for item in payload.prices
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"upserted": count}

    @app.post("/research", status_code=201, tags=["research"], dependencies=[Depends(require_write_access)])
    def create_research(payload: ResearchCreate, db: Database = Depends(get_database)) -> dict[str, str]:
        try:
            report = ResearchReport(
                payload.report_id, payload.product_id, payload.as_of_date,
                payload.market_regime, payload.summary, payload.confidence,
                tuple(
                    ResearchEvidence(
                        item.evidence_id, item.title, item.source, item.url, item.published_at
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

    @app.post("/proposals", status_code=201, tags=["workflow"], dependencies=[Depends(require_write_access)])
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

    @app.post("/workflows/{run_id}/risk-review", tags=["workflow"], dependencies=[Depends(require_write_access)])
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

    @app.post("/workflows/{run_id}/committee-decision", tags=["workflow"], dependencies=[Depends(require_write_access)])
    def decide_workflow(
        run_id: str, payload: CommitteeDecisionInput, db: Database = Depends(get_database)
    ) -> dict[str, str]:
        try:
            decision = record_committee_decision(
                db, run_id=run_id, approved=payload.approved,
                rationale=payload.rationale, decided_by=payload.decided_by,
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            raise _domain_error(error) from error
        return {"run_id": run_id, "decision": decision}

    @app.post("/workflows/{run_id}/publish", tags=["workflow"], dependencies=[Depends(require_write_access)])
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
        snapshots = _rows(db.fetch_all(
            "SELECT * FROM performance_snapshots WHERE product_id = ? ORDER BY as_of_date DESC",
            (product_id,),
        ))
        return {"nav": nav, "snapshots": snapshots}

    @app.get("/products/{product_id}/operations", tags=["operations"])
    def list_operations_cycles(
        product_id: str,
        limit: int = Query(default=30, ge=1, le=365),
        db: Database = Depends(get_database),
    ) -> list[dict[str, Any]]:
        return _rows(db.fetch_all(
            """
            SELECT * FROM operations_cycles
            WHERE product_id = ? ORDER BY as_of_date DESC LIMIT ?
            """,
            (product_id, limit),
        ))

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

    @app.get("/workflows/{run_id}", tags=["workflow"])
    def get_workflow(run_id: str, db: Database = Depends(get_database)) -> dict[str, Any]:
        runs = db.fetch_all("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,))
        if not runs:
            raise HTTPException(status_code=404, detail="workflow run not found")
        proposal = db.fetch_all("SELECT * FROM portfolio_proposals WHERE run_id = ?", (run_id,))
        checks = _rows(db.fetch_all("SELECT * FROM risk_checks WHERE run_id = ? ORDER BY check_id", (run_id,)))
        decision = db.fetch_all("SELECT * FROM committee_decisions WHERE run_id = ?", (run_id,))
        return {
            "run": dict(runs[0]),
            "proposal": dict(proposal[0]) if proposal else None,
            "risk_checks": checks,
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
