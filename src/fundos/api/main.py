import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request

from fundos.storage import Database


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def get_database(request: Request) -> Database:
    return request.app.state.database


def create_app(database_path: str | Path | None = None) -> FastAPI:
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

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/products", tags=["portfolios"])
    def list_products(db: Database = Depends(get_database)) -> list[dict[str, Any]]:
        return _rows(db.fetch_all("SELECT * FROM portfolio_products ORDER BY created_at"))

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

