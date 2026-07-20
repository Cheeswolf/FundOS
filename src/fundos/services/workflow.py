from dataclasses import dataclass
from datetime import date
from typing import Mapping
from uuid import uuid4

from fundos.domain import PortfolioVersion
from fundos.services.publication import PublicationResult, publish_portfolio_version
from fundos.storage import Database


@dataclass(frozen=True, slots=True)
class RiskCheck:
    rule_code: str
    passed: bool
    severity: str
    actual_value: float | None
    limit_value: float | None
    message: str


@dataclass(frozen=True, slots=True)
class RiskReviewReport:
    run_id: str
    passed: bool
    hard_failure_count: int
    checks: tuple[RiskCheck, ...]

    def __iter__(self):
        return iter(self.checks)


def create_proposal(
    database: Database,
    *,
    version: PortfolioVersion,
    rationale: str,
    created_by: str,
    run_id: str | None = None,
) -> str:
    if not rationale.strip() or not created_by.strip():
        raise ValueError("proposal rationale and creator are required")
    run_id = run_id or str(uuid4())
    proposal_id = str(uuid4())
    with database.connect() as connection:
        product = connection.execute(
            "SELECT 1 FROM portfolio_products WHERE product_id = ?", (version.product_id,)
        ).fetchone()
        if product is None:
            raise ValueError("portfolio product does not exist")
        connection.execute(
            """
            INSERT INTO portfolio_versions
                (version_id, product_id, version_number, effective_date, status)
            VALUES (?, ?, ?, ?, 'draft')
            """,
            (version.version_id, version.product_id, version.version_number, version.effective_date.isoformat()),
        )
        connection.executemany(
            "INSERT INTO portfolio_version_weights (version_id, asset_symbol, weight) VALUES (?, ?, ?)",
            [(version.version_id, item.asset_symbol, float(item.weight)) for item in version.weights],
        )
        connection.execute(
            "INSERT INTO workflow_runs (run_id, product_id, version_id, state) VALUES (?, ?, ?, 'proposed')",
            (run_id, version.product_id, version.version_id),
        )
        connection.execute(
            "INSERT INTO portfolio_proposals (proposal_id, run_id, rationale, created_by) VALUES (?, ?, ?, ?)",
            (proposal_id, run_id, rationale.strip(), created_by.strip()),
        )
    return run_id


def run_risk_review(
    database: Database,
    *,
    run_id: str,
    provider: str,
    as_of_date: date,
    stress_scenarios: Mapping[str, Mapping[str, float]],
) -> RiskReviewReport:
    if not provider.strip():
        raise ValueError("market data provider is required")
    if not stress_scenarios:
        raise ValueError("at least one stress scenario is required")
    with database.connect() as connection:
        run = connection.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError("workflow run does not exist")
        if run["state"] != "proposed":
            raise ValueError("risk review requires a proposed workflow")
        mandate = connection.execute(
            "SELECT * FROM investment_mandates WHERE product_id = ?", (run["product_id"],)
        ).fetchone()
        if mandate is None:
            raise ValueError("investment mandate is required")
        rows = connection.execute(
            """
            SELECT w.asset_symbol, w.weight, a.asset_class
            FROM portfolio_version_weights w JOIN assets a ON a.symbol = w.asset_symbol
            WHERE w.version_id = ?
            """,
            (run["version_id"],),
        ).fetchall()
        weights = {row["asset_symbol"]: row["weight"] for row in rows}
        total = sum(weights.values())
        maximum = max(weights.values(), default=0.0)
        cash = sum(row["weight"] for row in rows if row["asset_class"] == "cash")
        previous = connection.execute(
            """
            SELECT version_id FROM portfolio_versions
            WHERE product_id = ? AND status = 'published'
            ORDER BY version_number DESC LIMIT 1
            """,
            (run["product_id"],),
        ).fetchone()
        turnover = 0.0
        if previous is not None:
            previous_weights = {
                row["asset_symbol"]: row["weight"]
                for row in connection.execute(
                    "SELECT asset_symbol, weight FROM portfolio_version_weights WHERE version_id = ?",
                    (previous["version_id"],),
                ).fetchall()
            }
            turnover = sum(
                abs(weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0))
                for symbol in set(weights) | set(previous_weights)
            ) / 2

        checks = [
            RiskCheck("WEIGHT_SUM", abs(total - 1.0) <= 0.0001, "hard", total, 1.0, "权重总和必须等于100%"),
            RiskCheck("SINGLE_ASSET_LIMIT", maximum <= mandate["max_single_asset_weight"] + 1e-12, "hard", maximum, mandate["max_single_asset_weight"], "单一资产权重不得超限"),
            RiskCheck("MIN_CASH", cash + 1e-12 >= mandate["min_cash_weight"], "hard", cash, mandate["min_cash_weight"], "现金仓位不得低于下限"),
            RiskCheck("MAX_TURNOVER", turnover <= mandate["max_turnover"] + 1e-12, "hard", turnover, mandate["max_turnover"], "组合换手率不得超限"),
        ]
        latest_dates = {
            row["symbol"]: date.fromisoformat(row["latest_date"])
            for row in connection.execute(
                f"""
                SELECT symbol, MAX(trade_date) AS latest_date
                FROM market_prices
                WHERE provider = ? AND symbol IN ({', '.join('?' for _ in weights)})
                  AND trade_date <= ?
                GROUP BY symbol
                """,
                (provider, *weights, as_of_date.isoformat()),
            ).fetchall()
        }
        missing_data = set(weights) - set(latest_dates)
        maximum_age = max(
            ((as_of_date - latest_dates[symbol]).days for symbol in latest_dates),
            default=None,
        )
        data_fresh = not missing_data and maximum_age is not None and maximum_age <= mandate["maximum_data_age_days"]
        data_message = "行情数据在允许时效内"
        if missing_data:
            data_message = f"缺少行情数据: {', '.join(sorted(missing_data))}"
        elif not data_fresh:
            data_message = "行情数据已过期"
        checks.append(
            RiskCheck(
                "DATA_FRESHNESS", data_fresh, "hard", maximum_age,
                mandate["maximum_data_age_days"], data_message,
            )
        )

        scenario_losses = {
            name: sum(weight * shocks.get(symbol, 0.0) for symbol, weight in weights.items())
            for name, shocks in stress_scenarios.items()
        }
        worst_name, worst_loss = min(scenario_losses.items(), key=lambda item: item[1])
        stress_passed = worst_loss >= -mandate["maximum_stress_loss"] - 1e-12
        checks.append(
            RiskCheck(
                "STRESS_LOSS", stress_passed, "hard", worst_loss,
                -mandate["maximum_stress_loss"], f"最差压力情景: {worst_name}",
            )
        )
        connection.execute("DELETE FROM risk_checks WHERE run_id = ?", (run_id,))
        connection.executemany(
            """
            INSERT INTO risk_checks
                (run_id, rule_code, passed, severity, actual_value, limit_value, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [(run_id, item.rule_code, int(item.passed), item.severity, item.actual_value, item.limit_value, item.message) for item in checks],
        )
        next_state = "risk_passed" if all(item.passed or item.severity == "soft" for item in checks) else "rejected"
        connection.execute(
            "UPDATE workflow_runs SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            (next_state, run_id),
        )
        failures = sum(1 for item in checks if item.severity == "hard" and not item.passed)
        return RiskReviewReport(run_id, failures == 0, failures, tuple(checks))


def record_committee_decision(
    database: Database,
    *,
    run_id: str,
    approved: bool,
    rationale: str,
    decided_by: str,
) -> str:
    if not rationale.strip() or not decided_by.strip():
        raise ValueError("decision rationale and decision maker are required")
    with database.connect() as connection:
        run = connection.execute("SELECT state FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError("workflow run does not exist")
        if run["state"] != "risk_passed":
            raise ValueError("committee decision requires passed risk review")
        decision = "approved" if approved else "rejected"
        connection.execute(
            "INSERT INTO committee_decisions (run_id, decision, rationale, decided_by) VALUES (?, ?, ?, ?)",
            (run_id, decision, rationale.strip(), decided_by.strip()),
        )
        connection.execute(
            "UPDATE workflow_runs SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            (decision, run_id),
        )
        return decision


def publish_approved_workflow(database: Database, *, run_id: str) -> PublicationResult:
    run = database.fetch_all("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,))
    if not run:
        raise ValueError("workflow run does not exist")
    if run[0]["state"] != "approved":
        raise ValueError("publication requires committee approval")
    decision = database.fetch_all("SELECT * FROM committee_decisions WHERE run_id = ?", (run_id,))[0]
    result = publish_portfolio_version(
        database,
        version_id=run[0]["version_id"],
        reason=decision["rationale"],
        approved_by=decision["decided_by"],
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE workflow_runs SET state = 'published', updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            (run_id,),
        )
    return result
