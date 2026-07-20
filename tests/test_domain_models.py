import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.domain.models import PortfolioVersion, PositionWeight  # noqa: E402


class PortfolioVersionTests(unittest.TestCase):
    def test_accepts_valid_version(self) -> None:
        version = PortfolioVersion(
            version_id="version-1",
            product_id="product-1",
            version_number=1,
            effective_date=date(2026, 7, 21),
            weights=(
                PositionWeight("EQUITY", Decimal("0.60")),
                PositionWeight("BOND", Decimal("0.40")),
            ),
        )
        self.assertEqual(version.version_number, 1)

    def test_rejects_weight_sum_below_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            PortfolioVersion(
                version_id="version-1",
                product_id="product-1",
                version_number=1,
                effective_date=date(2026, 7, 21),
                weights=(PositionWeight("EQUITY", Decimal("0.80")),),
            )


if __name__ == "__main__":
    unittest.main()
