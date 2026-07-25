import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.data_providers import (  # noqa: E402
    TushareFundError,
    TushareFundNavProvider,
    TushareHttpClient,
    validate_nav_history,
)
from fundos.domain import Asset  # noqa: E402
from fundos.storage import Database  # noqa: E402


class FakeClient:
    def __init__(self, nav_rows, basic_rows=None):
        self.nav_rows = nav_rows
        self.basic_rows = basic_rows or [
            {"ts_code": "000051.OF", "name": "华夏沪深300ETF联接A", "status": "L"}
        ]

    def fund_basic(self, **kwargs):
        return self.basic_rows

    def fund_nav(self, **kwargs):
        return self.nav_rows


class TushareFundNavProviderTests(unittest.TestCase):
    def test_http_client_maps_official_fields_and_items_response(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def opener(request, timeout, context):
            request_payload = json.loads(request.data)
            self.assertEqual(request_payload["api_name"], "fund_nav")
            self.assertEqual(request_payload["params"]["ts_code"], "000051.OF")
            return Response(json.dumps({
                "code": 0,
                "msg": None,
                "data": {
                    "fields": ["ts_code", "nav_date", "adj_nav"],
                    "items": [["000051.OF", "20260724", 1.23]],
                },
            }).encode())

        rows = TushareHttpClient("secret", opener=opener).fund_nav(ts_code="000051.OF")
        self.assertEqual(rows[0]["adj_nav"], 1.23)

    def test_uses_one_consistent_nav_field_and_announcement_date(self) -> None:
        provider = TushareFundNavProvider(FakeClient([
            {
                "nav_date": "20260723", "ann_date": "20260725",
                "adj_nav": "1.12", "accum_nav": "1.10", "unit_nav": "1.02",
            },
            {
                "nav_date": "20260724", "ann_date": "20260726",
                "adj_nav": "1.13", "accum_nav": "1.11", "unit_nav": "1.03",
            },
        ]))
        rows = provider.get_fund_nav("000051.OF", internal_symbol="CSI300")
        self.assertEqual(rows[0].value_field, "adj_nav")
        self.assertEqual(rows[0].available_date, date(2026, 7, 25))
        prices = provider.get_daily_prices("000051.OF", internal_symbol="CSI300")
        self.assertEqual(prices[0].trade_date, date(2026, 7, 25))
        self.assertEqual(prices[0].close, 1.12)

    def test_uses_latest_nav_when_multiple_observations_share_available_date(self) -> None:
        provider = TushareFundNavProvider(FakeClient([
            {
                "nav_date": "20230419", "ann_date": "20230421",
                "adj_nav": "1.10",
            },
            {
                "nav_date": "20230420", "ann_date": "20230421",
                "adj_nav": "1.12",
            },
        ]))
        prices = provider.get_daily_prices("000051.OF", internal_symbol="CSI300")
        self.assertEqual(len(prices), 1)
        self.assertEqual(prices[0].trade_date, date(2023, 4, 21))
        self.assertEqual(prices[0].close, 1.12)

    def test_rejects_missing_announcement_date(self) -> None:
        provider = TushareFundNavProvider(FakeClient([
            {"nav_date": "20260724", "ann_date": None, "adj_nav": "1.1"}
        ]))
        with self.assertRaisesRegex(TushareFundError, "ann_date"):
            provider.get_fund_nav("000051.OF", internal_symbol="CSI300")

    def test_rejects_switching_nav_fields_mid_series(self) -> None:
        provider = TushareFundNavProvider(FakeClient([
            {"nav_date": "20260723", "ann_date": "20260724", "adj_nav": "1.1"},
            {"nav_date": "20260724", "ann_date": "20260725", "unit_nav": "1.2"},
        ]))
        with self.assertRaisesRegex(TushareFundError, "no single positive NAV field"):
            provider.get_fund_nav("000051.OF", internal_symbol="CSI300")

    def test_validates_metadata_identity_and_status(self) -> None:
        provider = TushareFundNavProvider(FakeClient([]))
        row = provider.validate_fund("000051.OF", expected_name="华夏沪深300")
        self.assertEqual(row["status"], "L")

    def test_history_quality_detects_large_gap_and_move(self) -> None:
        provider = TushareFundNavProvider(FakeClient([
            {"nav_date": "20250101", "ann_date": "20250102", "adj_nav": "1.0"},
            {"nav_date": "20250120", "ann_date": "20250121", "adj_nav": "1.5"},
        ]))
        rows = provider.get_fund_nav("000051.OF", internal_symbol="CSI300")
        with self.assertRaisesRegex(TushareFundError, "gap exceeds"):
            validate_nav_history(rows, minimum_history_days=0, maximum_gap_days=14)
        with self.assertRaisesRegex(TushareFundError, "NAV move exceeds"):
            validate_nav_history(
                rows,
                minimum_history_days=0,
                maximum_gap_days=30,
                maximum_absolute_return=0.35,
            )

    def test_valid_history_passes_quality_checks(self) -> None:
        provider = TushareFundNavProvider(FakeClient([
            {"nav_date": "20230101", "ann_date": "20230102", "adj_nav": "1.00"},
            {"nav_date": "20240101", "ann_date": "20240102", "adj_nav": "1.05"},
            {"nav_date": "20250101", "ann_date": "20250102", "adj_nav": "1.10"},
            {"nav_date": "20260101", "ann_date": "20260102", "adj_nav": "1.15"},
        ]))
        rows = provider.get_fund_nav("000051.OF", internal_symbol="CSI300")
        validate_nav_history(
            rows,
            minimum_history_days=1095,
            maximum_gap_days=370,
        )

    def test_persists_announcement_dates_and_tracks_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.upsert_assets([Asset("CSI300", "沪深300基金", "equity")])
            original = (
                "tushare-fund-nav", "000051.OF", "CSI300",
                date(2026, 7, 23), date(2026, 7, 25), date(2026, 7, 25),
                "adj_nav", 1.12, 1.12,
            )
            database.upsert_market_data_observations([original])
            database.upsert_market_data_observations([original])
            row = database.fetch_all("SELECT * FROM market_data_observations")[0]
            self.assertEqual(row["announced_date"], "2026-07-25")
            self.assertEqual(row["revision"], 0)

            corrected = (*original[:-2], 1.13, 1.13)
            database.upsert_market_data_observations([corrected])
            row = database.fetch_all("SELECT * FROM market_data_observations")[0]
            self.assertEqual(row["normalized_value"], 1.13)
            self.assertEqual(row["revision"], 1)


if __name__ == "__main__":
    unittest.main()
