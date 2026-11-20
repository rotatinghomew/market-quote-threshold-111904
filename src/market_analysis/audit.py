"""按报价编号的审计回查。

主管拿任意一个旧报价编号来，都能还原：当时适用的规则版本与门槛、该报价的原始
资格（含豁免依据）、实际兑付情况、截至两个观察时点的净贡献与成熟度，以及若该
报价仍受保护时占用的预算与车位。回查结果按“当时事实”呈现，不被后续换版覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .accounting import QuoteEconomics, quote_economics
from .data import Dataset
from .eligibility import EligibilityRecord, evaluate_quote
from .simulation import Commitment, active_commitments


@dataclass(frozen=True)
class QuoteAudit:
    quote_id: str
    rule_version: str
    threshold_boxes: int
    regular_fee_cents: int
    discount_fee_cents: int
    issued_at: datetime
    expires_at: datetime
    original_boxes: int
    other_eligibility: str
    waiver: str
    eligibility: EligibilityRecord
    economics_by_asof: dict[str, QuoteEconomics]
    open_commitment: Commitment | None
    narrative: list[str]


def audit_quote(ds: Dataset, quote_id: str) -> QuoteAudit:
    q = ds.quote_by_id(quote_id)
    rule = ds.rule_for(q.rule_version)
    eligibility = evaluate_quote(ds, q)

    economics = {
        ts.isoformat(): quote_economics(ds, quote_id, ts)
        for ts in ds.policy.observe_at
    }

    commitment = next(
        (c for c in active_commitments(ds, max(ds.policy.observe_at)) if c.quote == quote_id),
        None,
    )

    narrative = []
    if eligibility.met_threshold:
        narrative.append(
            f"询价意向 {q.original_boxes} 箱达到 {q.rule_version} 版门槛 "
            f"{rule.threshold_boxes} 箱，原始资格成立，优惠运费 {rule.discount_fee_cents} 分。"
        )
    elif eligibility.waiver_approved:
        narrative.append(
            f"意向 {q.original_boxes} 箱未达门槛 {rule.threshold_boxes} 箱，"
            f"但豁免字段为“{q.waiver}”，按主管批准还原优惠资格；成交结果不参与资格判定。"
        )
    else:
        narrative.append(
            f"意向 {q.original_boxes} 箱未达门槛 {rule.threshold_boxes} 箱且无豁免，"
            "原始资格不成立；即使后续凑量或成交放量，也不能倒推为合格。"
        )

    if eligibility.redemption_known:
        narrative.append(
            f"实装 {eligibility.loaded_orders} 单：实收优惠运费 "
            f"{eligibility.redeemed_discount} 单、普通运费 {eligibility.paid_regular} 单；"
            "兑付以实收运费而非 used_promo 标记为准。"
        )
    else:
        narrative.append("无摊位实装记录，实际运费兑付不可核对。")

    for ts, econ in economics.items():
        if econ.pending_orders:
            narrative.append(
                f"截至 {ts} 仍有 {econ.pending_orders} 单售后未成熟，"
                f"{econ.unadjusted_margin_pending_cents} 分毛利未经退货调整，不得视为最终毛利。"
            )
        if econ.freight_unobservable_orders:
            narrative.append(f"截至 {ts}：{econ.coverage_note}")

    if commitment is not None and commitment.still_protected:
        narrative.append(
            f"该报价仍受保护（{commitment.state}），未兑保留 {commitment.valid_boxes} 箱、"
            f"预算 {commitment.reserved_cents} 分、车位 {commitment.reserved_truck_slots} 个；"
            "新门槛模拟不得取消或重复占用这部分承诺。"
        )

    return QuoteAudit(
        quote_id=quote_id,
        rule_version=q.rule_version,
        threshold_boxes=rule.threshold_boxes,
        regular_fee_cents=rule.regular_fee_cents,
        discount_fee_cents=rule.discount_fee_cents,
        issued_at=q.issued_at,
        expires_at=q.expires_at,
        original_boxes=q.original_boxes,
        other_eligibility=q.other_eligibility,
        waiver=q.waiver or "无",
        eligibility=eligibility,
        economics_by_asof=economics,
        open_commitment=commitment,
        narrative=narrative,
    )
