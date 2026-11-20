"""资格还原。

核心纪律（对应业务约定）：

* 原始资格只能用询价时的 ``original_boxes`` 与获批 ``waiver`` 判定；客户事后凑量、
  拆并单、成交结果都不能倒推资格。
* 优惠是否实际兑现以实装 ``loading.paid_freight_cents`` 为准；``used_promo`` 标记
  在本批资料里与实收运费不一致，不能作为依据。
* 每条询价的资格、报价、实际兑付分别留痕，供审计按报价编号回查。
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import Dataset, Quote


@dataclass(frozen=True)
class EligibilityRecord:
    quote: str
    rule_version: str
    threshold_boxes: int
    original_boxes: int
    met_threshold: bool
    waiver_approved: bool
    originally_eligible: bool
    other_eligibility: str
    offered_fee_cents: int
    discount_fee_cents: int
    regular_fee_cents: int
    # 实际兑付（仅在有摊位实装记录时可知）
    realized_orders: int
    loaded_orders: int
    redeemed_discount: int  # 实装且实收等于优惠运费的订单数
    paid_regular: int       # 实装且实收等于普通运费的订单数
    redemption_known: bool


def evaluate_quote(ds: Dataset, quote: Quote) -> EligibilityRecord:
    rule = ds.rule_for(quote.rule_version)
    met = quote.original_boxes >= rule.threshold_boxes
    waiver = quote.has_waiver
    eligible = met or waiver

    redeemed = 0
    paid_regular = 0
    loaded = 0
    for order in ds.orders_for_quote(quote.quote):
        loading = ds.loading_for_order(order.order)
        if loading is None:
            continue  # 代销渠道无实装，运费不可核对
        loaded += 1
        if loading.paid_freight_cents == rule.discount_fee_cents:
            redeemed += 1
        elif loading.paid_freight_cents == rule.regular_fee_cents:
            paid_regular += 1

    return EligibilityRecord(
        quote=quote.quote,
        rule_version=quote.rule_version,
        threshold_boxes=rule.threshold_boxes,
        original_boxes=quote.original_boxes,
        met_threshold=met,
        waiver_approved=waiver,
        originally_eligible=eligible,
        other_eligibility=quote.other_eligibility,
        offered_fee_cents=quote.offered_fee_cents,
        discount_fee_cents=rule.discount_fee_cents,
        regular_fee_cents=rule.regular_fee_cents,
        realized_orders=len(ds.orders_for_quote(quote.quote)),
        loaded_orders=loaded,
        redeemed_discount=redeemed,
        paid_regular=paid_regular,
        redemption_known=loaded > 0,
    )


def evaluate_all(ds: Dataset) -> dict[str, EligibilityRecord]:
    return {q.quote: evaluate_quote(ds, q) for q in ds.quotes}


def find_split_groups(ds: Dataset) -> list[dict]:
    """同一买方、同一采购窗、同一规则版本下的拆并单分组。

    返回每个多询价分组及其门槛规避标记：每一条单独都不够门槛（且无豁免），
    但合并意向量达到门槛——即拆单以避免/失去优惠资格的直接证据。
    """
    groups: dict[tuple, list[Quote]] = {}
    for q in ds.quotes:
        groups.setdefault((q.buyer, q.purchase_window, q.rule_version), []).append(q)

    findings = []
    for (buyer, window, version), quotes in sorted(groups.items()):
        if len(quotes) < 2:
            continue
        rule = ds.rule_for(version)
        boxes = sorted(q.original_boxes for q in quotes)
        any_eligible = any(
            q.original_boxes >= rule.threshold_boxes or q.has_waiver for q in quotes
        )
        merged_total = sum(boxes)
        findings.append(
            {
                "buyer": buyer,
                "purchase_window": window,
                "rule_version": version,
                "quotes": [q.quote for q in quotes],
                "original_boxes": boxes,
                "merged_boxes": merged_total,
                "threshold": rule.threshold_boxes,
                "any_quote_eligible": any_eligible,
                "merged_would_qualify": merged_total >= rule.threshold_boxes,
                "split_below_threshold": (
                    not any_eligible and merged_total >= rule.threshold_boxes
                ),
            }
        )
    return findings
