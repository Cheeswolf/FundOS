from datetime import datetime, timezone

from fundos.domain import ResearchReport
from fundos.storage import Database


def create_research_report(database: Database, report: ResearchReport) -> str:
    if any(item.published_at.date() > report.as_of_date for item in report.evidence):
        raise ValueError("research evidence cannot be published after the report date")
    with database.connect() as connection:
        if connection.execute(
            "SELECT 1 FROM portfolio_products WHERE product_id = ?", (report.product_id,)
        ).fetchone() is None:
            raise ValueError("portfolio product does not exist")
        connection.execute(
            """
            INSERT INTO research_reports (
                report_id, product_id, as_of_date, market_regime, summary, confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                report.report_id, report.product_id, report.as_of_date.isoformat(),
                report.market_regime, report.summary, float(report.confidence),
            ),
        )
        connection.executemany(
            """
            INSERT INTO research_evidence
                (evidence_id, report_id, title, source, url, published_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (item.evidence_id, report.report_id, item.title, item.source, item.url, item.published_at.isoformat())
                for item in report.evidence
            ],
        )
        for view in report.asset_views:
            cursor = connection.execute(
                """
                INSERT INTO asset_views
                    (report_id, asset_symbol, direction, confidence, thesis)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report.report_id, view.asset_symbol, view.direction, float(view.confidence), view.thesis),
            )
            connection.executemany(
                "INSERT INTO asset_view_evidence (view_id, evidence_id) VALUES (?, ?)",
                [(cursor.lastrowid, evidence_id) for evidence_id in view.evidence_ids],
            )
    return report.report_id


def finalize_research_report(database: Database, *, report_id: str) -> None:
    with database.connect() as connection:
        report = connection.execute(
            "SELECT status FROM research_reports WHERE report_id = ?", (report_id,)
        ).fetchone()
        if report is None:
            raise ValueError("research report does not exist")
        if report["status"] != "draft":
            raise ValueError("only a draft research report can be finalized")
        uncited_views = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM asset_views v
            WHERE v.report_id = ?
              AND NOT EXISTS (SELECT 1 FROM asset_view_evidence e WHERE e.view_id = v.view_id)
            """,
            (report_id,),
        ).fetchone()["count"]
        if uncited_views:
            raise ValueError("every asset view must cite research evidence")
        connection.execute(
            "UPDATE research_reports SET status = 'final', finalized_at = ? WHERE report_id = ?",
            (datetime.now(timezone.utc).isoformat(), report_id),
        )

