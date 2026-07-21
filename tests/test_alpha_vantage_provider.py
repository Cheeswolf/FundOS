import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.data_providers import AlphaVantageDailyProvider, AlphaVantageError  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener_for(payload):
    def open_request(request, timeout):
        return FakeResponse(json.dumps(payload).encode("utf-8"))
    return open_request


class AlphaVantageProviderTests(unittest.TestCase):
    def test_parses_filters_and_maps_daily_prices(self) -> None:
        provider = AlphaVantageDailyProvider(
            "test-key",
            opener=opener_for({
                "Time Series (Daily)": {
                    "2026-07-03": {"4. close": "103.50"},
                    "2026-07-02": {"4. close": "102.00"},
                    "2026-07-01": {"4. close": "100.00"},
                }
            }),
        )
        rows = provider.get_daily_prices(
            "QQQ",
            internal_symbol="NASDAQ100",
            start_date=date(2026, 7, 2),
        )
        self.assertEqual([row.trade_date for row in rows], [date(2026, 7, 2), date(2026, 7, 3)])
        self.assertEqual(rows[0].symbol, "NASDAQ100")
        self.assertEqual(rows[-1].close, 103.50)

    def test_surfaces_provider_rate_limit_message(self) -> None:
        provider = AlphaVantageDailyProvider(
            "test-key",
            opener=opener_for({"Note": "API call frequency exceeded"}),
        )
        with self.assertRaisesRegex(AlphaVantageError, "frequency exceeded"):
            provider.get_daily_prices("QQQ")

    def test_rejects_missing_api_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "API key"):
            AlphaVantageDailyProvider("")


if __name__ == "__main__":
    unittest.main()

