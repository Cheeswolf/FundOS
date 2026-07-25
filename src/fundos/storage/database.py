import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fundos.domain.models import Asset, InvestmentMandate, PortfolioProduct, PortfolioVersion, PositionWeight
from fundos.analytics.time_series import DatedNav, DatedPrice
from fundos.storage.migrations import apply_migrations


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS assets (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_class TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_prices (
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL REFERENCES assets(symbol),
    trade_date TEXT NOT NULL,
    close REAL NOT NULL CHECK (close > 0),
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider, symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS market_data_observations (
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    symbol TEXT NOT NULL REFERENCES assets(symbol),
    valuation_date TEXT NOT NULL,
    announced_date TEXT NOT NULL,
    available_date TEXT NOT NULL,
    value_field TEXT NOT NULL,
    raw_value REAL NOT NULL CHECK (raw_value > 0),
    normalized_value REAL NOT NULL CHECK (normalized_value > 0),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    retrieved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider, provider_symbol, valuation_date)
);

CREATE TABLE IF NOT EXISTS portfolio_products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    benchmark_symbol TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investment_mandates (
    product_id TEXT PRIMARY KEY REFERENCES portfolio_products(product_id),
    objective TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    max_single_asset_weight REAL NOT NULL CHECK (max_single_asset_weight BETWEEN 0 AND 1),
    min_cash_weight REAL NOT NULL CHECK (min_cash_weight BETWEEN 0 AND 1),
    max_turnover REAL NOT NULL CHECK (max_turnover BETWEEN 0 AND 1),
    maximum_data_age_days INTEGER NOT NULL DEFAULT 3 CHECK (maximum_data_age_days >= 0),
    maximum_stress_loss REAL NOT NULL DEFAULT 0.20 CHECK (maximum_stress_loss BETWEEN 0 AND 1),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_versions (
    version_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    effective_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'cancelled')),
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, version_number)
);

CREATE TABLE IF NOT EXISTS portfolio_version_weights (
    version_id TEXT NOT NULL REFERENCES portfolio_versions(version_id),
    asset_symbol TEXT NOT NULL REFERENCES assets(symbol),
    weight REAL NOT NULL CHECK (weight >= 0 AND weight <= 1),
    PRIMARY KEY (version_id, asset_symbol)
);

CREATE TABLE IF NOT EXISTS portfolio_nav (
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    nav_date TEXT NOT NULL,
    nav REAL NOT NULL CHECK (nav > 0),
    PRIMARY KEY (product_id, nav_date)
);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    as_of_date TEXT NOT NULL,
    cumulative_return REAL NOT NULL,
    benchmark_return REAL NOT NULL,
    excess_return REAL NOT NULL,
    annualized_return REAL NOT NULL,
    annualized_volatility REAL NOT NULL,
    maximum_drawdown REAL NOT NULL,
    sharpe_ratio REAL,
    PRIMARY KEY (product_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS rebalance_records (
    rebalance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    previous_version_id TEXT REFERENCES portfolio_versions(version_id),
    new_version_id TEXT NOT NULL REFERENCES portfolio_versions(version_id),
    effective_date TEXT NOT NULL,
    turnover REAL NOT NULL CHECK (turnover >= 0),
    reason TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    version_id TEXT NOT NULL UNIQUE REFERENCES portfolio_versions(version_id),
    state TEXT NOT NULL CHECK (state IN ('proposed', 'risk_passed', 'rejected', 'approved', 'published')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research_reports (
    report_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    as_of_date TEXT NOT NULL,
    market_regime TEXT NOT NULL,
    summary TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'final')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finalized_at TEXT
);

CREATE TABLE IF NOT EXISTS research_evidence (
    evidence_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES research_reports(report_id),
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS research_evidence_sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('official', 'licensed', 'internal')),
    allowed_domains TEXT NOT NULL,
    asset_symbols TEXT NOT NULL,
    license_note TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_research_evidence (
    raw_evidence_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES research_evidence_sources(source_id),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    asset_symbols TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    UNIQUE (source_id, url, content_sha256)
);

CREATE TABLE IF NOT EXISTS asset_views (
    view_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL REFERENCES research_reports(report_id),
    asset_symbol TEXT NOT NULL REFERENCES assets(symbol),
    direction TEXT NOT NULL CHECK (direction IN ('negative', 'neutral', 'positive')),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    thesis TEXT NOT NULL,
    UNIQUE (report_id, asset_symbol)
);

CREATE TABLE IF NOT EXISTS asset_view_evidence (
    view_id INTEGER NOT NULL REFERENCES asset_views(view_id),
    evidence_id TEXT NOT NULL REFERENCES research_evidence(evidence_id),
    PRIMARY KEY (view_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS portfolio_proposals (
    proposal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(run_id),
    research_report_id TEXT REFERENCES research_reports(report_id),
    rationale TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_checks (
    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
    rule_code TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    severity TEXT NOT NULL CHECK (severity IN ('hard', 'soft')),
    actual_value REAL,
    limit_value REAL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS committee_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(run_id),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    rationale TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_reports (
    review_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    research_report_id TEXT NOT NULL REFERENCES research_reports(report_id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    actual_return REAL NOT NULL,
    counterfactual_return REAL NOT NULL,
    rebalance_effect REAL NOT NULL,
    summary TEXT NOT NULL,
    lessons TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS review_contributions (
    review_id TEXT NOT NULL REFERENCES review_reports(review_id),
    asset_symbol TEXT NOT NULL REFERENCES assets(symbol),
    contribution REAL NOT NULL,
    PRIMARY KEY (review_id, asset_symbol)
);

CREATE TABLE IF NOT EXISTS research_view_outcomes (
    review_id TEXT NOT NULL REFERENCES review_reports(review_id),
    asset_symbol TEXT NOT NULL REFERENCES assets(symbol),
    direction TEXT NOT NULL,
    realized_return REAL NOT NULL,
    was_correct INTEGER NOT NULL CHECK (was_correct IN (0, 1)),
    PRIMARY KEY (review_id, asset_symbol)
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operations_cycles (
    cycle_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    as_of_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('healthy', 'attention_required', 'blocked')),
    latest_data_date TEXT,
    data_age_days INTEGER,
    performance_updated INTEGER NOT NULL CHECK (performance_updated IN (0, 1)),
    weekly_decision_due INTEGER NOT NULL CHECK (weekly_decision_due IN (0, 1)),
    monthly_review_due INTEGER NOT NULL CHECK (monthly_review_due IN (0, 1)),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    as_of_date TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    price_rows_written INTEGER NOT NULL DEFAULT 0,
    successful_steps INTEGER NOT NULL DEFAULT 0,
    failed_steps INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_steps (
    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    step_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'blocked')),
    attempts INTEGER NOT NULL CHECK (attempts > 0),
    rows_written INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, step_name)
);

CREATE TABLE IF NOT EXISTS alert_events (
    alert_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('warning', 'critical')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'failed')),
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TEXT,
    UNIQUE (source_type, source_id)
);

CREATE TABLE IF NOT EXISTS model_calls (
    call_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    attempts INTEGER NOT NULL CHECK (attempts > 0),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd REAL CHECK (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0),
    prompt_sha256 TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_circuit_resets (
    reset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    reset_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    reset_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_lifecycle (
    alert_id TEXT PRIMARY KEY REFERENCES alert_events(alert_id),
    state TEXT NOT NULL CHECK (state IN ('acknowledged', 'resolved')),
    updated_by TEXT NOT NULL,
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_audit_events (
    audit_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'rejected', 'failed')),
    status_code INTEGER NOT NULL,
    client_ip TEXT,
    created_at TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS audit_retention_anchors (
    anchor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cutoff_at TEXT NOT NULL,
    anchor_audit_id TEXT NOT NULL,
    anchor_hash TEXT NOT NULL,
    deleted_count INTEGER NOT NULL CHECK (deleted_count > 0),
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            apply_migrations(connection, SCHEMA)

    def get_schema_version(self) -> int:
        rows = self.fetch_all("SELECT MAX(version) AS version FROM schema_migrations")
        return int(rows[0]["version"] or 0)

    def upsert_assets(self, assets: Iterable[Asset]) -> None:
        rows = [(asset.symbol, asset.name, asset.asset_class) for asset in assets]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO assets (symbol, name, asset_class) VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    asset_class = excluded.asset_class
                """,
                rows,
            )

    def upsert_prices(self, rows: Iterable[tuple[str, str, date, float]]) -> int:
        values = [(provider, symbol, trade_date.isoformat(), close) for provider, symbol, trade_date, close in rows]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO market_prices (provider, symbol, trade_date, close)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider, symbol, trade_date) DO UPDATE SET close = excluded.close
                """,
                values,
            )
        return len(values)

    def upsert_market_data_observations(
        self,
        rows: Iterable[tuple[str, str, str, date, date, date, str, float, float]],
    ) -> int:
        values = [
            (
                provider,
                provider_symbol,
                symbol,
                valuation_date.isoformat(),
                announced_date.isoformat(),
                available_date.isoformat(),
                value_field,
                raw_value,
                normalized_value,
            )
            for (
                provider,
                provider_symbol,
                symbol,
                valuation_date,
                announced_date,
                available_date,
                value_field,
                raw_value,
                normalized_value,
            ) in rows
        ]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO market_data_observations (
                    provider, provider_symbol, symbol, valuation_date,
                    announced_date, available_date, value_field,
                    raw_value, normalized_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_symbol, valuation_date) DO UPDATE SET
                    symbol = excluded.symbol,
                    announced_date = excluded.announced_date,
                    available_date = excluded.available_date,
                    value_field = excluded.value_field,
                    raw_value = excluded.raw_value,
                    normalized_value = excluded.normalized_value,
                    revision = market_data_observations.revision + CASE
                        WHEN market_data_observations.announced_date != excluded.announced_date
                          OR market_data_observations.available_date != excluded.available_date
                          OR market_data_observations.value_field != excluded.value_field
                          OR market_data_observations.raw_value != excluded.raw_value
                          OR market_data_observations.normalized_value != excluded.normalized_value
                        THEN 1 ELSE 0 END,
                    retrieved_at = CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def get_prices(
        self,
        provider: str,
        symbols: Iterable[str],
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[DatedPrice]:
        symbol_list = tuple(symbols)
        if not symbol_list:
            raise ValueError("symbols cannot be empty")
        placeholders = ", ".join("?" for _ in symbol_list)
        conditions = ["provider = ?", f"symbol IN ({placeholders})"]
        parameters: list[Any] = [provider, *symbol_list]
        if start_date is not None:
            conditions.append("trade_date >= ?")
            parameters.append(start_date.isoformat())
        if end_date is not None:
            conditions.append("trade_date <= ?")
            parameters.append(end_date.isoformat())
        query = f"""
            SELECT symbol, trade_date, close
            FROM market_prices
            WHERE {' AND '.join(conditions)}
            ORDER BY trade_date, symbol
        """
        rows = self.fetch_all(query, tuple(parameters))
        return [
            DatedPrice(row["symbol"], date.fromisoformat(row["trade_date"]), row["close"])
            for row in rows
        ]

    def upsert_portfolio_nav(self, product_id: str, values: Iterable[DatedNav]) -> int:
        rows = [(product_id, item.nav_date.isoformat(), item.nav) for item in values]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO portfolio_nav (product_id, nav_date, nav)
                VALUES (?, ?, ?)
                ON CONFLICT(product_id, nav_date) DO UPDATE SET nav = excluded.nav
                """,
                rows,
            )
        return len(rows)

    def create_product(self, product: PortfolioProduct) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_products (product_id, name, benchmark_symbol, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (product.product_id, product.name, product.benchmark_symbol, product.created_at.isoformat()),
            )

    def create_product_with_mandate(
        self,
        product: PortfolioProduct,
        mandate: InvestmentMandate,
    ) -> None:
        if product.product_id != mandate.product_id:
            raise ValueError("product and investment mandate IDs must match")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_products (product_id, name, benchmark_symbol, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (product.product_id, product.name, product.benchmark_symbol, product.created_at.isoformat()),
            )
            connection.execute(
                """
                INSERT INTO investment_mandates (
                    product_id, objective, risk_level, max_single_asset_weight,
                    min_cash_weight, max_turnover, maximum_data_age_days, maximum_stress_loss
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mandate.product_id, mandate.objective, mandate.risk_level,
                    float(mandate.max_single_asset_weight), float(mandate.min_cash_weight),
                    float(mandate.max_turnover), mandate.maximum_data_age_days,
                    float(mandate.maximum_stress_loss),
                ),
            )

    def upsert_investment_mandate(self, mandate: InvestmentMandate) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO investment_mandates (
                    product_id, objective, risk_level, max_single_asset_weight,
                    min_cash_weight, max_turnover, maximum_data_age_days, maximum_stress_loss
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    objective = excluded.objective,
                    risk_level = excluded.risk_level,
                    max_single_asset_weight = excluded.max_single_asset_weight,
                    min_cash_weight = excluded.min_cash_weight,
                    max_turnover = excluded.max_turnover,
                    maximum_data_age_days = excluded.maximum_data_age_days,
                    maximum_stress_loss = excluded.maximum_stress_loss,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    mandate.product_id, mandate.objective, mandate.risk_level,
                    float(mandate.max_single_asset_weight), float(mandate.min_cash_weight),
                    float(mandate.max_turnover), mandate.maximum_data_age_days,
                    float(mandate.maximum_stress_loss),
                ),
            )

    def create_version(self, version: PortfolioVersion) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_versions
                    (version_id, product_id, version_number, effective_date)
                VALUES (?, ?, ?, ?)
                """,
                (version.version_id, version.product_id, version.version_number, version.effective_date.isoformat()),
            )
            connection.executemany(
                """
                INSERT INTO portfolio_version_weights (version_id, asset_symbol, weight)
                VALUES (?, ?, ?)
                """,
                [(version.version_id, item.asset_symbol, float(item.weight)) for item in version.weights],
            )

    def get_portfolio_versions(self, product_id: str, *, published_only: bool = False) -> list[PortfolioVersion]:
        status_clause = "AND v.status = 'published'" if published_only else ""
        rows = self.fetch_all(
            f"""
            SELECT v.version_id, v.product_id, v.version_number, v.effective_date,
                   w.asset_symbol, w.weight
            FROM portfolio_versions v
            JOIN portfolio_version_weights w ON w.version_id = v.version_id
            WHERE v.product_id = ?
            {status_clause}
            ORDER BY v.version_number, w.asset_symbol
            """,
            (product_id,),
        )
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = grouped.setdefault(
                row["version_id"],
                {
                    "version_id": row["version_id"],
                    "product_id": row["product_id"],
                    "version_number": row["version_number"],
                    "effective_date": date.fromisoformat(row["effective_date"]),
                    "weights": [],
                },
            )
            entry["weights"].append(PositionWeight(row["asset_symbol"], Decimal(str(row["weight"]))))
        return [
            PortfolioVersion(**{**entry, "weights": tuple(entry["weights"])})
            for entry in grouped.values()
        ]

    def upsert_performance_snapshot(
        self,
        product_id: str,
        as_of_date: date,
        *,
        cumulative_return: float,
        benchmark_return: float,
        annualized_return: float,
        annualized_volatility: float,
        maximum_drawdown: float,
        sharpe_ratio: float | None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO performance_snapshots (
                    product_id, as_of_date, cumulative_return, benchmark_return,
                    excess_return, annualized_return, annualized_volatility,
                    maximum_drawdown, sharpe_ratio
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id, as_of_date) DO UPDATE SET
                    cumulative_return = excluded.cumulative_return,
                    benchmark_return = excluded.benchmark_return,
                    excess_return = excluded.excess_return,
                    annualized_return = excluded.annualized_return,
                    annualized_volatility = excluded.annualized_volatility,
                    maximum_drawdown = excluded.maximum_drawdown,
                    sharpe_ratio = excluded.sharpe_ratio
                """,
                (
                    product_id, as_of_date.isoformat(), cumulative_return, benchmark_return,
                    cumulative_return - benchmark_return, annualized_return,
                    annualized_volatility, maximum_drawdown, sharpe_ratio,
                ),
            )

    def fetch_all(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(query, parameters).fetchall())
