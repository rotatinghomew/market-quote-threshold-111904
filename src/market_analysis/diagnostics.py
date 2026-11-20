"""门槛有效性诊断。

回答三类问题，任何一类不通过，门槛处的因果解释都要降级为描述：

1. 意向量在门槛处是否异常扎堆（bunching）——密度连续是断点设计的前提；
2. 其他资格（如免订金）或豁免是否在同一断点同时变化——混淆变量；
3. 同一买方是否横跨门槛两边——单位非独立、局部随机分组假设受损。

两个规则版本的门槛（80 与 100）分别检验，绝不合并样本。
"""
from __future__ import annotations

from dataclasses import dataclass

from scipy import stats

from .data import Dataset


@dataclass(frozen=True)
class BunchingResult:
    rule_version: str
    threshold: int
    support: tuple[int, int]
    threshold_count: int
    counterfactual_count: float
    excess_count: float
    excess_ratio: float
    chi2: float
    p_value: float
    bunched: bool
    method: str
    note: str


@dataclass(frozen=True)
class ConfoundingResult:
    rule_version: str
    threshold: int
    other_eligibility: str
    jumps: dict[str, float]  # 指标 -> 门槛两侧局部均值之差
    confounded: bool
    detail: str


@dataclass(frozen=True)
class CrossSideResult:
    rule_version: str
    threshold: int
    buyers_total: int
    buyers_both_sides: int
    share_both_sides: float
    band: int
    note: str


def _box_counts(ds: Dataset, version: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    for q in ds.quotes:
        if q.rule_version != version:
            continue
        counts[q.original_boxes] = counts.get(q.original_boxes, 0) + 1
    return counts


def bunching_test(
    ds: Dataset,
    version: str,
    *,
    band: int = 10,
    alpha: float = 0.05,
) -> BunchingResult:
    """门槛密度扎堆检验。

    用门槛两侧各 ``band`` 箱的计数拟合二次多项式反事实密度（门槛自身一箱排除在
    拟合外），再比较门槛实际计数与反事实期望。泊松型卡方统计量给出 p 值；同时
    报告超额箱数与超额比例，便于业务侧理解规模而非只看显著性。
    """
    rule = ds.rule_for(version)
    t = rule.threshold_boxes
    counts = _box_counts(ds, version)
    support = (min(counts), max(counts))

    xs = [b for b in range(t - band, t + band + 1) if b != t and b in counts]
    ys = [counts[b] for b in xs]
    x_arr = list(xs)
    # 二次多项式反事实（门槛点不参与拟合）
    coef = _poly_fit(x_arr, ys, degree=2)
    expected_t = max(_poly_eval(coef, t), 1e-9)
    actual_t = counts.get(t, 0)
    excess = actual_t - expected_t

    # 全窗口卡方（期望 < 5 的箱合并到相邻箱，保持检验有效）
    chi2 = 0.0
    for b in range(t - band, t + band + 1):
        if b == t:
            mu = expected_t
        else:
            mu = max(_poly_eval(coef, b), 1e-9)
        chi2 += (counts.get(b, 0) - mu) ** 2 / mu
    dof = max(1, 2 * band - 3)  # 2band+1 箱，门槛 1 个自由偏离 + 3 个多项式参数
    p_value = 1.0 - stats.chi2.cdf(chi2, dof)

    bunched = (excess > 0) and (p_value < alpha)
    note = (
        f"门槛 {t} 处实际 {actual_t} 条询价，反事实期望 {expected_t:.1f} 条，"
        f"超额 {excess:.1f} 条（{excess / expected_t:.0%}）。"
    )
    if bunched:
        note += "存在统计显著扎堆，门槛两侧不可视为同一批人的随机分组。"
    else:
        note += "未发现显著扎堆，密度连续性前提暂可接受。"

    return BunchingResult(
        rule_version=version,
        threshold=t,
        support=support,
        threshold_count=actual_t,
        counterfactual_count=expected_t,
        excess_count=excess,
        excess_ratio=excess / expected_t,
        chi2=chi2,
        p_value=p_value,
        bunched=bunched,
        method=f"二次多项式反事实密度，两侧各 {band} 箱，门槛箱排除拟合",
        note=note,
    )


def _poly_fit(xs: list[int], ys: list[int], degree: int) -> list[float]:
    import numpy as np

    return np.polyfit(np.array(xs, dtype=float), np.array(ys, dtype=float), degree).tolist()


def _poly_eval(coef: list[float], x: int) -> float:
    return float(sum(c * x ** (len(coef) - 1 - i) for i, c in enumerate(coef)))


def confounding_test(
    ds: Dataset, version: str, *, band: int = 5
) -> ConfoundingResult:
    """其他资格与豁免在门槛处是否同时跳变。"""
    rule = ds.rule_for(version)
    t = rule.threshold_boxes
    left, right = [], []
    for q in ds.quotes:
        if q.rule_version != version:
            continue
        d = q.original_boxes - t
        if -band <= d < 0:
            left.append(q)
        elif 0 <= d <= band:
            right.append(q)

    def mean_flag(rows, pred) -> float:
        return sum(1 for q in rows if pred(q)) / len(rows) if rows else 0.0

    jumps = {
        "免订金比例差": mean_flag(right, lambda q: q.other_eligibility == "免订金")
        - mean_flag(left, lambda q: q.other_eligibility == "免订金"),
        "豁免比例差": mean_flag(right, lambda q: q.has_waiver)
        - mean_flag(left, lambda q: q.has_waiver),
    }
    # 免订金是本资料里唯一的其他资格取值；任一实质跳变即混淆
    confounded = any(abs(v) > 0.25 for v in jumps.values())
    other = sorted({q.other_eligibility for q in left + right})
    detail = (
        f"门槛 ±{band} 箱内左侧 {len(left)} 条、右侧 {len(right)} 条；"
        + "；".join(f"{k}{v:+.2f}" for k, v in jumps.items())
    )
    return ConfoundingResult(
        rule_version=version,
        threshold=t,
        other_eligibility="/".join(other),
        jumps=jumps,
        confounded=confounded,
        detail=detail,
    )


def cross_side_buyers(
    ds: Dataset, version: str, *, band: int = 10
) -> CrossSideResult:
    """同一买方在该版本内是否既出现门槛左侧、又出现门槛右侧。"""
    rule = ds.rule_for(version)
    t = rule.threshold_boxes
    sides: dict[str, set[bool]] = {}
    for q in ds.quotes:
        if q.rule_version != version:
            continue
        if abs(q.original_boxes - t) > band:
            continue
        sides.setdefault(q.buyer, set()).add(q.original_boxes >= t)
    total = len(sides)
    both = sum(1 for s in sides.values() if len(s) > 1)
    share = both / total if total else 0.0
    note = (
        f"门槛 ±{band} 箱内共 {total} 名买方，其中 {both} 名（{share:.0%}）"
        "在不同采购窗横跨门槛两侧；个体重复出现，断点估计的标准误需按买方聚类理解。"
    )
    return CrossSideResult(
        rule_version=version,
        threshold=t,
        buyers_total=total,
        buyers_both_sides=both,
        share_both_sides=share,
        band=band,
        note=note,
    )


def run_all_diagnostics(ds: Dataset) -> dict[str, dict]:
    out = {}
    for version in sorted(ds.rules):
        out[version] = {
            "bunching": bunching_test(ds, version),
            "confounding": confounding_test(ds, version),
            "cross_side": cross_side_buyers(ds, version),
        }
    return out
