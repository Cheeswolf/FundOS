from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from fundos.analytics import (
    align_prices,
    calculate_linked_contributions,
    calculate_nav,
    calculate_portfolio_returns,
    calculate_versioned_nav,
    evaluate_view,
    prices_to_returns,
)
from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class ReviewResult:
    review_id: str
    actual_return: float
    counterfactual_return: float
    rebalance_effect: float
    contributions: dict[str, float]
    view_outcomes: dict[str, bool]


def generate_review_report(
    database: Database,
    *,
    product_id: str,
    research_report_id: str,
    provider: str,
    period_start: date,
    period_end: date,
    summary: str,
    lessons: str,
    neutral_band: float = 0.02,
) -> ReviewResult:
    if period_end <= period_start:
        raise ValueError("review period end must be after its start")
    if not summary.strip() or not lessons.strip():
        raise ValueError("review summary and lessons are required")

    report_rows = database.fetch_all(
        "SELECT * FROM research_reports WHERE report_id = ? AND product_id = ? AND status = 'final'",
        (research_report_id, product_id),
    )
    if not report_rows:
        raise ValueError("a finalized research report for the product is required")
    if date.fromisoformat(report_rows[0]["as_of_date"]) > period_start:
        raise ValueError("research report cannot be dated after the review period starts")

    versions = [
        version for version in database.get_portfolio_versions(product_id, published_only=True)
        if version.effective_date <= period_end
    ]
    start_versions = [version for version in versions if version.effective_date <= period_start]
    if not start_versions:
        raise ValueError("no published portfolio version exists at the period start")
    start_version = max(start_versions, key=lambda item: (item.effective_date, item.version_number))
    view_rows = database.fetch_all(
        "SELECT asset_symbol, direction FROM asset_views WHERE report_id = ?",
        (research_report_id,),
    )
    portfolio_symbols = {position.asset_symbol for version in versions for position in version.weights}
    all_symbols = sorted(portfolio_symbols | {row["asset_symbol"] for row in view_rows})
    prices = database.get_prices(
        provider, all_symbols, start_date=period_start, end_date=period_end
    )
    dates, aligned = align_prices(prices, all_symbols)
    asset_returns = prices_to_returns(aligned)

    actual_nav = calculate_versioned_nav(
        dates,
        {symbol: aligned[symbol] for symbol in portfolio_symbols},
        versions,
    )
    start_weights = {position.asset_symbol: float(position.weight) for position in start_version.weights}
    start_asset_returns = {symbol: asset_returns[symbol] for symbol in start_weights}
    counterfactual_returns = calculate_portfolio_returns(start_asset_returns, start_weights)
    counterfactual_nav = calculate_nav(counterfactual_returns)
    actual_return = actual_nav[-1].nav / actual_nav[0].nav - 1
    counterfactual_return = counterfactual_nav[-1] / counterfactual_nav[0] - 1
    contributions = calculate_linked_contributions(start_asset_returns, start_weights)

    outcomes: dict[str, tuple[float, bool, str]] = {}
    for row in view_rows:
        values = aligned[row["asset_symbol"]]
        realized_return = values[-1] / values[0] - 1
        outcomes[row["asset_symbol"]] = (
            realized_return,
            evaluate_view(row["direction"], realized_return, neutral_band=neutral_band),
            row["direction"],
        )

    review_id = str(uuid4())
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO review_reports (
                review_id, product_id, research_report_id, period_start, period_end,
                actual_return, counterfactual_return, rebalance_effect, summary, lessons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id, product_id, research_report_id, period_start.isoformat(), period_end.isoformat(),
                actual_return, counterfactual_return, actual_return - counterfactual_return,
                summary.strip(), lessons.strip(),
            ),
        )
        connection.executemany(
            "INSERT INTO review_contributions (review_id, asset_symbol, contribution) VALUES (?, ?, ?)",
            [(review_id, symbol, contribution) for symbol, contribution in contributions.items()],
        )
        connection.executemany(
            """
            INSERT INTO research_view_outcomes
                (review_id, asset_symbol, direction, realized_return, was_correct)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (review_id, symbol, direction, realized_return, int(was_correct))
                for symbol, (realized_return, was_correct, direction) in outcomes.items()
            ],
        )
    return ReviewResult(
        review_id,
        actual_return,
        counterfactual_return,
        actual_return - counterfactual_return,
        contributions,
        {symbol: item[1] for symbol, item in outcomes.items()},
    )

