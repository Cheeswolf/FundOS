from datetime import date
from typing import Sequence

from fundos.analytics.time_series import DatedNav
from fundos.domain.models import PortfolioVersion


def calculate_versioned_nav(
    price_dates: Sequence[date],
    aligned_prices: dict[str, list[float]],
    versions: Sequence[PortfolioVersion],
    *,
    initial_nav: float = 1.0,
) -> list[DatedNav]:
    """Use the latest version effective at the start of each return period."""
    if len(price_dates) < 2:
        raise ValueError("at least two price dates are required")
    if not versions:
        raise ValueError("at least one portfolio version is required")
    if any(len(values) != len(price_dates) for values in aligned_prices.values()):
        raise ValueError("each price series must match the price date count")

    ordered = sorted(versions, key=lambda item: (item.effective_date, item.version_number))
    if len({item.version_number for item in ordered}) != len(ordered):
        raise ValueError("portfolio version numbers must be unique")

    nav = [DatedNav(price_dates[0], initial_nav)]
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
        nav.append(DatedNav(price_dates[index], nav[-1].nav * (1 + periodic_return)))
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

