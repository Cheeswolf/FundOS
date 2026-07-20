import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fundos.analytics import calculate_metrics, calculate_nav, calculate_portfolio_returns  # noqa: E402
from fundos.domain import Asset, InvestmentMandate, PortfolioProduct, PortfolioVersion, PositionWeight  # noqa: E402
from fundos.services import publish_portfolio_version  # noqa: E402
from fundos.storage import Database  # noqa: E402


def main() -> None:
    config = json.loads((PROJECT_ROOT / "config" / "assets.json").read_text(encoding="utf-8"))
    assets = [Asset(**item) for item in config["assets"]]
    database_path = PROJECT_ROOT / "data" / "fundos_demo.sqlite3"
    database_path.parent.mkdir(exist_ok=True)
    database = Database(database_path)
    database.initialize()
    database.upsert_assets(assets)

    product = PortfolioProduct(
        product_id="fundos-index-allocation",
        name="FundOS 场外指数基金配置组合",
        benchmark_symbol=config["benchmark"],
        created_at=datetime.now(),
    )
    existing = database.fetch_all("SELECT product_id FROM portfolio_products WHERE product_id = ?", (product.product_id,))
    if not existing:
        database.create_product(product)
    database.upsert_investment_mandate(
        InvestmentMandate(
            product.product_id,
            "中长期多资产稳健增值",
            "medium",
            Decimal("0.35"),
            Decimal("0.05"),
            Decimal("0.30"),
        )
    )
    version_rows = database.fetch_all(
        "SELECT status FROM portfolio_versions WHERE version_id = ?",
        ("fundos-index-allocation-v1",),
    )
    if not version_rows:
        database.create_version(
            PortfolioVersion(
                version_id="fundos-index-allocation-v1",
                product_id=product.product_id,
                version_number=1,
                effective_date=date.today(),
                weights=(
                    PositionWeight("CSI300", Decimal("0.25")),
                    PositionWeight("CSI500", Decimal("0.10")),
                    PositionWeight("NASDAQ100", Decimal("0.15")),
                    PositionWeight("GOLD", Decimal("0.15")),
                    PositionWeight("BOND", Decimal("0.30")),
                    PositionWeight("CASH", Decimal("0.05")),
                ),
            )
        )
        version_rows = database.fetch_all(
            "SELECT status FROM portfolio_versions WHERE version_id = ?",
            ("fundos-index-allocation-v1",),
        )
    if version_rows[0]["status"] == "draft":
        publish_portfolio_version(
            database,
            version_id="fundos-index-allocation-v1",
            reason="发布初始战略资产配置",
            approved_by="initial-committee",
        )

    sample_returns = {
        "CSI300": [0.010, -0.006, 0.008],
        "CSI500": [0.013, -0.009, 0.011],
        "NASDAQ100": [0.016, -0.012, 0.014],
        "GOLD": [0.004, 0.006, -0.002],
        "BOND": [0.002, 0.001, 0.002],
        "CASH": [0.0001, 0.0001, 0.0001],
    }
    weights = {"CSI300": 0.25, "CSI500": 0.10, "NASDAQ100": 0.15, "GOLD": 0.15, "BOND": 0.30, "CASH": 0.05}
    portfolio_returns = calculate_portfolio_returns(sample_returns, weights)
    metrics = calculate_metrics(portfolio_returns)
    print(f"数据库: {database_path}")
    print(f"组合净值: {calculate_nav(portfolio_returns)[-1]:.6f}")
    print(f"累计收益: {metrics.cumulative_return:.4%}")
    print(f"最大回撤: {metrics.maximum_drawdown:.4%}")


if __name__ == "__main__":
    main()
