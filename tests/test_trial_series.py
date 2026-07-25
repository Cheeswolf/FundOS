import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain import Asset  # noqa: E402
from fundos.services import build_trial_valuation_series  # noqa: E402
from fundos.storage import Database  # noqa: E402


class TrialSeriesTests(unittest.TestCase):
    def test_builds_cash_and_daily_rebalanced_benchmark_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            symbols = ["CSI300", "NASDAQ100", "BOND"]
            database.upsert_assets(
                Asset(symbol, symbol, "equity" if symbol != "BOND" else "fixed_income")
                for symbol in symbols
            )
            dates = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
            values = {
                "CSI300": [100, 110, 110],
                "NASDAQ100": [100, 100, 120],
                "BOND": [100, 102, 104.04],
            }
            database.upsert_prices(
                ("source", symbol, trade_date, close)
                for symbol, closes in values.items()
                for trade_date, close in zip(dates, closes, strict=True)
            )

            arguments = {
                "source_provider": "source",
                "target_provider": "trial",
                "fund_symbols": symbols,
                "cash_symbol": "CASH",
                "benchmark_symbol": "BM",
                "benchmark_weights": {
                    "CSI300": 0.4,
                    "NASDAQ100": 0.2,
                    "BOND": 0.4,
                },
            }
            first = build_trial_valuation_series(database, **arguments)
            second = build_trial_valuation_series(database, **arguments)
            self.assertEqual(first, second)

            cash = database.get_prices("trial", ["CASH"])
            self.assertEqual([row.close for row in cash], [1.0, 1.0, 1.0])
            benchmark = database.get_prices("trial", ["BM"])
            self.assertEqual(len(benchmark), 3)
            self.assertAlmostEqual(benchmark[0].close, 1.0)
            self.assertAlmostEqual(benchmark[1].close, 1.048)
            self.assertAlmostEqual(benchmark[2].close, 1.098304)
            self.assertEqual(
                len(database.get_prices("trial", [*symbols, "CASH", "BM"])),
                15,
            )

    def test_rejects_missing_source_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.upsert_assets([Asset("A", "A", "equity")])
            database.upsert_prices([("source", "A", date(2026, 7, 1), 1.0)])
            with self.assertRaisesRegex(ValueError, "missing source prices for: B"):
                build_trial_valuation_series(
                    database,
                    source_provider="source",
                    target_provider="trial",
                    fund_symbols=["A", "B"],
                    cash_symbol="CASH",
                    benchmark_symbol="BM",
                    benchmark_weights={"A": 1.0},
                )


if __name__ == "__main__":
    unittest.main()
