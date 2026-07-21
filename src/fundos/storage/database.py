import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fundos.domain.models import Asset, InvestmentMandate, PortfolioProduct, PortfolioVersion, PositionWeight
from fundos.analytics.time_series import DatedNav, DatedPrice


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
    published_at TEXT NOT NULL
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
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(portfolio_versions)")}
            if "status" not in columns:
                connection.execute("ALTER TABLE portfolio_versions ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
            if "published_at" not in columns:
                connection.execute("ALTER TABLE portfolio_versions ADD COLUMN published_at TEXT")
            mandate_columns = {row[1] for row in connection.execute("PRAGMA table_info(investment_mandates)")}
            if "maximum_data_age_days" not in mandate_columns:
                connection.execute("ALTER TABLE investment_mandates ADD COLUMN maximum_data_age_days INTEGER NOT NULL DEFAULT 3")
            if "maximum_stress_loss" not in mandate_columns:
                connection.execute("ALTER TABLE investment_mandates ADD COLUMN maximum_stress_loss REAL NOT NULL DEFAULT 0.20")
            proposal_columns = {row[1] for row in connection.execute("PRAGMA table_info(portfolio_proposals)")}
            if "research_report_id" not in proposal_columns:
                connection.execute("ALTER TABLE portfolio_proposals ADD COLUMN research_report_id TEXT")

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
