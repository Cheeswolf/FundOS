import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.analytics import calculate_linked_contributions, evaluate_view  # noqa: E402


class AttributionTests(unittest.TestCase):
    def test_linked_contributions_sum_to_compounded_return(self) -> None:
        returns = {"A": [0.10, 0.00], "B": [0.00, 0.20]}
        contributions = calculate_linked_contributions(returns, {"A": 0.5, "B": 0.5})
        cumulative = (1.05 * 1.10) - 1
        self.assertAlmostEqual(sum(contributions.values()), cumulative)

    def test_evaluates_directional_views(self) -> None:
        self.assertTrue(evaluate_view("positive", 0.05))
        self.assertTrue(evaluate_view("negative", -0.05))
        self.assertTrue(evaluate_view("neutral", 0.01))
        self.assertFalse(evaluate_view("positive", 0.01))


if __name__ == "__main__":
    unittest.main()

