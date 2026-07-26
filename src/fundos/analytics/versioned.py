from datetime import date
from dataclasses import dataclass
from typing import Sequence

from fundos.analytics.time_series import DatedNav
from fundos.domain.models import PortfolioVersion


@dataclass(frozen=True, slots=True)
class TransactionCostPolicy:
    """Costs charged when a new target-weight version becomes effective.

    ``rate`` is expressed as a fraction of one-way turnover.  For example,
    10 basis points is ``0.001``.  The initial allocation is free by default
    because a backtest normally starts after the portfolio has been funded.
    """

    rate: float = 0.0
    charge_initial_allocation: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.rate < 1:
            raise ValueError("transaction cost rate must be between 0 and 1")


def _turnover(previous: PortfolioVersion | None, current: PortfolioVersion) -> float:
    if previous is None:
        return 1.0
    previous_weights = {item.asset_symbol: float(item.weight) for item in previous.weights}
    current_weights = {item.asset_symbol: float(item.weight) for item in current.weights}
    symbols = previous_weights.keys() | current_weights.keys()
    return sum(
        abs(current_weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0))
        for symbol in symbols
    ) / 2


def calculate_versioned_nav(
    price_dates: Sequence[date],
    aligned_prices: dict[str, list[float]],
    versions: Sequence[PortfolioVersion],
    *,
    initial_nav: float = 1.0,
    transaction_cost_policy: TransactionCostPolicy | None = None,
) -> list[DatedNav]:
    """Use the latest version effective at the start of each return period.

    A version dated on a common valuation date applies to the return period
    beginning on that date.  Its one-off transaction cost is therefore
    deducted from NAV on that date, before the new period begins.
    """
    if len(price_dates) < 2:
        raise ValueError("at least two price dates are required")
    if not versions:
        raise ValueError("at least one portfolio version is required")
    if any(len(values) != len(price_dates) for values in aligned_prices.values()):
        raise ValueError("each price series must match the price date count")
    if initial_nav <= 0:
        raise ValueError("initial NAV must be positive")

    ordered = sorted(versions, key=lambda item: (item.effective_date, item.version_number))
    if len({item.version_number for item in ordered}) != len(ordered):
        raise ValueError("portfolio version numbers must be unique")

    policy = transaction_cost_policy or TransactionCostPolicy()
    initially_eligible = [version for version in ordered if version.effective_date <= price_dates[0]]
    initial_version = initially_eligible[-1] if initially_eligible else None
    initial_cost = (
        _turnover(None, initial_version) * policy.rate
        if initial_version is not None and policy.charge_initial_allocation
        else 0.0
    )
    nav = [DatedNav(price_dates[0], initial_nav * (1 - initial_cost))]
    for index in range(1, len(price_dates)):
        period_start = price_dates[index - 1]
        eligible = [version for version in ordered if version.effective_date <= period_start]
        if not eligible:
            raise ValueError(f"no portfolio version effective on {period_start}")
        periodic_return = 0.0
        for position in eligible[-1].weights:
            if position.asset_symbol not in aligned_prices:
                raise ValueError(f"missing prices for {position.asset_symbol}")
            previous = aligned_prices[position.asset_symbol][index - 1]
            current = aligned_prices[position.asset_symbol][index]
            if previous <= 0 or current <= 0:
                raise ValueError("prices must be positive")
            periodic_return += (current / previous - 1) * float(position.weight)
        value = nav[-1].nav * (1 + periodic_return)

        next_eligible = [
            version for version in ordered
            if version.effective_date <= price_dates[index]
        ]
        active_version = eligible[-1]
        next_version = next_eligible[-1] if next_eligible else None
        if next_version is not None and next_version.version_id != active_version.version_id:
            value *= 1 - _turnover(active_version, next_version) * policy.rate
        nav.append(DatedNav(price_dates[index], value))
    return nav


def normalize_benchmark_nav(
    price_dates: Sequence[date],
    prices: Sequence[float],
    *,
    initial_nav: float = 1.0,
) -> list[DatedNav]:
    if len(price_dates) != len(prices) or not prices:
        raise ValueError("benchmark dates and prices must have the same non-zero length")
    if initial_nav <= 0 or any(price <= 0 for price in prices):
        raise ValueError("NAV and benchmark prices must be positive")
    base = prices[0]
    return [DatedNav(nav_date, initial_nav * price / base) for nav_date, price in zip(price_dates, prices, strict=True)]
