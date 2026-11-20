"""余量与成熟证据的滚动更新规则。

业务约定的关键区分：

* 旧报价到期/状态失效 → 释放其预留的预算与车位；
* 部分兑付 → 只按“已兑付部分”缩减保留量，未兑部分继续占资源；
* 退货（整箱退回）→ 只减毛利，**绝不释放用车承诺**；退回一箱不等于退一次用车。

本模块只基于新到达的事实记录推演资源快照，不修改原始资料，也不生成报价。
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import Dataset, OpenQuote
from .simulation import Capacity, remaining_capacity


@dataclass(frozen=True)
class CapacityChange:
    quote: str
    event: str
    budget_delta_cents: int
    truck_slot_delta: int
    note: str


@dataclass(frozen=True)
class RollForward:
    asof: object
    capacity: Capacity
    changes: tuple[CapacityChange, ...]
    matured_orders: int
    returned_orders: int
    note: str


def roll_forward(
    ds: Dataset,
    previous_asof,
    new_asof,
    *,
    open_quote_states: dict[str, str] | None = None,
) -> RollForward:
    """从 ``previous_asof`` 滚动到 ``new_asof``。

    ``open_quote_states`` 可选，传入报价编号到最新状态（如“已兑付”“失效”）的
    映射，模拟旧报价到期后的释放；不传则按资料里的 ``open_quotes`` 现状计算。
    退货事件在两个时点之间到达时，只反映为成熟证据增加，不改动车位。
    """
    previous_cap = remaining_capacity(ds, previous_asof)
    changes: list[CapacityChange] = []

    effective: list[OpenQuote] = []
    for oq in ds.open_quotes:
        new_state = (open_quote_states or {}).get(oq.quote, oq.state)
        if new_state in {"有效", "部分兑付"}:
            effective.append(oq)
        else:
            changes.append(
                CapacityChange(
                    quote=oq.quote,
                    event=f"状态变为{new_state}",
                    budget_delta_cents=-oq.reserved_cents,
                    truck_slot_delta=-oq.reserved_truck_slots,
                    note="旧报价不再受保护，预留预算与车位全额释放",
                )
            )

    # 部分兑付：资料里的保留量已是“截至记录时的未兑保留量”，对照上一时点若
    # 出现缩减，按差额报告，而不是释放整条承诺
    for oq in effective:
        if oq.state == "部分兑付":
            changes.append(
                CapacityChange(
                    quote=oq.quote,
                    event="部分兑付",
                    budget_delta_cents=0,
                    truck_slot_delta=0,
                    note=(
                        f"未兑保留 {oq.valid_boxes} 箱、{oq.reserved_cents} 分继续占用；"
                        "已兑付部分此前已缩减保留量，整箱退货不释放车位"
                    ),
                )
            )

    new_cap = remaining_capacity(ds, new_asof)
    if open_quote_states:
        released_budget = sum(-c.budget_delta_cents for c in changes if c.budget_delta_cents < 0)
        released_slots = sum(-c.truck_slot_delta for c in changes if c.truck_slot_delta < 0)
        object.__setattr__(
            new_cap,
            "reserved_budget_cents",
            previous_cap.reserved_budget_cents - released_budget,
        )
        object.__setattr__(
            new_cap,
            "available_budget_cents",
            previous_cap.available_budget_cents + released_budget,
        )
        object.__setattr__(
            new_cap,
            "reserved_truck_slots",
            previous_cap.reserved_truck_slots - released_slots,
        )
        object.__setattr__(
            new_cap,
            "available_truck_slots",
            previous_cap.available_truck_slots + released_slots,
        )

    matured = 0
    returned = 0
    for order in ds.orders:
        if previous_asof < order.returns_close <= new_asof:
            matured += 1
        for ret in ds.returns_for_order(order.order):
            if previous_asof < ret.known_at <= new_asof:
                returned += 1

    note = (
        f"窗口内 {matured} 个订单售后成熟、{returned} 条退货记录入账；"
        "退货仅调整毛利，不产生车位或预算回流。"
    )
    return RollForward(
        asof=new_asof,
        capacity=new_cap,
        changes=tuple(changes),
        matured_orders=matured,
        returned_orders=returned,
        note=note,
    )
