from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class DatedPrice:
    symbol: str
    trade_date: date
    close: float


@dataclass(frozen=True, slots=True)
class DatedNav:
    nav_date: date
    nav: float


@dataclass(frozen=True, slots=True)
class AlignmentQuality:
    observation_dates: int
    carried_values: int
    maximum_age_days: int


def align_prices(
    prices: Iterable[DatedPrice],
    symbols: Sequence[str],
) -> tuple[list[date], dict[str, list[float]]]:
    """Align prices using the intersection of dates available for every asset."""
    required_symbols = tuple(symbols)
    if not required_symbols or len(required_symbols) != len(set(required_symbols)):
        raise ValueError("symbols must be a non-empty unique sequence")

    by_symbol: dict[str, dict[date, float]] = defaultdict(dict)
    for item in prices:
        if item.symbol not in required_symbols:
            continue
        if item.close <= 0:
            raise ValueError("prices must be positive")
        if item.trade_date in by_symbol[item.symbol]:
            raise ValueError(f"duplicate price for {item.symbol} on {item.trade_date}")
        by_symbol[item.symbol][item.trade_date] = item.close

    missing_symbols = set(required_symbols) - set(by_symbol)
    if missing_symbols:
        raise ValueError(f"no prices for symbols: {', '.join(sorted(missing_symbols))}")

    common_dates = set(by_symbol[required_symbols[0]])
    for symbol in required_symbols[1:]:
        common_dates &= set(by_symbol[symbol])
    dates = sorted(common_dates)
    if len(dates) < 2:
        raise ValueError("at least two common price dates are required")

    aligned = {
        symbol: [by_symbol[symbol][trade_date] for trade_date in dates]
        for symbol in required_symbols
    }
    return dates, aligned


def align_prices_asof(
    prices: Iterable[DatedPrice],
    symbols: Sequence[str],
    *,
    maximum_age_days: int = 14,
) -> tuple[list[date], dict[str, list[float]], AlignmentQuality]:
    """Align asynchronous observations without using information from the future.

    Dates are drawn from the union of observations inside the period covered by
    every symbol.  Missing values are carried forward only when their age stays
    within ``maximum_age_days``.
    """
    required_symbols = tuple(symbols)
    if not required_symbols or len(required_symbols) != len(set(required_symbols)):
        raise ValueError("symbols must be a non-empty unique sequence")
    if maximum_age_days < 0:
        raise ValueError("maximum price age days cannot be negative")

    by_symbol: dict[str, dict[date, float]] = defaultdict(dict)
    for item in prices:
        if item.symbol not in required_symbols:
            continue
        if item.close <= 0:
            raise ValueError("prices must be positive")
        if item.trade_date in by_symbol[item.symbol]:
            raise ValueError(f"duplicate price for {item.symbol} on {item.trade_date}")
        by_symbol[item.symbol][item.trade_date] = item.close

    missing_symbols = set(required_symbols) - set(by_symbol)
    if missing_symbols:
        raise ValueError(f"no prices for symbols: {', '.join(sorted(missing_symbols))}")

    first_date = max(min(by_symbol[symbol]) for symbol in required_symbols)
    last_date = min(max(by_symbol[symbol]) for symbol in required_symbols)
    dates = sorted({
        observed_date
        for symbol in required_symbols
        for observed_date in by_symbol[symbol]
        if first_date <= observed_date <= last_date
    })
    if len(dates) < 2:
        raise ValueError("at least two aligned price dates are required")

    aligned = {symbol: [] for symbol in required_symbols}
    latest_dates: dict[str, date] = {
        symbol: max(
            observed_date
            for observed_date in by_symbol[symbol]
            if observed_date <= dates[0]
        )
        for symbol in required_symbols
    }
    carried_values = 0
    maximum_age = 0
    for valuation_date in dates:
        for symbol in required_symbols:
            if valuation_date in by_symbol[symbol]:
                latest_dates[symbol] = valuation_date
            source_date = latest_dates.get(symbol)
            if source_date is None:
                raise ValueError(f"no price available for {symbol} on {valuation_date}")
            age_days = (valuation_date - source_date).days
            if age_days > maximum_age_days:
                raise ValueError(
                    f"price for {symbol} is {age_days} days old on {valuation_date}"
                )
            aligned[symbol].append(by_symbol[symbol][source_date])
            if age_days:
                carried_values += 1
                maximum_age = max(maximum_age, age_days)
    return dates, aligned, AlignmentQuality(len(dates), carried_values, maximum_age)


def prices_to_returns(
    aligned_prices: Mapping[str, Sequence[float]],
) -> dict[str, list[float]]:
    if not aligned_prices:
        raise ValueError("aligned prices cannot be empty")
    lengths = {len(values) for values in aligned_prices.values()}
    if len(lengths) != 1 or next(iter(lengths)) < 2:
        raise ValueError("each asset must contain at least two aligned prices")
    if any(price <= 0 for values in aligned_prices.values() for price in values):
        raise ValueError("prices must be positive")

    return {
        symbol: [values[index] / values[index - 1] - 1 for index in range(1, len(values))]
        for symbol, values in aligned_prices.items()
    }


def returns_to_dated_nav(
    price_dates: Sequence[date],
    portfolio_returns: Sequence[float],
    *,
    initial_nav: float = 1.0,
) -> list[DatedNav]:
    if len(price_dates) != len(portfolio_returns) + 1:
        raise ValueError("price dates must contain one more item than returns")
    if sorted(price_dates) != list(price_dates) or len(set(price_dates)) != len(price_dates):
        raise ValueError("price dates must be unique and ascending")
    if initial_nav <= 0:
        raise ValueError("initial NAV must be positive")

    result = [DatedNav(price_dates[0], initial_nav)]
    for nav_date, periodic_return in zip(price_dates[1:], portfolio_returns, strict=True):
        if periodic_return <= -1:
            raise ValueError("a periodic return cannot be less than or equal to -100%")
        result.append(DatedNav(nav_date, result[-1].nav * (1 + periodic_return)))
    return result
