"""净贡献核算与成熟度管理。

口径：

* 毛利 = 订单 ``margin_before_shipping_cents`` 之和，减去已成熟订单的退货
  ``margin_delta_cents``；采购窗内同买方的代销渠道成交一并计入净贡献。
* 运费只减有实装记录、且在观察时点已知的实收 ``paid_freight_cents``；代销订单
  无实装，运费不可核对时计入覆盖率缺口并标注可能方向，不臆造金额。
* 退货窗口（``returns_close``）晚于观察时点的订单标记为未成熟，其毛利只能按
  “未调整”展示，不得当作最终毛利。
* 退货只减毛利，不释放用车承诺；用车余量由实装与保护报价单独管理。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .data import Dataset, Quote


@dataclass(frozen=True)
class OrderEconomics:
    order: str
    channel: str
    boxes: int
    margin_before_shipping_cents: int
    returns_known_cents: int
    freight_paid_cents: int | None  # None = 无实装、不可核对
    mature: bool
    visible: bool


@dataclass(frozen=True)
class QuoteEconomics:
    quote: str
    rule_version: str
    originally_eligible: bool
    threshold_boxes: int
    original_boxes: int
    visible_orders: int
    mature_orders: int
    pending_orders: int
    margin_after_returns_cents: int
    unadjusted_margin_pending_cents: int
    freight_observed_cents: int
    freight_unobservable_orders: int
    net_contribution_cents: int
    stall_boxes_loaded: int
    all_channel_boxes: int
    coverage_note: str


def quote_economics(ds: Dataset, quote_id: str, asof: datetime) -> QuoteEconomics:
    q = ds.quote_by_id(quote_id)
    rule = ds.rule_for(q.rule_version)
    eligible = q.original_boxes >= rule.threshold_boxes or q.has_waiver

    margin = 0
    pending_margin = 0
    freight = 0
    visible = mature = pending = no_loading = 0
    stall_loaded = 0
    all_boxes = 0

    for order in ds.orders_for_quote(quote_id):
        if order.booked_at > asof:
            continue  # 观察时点之后才成交，当期不可见
        visible += 1
        all_boxes += order.boxes
        margin += order.margin_before_shipping_cents
        is_mature = order.returns_close <= asof
        if is_mature:
            mature += 1
            for ret in ds.returns_for_order(order.order):
                if ret.known_at <= asof:
                    margin -= ret.margin_delta_cents
        else:
            pending += 1
            pending_margin += order.margin_before_shipping_cents

        loading = ds.loading_for_order(order.order)
        if loading is not None and loading.known_at <= asof:
            freight += loading.paid_freight_cents
            stall_loaded += loading.loaded_boxes
        elif order.channel != "摊位":
            no_loading += 1  # 代销无实装：运费不可核对

    note = "全部可见订单运费可核对"
    if no_loading:
        note = (
            f"{no_loading} 个非摊位渠道订单无实装记录，卖方运费不可核对；"
            "净贡献未减这部分运费，真实净贡献可能更低（方向单向偏上）。"
        )

    return QuoteEconomics(
        quote=quote_id,
        rule_version=q.rule_version,
        originally_eligible=eligible,
        threshold_boxes=rule.threshold_boxes,
        original_boxes=q.original_boxes,
        visible_orders=visible,
        mature_orders=mature,
        pending_orders=pending,
        margin_after_returns_cents=margin,
        unadjusted_margin_pending_cents=pending_margin,
        freight_observed_cents=freight,
        freight_unobservable_orders=no_loading,
        net_contribution_cents=margin - freight,
        stall_boxes_loaded=stall_loaded,
        all_channel_boxes=all_boxes,
        coverage_note=note,
    )


@dataclass(frozen=True)
class WindowAggregate:
    purchase_window: str
    rule_version: str
    quotes: int
    mature_quotes: int
    pending_quotes: int
    net_contribution_cents: int
    pending_margin_cents: int
    unobservable_freight_orders: int


def window_aggregates(ds: Dataset, asof: datetime) -> list[WindowAggregate]:
    by_window: dict[str, list[QuoteEconomics]] = {}
    versions: dict[str, str] = {}
    for q in ds.quotes:
        econ = quote_economics(ds, q.quote, asof)
        by_window.setdefault(q.purchase_window, []).append(econ)
        versions[q.purchase_window] = q.rule_version

    out = []
    for window in sorted(by_window):
        econs = by_window[window]
        out.append(
            WindowAggregate(
                purchase_window=window,
                rule_version=versions[window],
                quotes=len(econs),
                mature_quotes=sum(1 for e in econs if e.pending_orders == 0),
                pending_quotes=sum(1 for e in econs if e.pending_orders > 0),
                net_contribution_cents=sum(e.net_contribution_cents for e in econs),
                pending_margin_cents=sum(
                    e.unadjusted_margin_pending_cents for e in econs
                ),
                unobservable_freight_orders=sum(
                    e.freight_unobservable_orders for e in econs
                ),
            )
        )
    return out


@dataclass(frozen=True)
class GroupStat:
    label: str
    n_quotes: int
    n_with_orders: int
    net_total_cents: int
    net_per_quote_cents: float
    pending_quotes: int
    unobservable_freight_orders: int


def group_statistics(ds: Dataset, asof: datetime) -> dict[tuple[str, bool], GroupStat]:
    """按规则版本 × 原始资格汇总——采购会上引用的均值口径，带成熟度与覆盖标注。"""
    buckets: dict[tuple[str, bool], list[QuoteEconomics]] = {}
    for q in ds.quotes:
        econ = quote_economics(ds, q.quote, asof)
        buckets.setdefault((q.rule_version, econ.originally_eligible), []).append(econ)

    out = {}
    for key, econs in sorted(buckets.items()):
        n = len(econs)
        net = sum(e.net_contribution_cents for e in econs)
        out[key] = GroupStat(
            label=f"{key[0]}/{'原始合格' if key[1] else '原始不合格'}",
            n_quotes=n,
            n_with_orders=sum(1 for e in econs if e.visible_orders > 0),
            net_total_cents=net,
            net_per_quote_cents=net / n,
            pending_quotes=sum(1 for e in econs if e.pending_orders > 0),
            unobservable_freight_orders=sum(
                e.freight_unobservable_orders for e in econs
            ),
        )
    return out
