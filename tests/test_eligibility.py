import path_setup  # noqa: F401
import unittest
from datetime import datetime

from market_analysis.data import load_dataset
from market_analysis.eligibility import evaluate_all, evaluate_quote, find_split_groups

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


class EligibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(ROOT / "data")

    def test_eligibility_uses_original_boxes_and_waiver_only(self):
        for record in evaluate_all(self.ds).values():
            rule = self.ds.rule_for(record.rule_version)
            expected = (
                record.original_boxes >= rule.threshold_boxes
                or record.waiver_approved
            )
            self.assertEqual(record.originally_eligible, expected)

    def test_waiver_quotes_all_below_threshold_and_discounted(self):
        for q in self.ds.quotes:
            if q.has_waiver:
                rule = self.ds.rule_for(q.rule_version)
                self.assertLess(q.original_boxes, rule.threshold_boxes)
                self.assertEqual(q.offered_fee_cents, rule.discount_fee_cents)

    def test_redemption_based_on_paid_freight_not_flag(self):
        # 资料里 used_promo=1 与普通运费并存；本模块不得用该标记判兑付
        record = evaluate_quote(self.ds, self.ds.quote_by_id("Q0001"))
        self.assertFalse(record.originally_eligible)
        self.assertEqual(record.redeemed_discount, 0)
        self.assertEqual(record.paid_regular, 1)

    def test_split_below_threshold_detected(self):
        splits = find_split_groups(self.ds)
        gaming = [s for s in splits if s["split_below_threshold"]]
        self.assertGreater(len(gaming), 0)
        example = next(s for s in gaming if s["buyer"] == "M006")
        self.assertEqual(example["merged_boxes"], 155)
        self.assertTrue(all(b < 80 for b in example["original_boxes"]))
        self.assertFalse(example["any_quote_eligible"])


if __name__ == "__main__":
    unittest.main()
