import path_setup  # noqa: F401
import unittest
from pathlib import Path

from market_analysis.data import load_dataset
from market_analysis.diagnostics import (
    bunching_test,
    confounding_test,
    cross_side_buyers,
    run_all_diagnostics,
)

ROOT = Path(__file__).resolve().parents[1]


class DiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(ROOT / "data")

    def test_qiumo_threshold_is_bunched(self):
        result = bunching_test(self.ds, "秋末")
        self.assertTrue(result.bunched)
        self.assertEqual(result.threshold, 100)
        # 148 条挤在 100 箱，邻箱仅 7-14 条
        self.assertGreater(result.excess_ratio, 3.0)
        self.assertLess(result.p_value, 0.01)

    def test_qiuchu_threshold_not_bunched(self):
        result = bunching_test(self.ds, "秋初")
        self.assertFalse(result.bunched)
        self.assertEqual(result.threshold, 80)

    def test_confounding_at_qiumo_only(self):
        late = confounding_test(self.ds, "秋末")
        self.assertTrue(late.confounded)
        self.assertGreater(late.jumps["免订金比例差"], 0.9)
        early = confounding_test(self.ds, "秋初")
        self.assertFalse(early.confounded)

    def test_cross_side_buyers_reported(self):
        result = cross_side_buyers(self.ds, "秋初")
        self.assertGreater(result.buyers_both_sides, 0)
        self.assertLessEqual(result.share_both_sides, 1.0)

    def test_versions_never_pooled(self):
        diag = run_all_diagnostics(self.ds)
        self.assertEqual(set(diag), {"秋初", "秋末"})
        for version, d in diag.items():
            self.assertEqual(d["bunching"].rule_version, version)


if __name__ == "__main__":
    unittest.main()
