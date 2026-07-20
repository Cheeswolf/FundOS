from dataclasses import dataclass
from math import sqrt
from statistics import stdev
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    cumulative_return: float
    annualized_return: float
    annualized_volatility: float
    maximum_drawdown: float
    sharpe_ratio: float | None
    observations: int


def _validate_returns(returns: Sequence[float]) -> None:
    if not returns:
        raise ValueError("returns cannot be empty")
    if any(value <= -1 for value in returns):
        raise ValueError("a periodic return cannot be less than or equal to -100%")


def calculate_portfolio_returns(
    asset_returns: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
    *,
    tolerance: float = 1e-8,
) -> list[float]:
    if not weights:
        raise ValueError("weights cannot be empty")
    if any(weight < 0 or weight > 1 for weight in weights.values()):
        raise ValueError("weights must be between 0 and 1")
    if abs(sum(weights.values()) - 1.0) > tolerance:
        raise ValueError("weights must sum to 1")
    if set(weights) != set(asset_returns):
        raise ValueError("asset returns and weights must contain identical symbols")

    lengths = {len(values) for values in asset_returns.values()}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise ValueError("all assets must contain the same non-zero number of returns")

    return [
        sum(asset_returns[symbol][index] * weight for symbol, weight in weights.items())
        for index in range(next(iter(lengths)))
    ]


def calculate_nav(returns: Sequence[float], *, initial_nav: float = 1.0) -> list[float]:
    _validate_returns(returns)
    if initial_nav <= 0:
        raise ValueError("initial NAV must be positive")

    nav = [initial_nav]
    for periodic_return in returns:
        nav.append(nav[-1] * (1 + periodic_return))
    return nav


def calculate_maximum_drawdown(nav: Sequence[float]) -> float:
    if not nav or any(value <= 0 for value in nav):
        raise ValueError("NAV values must be positive")
    peak = nav[0]
    maximum_drawdown = 0.0
    for value in nav:
        peak = max(peak, value)
        maximum_drawdown = min(maximum_drawdown, value / peak - 1)
    return maximum_drawdown


def calculate_metrics(
    returns: Sequence[float],
    *,
    periods_per_year: int = 252,
    annual_risk_free_rate: float = 0.0,
) -> PerformanceMetrics:
    _validate_returns(returns)
    if periods_per_year <= 0:
        raise ValueError("periods per year must be positive")

    nav = calculate_nav(returns)
    cumulative_return = nav[-1] / nav[0] - 1
    years = len(returns) / periods_per_year
    annualized_return = (nav[-1] / nav[0]) ** (1 / years) - 1
    annualized_volatility = stdev(returns) * sqrt(periods_per_year) if len(returns) > 1 else 0.0
    sharpe_ratio = None
    if annualized_volatility > 0:
        sharpe_ratio = (annualized_return - annual_risk_free_rate) / annualized_volatility

    return PerformanceMetrics(
        cumulative_return=cumulative_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        maximum_drawdown=calculate_maximum_drawdown(nav),
        sharpe_ratio=sharpe_ratio,
        observations=len(returns),
    )

