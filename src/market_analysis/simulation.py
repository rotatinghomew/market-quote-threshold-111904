"""下一期候选门槛的试算（不产生真报价）。

流程纪律：

1. 先扣除同一采购窗内仍受保护的旧报价：车与预算按 ``open_quotes`` 的未兑保留量
   预留；模拟的新优惠不得与旧承诺重复占用同一预算/车位。
2. 历史资料是多个采购窗的累计，而政策给的是“下一采购窗”的 8 个车位。所有
   装载与补贴测算都先按历史窗口逐窗计算，再取均值与高分位，绝不用全期累计箱量
   直接除单车容量。
3. 对每个候选门槛区分
   - 可识别行为响应范围：落在已通过诊断的历史门槛邻域内、且本批资料实际观察到
     的意向量；
   - 描述范围：在历史意向量支撑内、但没有因果证据的部分；
   - 压力试算范围：低于历史意向量下限（无观测支撑）的部分，只能在最有利假设下
     估算并显式标注。
4. 即使最有利假设也装不下现有车位/预算的候选，不得进入可批准清单。
5. 限额试用同样占用真实车与预算，且车数受 ``max_trial_trucks`` 约束。
6. 本模块输出均为试算结果，不生成、不修改任何报价。
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime

from .data import Dataset

from .diagnostics import BunchingResult, ConfoundingResult


@dataclass(frozen=True)
class Commitment:
    quote: str
    state: str
    valid_boxes: int
    reserved_cents: int
    reserved_truck_slots: int
    still_protected: bool
    reason: str


def active_commitments(ds: Dataset, asof: datetime) -> list[Commitment]:
    """按观察时点还原仍有效的旧报价承诺。

    资料约定：``open_quotes`` 只列“已经发出仍受保护”的特殊报价；状态为“有效/
    部分兑付”即占资源，是否已过报价纸面到期日仅作附注，不据此自行解除保护。
    """
    out = []
    for oq in ds.open_quotes:
        q = ds.quote_by_id(oq.quote)
        protected = oq.is_protected
        reason = (
            f"{oq.state}；记录于 {oq.known_at.isoformat()}，报价纸面到期 "
            f"{q.expires_at.isoformat()}；观察时点 {asof.isoformat()}"
        )
        if not protected:
            reason = "状态不再受保护，不占下一期资源"
        out.append(
            Commitment(
                quote=oq.quote,
                state=oq.state,
                valid_boxes=oq.valid_boxes if protected else 0,
                reserved_cents=oq.reserved_cents if protected else 0,
                reserved_truck_slots=oq.reserved_truck_slots if protected else 0,
                still_protected=protected,
                reason=reason,
            )
        )
    return out


@dataclass(frozen=True)
class Capacity:
    total_truck_slots: int
    reserved_truck_slots: int
    available_truck_slots: int
    boxes_per_truck: int
    available_boxes: int
    total_budget_cents: int
    reserved_budget_cents: int
    available_budget_cents: int
    fixed_cost_per_truck_cents: int
    commitments: tuple[Commitment, ...]


def remaining_capacity(ds: Dataset, asof: datetime) -> Capacity:
    policy = ds.policy
    commitments = tuple(active_commitments(ds, asof))
    reserved_slots = sum(c.reserved_truck_slots for c in commitments)
    reserved_budget = sum(c.reserved_cents for c in commitments)
    available_slots = policy.truck_slots - reserved_slots
    return Capacity(
        total_truck_slots=policy.truck_slots,
        reserved_truck_slots=reserved_slots,
        available_truck_slots=available_slots,
        boxes_per_truck=policy.boxes_per_truck,
        available_boxes=available_slots * policy.boxes_per_truck,
        total_budget_cents=policy.promo_budget_cents,
        reserved_budget_cents=reserved_budget,
        available_budget_cents=policy.promo_budget_cents - reserved_budget,
        fixed_cost_per_truck_cents=policy.truck_fixed_cost_cents,
        commitments=commitments,
    )


@dataclass(frozen=True)
class ReclassifiedQuote:
    quote: str
    buyer: str
    purchase_window: str
    original_boxes: int
    stall_boxes_realized: int
    evidence_status: str  # identified / descriptive / out_of_support


@dataclass(frozen=True)
class WindowDraw:
    """单个历史采购窗在某候选门槛下的资源占用。"""

    purchase_window: str
    base_claims: int          # 现行门槛下合格的询价数
    new_claims: int           # 新门槛新增的合格询价数
    new_identified: int
    new_descriptive: int
    new_out_of_support: int
    base_stall_boxes: int
    new_stall_boxes: int
    extra_subsidy_cents: int
    base_subsidy_cents: int
    promo_trucks_best_pack: int


@dataclass(frozen=True)
class Scenario:
    name: str
    mean_new_claims: float
    p90_new_claims: float
    mean_extra_subsidy_cents: float
    mean_total_promo_cents: float
    p90_total_promo_cents: float
    mean_promo_trucks: float
    p90_promo_trucks: int
    windows_over_slot: int     # 超出可用车位的历史窗口数
    windows_over_budget: int
    fits_slots_mean: bool
    fits_slots_p90: bool
    fits_budget_mean: bool
    fits_budget_p90: bool
    note: str


@dataclass(frozen=True)
class CandidateAssessment:
    threshold: int
    classification: str  # approvable / limited_trial / reject
    identified_range: tuple[int, int] | None
    descriptive_range: tuple[int, int] | None
    out_of_support_range: tuple[int, int] | None
    newly_eligible_quotes: int
    identified_new_quotes: int
    descriptive_new_quotes: int
    out_of_support_new_quotes: int
    blockers: tuple[str, ...]
    scenarios: tuple[Scenario, ...]
    rationale: str


def _stall_boxes(ds: Dataset, quote_id: str) -> int:
    return sum(
        o.boxes
        for o in ds.orders_for_quote(quote_id)
        if o.channel == "摊位"
    )


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)
    return float(ordered[idx])


def _window_draws(
    ds: Dataset,
    threshold: int,
    capacity: Capacity,
    *,
    identified_thresholds: dict[int, tuple[str, int]],
    observed_floor: int,
    current_threshold: int,
    subsidy_cents: int,
    include_oos: bool,
    include_descriptive: bool,
) -> list[WindowDraw]:
    version = "秋末"

    def evidence_for(boxes: int) -> str:
        # 低于历史意向量下限的部分无任何观测支撑，优先级最高：即便数值恰好靠近
        # 旧门槛，也不能借用另一规则版本的因果证据
        if boxes < observed_floor:
            return "out_of_support"
        for t0, (_src, band) in identified_thresholds.items():
            if abs(boxes - t0) <= band:
                return "identified"
        return "descriptive"

    by_window: dict[str, list] = {}
    for q in ds.quotes:
        if q.rule_version != version:
            continue
        by_window.setdefault(q.purchase_window, []).append(q)

    draws = []
    for window, quotes in sorted(by_window.items()):
        base_claims = new_claims = 0
        n_id = n_desc = n_oos = 0
        base_boxes = new_boxes = 0
        for q in quotes:
            stall = _stall_boxes(ds, q.quote)
            if q.original_boxes >= current_threshold:
                base_claims += 1
                base_boxes += stall
                continue
            if q.original_boxes < threshold:
                continue
            status = evidence_for(q.original_boxes)
            if status == "out_of_support" and not include_oos:
                continue
            if status == "descriptive" and not include_descriptive:
                continue
            new_claims += 1
            new_boxes += stall
            n_id += status == "identified"
            n_desc += status == "descriptive"
            n_oos += status == "out_of_support"

        promo_boxes = base_boxes + new_boxes
        draws.append(
            WindowDraw(
                purchase_window=window,
                base_claims=base_claims,
                new_claims=new_claims,
                new_identified=n_id,
                new_descriptive=n_desc,
                new_out_of_support=n_oos,
                base_stall_boxes=base_boxes,
                new_stall_boxes=new_boxes,
                extra_subsidy_cents=new_claims * subsidy_cents,
                base_subsidy_cents=base_claims * subsidy_cents,
                promo_trucks_best_pack=math.ceil(
                    promo_boxes / capacity.boxes_per_truck
                )
                if promo_boxes
                else 0,
            )
        )
    return draws


def _build_scenario(name: str, draws: list[WindowDraw], capacity: Capacity, note: str) -> Scenario:
    extra = [d.extra_subsidy_cents for d in draws]
    total = [d.base_subsidy_cents + d.extra_subsidy_cents for d in draws]
    trucks = [d.promo_trucks_best_pack for d in draws]
    claims = [d.new_claims for d in draws]
    mean_trucks = statistics.mean(trucks)
    p90_trucks = int(_percentile([float(t) for t in trucks], 0.9))
    over_slot = sum(1 for t in trucks if t > capacity.available_truck_slots)
    over_budget = sum(1 for v in total if v > capacity.available_budget_cents)
    return Scenario(
        name=name,
        mean_new_claims=statistics.mean(claims),
        p90_new_claims=_percentile([float(c) for c in claims], 0.9),
        mean_extra_subsidy_cents=statistics.mean(extra),
        mean_total_promo_cents=statistics.mean(total),
        p90_total_promo_cents=_percentile([float(v) for v in total], 0.9),
        mean_promo_trucks=mean_trucks,
        p90_promo_trucks=p90_trucks,
        windows_over_slot=over_slot,
        windows_over_budget=over_budget,
        fits_slots_mean=mean_trucks <= capacity.available_truck_slots,
        fits_slots_p90=p90_trucks <= capacity.available_truck_slots,
        fits_budget_mean=statistics.mean(total) <= capacity.available_budget_cents,
        fits_budget_p90=_percentile([float(v) for v in total], 0.9)
        <= capacity.available_budget_cents,
        note=note,
    )


def classify_candidate(
    ds: Dataset,
    threshold: int,
    asof: datetime,
    capacity: Capacity,
    *,
    identified_thresholds: dict[int, tuple[str, int]],
    observed_floor: int,
    current_threshold: int,
    regular_fee_cents: int,
    discount_fee_cents: int,
) -> CandidateAssessment:
    """以最近一期（秋末）逐采购窗询价为基准队列，评估把门槛改为 ``threshold``。"""
    policy = ds.policy
    subsidy = regular_fee_cents - discount_fee_cents
    common = dict(
        identified_thresholds=identified_thresholds,
        observed_floor=observed_floor,
        current_threshold=current_threshold,
        subsidy_cents=subsidy,
    )

    draws_id = _window_draws(
        ds, threshold, capacity, include_oos=False, include_descriptive=False, **common
    )
    draws_supported = _window_draws(
        ds, threshold, capacity, include_oos=False, include_descriptive=True, **common
    )
    draws_all = _window_draws(
        ds, threshold, capacity, include_oos=True, include_descriptive=True, **common
    )

    n_id = sum(d.new_identified for d in draws_all)
    n_desc = sum(d.new_descriptive for d in draws_all)
    n_oos = sum(d.new_out_of_support for d in draws_all)

    scenarios = (
        _build_scenario(
            "仅可识别范围（逐窗均值/P90）",
            draws_id,
            capacity,
            "只计入落在已验证门槛邻域内的新合格询价；最保守口径，可作批准依据。",
        ),
        _build_scenario(
            "全部观察支撑（含描述区）",
            draws_supported,
            capacity,
            "描述区无因果证据，仅作压力试算，不得当作行为响应承诺。",
        ),
        _build_scenario(
            "最有利满员假设（含无支撑区）",
            draws_all,
            capacity,
            "含低于历史意向量下限的人群，假设全部申领且按历史摊位量装车；上限压力试算。",
        ),
    )

    # 区间按数值覆盖给出（即使该区间内本批恰好没有询价，区间本身仍属于该证据
    # 层级）；计数字段 n_* 才反映实际观察到的询价数
    id_bounds = [
        (max(threshold, observed_floor, t0 - band),
         min(current_threshold - 1, t0 + band))
        for t0, (_src, band) in identified_thresholds.items()
    ]
    id_bounds = [b for b in id_bounds if b[0] <= b[1]]
    id_range = id_bounds[0] if id_bounds else None
    desc_lo = max(threshold, observed_floor)
    desc_range = (desc_lo, current_threshold - 1) if desc_lo <= current_threshold - 1 else None
    oos_hi = min(observed_floor - 1, current_threshold - 1)
    oos_range = (threshold, oos_hi) if threshold <= oos_hi else None

    blockers = []
    worst = scenarios[-1]
    if not worst.fits_slots_p90:
        blockers.append(
            f"最有利假设下 P90 窗口仍需 {worst.p90_promo_trucks} 个车位，"
            f"超过可用 {capacity.available_truck_slots} 个；"
            f"{worst.windows_over_slot} 个历史窗口会超载"
        )
    if not worst.fits_budget_p90:
        blockers.append(
            f"最有利假设下 P90 窗口优惠支出 {worst.p90_total_promo_cents:.0f} 分，"
            f"超过可用预算 {capacity.available_budget_cents} 分；"
            f"{worst.windows_over_budget} 个历史窗口会超预算"
        )
    if n_oos:
        blockers.append(
            f"{n_oos} 条新合格询价低于历史意向量下限 {observed_floor} 箱，行为响应无观测支撑"
        )
    elif oos_range is not None:
        blockers.append(
            f"门槛 {threshold} 低于历史意向量下限 {observed_floor} 箱，"
            f"区间 {oos_range[0]}–{oos_range[1]} 内无观测支撑；落入该区间的新询价只能做压力试算"
        )
    if threshold == current_threshold:
        blockers.append("与现行门槛相同，不构成新方案，无新增行为可评估")
    if threshold > current_threshold:
        blockers.append(
            f"门槛 {threshold} 高于现行 {current_threshold}，方向是收紧而非降门槛；"
            "历史上无该门槛运行记录，仅有压力试算"
        )

    identified = scenarios[0]
    is_tightening = threshold >= current_threshold
    if not worst.fits_slots_p90 or not worst.fits_budget_p90:
        classification = "reject"
    elif is_tightening or (n_id + n_desc + n_oos) == 0:
        # 收紧门槛或不改变任何资格：没有可评估的新行为，不存在试用对象
        classification = "reject"
    elif oos_range is not None:
        # 门槛本身探到观测支撑之外：可识别部分仍可限额试用，整体不得无条件批准
        trial_trucks = min(policy.max_trial_trucks, capacity.available_truck_slots)
        classification = "limited_trial" if trial_trucks > 0 else "reject"
    elif n_id == 0:
        trial_trucks = min(policy.max_trial_trucks, capacity.available_truck_slots)
        classification = "limited_trial" if trial_trucks > 0 else "reject"
        if trial_trucks <= 0:
            blockers.append("旧承诺已占满车位，无试用空间")
    elif identified.fits_slots_p90 and identified.fits_budget_p90:
        classification = "approvable"
    else:
        classification = "reject"

    rationale = _rationale(
        threshold, classification, n_id, n_desc, n_oos, scenarios, capacity
    )

    return CandidateAssessment(
        threshold=threshold,
        classification=classification,
        identified_range=id_range,
        descriptive_range=desc_range,
        out_of_support_range=oos_range,
        newly_eligible_quotes=n_id + n_desc + n_oos,
        identified_new_quotes=n_id,
        descriptive_new_quotes=n_desc,
        out_of_support_new_quotes=n_oos,
        blockers=tuple(blockers),
        scenarios=scenarios,
        rationale=rationale,
    )


def _rationale(threshold, classification, n_id, n_desc, n_oos, scenarios, cap) -> str:
    worst = scenarios[-1]
    parts = [
        f"候选门槛 {threshold}：新合格询价 {n_id + n_desc + n_oos} 条"
        f"（可识别 {n_id}、仅描述 {n_desc}、无观测支撑 {n_oos}）。",
        f"逐窗试算：最有利假设 P90 需 {worst.p90_promo_trucks} 车位"
        f"（可用 {cap.available_truck_slots}）、P90 优惠 {worst.p90_total_promo_cents:.0f} 分"
        f"（可用预算 {cap.available_budget_cents} 分）。",
    ]
    if classification == "approvable":
        parts.append("可识别场景的 P90 资源占用在车位与预算内，可列入批准清单，批准范围以可识别场景为限。")
    elif classification == "limited_trial":
        parts.append("缺乏可识别因果证据或含描述区人群，只能按限额试用上报，试用名额与预算单独锁定。")
    else:
        parts.append("存在硬约束冲突或无证据支撑，不进入可批准清单。")
    return "".join(parts)


def evaluate_all_candidates(
    ds: Dataset,
    asof: datetime,
    diagnostics: dict[str, dict],
    *,
    rd_band: int = 10,
) -> list[CandidateAssessment]:
    policy = ds.policy
    cap = remaining_capacity(ds, asof)
    current_rule = ds.rule_for("秋末")
    observed_floor = min(
        q.original_boxes for q in ds.quotes if q.rule_version == "秋末"
    )
    # 只有通过扎堆与混淆诊断的历史门槛才提供可识别证据
    identified_thresholds: dict[int, tuple[str, int]] = {}
    for version, diag in diagnostics.items():
        b: BunchingResult = diag["bunching"]
        c: ConfoundingResult = diag["confounding"]
        if not b.bunched and not c.confounded:
            identified_thresholds[b.threshold] = (version, rd_band)

    return [
        classify_candidate(
            ds,
            t,
            asof,
            cap,
            identified_thresholds=identified_thresholds,
            observed_floor=observed_floor,
            current_threshold=current_rule.threshold_boxes,
            regular_fee_cents=current_rule.regular_fee_cents,
            discount_fee_cents=current_rule.discount_fee_cents,
        )
        for t in policy.next_threshold_candidates
    ]
