import path_setup  # noqa: F401
import unittest
from pathlib import Path

from market_analysis.audit import audit_quote
from market_analysis.data import load_dataset

ROOT = Path(__file__).resolve().parents[1]


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(ROOT / "data")

    def test_audit_reconstructs_threshold_at_issue_time(self):
        result = audit_quote(self.ds, "Q0000")  # 秋初，64 箱 + 豁免
        self.assertEqual(result.rule_version, "秋初")
        self.assertEqual(result.threshold_boxes, 80)
        self.assertTrue(result.eligibility.waiver_approved)
        self.assertTrue(result.eligibility.originally_eligible)
        self.assertIn("豁免", "".join(result.narrative))

    def test_audit_does_not_backfill_eligibility_from_deals(self):
        # Q0003 意向 79 箱不合格，实际成交 79 箱（26+53）仍不得改判
        result = audit_quote(self.ds, "Q0003")
        self.assertFalse(result.eligibility.originally_eligible)
        self.assertFalse(result.eligibility.met_threshold)
        self.assertIn("不能倒推", "".join(result.narrative))

    def test_audit_shows_both_asof_snapshots(self):
        result = audit_quote(self.ds, "Q4403")
        self.assertEqual(len(result.economics_by_asof), 2)
        early = min(self.ds.policy.observe_at).isoformat()
        late = max(self.ds.policy.observe_at).isoformat()
        self.assertIn(early, result.economics_by_asof)
        self.assertIn(late, result.economics_by_asof)

    def test_audit_open_quote_commitment(self):
        result = audit_quote(self.ds, "Q4403")
        self.assertIsNotNone(result.open_commitment)
        self.assertTrue(result.open_commitment.still_protected)
        self.assertEqual(result.open_commitment.valid_boxes, 60)
        self.assertEqual(result.open_commitment.reserved_truck_slots, 1)

    def test_audit_redemption_uses_paid_freight(self):
        result = audit_quote(self.ds, "Q0002")  # 95 箱合格
        self.assertEqual(result.eligibility.redeemed_discount, 1)
        self.assertEqual(result.eligibility.paid_regular, 0)


if __name__ == "__main__":
    unittest.main()
