import path_setup  # noqa: F401
import unittest
from pathlib import Path

from market_analysis.data import load_dataset
from market_analysis.diagnostics import run_all_diagnostics
from market_analysis.simulation import (
    active_commitments,
    evaluate_all_candidates,
    remaining_capacity,
)

ROOT = Path(__file__).resolve().parents[1]
SUBSIDY = 4400  # 秋末普通运费 9200 - 优惠运费 4800


class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(ROOT / "data")
        cls.asof = max(cls.ds.policy.observe_at)
        cls.diag = run_all_diagnostics(cls.ds)
        cls.candidates = {
            c.threshold: c
            for c in evaluate_all_candidates(cls.ds, cls.asof, cls.diag)
        }
        cls.cap = remaining_capacity(ds=cls.ds, asof=cls.asof)

    def test_old_commitments_reserved_first(self):
        commitments = active_commitments(self.ds, self.asof)
        protected = [c for c in commitments if c.still_protected]
        self.assertEqual(len(protected), 3)
        self.assertEqual(sum(c.reserved_truck_slots for c in protected), 3)
        self.assertEqual(sum(c.reserved_cents for c in protected), 11500)
        # 8 个车位扣 3，预算 34000 扣 11500
        self.assertEqual(self.cap.available_truck_slots, 5)
        self.assertEqual(self.cap.available_budget_cents, 22500)

    def test_reserved_budget_never_reallocated_to_scenarios(self):
        # 所有场景只与“可用预算”比较；预留 11500 不进入任何场景支出
        self.assertEqual(self.cap.reserved_budget_cents, 11500)
        for cand in self.candidates.values():
            for sc in cand.scenarios:
                if sc.fits_budget_p90:
                    self.assertLessEqual(
                        sc.p90_total_promo_cents,
                        self.cap.available_budget_cents,
                    )

    def test_out_of_support_threshold_marked(self):
        # 秋末意向量下限为 78；门槛 65 的区间 65–77 完全落在观测支撑之外。
        # 区间按数值覆盖标注；该区间内本批没有询价，故计数为 0，但任何低于 78
        # 的新询价都只能做压力试算
        cand65 = self.candidates[65]
        self.assertEqual(cand65.out_of_support_range, (65, 77))
        self.assertEqual(cand65.out_of_support_new_quotes, 0)
        self.assertTrue(
            any("无观测支撑" in b for b in cand65.blockers)
        )

    def test_identified_range_only_from_clean_threshold(self):
        # 只有秋初 80 箱门槛通过诊断；秋末 100 箱因扎堆+混淆不可识别
        cand78 = self.candidates[78]
        self.assertIsNotNone(cand78.identified_range)
        lo, hi = cand78.identified_range
        self.assertEqual(lo, 78)
        self.assertEqual(hi, 90)  # 80±10 与候选区间上界 99 的交集
        self.assertGreater(cand78.identified_new_quotes, 0)
        # 92 距 80 为 12，超出 ±10 带宽：无可识别部分
        self.assertEqual(self.candidates[92].identified_new_quotes, 0)
        self.assertEqual(self.candidates[101].identified_new_quotes, 0)

    def test_tightening_candidates_rejected(self):
        for t in (101, 115):
            self.assertEqual(self.candidates[t].classification, "reject")

    def test_scenarios_are_per_window_not_pooled(self):
        # 历史 241 个秋末窗口；任何场景的车数都应是个位数（车位 8），
        # 若误用全期累计箱量会得到上百车
        for cand in self.candidates.values():
            for sc in cand.scenarios:
                self.assertLessEqual(sc.p90_promo_trucks, 8)
                self.assertGreaterEqual(sc.mean_promo_trucks, 0.0)

    def test_worst_case_that_does_not_fit_is_rejected(self):
        for cand in self.candidates.values():
            worst = cand.scenarios[-1]
            if not worst.fits_slots_p90 or not worst.fits_budget_p90:
                self.assertEqual(cand.classification, "reject")
                self.assertTrue(cand.blockers)

    def test_every_candidate_has_three_scenarios_and_rationale(self):
        for cand in self.candidates.values():
            self.assertEqual(len(cand.scenarios), 3)
            self.assertTrue(cand.rationale)
            names = [s.name for s in cand.scenarios]
            self.assertEqual(
                names,
                [
                    "仅可识别范围（逐窗均值/P90）",
                    "全部观察支撑（含描述区）",
                    "最有利满员假设（含无支撑区）",
                ],
            )

    def test_trial_classification_respects_truck_cap(self):
        for cand in self.candidates.values():
            if cand.classification == "limited_trial":
                self.assertLessEqual(
                    self.ds.policy.max_trial_trucks,
                    self.cap.available_truck_slots,
                )

    def test_higher_threshold_flagged_as_tightening(self):
        for t in (101, 115):
            cand = self.candidates[t]
            self.assertTrue(
                any("收紧" in b or "无新增行为" in b for b in cand.blockers)
            )

    def test_decision_labels_are_from_allowed_set(self):
        for cand in self.candidates.values():
            self.assertIn(
                cand.classification, {"approvable", "limited_trial", "reject"}
            )


if __name__ == "__main__":
    unittest.main()
