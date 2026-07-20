import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.data_providers import CsvPriceProvider  # noqa: E402


class CsvPriceProviderTests(unittest.TestCase):
    def test_loads_valid_prices(self) -> None:
        rows = CsvPriceProvider().load(Path(__file__).parent / "fixtures" / "prices.csv")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].symbol, "CSI300")
        self.assertEqual(rows[0].close, 4012.50)

    def test_rejects_duplicate_prices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.csv"
            path.write_text(
                "symbol,trade_date,close\nA,2026-07-21,1\nA,2026-07-21,2\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate price"):
                CsvPriceProvider().load(path)


if __name__ == "__main__":
    unittest.main()

