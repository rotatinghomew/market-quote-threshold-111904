"""只读数据访问层。

所有读取都不修改原始 CSV/JSON；时间字段保留带偏移的 ISO 字符串，比较时统一
解析为带时区的 ``datetime``。空字符串视为缺失值，绝不默认为零。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def parse_ts(value: str) -> datetime:
    """解析带 UTC 偏移的时间字符串。"""
    return datetime.fromisoformat(value)


def _as_int(value: str) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


@dataclass(frozen=True)
class PriceRule:
    version: str
    first_day: datetime
    threshold_boxes: int
    regular_fee_cents: int
    discount_fee_cents: int
    sales_channels: int

    @property
    def subsidy_cents(self) -> int:
        return self.regular_fee_cents - self.discount_fee_cents


@dataclass(frozen=True)
class Quote:
    quote: str
    buyer: str
    purchase_window: str
    issued_at: datetime
    expires_at: datetime
    rule_version: str
    original_boxes: int
    other_eligibility: str
    waiver: str  # "" 表示无豁免；"同意" 表示主管批准
    offered_fee_cents: int

    @property
    def has_waiver(self) -> bool:
        return self.waiver == "同意"


@dataclass(frozen=True)
class Order:
    order: str
    quote: str
    purchase_window: str
    channel: str
    boxes: int
    margin_before_shipping_cents: int
    booked_at: datetime
    returns_close: datetime


@dataclass(frozen=True)
class Loading:
    order: str
    truck: str
    loaded_boxes: int
    paid_freight_cents: int
    used_promo: int
    known_at: datetime


@dataclass(frozen=True)
class Return:
    order: str
    returned_boxes: int
    margin_delta_cents: int
    known_at: datetime


@dataclass(frozen=True)
class OpenQuote:
    quote: str
    valid_boxes: int
    reserved_cents: int
    reserved_truck_slots: int
    state: str
    known_at: datetime

    @property
    def is_protected(self) -> bool:
        return self.state in {"有效", "部分兑付"}


@dataclass(frozen=True)
class Policy:
    observe_at: tuple[datetime, ...]
    next_threshold_candidates: tuple[int, ...]
    truck_slots: int
    truck_fixed_cost_cents: int
    promo_budget_cents: int
    boxes_per_truck: int
    max_trial_trucks: int


@dataclass(frozen=True)
class Dataset:
    rules: dict[str, PriceRule]
    quotes: list[Quote]
    orders: list[Order]
    loading: list[Loading]
    returns: list[Return]
    open_quotes: list[OpenQuote]
    policy: Policy

    def rule_for(self, version: str) -> PriceRule:
        return self.rules[version]

    def quote_by_id(self, quote_id: str) -> Quote:
        return self._quote_index[quote_id]

    def orders_for_quote(self, quote_id: str) -> list[Order]:
        return self._orders_by_quote.get(quote_id, [])

    def loading_for_order(self, order_id: str) -> Loading | None:
        return self._loading_by_order.get(order_id)

    def returns_for_order(self, order_id: str) -> list[Return]:
        return self._returns_by_order.get(order_id, [])

    def open_quote(self, quote_id: str) -> OpenQuote | None:
        return self._open_by_quote.get(quote_id)

    def __post_init__(self) -> None:  # pragma: no cover - dataclass 钩子
        object.__setattr__(self, "_quote_index", {q.quote: q for q in self.quotes})
        object.__setattr__(self, "_orders_by_quote", _group(self.orders, lambda o: o.quote))
        object.__setattr__(
            self, "_loading_by_order", {ld.order: ld for ld in self.loading}
        )
        object.__setattr__(
            self, "_returns_by_order", _group(self.returns, lambda r: r.order)
        )
        object.__setattr__(
            self, "_open_by_quote", {oq.quote: oq for oq in self.open_quotes}
        )


def _group(rows, key):
    out: dict[str, list] = {}
    for row in rows:
        out.setdefault(key(row), []).append(row)
    return out


def load_dataset(data_dir: str | Path) -> Dataset:
    data_dir = Path(data_dir)
    rules = {}
    with (data_dir / "price_rules.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rule = PriceRule(
                version=row["version"],
                first_day=parse_ts(row["first_day"] + "T00:00:00+08:00"),
                threshold_boxes=int(row["threshold_boxes"]),
                regular_fee_cents=int(row["regular_fee_cents"]),
                discount_fee_cents=int(row["discount_fee_cents"]),
                sales_channels=int(row["sales_channels"]),
            )
            rules[rule.version] = rule

    with (data_dir / "quotes.csv").open(encoding="utf-8", newline="") as fh:
        quotes = [
            Quote(
                quote=row["quote"],
                buyer=row["buyer"],
                purchase_window=row["purchase_window"],
                issued_at=parse_ts(row["issued_at"]),
                expires_at=parse_ts(row["expires_at"]),
                rule_version=row["rule_version"],
                original_boxes=int(row["original_boxes"]),
                other_eligibility=row["other_eligibility"],
                waiver=row["waiver"],
                offered_fee_cents=int(row["offered_fee_cents"]),
            )
            for row in csv.DictReader(fh)
        ]

    with (data_dir / "orders.csv").open(encoding="utf-8", newline="") as fh:
        orders = [
            Order(
                order=row["order"],
                quote=row["quote"],
                purchase_window=row["purchase_window"],
                channel=row["channel"],
                boxes=int(row["boxes"]),
                margin_before_shipping_cents=int(
                    row["margin_before_shipping_cents"]
                ),
                booked_at=parse_ts(row["booked_at"]),
                returns_close=parse_ts(row["returns_close"]),
            )
            for row in csv.DictReader(fh)
        ]

    with (data_dir / "loading.csv").open(encoding="utf-8", newline="") as fh:
        loading = [
            Loading(
                order=row["order"],
                truck=row["truck"],
                loaded_boxes=int(row["loaded_boxes"]),
                paid_freight_cents=int(row["paid_freight_cents"]),
                used_promo=int(row["used_promo"]),
                known_at=parse_ts(row["known_at"]),
            )
            for row in csv.DictReader(fh)
        ]

    with (data_dir / "returns.csv").open(encoding="utf-8", newline="") as fh:
        returns = [
            Return(
                order=row["order"],
                returned_boxes=int(row["returned_boxes"]),
                margin_delta_cents=int(row["margin_delta_cents"]),
                known_at=parse_ts(row["known_at"]),
            )
            for row in csv.DictReader(fh)
        ]

    open_quotes: list[OpenQuote] = []
    with (data_dir / "open_quotes.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            open_quotes.append(
                OpenQuote(
                    quote=row["quote"],
                    valid_boxes=int(row["valid_boxes"]),
                    reserved_cents=int(row["reserved_cents"]),
                    reserved_truck_slots=int(row["reserved_truck_slots"]),
                    state=row["state"],
                    known_at=parse_ts(row["known_at"]),
                )
            )

    policy_raw = json.loads((data_dir / "policy.json").read_text(encoding="utf-8"))
    policy = Policy(
        observe_at=tuple(parse_ts(ts) for ts in policy_raw["observe_at"]),
        next_threshold_candidates=tuple(
            policy_raw["next_threshold_candidates"]
        ),
        truck_slots=int(policy_raw["truck_slots"]),
        truck_fixed_cost_cents=int(policy_raw["truck_fixed_cost_cents"]),
        promo_budget_cents=int(policy_raw["promo_budget_cents"]),
        boxes_per_truck=int(policy_raw["boxes_per_truck"]),
        max_trial_trucks=int(policy_raw["max_trial_trucks"]),
    )

    return Dataset(
        rules=rules,
        quotes=quotes,
        orders=orders,
        loading=loading,
        returns=returns,
        open_quotes=open_quotes,
        policy=policy,
    )
