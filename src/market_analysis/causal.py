"""门槛附近的局部因果估计。

仅当诊断通过（无显著扎堆、无同点混淆）时输出可解释的局部效应；否则只给描述性
均值差并标注失效原因。估计使用局部线性 OLS（门槛两侧允许不同斜率），标准误按
买方聚类，并报告多个带宽做稳健性对照。效应的适用范围严格限定在门槛邻域与该
规则版本内，不外推到其他门槛或换版后的人群。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import Dataset
from .diagnostics import (
    BunchingResult,
    ConfoundingResult,
    CrossSideResult,
)


@dataclass(frozen=True)
class RDEstimate:
    rule_version: str
    threshold: int
    bandwidth: int
    n_left: int
    n_right: int
    jump_cents: float
    clustered_se: float
    t_stat: float
    ci95_low: float
    ci95_high: float
    outcome: str
    includes_waivers: bool


@dataclass(frozen=True)
class CausalAssessment:
    rule_version: str
    threshold: int
    identifiable: bool
    applicability: str
    estimates: tuple[RDEstimate, ...]
    blockers: tuple[str, ...]
    descriptive_note: str


def _outcome_table(ds: Dataset, version: str, asof, outcome: str) -> list[tuple]:
    from .accounting import quote_economics
    from .eligibility import evaluate_quote

    rows = []
    for q in ds.quotes:
        if q.rule_version != version:
            continue
        # 处理变量按门槛规则定义：豁免单是门槛之外的特殊通道，默认剔除，
        # 不允许它们以“合格”身份混入门槛左侧
        record = evaluate_quote(ds, q)
        econ = quote_economics(ds, q.quote, asof)
        if outcome == "net_contribution":
            y = econ.net_contribution_cents
        elif outcome == "margin_minus_returns":
            y = econ.margin_after_returns_cents
        else:
            raise ValueError(f"未知结局指标 {outcome}")
        rows.append(
            (
                q.original_boxes,
                1.0 if record.met_threshold else 0.0,
                y,
                q.buyer,
                q.has_waiver,
            )
        )
    return rows


def local_linear_rd(
    ds: Dataset,
    version: str,
    asof,
    *,
    bandwidths: tuple[int, ...] = (5, 8, 10, 12),
    outcome: str = "net_contribution",
    include_waivers: bool = False,
) -> CausalAssessment:
    rule = ds.rule_for(version)
    t = rule.threshold_boxes
    raw = _outcome_table(ds, version, asof, outcome)
    if not include_waivers:
        raw = [r for r in raw if not r[4]]

    estimates = []
    for h in bandwidths:
        sub = [r for r in raw if abs(r[0] - t) <= h]
        if not sub:
            continue
        boxes = np.array([r[0] - t for r in sub], dtype=float)
        treat = np.array([r[1] for r in sub], dtype=float)
        y = np.array([r[2] for r in sub], dtype=float)
        buyers = [r[3] for r in sub]
        X = np.column_stack([np.ones_like(boxes), boxes, treat, boxes * treat])
        beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        # 按买方聚类的夹心标准误（cluster-robust）
        k = X.shape[1]
        n = len(y)
        meat = np.zeros((k, k))
        for buyer in set(buyers):
            idx = [i for i, b in enumerate(buyers) if b == buyer]
            xg = X[idx]
            eg = resid[idx]
            score = xg.T @ eg
            meat += np.outer(score, score)
        xtx_inv = np.linalg.inv(X.T @ X)
        g = len(set(buyers))
        dfc = (g / (g - 1)) * ((n - 1) / (n - k)) if g > 1 else 1.0
        vcov = dfc * xtx_inv @ meat @ xtx_inv
        se = float(np.sqrt(max(vcov[2, 2], 0.0)))
        jump = float(beta[2])
        estimates.append(
            RDEstimate(
                rule_version=version,
                threshold=t,
                bandwidth=h,
                n_left=int((treat == 0).sum()),
                n_right=int((treat == 1).sum()),
                jump_cents=jump,
                clustered_se=se,
                t_stat=jump / se if se > 0 else float("nan"),
                ci95_low=jump - 1.96 * se,
                ci95_high=jump + 1.96 * se,
                outcome=outcome,
                includes_waivers=include_waivers,
            )
        )

    return estimates


def assess_threshold(
    ds: Dataset,
    version: str,
    asof,
    bunching: BunchingResult,
    confounding: ConfoundingResult,
    cross_side: CrossSideResult,
) -> CausalAssessment:
    rule = ds.rule_for(version)
    t = rule.threshold_boxes
    blockers = []
    if bunching.bunched:
        blockers.append(
            f"意向量在 {t} 处显著扎堆（超额 {bunching.excess_ratio:.0%}），"
            "操纵破坏密度连续性，局部随机分组不成立"
        )
    if confounding.confounded:
        blockers.append(
            "其他资格在同一断点跳变：" + confounding.detail
        )

    estimates = tuple(local_linear_rd(ds, version, asof))
    identifiable = not blockers
    if identifiable:
        applicability = (
            f"仅适用于 {version} 版本、意向量距门槛约 ±{max(e.bandwidth for e in estimates)} 箱"
            f"（{t - max(e.bandwidth for e in estimates)}–{t + max(e.bandwidth for e in estimates)} 箱）"
            "的普通资格询价；豁免单与窗口外人群不外推。"
        )
        note = "诊断通过，局部线性断点效应可作门槛附近的因果证据，但仍需结合拆单行为解读。"
    else:
        applicability = "该门槛无可识别因果效应的邻域；以下数字仅为描述性对照，不能作为改价依据。"
        note = "诊断失效：" + "；".join(blockers)
        note += f" 另注：{cross_side.note}"

    return CausalAssessment(
        rule_version=version,
        threshold=t,
        identifiable=identifiable,
        applicability=applicability,
        estimates=tuple(estimates),
        blockers=tuple(blockers),
        descriptive_note=note,
    )
