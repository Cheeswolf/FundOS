import json
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.data_providers import AlphaVantageError, PriceRow  # noqa: E402
from fundos.services import run_production_pipeline  # noqa: E402
from fundos.storage import Database  # noqa: E402


class FakeProvider:
    def __init__(self, *, fail_first_symbol: str | None = None, always_fail_symbol: str | None = None) -> None:
        self.fail_first_symbol = fail_first_symbol
        self.always_fail_symbol = always_fail_symbol
        self.attempts = defaultdict(int)

    def get_daily_prices(self, provider_symbol: str, *, internal_symbol: str, **kwargs):
        self.attempts[provider_symbol] += 1
        if provider_symbol == self.always_fail_symbol:
            raise AlphaVantageError("permanent provider failure")
        if provider_symbol == self.fail_first_symbol and self.attempts[provider_symbol] == 1:
            raise AlphaVantageError("temporary provider failure")
        return [
            PriceRow(internal_symbol, date(2026, 7, 9), 100.0),
            PriceRow(internal_symbol, date(2026, 7, 10), 101.0),
        ]


class ProductionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = Path(__file__).parents[1]
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "pipeline.sqlite3"
        subprocess.run(
            [sys.executable, str(self.project_root / "scripts" / "seed_demo.py"), "--database", str(self.database_path)],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        self.database = Database(self.database_path)
        self.market_config = json.loads(
            (self.project_root / "config" / "market_data.alpha_vantage.json").read_text(encoding="utf-8")
        )
        self.portfolios = [{
            "product_id": "fundos-demo-balanced",
            "benchmark_symbol": "BALANCED_BENCHMARK",
        }]

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_retries_then_completes_pipeline(self) -> None:
        provider = FakeProvider(fail_first_symbol="QQQ")
        result = run_production_pipeline(
            self.database,
            market_provider=provider,
            provider_name="real-fixture",
            symbol_mappings=self.market_config["symbols"],
            portfolios=self.portfolios,
            as_of_date=date(2026, 7, 10),
            retry_delay_seconds=0,
            sleep=lambda _: None,
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(provider.attempts["QQQ"], 2)
        self.assertEqual(result.price_rows_written, 14)
        self.assertEqual(result.failed_steps, 0)
        self.assertEqual(len(self.database.fetch_all("SELECT * FROM pipeline_steps")), 8)

    def test_records_partial_pipeline_when_symbol_never_recovers(self) -> None:
        provider = FakeProvider(always_fail_symbol="ASHS")
        result = run_production_pipeline(
            self.database,
            market_provider=provider,
            provider_name="partial-fixture",
            symbol_mappings=self.market_config["symbols"],
            portfolios=self.portfolios,
            as_of_date=date(2026, 7, 10),
            max_attempts=2,
            retry_delay_seconds=0,
            sleep=lambda _: None,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(provider.attempts["ASHS"], 2)
        self.assertGreaterEqual(result.failed_steps, 2)
        run = self.database.fetch_all("SELECT * FROM pipeline_runs WHERE run_id = ?", (result.run_id,))[0]
        self.assertEqual(run["status"], "partial")
        self.assertIn("permanent provider failure", run["error_summary"])
        alerts = self.database.fetch_all("SELECT * FROM alert_events WHERE source_id = ?", (result.run_id,))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "warning")


if __name__ == "__main__":
    unittest.main()
