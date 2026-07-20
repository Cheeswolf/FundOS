from datetime import date
from typing import Mapping

from fundos.analytics import align_prices, calculate_portfolio_returns, prices_to_returns, returns_to_dated_nav
from fundos.analytics.time_series import DatedNav
from fundos.storage import Database


def calculate_and_store_nav(
    database: Database,
    *,
    product_id: str,
    provider: str,
    weights: Mapping[str, float],
    start_date: date | None = None,
    end_date: date | None = None,
    initial_nav: float = 1.0,
) -> list[DatedNav]:
    prices = database.get_prices(
        provider,
        weights,
        start_date=start_date,
        end_date=end_date,
    )
    dates, aligned_prices = align_prices(prices, tuple(weights))
    asset_returns = prices_to_returns(aligned_prices)
    portfolio_returns = calculate_portfolio_returns(asset_returns, weights)
    nav = returns_to_dated_nav(dates, portfolio_returns, initial_nav=initial_nav)
    database.upsert_portfolio_nav(product_id, nav)
    return nav

