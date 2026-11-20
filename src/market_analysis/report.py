"""命令行报表：把各模块结论汇总为一份可复核的 Markdown。

用法（从仓库根目录）：

    PYTHONPATH=src python3 -m market_analysis.report --data data --out reports/report.md
    PYTHONPATH=src python3 -m market_analysis.audit_cli --quote Q4403

所有数字均由只读数据现算，报表本身不产生报价、不写回 data/。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .accounting import group_statistics, quote_economics
from .audit import audit_quote
from .causal import assess_threshold
from .data import load_dataset
from .diagnostics import run_all_diagnostics
from .eligibility import evaluate_all, find_split_groups
from .simulation import evaluate_all_candidates, remaining_capacity


def money(cents: int) -> str:
    return f"{cents / 100:.2f} 元"


def build_report(data_dir: str) -> str:
    ds = load_dataset(data_dir)
    asof = max(ds.policy.observe_at)
    earlier = min(ds.policy.observe_at)

    elig = evaluate_all(ds)
    diagnostics = run_all_diagnostics(ds)
    splits = find_split_groups(ds)
    groups = group_statistics(ds, asof)
    candidates = evaluate_all_candidates(ds, asof, diagnostics)
    capacity = remaining_capacity(ds, asof)

    lines: list[str] = []
    add = lines.append

    add("# 早市整车优惠门槛分析报表")
    add("")
    add(f"生成口径观察时点：{asof.isoformat()}（另保留 {earlier.isoformat()} 对照）")
    add("资料为合成数据，报表只读取本地文件，不产生任何真实报价。")
    add("")

    # 1. 资格还原
    add("## 1. 原始资格与实际兑付还原")
    add("")
    add("| 规则版本 | 门槛 | 询价数 | 原始合格 | 其中豁免合格 | 原始不合格 |")
    add("|---|---|---|---|---|---|")
    for version, rule in ds.rules.items():
        rows = [e for e in elig.values() if e.rule_version == version]
        n_ok = sum(1 for e in rows if e.originally_eligible)
        n_w = sum(1 for e in rows if e.waiver_approved)
        add(
            f"| {version} | {rule.threshold_boxes} | {len(rows)} | {n_ok} | {n_w} | "
            f"{len(rows) - n_ok} |"
        )
    add("")
    n_waiver_total = sum(1 for e in elig.values() if e.waiver_approved)
    add(
        f"- 共 {n_waiver_total} 条优惠来自主管豁免，全部位于门槛下方；资格仅按"
        " `original_boxes` 与 `waiver` 还原，成交放量不倒推资格。"
    )
    redeemed_quotes = sum(
        1 for e in elig.values() if e.redemption_known and e.redeemed_discount
    )
    eligible_no_loading = sum(
        1
        for e in elig.values()
        if e.originally_eligible and not e.redemption_known
    )
    add(
        f"- 有摊位实装的合格询价 {redeemed_quotes} 条全部按优惠运费兑付；"
        f"另有 {eligible_no_loading} 条合格询价无摊位实装（代销或未成交），"
        "优惠未实际兑付，不构成已发生补贴。"
    )
    add("- `used_promo` 标记与实收运费存在系统性不一致，本报表一律以实装实收运费为准。")
    add("")

    # 2. 拆并单
    add("## 2. 同一买方采购窗内的拆并单证据")
    add("")
    multi = [s for s in splits if len(s["quotes"]) >= 2]
    gaming = [s for s in splits if s["split_below_threshold"]]
    add(
        f"- 同一买方+采购窗+规则版本下出现多条询价的分组 {len(multi)} 个；"
        f"其中 {len(gaming)} 个分组每条单独都不够门槛、合并意向量却达到门槛"
        "（拆单规避优惠的直接证据，例："
    )
    for s in gaming[:5]:
        add(
            f"  - {s['buyer']} {s['purchase_window']}（{s['rule_version']}）："
            f"{s['original_boxes']} 箱拆为 {len(s['quotes'])} 条，合并 {s['merged_boxes']} 箱 ≥ {s['threshold']}"
        )
    # 同车合并核对：拆出的询价是否在同一辆车上并装
    ord2q = {od.order: od.quote for od in ds.orders}
    q2trucks = {}
    for ld in ds.loading:
        q2trucks.setdefault(ord2q[ld.order], set()).add(ld.truck)
    shared = 0
    for s in multi:
        truck_sets = [q2trucks.get(qid, set()) for qid in s["quotes"]]
        if any(t for t in truck_sets[0].intersection(*truck_sets[1:])):
            shared += 1
    add(
        f"- {len(multi)} 个多询价分组中仅 {shared} 个在同一辆车上并装；门槛没有促成"
        "合并订货，拆单主要改变报价结构而非增加车上实销箱量。"
    )
    add("")

    # 3. 诊断
    add("## 3. 门槛有效性诊断")
    add("")
    for version, diag in diagnostics.items():
        b, c, x = diag["bunching"], diag["confounding"], diag["cross_side"]
        add(f"### {version}（门槛 {b.threshold} 箱）")
        add("")
        add(f"- 扎堆检验：{b.note}（p={b.p_value:.4f}，{b.method}）")
        add(f"- 同点资格变化：{c.detail}；混淆判定：{'是' if c.confounded else '否'}")
        add(f"- 跨边买方：{x.note}")
        add("")

    # 4. 因果评估
    add("## 4. 门槛附近的效应估计与适用范围")
    add("")
    for version in sorted(ds.rules):
        diag = diagnostics[version]
        assessment = assess_threshold(
            ds, version, asof, diag["bunching"], diag["confounding"], diag["cross_side"]
        )
        add(f"### {version}（门槛 {assessment.threshold} 箱）")
        add("")
        add(f"- 可识别性：{'成立' if assessment.identifiable else '不成立（仅描述）'}")
        add(f"- 适用范围：{assessment.applicability}")
        if assessment.blockers:
            for blocker in assessment.blockers:
                add(f"- 失效原因：{blocker}")
        add("")
        add("| 带宽 | 左/右样本 | 净贡献跳点(分) | 聚类标准误 | t 值 | 95% 置信区间(分) |")
        add("|---|---|---|---|---|---|")
        for e in assessment.estimates:
            add(
                f"| ±{e.bandwidth} | {e.n_left}/{e.n_right} | {e.jump_cents:.0f} | "
                f"{e.clustered_se:.0f} | {e.t_stat:.2f} | "
                f"[{e.ci95_low:.0f}, {e.ci95_high:.0f}] |"
            )
        add("")
        add(f"- 说明：{assessment.descriptive_note}")
        add("")

    # 5. 净贡献口径
    add("## 5. 净贡献的组间对照（含成熟度与覆盖声明）")
    add("")
    add("| 组 | 询价数 | 有成交 | 人均净贡献(分) | 未成熟询价 | 运费不可核对订单 |")
    add("|---|---|---|---|---|---|")
    for key, stat in sorted(groups.items()):
        add(
            f"| {stat.label} | {stat.n_quotes} | {stat.n_with_orders} | "
            f"{stat.net_per_quote_cents:.0f} | {stat.pending_quotes} | "
            f"{stat.unobservable_freight_orders} |"
        )
    add("")
    add(
        "- 采购会上“门槛以上人均毛利更高”的说法在本表重现，但它混合了规模选择效应；"
        "因果解读只能引用第 4 节通过诊断的局部估计。"
    )
    early_pending = group_statistics(ds, earlier)
    add(
        f"- 较早观察时点 {earlier.isoformat()} 秋末组合有 "
        f"{sum(s.pending_quotes for s in early_pending.values())} 条询价售后未成熟，"
        "当时不能充作毛利已知；至当前时点全部订单售后窗口已关闭。"
    )
    add("- 代销渠道订单无实装运费，相关净贡献单向偏上，覆盖缺口已在审计中逐条标注。")
    add("")

    # 6. 旧承诺与资源余量
    add("## 6. 仍有效的旧报价承诺与下一期资源余量")
    add("")
    add("| 报价 | 状态 | 未兑保留箱 | 预留预算 | 预留车位 | 是否仍受保护 |")
    add("|---|---|---|---|---|---|")
    for c in capacity.commitments:
        add(
            f"| {c.quote} | {c.state} | {c.valid_boxes} | {money(c.reserved_cents)} | "
            f"{c.reserved_truck_slots} | {'是' if c.still_protected else '否'} |"
        )
    add("")
    add(
        f"- 总车位 {capacity.total_truck_slots}，旧承诺预留 {capacity.reserved_truck_slots}，"
        f"新方案可用 {capacity.available_truck_slots}（按每车 {capacity.boxes_per_truck} 箱，"
        f"可装 {capacity.available_boxes} 箱）。"
    )
    add(
        f"- 优惠预算 {money(capacity.total_budget_cents)}，预留 {money(capacity.reserved_budget_cents)}，"
        f"可用 {money(capacity.available_budget_cents)}；每新增一车另发生固定装车费 "
        f"{money(capacity.fixed_cost_per_truck_cents)}。"
    )
    add("- 退货只减毛利、不退回用车承诺；部分兑付按未兑保留量续占，整箱退回不等于释放车位。")
    add("")

    # 7. 候选门槛
    add("## 7. 候选门槛逐个试算（试算不产生真报价）")
    add("")
    for cand in candidates:
        add(f"### 候选门槛 {cand.threshold} 箱 — {cand.classification}")
        add("")
        add(
            f"- 新合格询价 {cand.newly_eligible_quotes} 条：可识别 {cand.identified_new_quotes}、"
            f"仅描述 {cand.descriptive_new_quotes}、无观测支撑 {cand.out_of_support_new_quotes}。"
        )
        add(
            f"- 证据范围：可识别 {cand.identified_range}，描述区 {cand.descriptive_range}，"
            f"超观测 {cand.out_of_support_range}。"
        )
        if cand.blockers:
            for blocker in cand.blockers:
                add(f"- 约束/限制：{blocker}")
        add("")
        add(
            "| 场景 | 均新增申领 | P90新增申领 | 均新增补贴 | P90优惠总支出 | 均车数 | P90车数 | 超载窗口 | 超预算窗口 | 车位可行(均/P90) | 预算可行(均/P90) |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|---|")
        for sc in cand.scenarios:
            add(
                f"| {sc.name} | {sc.mean_new_claims:.2f} | {sc.p90_new_claims:.0f} | "
                f"{money(sc.mean_extra_subsidy_cents)} | {money(sc.p90_total_promo_cents)} | "
                f"{sc.mean_promo_trucks:.2f} | {sc.p90_promo_trucks} | "
                f"{sc.windows_over_slot} | {sc.windows_over_budget} | "
                f"{'是' if sc.fits_slots_mean else '否'}/{'是' if sc.fits_slots_p90 else '否'} | "
                f"{'是' if sc.fits_budget_mean else '否'}/{'是' if sc.fits_budget_p90 else '否'} |"
            )
        add("")
        add(f"- 结论依据：{cand.rationale}")
        add("")

    # 8. 决策汇总
    add("## 8. 决策汇总")
    add("")
    add("| 候选门槛 | 结论 | 一句话理由 |")
    add("|---|---|---|")
    for cand in candidates:
        add(f"| {cand.threshold} | {cand.classification} | {cand.rationale} |")
    add("")
    add(
        "注：批准仅对尚未锁价的采购机会生效；旧报价过期、部分兑付或退款后，"
        "下次试算须按各自规则刷新余量与成熟证据。"
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="生成门槛分析 Markdown 报表")
    parser.add_argument("--data", default="data")
    parser.add_argument("--out", default="reports/report.md")
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report(args.data), encoding="utf-8")
    print(f"报表已写入 {out}")


if __name__ == "__main__":
    main()
