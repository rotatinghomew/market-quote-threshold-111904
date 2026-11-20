import path_setup  # noqa: F401
import unittest
from pathlib import Path

from market_analysis.causal import assess_threshold, local_linear_rd
from market_analysis.data import load_dataset
from market_analysis.diagnostics import run_all_diagnostics

ROOT = Path(__file__).resolve().parents[1]


class CausalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(ROOT / "data")
        cls.asof = max(cls.ds.policy.observe_at)
        cls.diag = run_all_diagnostics(cls.ds)

    def test_qiuchu_identifiable_with_uncertainty(self):
        d = self.diag["秋初"]
        assessment = assess_threshold(
            self.ds, "秋初", self.asof, d["bunching"], d["confounding"], d["cross_side"]
        )
        self.assertTrue(assessment.identifiable)
        self.assertTrue(assessment.estimates)
        for est in assessment.estimates:
            self.assertGreater(est.clustered_se, 0)
            self.assertLess(est.ci95_low, est.jump_cents)
            self.assertLess(est.jump_cents, est.ci95_high)
            self.assertGreater(est.n_left + est.n_right, 0)
        self.assertIn("68–92", assessment.applicability)

    def test_qiumo_not_identifiable(self):
        d = self.diag["秋末"]
        assessment = assess_threshold(
            self.ds, "秋末", self.asof, d["bunching"], d["confounding"], d["cross_side"]
        )
        self.assertFalse(assessment.identifiable)
        self.assertTrue(assessment.blockers)
        self.assertIn("描述", assessment.applicability)

    def test_rd_excludes_waivers_by_default(self):
        estimates = local_linear_rd(self.ds, "秋初", self.asof)
        # 豁免单全部位于门槛左侧；纳入它们会改变左侧样本数
        with_waiver = local_linear_rd(
            self.ds, "秋初", self.asof, include_waivers=True
        )
        base_left = estimates[0].n_left
        waiver_left = with_waiver[0].n_left
        self.assertGreater(waiver_left, base_left)

    def test_estimates_have_multiple_bandwidths(self):
        estimates = local_linear_rd(self.ds, "秋初", self.asof)
        self.assertEqual(len(estimates), 4)
        self.assertEqual([e.bandwidth for e in estimates], [5, 8, 10, 12])


if __name__ == "__main__":
    unittest.main()
