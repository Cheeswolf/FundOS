import argparse
import json
import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.domain import (  # noqa: E402
    Asset,
    AssetView,
    InvestmentMandate,
    PortfolioProduct,
    PortfolioVersion,
    PositionWeight,
    ResearchEvidence,
    ResearchReport,
)
from fundos.services import (  # noqa: E402
    calculate_and_store_versioned_performance,
    create_proposal,
    create_research_report,
    finalize_research_report,
    generate_review_report,
    publish_approved_workflow,
    record_committee_decision,
    run_risk_review,
)
from fundos.storage import Database  # noqa: E402


PRODUCT_ID = "fundos-demo-balanced"
PROVIDER = "demo-synthetic"


def business_dates(start: date, end: date) -> list[date]:
    result = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def seed(database_path: Path) -> dict[str, int | float | str]:
    config = json.loads((PROJECT_ROOT / "config" / "assets.json").read_text(encoding="utf-8"))
    database = Database(database_path)
    database.initialize()
    database.upsert_assets(Asset(**item) for item in config["assets"])

    if not database.fetch_all("SELECT 1 FROM portfolio_products WHERE product_id = ?", (PRODUCT_ID,)):
        database.create_product_with_mandate(
            PortfolioProduct(
                PRODUCT_ID,
                "FundOS 多资产平衡演示组合",
                config["benchmark"],
                datetime.now(timezone.utc),
            ),
            InvestmentMandate(
                PRODUCT_ID,
                "在控制回撤与换手率的前提下，通过权益、黄金、债券和现金实现中长期稳健增值。",
                "中等风险",
                Decimal("0.35"),
                Decimal("0.05"),
                Decimal("0.30"),
                3,
                Decimal("0.20"),
            ),
        )

    dates = business_dates(date(2026, 6, 1), date(2026, 7, 10))
    formulas = {
        "CSI300": lambda i: 100 + 0.28 * i + 1.8 * math.sin(i / 3),
        "CSI500": lambda i: 100 + 0.20 * i + 2.2 * math.sin(i / 2.6),
        "NASDAQ100": lambda i: 100 + 0.42 * i + 2.8 * math.sin(i / 3.8),
        "GOLD": lambda i: 100 + 0.25 * i + 1.1 * math.cos(i / 4),
        "BOND": lambda i: 100 + 0.07 * i + 0.25 * math.sin(i / 5),
        "CASH": lambda i: 100 + 0.01 * i,
        "BALANCED_BENCHMARK": lambda i: 100 + 0.22 * i + 0.6 * math.sin(i / 4.5),
    }
    database.upsert_prices(
        (PROVIDER, symbol, trade_date, round(formula(index), 6))
        for symbol, formula in formulas.items()
        for index, trade_date in enumerate(dates)
    )

    reports = [
        ResearchReport(
            "demo-research-1", PRODUCT_ID, date(2026, 6, 1), "温和风险偏好",
            "权益趋势保持韧性，黄金提供组合保护，债券承担稳定器角色。",
            Decimal("0.76"),
            (
                ResearchEvidence(
                    "demo-evidence-1", "模拟宏观与市场周报", "FundOS 演示数据",
                    "https://example.com/fundos/demo-weekly-1",
                    datetime.combine(date(2026, 6, 1), time(), tzinfo=timezone.utc),
                ),
            ),
            (
                AssetView("CSI300", "positive", Decimal("0.72"), "盈利预期稳定且估值处于合理区间。", ("demo-evidence-1",)),
                AssetView("GOLD", "positive", Decimal("0.68"), "黄金有助于对冲宏观不确定性。", ("demo-evidence-1",)),
                AssetView("BOND", "neutral", Decimal("0.70"), "债券以稳定组合波动为主要作用。", ("demo-evidence-1",)),
            ),
        ),
        ResearchReport(
            "demo-research-2", PRODUCT_ID, date(2026, 6, 22), "成长资产改善",
            "海外成长动能改善，同时提高黄金配置以控制权益相关风险。",
            Decimal("0.81"),
            (
                ResearchEvidence(
                    "demo-evidence-2", "模拟月中资产观察", "FundOS 演示数据",
                    "https://example.com/fundos/demo-midmonth",
                    datetime.combine(date(2026, 6, 22), time(), tzinfo=timezone.utc),
                ),
            ),
            (
                AssetView("NASDAQ100", "positive", Decimal("0.82"), "成长资产趋势和盈利预期同步改善。", ("demo-evidence-2",)),
                AssetView("GOLD", "positive", Decimal("0.74"), "提高防御资产可改善组合尾部风险。", ("demo-evidence-2",)),
                AssetView("CSI300", "neutral", Decimal("0.65"), "国内宽基维持中性配置。", ("demo-evidence-2",)),
            ),
        ),
    ]
    for report in reports:
        if not database.fetch_all("SELECT 1 FROM research_reports WHERE report_id = ?", (report.report_id,)):
            create_research_report(database, report)
            finalize_research_report(database, report_id=report.report_id)

    versions = [
        (
            "demo-run-1",
            PortfolioVersion(
                "demo-version-1", PRODUCT_ID, 1, date(2026, 6, 1),
                (
                    PositionWeight("CSI300", Decimal("0.30")),
                    PositionWeight("CSI500", Decimal("0.10")),
                    PositionWeight("NASDAQ100", Decimal("0.15")),
                    PositionWeight("GOLD", Decimal("0.15")),
                    PositionWeight("BOND", Decimal("0.25")),
                    PositionWeight("CASH", Decimal("0.05")),
                ),
            ),
            "demo-research-1",
            "建立多资产战略配置，权益提供增长，黄金和债券降低组合波动。",
        ),
        (
            "demo-run-2",
            PortfolioVersion(
                "demo-version-2", PRODUCT_ID, 2, date(2026, 6, 22),
                (
                    PositionWeight("CSI300", Decimal("0.25")),
                    PositionWeight("CSI500", Decimal("0.10")),
                    PositionWeight("NASDAQ100", Decimal("0.20")),
                    PositionWeight("GOLD", Decimal("0.20")),
                    PositionWeight("BOND", Decimal("0.20")),
                    PositionWeight("CASH", Decimal("0.05")),
                ),
            ),
            "demo-research-2",
            "适度增加成长和黄金配置，降低债券与国内大盘权重。",
        ),
    ]
    scenarios = {
        "权益快速下跌": {"CSI300": -0.20, "CSI500": -0.25, "NASDAQ100": -0.25, "GOLD": 0.05, "BOND": 0.02, "CASH": 0.0},
        "利率上行": {"CSI300": -0.08, "CSI500": -0.10, "NASDAQ100": -0.12, "GOLD": -0.05, "BOND": -0.08, "CASH": 0.0},
    }
    for run_id, version, report_id, rationale in versions:
        if not database.fetch_all("SELECT 1 FROM workflow_runs WHERE run_id = ?", (run_id,)):
            create_proposal(
                database,
                version=version,
                rationale=rationale,
                created_by="组合经理 Agent（演示）",
                research_report_id=report_id,
                run_id=run_id,
            )
            risk_report = run_risk_review(
                database,
                run_id=run_id,
                provider=PROVIDER,
                as_of_date=version.effective_date,
                stress_scenarios=scenarios,
            )
            if not risk_report.passed:
                raise RuntimeError(f"demo risk review failed for {run_id}")
            record_committee_decision(
                database,
                run_id=run_id,
                approved=True,
                rationale="研究证据充分，全部硬性风险规则通过，同意按提案发布。",
                decided_by="投资委员会（演示）",
            )
            publish_approved_workflow(database, run_id=run_id)

    performance = calculate_and_store_versioned_performance(
        database,
        product_id=PRODUCT_ID,
        provider=PROVIDER,
        benchmark_symbol=config["benchmark"],
    )
    if not database.fetch_all(
        "SELECT 1 FROM review_reports WHERE product_id = ? AND period_start = ? AND period_end = ?",
        (PRODUCT_ID, dates[0].isoformat(), dates[-1].isoformat()),
    ):
        generate_review_report(
            database,
            product_id=PRODUCT_ID,
            research_report_id="demo-research-1",
            provider=PROVIDER,
            period_start=dates[0],
            period_end=dates[-1],
            summary="组合取得正收益，月中调仓提高了成长与黄金暴露，并保持风险约束内运行。",
            lessons="继续观察成长资产与黄金相关性，并在后续周期验证增配效果的稳定性。",
        )
    return {
        "product_id": PRODUCT_ID,
        "price_rows": len(dates) * len(formulas),
        "portfolio_return": performance.cumulative_return,
        "benchmark_return": performance.benchmark_return,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a complete FundOS synthetic demonstration")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "fundos.sqlite3")
    arguments = parser.parse_args()
    result = seed(arguments.database)
    print(f"Demo product: {result['product_id']}")
    print(f"Synthetic price rows: {result['price_rows']}")
    print(f"Portfolio return: {result['portfolio_return']:.2%}")
    print(f"Benchmark return: {result['benchmark_return']:.2%}")
    print("Dashboard: http://127.0.0.1:8000/dashboard")


if __name__ == "__main__":
    main()

