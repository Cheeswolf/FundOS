from dataclasses import dataclass
from typing import Mapping, Sequence

from fundos.analytics import (
    align_prices_asof,
    calculate_portfolio_returns,
    prices_to_returns,
    returns_to_dated_nav,
)
from fundos.domain import Asset
from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class TrialSeriesResult:
    source_rows: int
    cash_rows: int
    benchmark_rows: int
    first_date: str
    last_date: str
    valuation_dates: int
    carried_values: int
    maximum_age_days: int


def build_trial_valuation_series(
    database: Database,
    *,
    source_provider: str,
    target_provider: str,
    fund_symbols: Sequence[str],
    cash_symbol: str,
    benchmark_symbol: str,
    benchmark_weights: Mapping[str, float],
    maximum_carry_days: int = 14,
) -> TrialSeriesResult:
    if not fund_symbols or len(set(fund_symbols)) != len(fund_symbols):
        raise ValueError("fund symbols must be a non-empty unique sequence")
    if cash_symbol in fund_symbols or benchmark_symbol in {*fund_symbols, cash_symbol}:
        raise ValueError("cash and benchmark symbols must be distinct")
    if not benchmark_weights or not set(benchmark_weights).issubset(fund_symbols):
        raise ValueError("benchmark components must be selected fund symbols")

    source_prices = database.get_prices(source_provider, fund_symbols)
    available_symbols = {row.symbol for row in source_prices}
    missing = set(fund_symbols) - available_symbols
    if missing:
        raise ValueError(f"missing source prices for: {', '.join(sorted(missing))}")

    database.upsert_assets([
        Asset(cash_symbol, "人民币现金账本", "cash"),
        Asset(benchmark_symbol, "受控试运行60/40复合基准", "benchmark"),
    ])
    dates, aligned, quality = align_prices_asof(
        source_prices,
        tuple(fund_symbols),
        maximum_age_days=maximum_carry_days,
    )
    source_rows = database.upsert_prices(
        (target_provider, symbol, valuation_date, aligned[symbol][index])
        for index, valuation_date in enumerate(dates)
        for symbol in fund_symbols
    )

    cash_rows = database.upsert_prices(
        (target_provider, cash_symbol, trade_date, 1.0)
        for trade_date in dates
    )

    returns = calculate_portfolio_returns(
        prices_to_returns({
            symbol: aligned[symbol]
            for symbol in benchmark_weights
        }),
        benchmark_weights,
    )
    benchmark_nav = returns_to_dated_nav(dates, returns)
    benchmark_rows = database.upsert_prices(
        (target_provider, benchmark_symbol, row.nav_date, row.nav)
        for row in benchmark_nav
    )
    return TrialSeriesResult(
        source_rows,
        cash_rows,
        benchmark_rows,
        dates[0].isoformat(),
        dates[-1].isoformat(),
        quality.observation_dates,
        quality.carried_values,
        quality.maximum_age_days,
    )
