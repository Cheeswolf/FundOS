from dataclasses import dataclass

from fundos.analytics import align_prices, calculate_metrics, calculate_versioned_nav, normalize_benchmark_nav
from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class PerformanceResult:
    portfolio_nav: float
    benchmark_nav: float
    cumulative_return: float
    benchmark_return: float
    excess_return: float


def calculate_and_store_versioned_performance(
    database: Database,
    *,
    product_id: str,
    provider: str,
    benchmark_symbol: str,
    periods_per_year: int = 252,
    annual_risk_free_rate: float = 0.0,
) -> PerformanceResult:
    versions = database.get_portfolio_versions(product_id, published_only=True)
    portfolio_symbols = sorted({position.asset_symbol for version in versions for position in version.weights})
    all_symbols = [*portfolio_symbols, benchmark_symbol]
    dates, aligned = align_prices(database.get_prices(provider, all_symbols), all_symbols)
    portfolio_nav = calculate_versioned_nav(
        dates,
        {symbol: aligned[symbol] for symbol in portfolio_symbols},
        versions,
    )
    benchmark_nav = normalize_benchmark_nav(dates, aligned[benchmark_symbol])
    portfolio_returns = [
        portfolio_nav[index].nav / portfolio_nav[index - 1].nav - 1
        for index in range(1, len(portfolio_nav))
    ]
    metrics = calculate_metrics(
        portfolio_returns,
        periods_per_year=periods_per_year,
        annual_risk_free_rate=annual_risk_free_rate,
    )
    benchmark_return = benchmark_nav[-1].nav / benchmark_nav[0].nav - 1
    database.upsert_portfolio_nav(product_id, portfolio_nav)
    database.upsert_performance_snapshot(
        product_id,
        dates[-1],
        cumulative_return=metrics.cumulative_return,
        benchmark_return=benchmark_return,
        annualized_return=metrics.annualized_return,
        annualized_volatility=metrics.annualized_volatility,
        maximum_drawdown=metrics.maximum_drawdown,
        sharpe_ratio=metrics.sharpe_ratio,
    )
    return PerformanceResult(
        portfolio_nav[-1].nav,
        benchmark_nav[-1].nav,
        metrics.cumulative_return,
        benchmark_return,
        metrics.cumulative_return - benchmark_return,
    )
