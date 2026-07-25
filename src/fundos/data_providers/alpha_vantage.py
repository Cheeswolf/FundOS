import json
import ssl
from datetime import date
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi

from fundos.data_providers.csv_provider import PriceRow


class AlphaVantageError(RuntimeError):
    pass


def _response_preview(raw: bytes, limit: int = 160) -> str:
    text = raw.decode("utf-8", errors="replace")
    return " ".join(text.split())[:limit]


def _verified_urlopen(request: Request, timeout: int):
    context = ssl.create_default_context(cafile=certifi.where())
    return urlopen(request, timeout=timeout, context=context)


class AlphaVantageDailyProvider:
    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(
        self,
        api_key: str,
        *,
        opener: Callable = _verified_urlopen,
        timeout_seconds: int = 30,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Alpha Vantage API key is required")
        self.api_key = api_key
        self.opener = opener
        self.timeout_seconds = timeout_seconds

    def get_daily_prices(
        self,
        provider_symbol: str,
        *,
        internal_symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        output_size: str = "full",
    ) -> list[PriceRow]:
        if output_size not in {"compact", "full"}:
            raise ValueError("output size must be compact or full")
        query = urlencode({
            "function": "TIME_SERIES_DAILY",
            "symbol": provider_symbol,
            "outputsize": output_size,
            "apikey": self.api_key,
        })
        request = Request(
            f"{self.BASE_URL}?{query}",
            headers={"User-Agent": "FundOS/0.1 market-data-client"},
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                raw_response = response.read()
        except HTTPError as error:
            preview = _response_preview(error.read())
            detail = f": {preview}" if preview else ""
            raise AlphaVantageError(
                f"market data provider returned HTTP {error.code}{detail}"
            ) from error
        except URLError as error:
            raise AlphaVantageError(
                f"market data connection failed: {error.reason}"
            ) from error
        except (TimeoutError, OSError) as error:
            raise AlphaVantageError(
                f"market data connection failed: {type(error).__name__}: {error}"
            ) from error

        try:
            decoded_response = raw_response.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AlphaVantageError(
                "market data provider returned a non-UTF-8 response"
            ) from error
        try:
            payload = json.loads(decoded_response)
        except json.JSONDecodeError as error:
            preview = _response_preview(raw_response)
            detail = f": {preview}" if preview else ""
            raise AlphaVantageError(
                f"market data provider returned a non-JSON response{detail}"
            ) from error

        for error_key in ("Error Message", "Note", "Information"):
            if error_key in payload:
                raise AlphaVantageError(str(payload[error_key]))
        time_series = payload.get("Time Series (Daily)")
        if not isinstance(time_series, dict):
            raise AlphaVantageError("daily time series is missing from provider response")

        target_symbol = internal_symbol or provider_symbol
        rows = []
        for raw_date, values in time_series.items():
            trade_date = date.fromisoformat(raw_date)
            if start_date and trade_date < start_date:
                continue
            if end_date and trade_date > end_date:
                continue
            try:
                close = float(values["4. close"])
            except (KeyError, TypeError, ValueError) as error:
                raise AlphaVantageError(f"invalid close price for {raw_date}") from error
            if close <= 0:
                raise AlphaVantageError(f"non-positive close price for {raw_date}")
            rows.append(PriceRow(target_symbol, trade_date, close))
        rows.sort(key=lambda item: item.trade_date)
        if not rows:
            raise AlphaVantageError("provider returned no prices in the requested date range")
        return rows
