"""按报价编号回查的命令行入口。

    PYTHONPATH=src python3 -m market_analysis.audit_cli --quote Q0006
"""
from __future__ import annotations

import argparse

from .audit import audit_quote
from .data import load_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="按报价编号还原当时门槛、资格与净贡献")
    parser.add_argument("--data", default="data")
    parser.add_argument("--quote", required=True)
    args = parser.parse_args()

    ds = load_dataset(args.data)
    result = audit_quote(ds, args.quote)

    print(f"报价编号：{result.quote_id}")
    print(
        f"规则版本：{result.rule_version}（门槛 {result.threshold_boxes} 箱，"
        f"普通运费 {result.regular_fee_cents} 分，优惠运费 {result.discount_fee_cents} 分）"
    )
    print(
        f"签发 {result.issued_at.isoformat()}，到期 {result.expires_at.isoformat()}；"
        f"意向量 {result.original_boxes} 箱；其他资格 {result.other_eligibility}；豁免 {result.waiver}"
    )
    e = result.eligibility
    print(
        f"原始资格：{'合格' if e.originally_eligible else '不合格'}"
        f"（达门槛={e.met_threshold}，豁免={e.waiver_approved}）"
    )
    print(
        f"实际兑付：实装 {e.loaded_orders} 单，优惠运费 {e.redeemed_discount} 单，"
        f"普通运费 {e.paid_regular} 单，可核对={e.redemption_known}"
    )
    print()
    for ts, econ in result.economics_by_asof.items():
        print(f"截至 {ts}")
        print(
            f"  可见订单 {econ.visible_orders}（成熟 {econ.mature_orders} / "
            f"未成熟 {econ.pending_orders}），调整后毛利 {econ.margin_after_returns_cents} 分，"
            f"已核运费 {econ.freight_observed_cents} 分，净贡献 {econ.net_contribution_cents} 分"
        )
        print(f"  覆盖说明：{econ.coverage_note}")
    if result.open_commitment is not None:
        c = result.open_commitment
        print(
            f"\n旧承诺：{c.state}，仍受保护={c.still_protected}，未兑保留 {c.valid_boxes} 箱、"
            f"{c.reserved_cents} 分、{c.reserved_truck_slots} 车位。{c.reason}"
        )
    print("\n审计叙述：")
    for line in result.narrative:
        print(f"- {line}")


if __name__ == "__main__":
    main()
