import path_setup  # noqa: F401
import unittest
from pathlib import Path

from market_analysis.accounting import (
    group_statistics,
    quote_economics,
    window_aggregates,
)
from market_analysis.data import load_dataset

ROOT = Path(__file__).resolve().parents[1]


class AccountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(ROOT / "data")
        cls.early = min(cls.ds.policy.observe_at)
        cls.late = max(cls.ds.policy.observe_at)

    def test_pending_maturity_flagged_at_early_asof(self):
        pending = 0
        for q in self.ds.quotes:
            if q.rule_version != "秋末":
                continue
            econ = quote_economics(self.ds, q.quote, self.early)
            pending += econ.pending_orders
        self.assertGreater(pending, 0)  # 早期时点售后未成熟，毛利不可视为已知

    def test_all_mature_at_late_asof(self):
        for q in self.ds.quotes:
            econ = quote_economics(self.ds, q.quote, self.late)
            self.assertEqual(econ.pending_orders, 0)

    def test_returns_reduce_margin_but_not_truck_commitment(self):
        # 取一条有退货的订单核对：毛利被减，但其实装车位仍在 loading 中
        returned_order = next(iter(self.ds.returns)).order
        quote_id = next(o.quote for o in self.ds.orders if o.order == returned_order)
        econ = quote_economics(self.ds, quote_id, self.late)
        raw = sum(
            o.margin_before_shipping_cents
            for o in self.ds.orders_for_quote(quote_id)
        )
        self.assertLess(econ.margin_after_returns_cents, raw)
        self.assertIsNotNone(self.ds.loading_for_order(returned_order))

    def test_consign_orders_flag_unobservable_freight(self):
        # Q0003 同时有摊位与代销订单
        econ = quote_economics(self.ds, "Q0003", self.late)
        self.assertEqual(econ.freight_unobservable_orders, 1)
        self.assertIn("不可核对", econ.coverage_note)

    def test_window_aggregates_cover_all_quotes(self):
        agg = window_aggregates(self.ds, self.late)
        self.assertEqual(sum(a.quotes for a in agg), len(self.ds.quotes))

    def test_group_stats_report_pending_and_coverage(self):
        stats = group_statistics(self.ds, self.early)
        late_groups = {k for k, s in stats.items() if k[0] == "秋末"}
        self.assertTrue(any(stats[k].pending_quotes > 0 for k in late_groups))


if __name__ == "__main__":
    unittest.main()
