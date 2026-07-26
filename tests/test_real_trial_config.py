import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


class RealTrialProductConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.configuration = json.loads(
            (PROJECT_ROOT / "config" / "product.real_trial.json").read_text(encoding="utf-8")
        )

    def test_strategic_allocation_is_complete_and_valid(self) -> None:
        allocation = self.configuration["strategic_allocation"]
        self.assertEqual(
            {item["symbol"] for item in allocation},
            {"CSI300", "CSI500", "NASDAQ100", "GOLD", "BOND", "CASH"},
        )
        self.assertAlmostEqual(sum(item["target"] for item in allocation), 1.0)
        for item in allocation:
            self.assertLessEqual(item["minimum"], item["target"])
            self.assertLessEqual(item["target"], item["maximum"])

    def test_benchmark_weights_sum_to_one(self) -> None:
        components = self.configuration["benchmark"]["components"]
        self.assertAlmostEqual(sum(item["weight"] for item in components), 1.0)
        self.assertEqual(len({item["symbol"] for item in components}), len(components))

    def test_trial_cannot_be_public_or_automatically_traded(self) -> None:
        product = self.configuration["product"]
        constraints = self.configuration["constraints"]
        self.assertFalse(product["public_distribution"])
        self.assertFalse(constraints["automatic_trading_allowed"])
        self.assertTrue(constraints["human_approval_required"])

    def test_unique_instrument_candidates_are_selected(self) -> None:
        instruments = self.configuration["data_policy"]["instruments"]
        selected = {
            item["symbol"]: item["selected_instrument"]["fund_code"]
            for item in instruments
        }
        self.assertEqual(
            selected,
            {
                "CSI300": "000051",
                "CSI500": "160119",
                "NASDAQ100": "270042",
                "GOLD": "000307",
                "BOND": "161119",
                "CASH": None,
            },
        )
        self.assertTrue(
            all(item["selected_instrument"]["selection_status"] == "provisional"
                for item in instruments)
        )
        self.assertEqual(self.configuration["status"], "fund_candidates_selected")
        provider_codes = {
            item["symbol"]: item["selected_instrument"]["provider_code"]
            for item in instruments
        }
        self.assertEqual(provider_codes["CSI500"], "160119.SZ")
        self.assertEqual(provider_codes["BOND"], "161119.SZ")

    def test_rebalance_cost_policy_is_explicit_but_not_silently_enabled(self) -> None:
        policy = self.configuration["performance_policy"]["rebalance_costs"]
        self.assertEqual(policy["model"], "one_way_turnover_times_rate")
        self.assertEqual(policy["deduction_timing"], "effective_common_valuation_date")
        self.assertIsNone(policy["rate_bps"])
        self.assertEqual(policy["approval_status"], "pending")

    def test_valuation_alignment_is_point_in_time_safe(self) -> None:
        policy = self.configuration["data_policy"]["valuation_alignment"]
        self.assertEqual(policy["date_basis"], "available_date")
        self.assertFalse(policy["future_backfill_allowed"])
        self.assertGreaterEqual(policy["maximum_carry_days"], 1)


if __name__ == "__main__":
    unittest.main()
