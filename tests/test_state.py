import path_setup  # noqa: F401
import unittest
from pathlib import Path

from market_analysis.data import load_dataset
from market_analysis.state import roll_forward

ROOT = Path(__file__).resolve().parents[1]


class StateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(ROOT / "data")
        cls.early = min(cls.ds.policy.observe_at)
        cls.late = max(cls.ds.policy.observe_at)

    def test_returns_do_not_release_capacity(self):
        rf = roll_forward(self.ds, self.early, self.late)
        # 两个时点间有退货成熟，但车位/预算预留不变
        self.assertGreater(rf.returned_orders, 0)
        self.assertEqual(rf.capacity.reserved_truck_slots, 3)
        self.assertEqual(rf.capacity.reserved_budget_cents, 11500)
        self.assertIn("不产生车位或预算回流", rf.note)

    def test_expired_quote_releases_reservation(self):
        rf = roll_forward(
            self.ds,
            self.early,
            self.late,
            open_quote_states={"Q4400": "已兑付", "Q4406": "失效"},
        )
        self.assertEqual(rf.capacity.reserved_truck_slots, 1)  # 只剩 Q4403
        self.assertEqual(rf.capacity.available_truck_slots, 7)
        self.assertEqual(rf.capacity.reserved_budget_cents, 2500)
        events = {c.quote: c.event for c in rf.changes}
        self.assertEqual(events["Q4400"], "状态变为已兑付")
        self.assertEqual(events["Q4406"], "状态变为失效")

    def test_partial_redemption_keeps_remaining_reservation(self):
        rf = roll_forward(self.ds, self.early, self.late)
        partial = next(c for c in rf.changes if c.quote == "Q4403")
        self.assertEqual(partial.event, "部分兑付")
        self.assertEqual(partial.truck_slot_delta, 0)
        self.assertEqual(partial.budget_delta_cents, 0)


if __name__ == "__main__":
    unittest.main()
