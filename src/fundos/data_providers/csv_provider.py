import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PriceRow:
    symbol: str
    trade_date: date
    close: float


class CsvPriceProvider:
    REQUIRED_COLUMNS = {"symbol", "trade_date", "close"}

    def load(self, path: str | Path) -> list[PriceRow]:
        rows: list[PriceRow] = []
        seen: set[tuple[str, date]] = set()
        with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            missing = self.REQUIRED_COLUMNS - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")

            for line_number, raw in enumerate(reader, start=2):
                try:
                    symbol = raw["symbol"].strip()
                    trade_date = date.fromisoformat(raw["trade_date"].strip())
                    close = float(raw["close"])
                except (TypeError, ValueError) as error:
                    raise ValueError(f"invalid price data at line {line_number}") from error
                if not symbol:
                    raise ValueError(f"empty symbol at line {line_number}")
                if close <= 0:
                    raise ValueError(f"close must be positive at line {line_number}")
                key = (symbol, trade_date)
                if key in seen:
                    raise ValueError(f"duplicate price for {symbol} on {trade_date}")
                seen.add(key)
                rows.append(PriceRow(symbol, trade_date, close))

        if not rows:
            raise ValueError("CSV contains no price rows")
        return rows

