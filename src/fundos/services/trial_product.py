from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from fundos.analytics import align_prices
from fundos.domain import (
    InvestmentMandate,
    PortfolioProduct,
    PortfolioVersion,
    PositionWeight,
)
from fundos.services.publication import publish_portfolio_version
from fundos.services.versioned_performance import (
    PerformanceResult,
    calculate_and_store_versioned_performance,
)
from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class TrialProductResult:
    product_id: str
    version_id: str
    effective_date: str
    created_product: bool
    created_version: bool
    performance: PerformanceResult


def initialize_trial_product(
    database: Database,
    *,
    configuration: Mapping[str, Any],
    provider: str,
) -> TrialProductResult:
    product_config = configuration["product"]
    constraints = configuration["constraints"]
    allocation = configuration["strategic_allocation"]
    benchmark_symbol = configuration["benchmark"]["symbol"]
    product_id = product_config["product_id"]
    version_id = f"{product_id}-v1"
    weights = {
        item["symbol"]: Decimal(str(item["target"]))
        for item in allocation
    }
    required_symbols = [*weights, benchmark_symbol]
    dates, _ = align_prices(
        database.get_prices(provider, required_symbols),
        required_symbols,
    )
    effective_date = dates[0]

    existing_products = database.fetch_all(
        "SELECT * FROM portfolio_products WHERE product_id = ?",
        (product_id,),
    )
    created_product = not existing_products
    mandate = InvestmentMandate(
        product_id,
        product_config["objective"],
        product_config["risk_level"],
        Decimal(str(constraints["maximum_single_asset_weight"])),
        Decimal(str(constraints["minimum_cash_weight"])),
        Decimal(str(constraints["maximum_turnover_per_rebalance"])),
        constraints["maximum_data_age_days"],
        Decimal(str(constraints["maximum_stress_loss"])),
    )
    if created_product:
        database.create_product_with_mandate(
            PortfolioProduct(
                product_id,
                product_config["name"],
                benchmark_symbol,
                datetime.now(timezone.utc),
            ),
            mandate,
        )
    else:
        existing = existing_products[0]
        if (
            existing["name"] != product_config["name"]
            or existing["benchmark_symbol"] != benchmark_symbol
        ):
            raise ValueError("existing trial product does not match configuration")
        database.upsert_investment_mandate(mandate)

    versions = database.get_portfolio_versions(product_id)
    matching = [version for version in versions if version.version_id == version_id]
    created_version = not matching
    expected_weights = tuple(
        PositionWeight(symbol, weight)
        for symbol, weight in weights.items()
    )
    if created_version:
        database.create_version(
            PortfolioVersion(
                version_id,
                product_id,
                1,
                effective_date,
                expected_weights,
            )
        )
        publish_portfolio_version(
            database,
            version_id=version_id,
            reason="受控历史模拟的初始战略配置，不构成投资建议。",
            approved_by="FundOS 试运行初始化",
        )
    else:
        version = matching[0]
        if (
            version.version_number != 1
            or version.effective_date != effective_date
            or dict((item.asset_symbol, item.weight) for item in version.weights) != weights
        ):
            raise ValueError("existing initial trial version does not match configuration")

    performance = calculate_and_store_versioned_performance(
        database,
        product_id=product_id,
        provider=provider,
        benchmark_symbol=benchmark_symbol,
        transaction_cost_rate=_approved_transaction_cost_rate(configuration),
        charge_initial_allocation=_charge_initial_allocation(configuration),
    )
    return TrialProductResult(
        product_id,
        version_id,
        effective_date.isoformat(),
        created_product,
        created_version,
        performance,
    )


def _approved_transaction_cost_rate(configuration: Mapping[str, Any]) -> float:
    performance_policy = configuration.get("performance_policy", {})
    policy = performance_policy.get("rebalance_costs", {})
    if not isinstance(policy, Mapping):
        return 0.0
    rate_bps = policy.get("rate_bps")
    if policy.get("approval_status") != "approved" or rate_bps is None:
        return 0.0
    rate = float(rate_bps) / 10_000
    if not 0 <= rate < 1:
        raise ValueError("approved rebalance cost rate is invalid")
    return rate


def _charge_initial_allocation(configuration: Mapping[str, Any]) -> bool:
    performance_policy = configuration.get("performance_policy", {})
    policy = performance_policy.get("rebalance_costs", {})
    return bool(policy.get("charge_initial_allocation", False)) if isinstance(policy, Mapping) else False
