from typing import Mapping, Sequence


def calculate_linked_contributions(
    asset_returns: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
) -> dict[str, float]:
    """Geometrically link periodic contributions so they sum to cumulative return."""
    if set(asset_returns) != set(weights) or not asset_returns:
        raise ValueError("asset returns and weights must contain identical symbols")
    lengths = {len(values) for values in asset_returns.values()}
    if len(lengths) != 1 or next(iter(lengths)) == 0:
        raise ValueError("asset return series must have the same non-zero length")
    if abs(sum(weights.values()) - 1.0) > 1e-8:
        raise ValueError("weights must sum to 1")

    periods = next(iter(lengths))
    portfolio_returns = [
        sum(weights[symbol] * asset_returns[symbol][index] for symbol in weights)
        for index in range(periods)
    ]
    future_growth = [1.0] * periods
    multiplier = 1.0
    for index in range(periods - 1, -1, -1):
        future_growth[index] = multiplier
        multiplier *= 1 + portfolio_returns[index]
    return {
        symbol: sum(
            weights[symbol] * values[index] * future_growth[index]
            for index in range(periods)
        )
        for symbol, values in asset_returns.items()
    }


def evaluate_view(direction: str, realized_return: float, *, neutral_band: float = 0.02) -> bool:
    if neutral_band < 0:
        raise ValueError("neutral band cannot be negative")
    if direction == "positive":
        return realized_return > neutral_band
    if direction == "negative":
        return realized_return < -neutral_band
    if direction == "neutral":
        return abs(realized_return) <= neutral_band
    raise ValueError("unknown asset view direction")

