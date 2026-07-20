import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fundos.domain.models import Asset, PortfolioProduct, PortfolioVersion
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

CREATE TABLE IF NOT EXISTS portfolio_versions (
    version_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES portfolio_products(product_id),
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    effective_date TEXT NOT NULL,
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

    def fetch_all(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(query, parameters).fetchall())
