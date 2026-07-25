import json
import ssl
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

from fundos.data_providers.csv_provider import PriceRow


class TushareFundError(RuntimeError):
    pass


class TushareHttpClient:
    API_URL = "https://api.tushare.pro"

    def __init__(self, token: str, *, opener=urlopen, timeout_seconds: int = 30) -> None:
        if not token.strip():
            raise ValueError("Tushare token is required")
        self.token = token
        self.opener = opener
        self.timeout_seconds = timeout_seconds

    def query(self, api_name: str, **parameters: Any) -> list[dict[str, Any]]:
        request = Request(
            self.API_URL,
            data=json.dumps({
                "api_name": api_name,
                "token": self.token,
                "params": parameters,
                "fields": "",
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "FundOS/0.1 market-data-client",
            },
            method="POST",
        )
        context = ssl.create_default_context(cafile=certifi.where())
        try:
            with self.opener(
                request,
                timeout=self.timeout_seconds,
                context=context,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise TushareFundError(f"Tushare returned HTTP {error.code}") from error
        except URLError as error:
            raise TushareFundError(
                f"Tushare connection failed: {error.reason}"
            ) from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TushareFundError("Tushare returned an invalid response") from error
        if payload.get("code") != 0:
            raise TushareFundError(
                f"Tushare API rejected {api_name}: {payload.get('msg') or payload.get('code')}"
            )
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        if any(len(item) != len(fields) for item in items):
            raise TushareFundError(f"Tushare returned malformed rows for {api_name}")
        return [dict(zip(fields, item, strict=True)) for item in items]

    def fund_basic(self, **parameters: Any) -> list[dict[str, Any]]:
        return self.query("fund_basic", **parameters)

    def fund_nav(self, **parameters: Any) -> list[dict[str, Any]]:
        return self.query("fund_nav", **parameters)


@dataclass(frozen=True, slots=True)
class FundNavRow:
    symbol: str
    nav_date: date
    announced_date: date
    available_date: date
    close: float
    value_field: str


def _records(result: Any) -> list[dict[str, Any]]:
    if hasattr(result, "to_dict"):
        return list(result.to_dict(orient="records"))
    if isinstance(result, Iterable) and not isinstance(result, (str, bytes, dict)):
        return [dict(item) for item in result]
    raise TushareFundError("Tushare returned an unsupported result type")


def _parse_compact_date(value: Any, field: str) -> date:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        raise TushareFundError(f"invalid {field}: {value!r}")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError as error:
        raise TushareFundError(f"invalid {field}: {value!r}") from error


class TushareFundNavProvider:
    VALUE_FIELDS = ("adj_nav", "accum_nav", "unit_nav")

    def __init__(self, client: Any) -> None:
        self.client = client

    def validate_fund(self, provider_code: str, *, expected_name: str) -> dict[str, Any]:
        try:
            rows = _records(self.client.fund_basic(ts_code=provider_code))
        except Exception as error:
            raise TushareFundError(f"failed to retrieve fund metadata: {error}") from error
        matches = [
            row for row in rows
            if str(row.get("ts_code", "")).split(".")[0] == provider_code.split(".")[0]
        ]
        if len(matches) != 1:
            raise TushareFundError(
                f"expected one metadata row for {provider_code}, received {len(matches)}"
            )
        row = matches[0]
        if expected_name not in str(row.get("name", "")):
            raise TushareFundError(
                f"fund name mismatch for {provider_code}: {row.get('name', '')}"
            )
        if str(row.get("status", "")).upper() not in {"L", "I"}:
            raise TushareFundError(f"fund is not active: {provider_code}")
        return row

    def get_fund_nav(
        self,
        provider_code: str,
        *,
        internal_symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[FundNavRow]:
        parameters: dict[str, Any] = {"ts_code": provider_code}
        if start_date:
            parameters["start_date"] = start_date.strftime("%Y%m%d")
        if end_date:
            parameters["end_date"] = end_date.strftime("%Y%m%d")
        try:
            raw_rows = _records(self.client.fund_nav(**parameters))
        except Exception as error:
            raise TushareFundError(f"failed to retrieve fund NAV: {error}") from error
        if not raw_rows:
            raise TushareFundError(f"fund NAV is empty: {provider_code}")

        value_field = next(
            (
                field for field in self.VALUE_FIELDS
                if all(_positive_number(row.get(field)) for row in raw_rows)
            ),
            None,
        )
        if value_field is None:
            raise TushareFundError(
                f"no single positive NAV field covers all rows for {provider_code}"
            )

        by_nav_date: dict[date, FundNavRow] = {}
        for raw in raw_rows:
            nav_date = _parse_compact_date(raw.get("nav_date"), "nav_date")
            announced_date = _parse_compact_date(raw.get("ann_date"), "ann_date")
            if announced_date < nav_date:
                raise TushareFundError(
                    f"announcement date precedes NAV date for {provider_code} on {nav_date}"
                )
            close = float(raw[value_field])
            row = FundNavRow(
                internal_symbol,
                nav_date,
                announced_date,
                max(nav_date, announced_date),
                close,
                value_field,
            )
            existing = by_nav_date.get(nav_date)
            if existing and existing != row:
                raise TushareFundError(
                    f"conflicting NAV revisions for {provider_code} on {nav_date}"
                )
            by_nav_date[nav_date] = row
        return sorted(by_nav_date.values(), key=lambda item: item.nav_date)

    def get_daily_prices(
        self,
        provider_code: str,
        *,
        internal_symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[PriceRow]:
        nav_rows = self.get_fund_nav(
            provider_code,
            internal_symbol=internal_symbol,
            start_date=start_date,
            end_date=end_date,
        )
        return fund_nav_rows_to_prices(nav_rows)


def validate_nav_history(
    rows: list[FundNavRow],
    *,
    minimum_history_days: int = 1095,
    maximum_gap_days: int = 14,
    maximum_absolute_return: float = 0.35,
) -> None:
    if len(rows) < 2:
        raise TushareFundError("at least two NAV observations are required")
    if (rows[-1].nav_date - rows[0].nav_date).days < minimum_history_days:
        raise TushareFundError("NAV history does not meet the minimum coverage")
    for previous, current in zip(rows[:-1], rows[1:], strict=True):
        gap = (current.nav_date - previous.nav_date).days
        if gap > maximum_gap_days:
            raise TushareFundError(
                f"NAV history gap exceeds {maximum_gap_days} days after {previous.nav_date}"
            )
        periodic_return = current.close / previous.close - 1
        if abs(periodic_return) > maximum_absolute_return:
            raise TushareFundError(
                f"NAV move exceeds {maximum_absolute_return:.0%} on {current.nav_date}"
            )


def fund_nav_rows_to_prices(rows: list[FundNavRow]) -> list[PriceRow]:
    latest_by_available_date: dict[date, FundNavRow] = {}
    for item in rows:
        existing = latest_by_available_date.get(item.available_date)
        if existing is None or item.nav_date > existing.nav_date:
            latest_by_available_date[item.available_date] = item
    return [
        PriceRow(item.symbol, item.available_date, item.close)
        for item in sorted(
            latest_by_available_date.values(),
            key=lambda row: row.available_date,
        )
    ]


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False
