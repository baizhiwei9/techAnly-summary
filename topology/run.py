#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py · 拓扑流形分析统一入口
================================
对 指数/行业/概念/板块 做 SPD 流形 + TDA + 里奇曲率分析并出图。

用法：
  python3 topology/run.py --target 沪深300
  python3 topology/run.py --target 沪深300,科创50,中证500,上证50,创业板指
  python3 topology/run.py --target 指数:沪深300 --target 行业:电子
  python3 topology/run.py --target 概念:人工智能 板块:半导体
  python3 topology/run.py --list                      # 列出支持的指数/行业/概念

参数：
  --target   目标标的（可多次、逗号分隔、混类型）；类型前缀 指数:/行业:/概念:/板块:/个股:
  --days     收益窗口交易日数（默认 60，实际取 days+1 个交易日收盘）
  --out      输出目录（默认 reports/topology）
  --no-spd   不画单标的四合一 SPD 流形图
  --no-phase 不画多标的二维相位图
  --list     列出支持的指数/行业/概念/板块后退出
"""
from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from topology import targets, data_loader, spd, plot
else:
    from . import targets, data_loader, spd, plot


def _stock_name_map():
    """ts_code → 中文名（取数层尽力取，失败 {}）"""
    mod, mode = data_loader.bridge()
    if mod is None:
        return {}
    try:
        if mode == "bridge":
            return mod.stock_name_map()
        df = mod.get_stock_list()
        m = {}
        if df is not None and hasattr(df, "columns") and len(df):
            if "ts_code" in df.columns and "name" in df.columns:
                m = {str(c): str(n) for c, n in zip(df["ts_code"], df["name"]) if c and n}
        return m
    except Exception:
        return {}


def _safe_name(s):
    import re
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", str(s)).strip("_") or "target"


def do_list():
    print("== 支持的指数 ==")
    for alias, code in sorted(set(targets.INDEX_ALIASES.items())):
        if "." in code:
            print("  %-10s -> %s" % (alias, code))
    print("\n== 行业板块（前 30）==")
    for n, c in data_loader.board_list("行业")[:30]:
        print("  %-12s %s" % (n, c))
    print("\n== 概念板块（前 30）==")
    for n, c in data_loader.board_list("概念")[:30]:
        print("  %-12s %s" % (n, c))


def main(argv=None):
    ap = argparse.ArgumentParser(description="拓扑流形分析统一入口")
    ap.add_argument("--target", action="append", default=[], help="目标标的（可多次/逗号分隔）")
    ap.add_argument("--days", type=int, default=60, help="收益窗口交易日数（默认 60）")
    ap.add_argument("--out", default="reports/topology", help="输出目录")
    ap.add_argument("--no-spd", action="store_true", help="不画单标的四合一图")
    ap.add_argument("--no-phase", action="store_true", help="不画二维相位图")
    ap.add_argument("--list", action="store_true", help="列出支持的标的后退出")
    a = ap.parse_args(argv)

    if a.list:
        do_list()
        return 0

    if not a.target:
        ap.error("请用 --target 指定目标（--list 查看支持的标的）")

    os.makedirs(a.out, exist_ok=True)
    name_map = _stock_name_map()

    # 预取板块列表（解析无前缀行业/概念/板块用）
    idx_boards = data_loader.board_list("行业")
    cpt_boards = data_loader.board_list("概念")

    resolved = []
    for arg in a.target:
        for seg in str(arg).split(","):
            seg = seg.strip()
            if seg:
                resolved.append(targets.resolve(seg, idx_boards, cpt_boards))

    results = []          # [(name, color, spd_res), ...]
    done, skipped = [], []
    for t in resolved:
        if t.kind == "stock":
            skipped.append((t.raw, "个股无成分股，SPD 流形分析不支持"))
            continue
        if t.kind == "unknown":
            skipped.append((t.raw, "无法识别标的类型"))
            continue

        print("\n=== %s（%s）===" % (t.name, t.kind), flush=True)
        codes = data_loader.components(t)
        if not codes:
            skipped.append((t.name, "取不到成分股（概念板块暂不支持）"))
            continue
        print("  成分股 %d 只" % len(codes), flush=True)
        dates, close = data_loader.close_panel(codes, a.days)
        if close is None:
            skipped.append((t.name, "成分股日线面板取数失败"))
            continue
        idx_close = data_loader.index_close(t.code, a.days) if t.kind == "index" else None
        res = spd.compute(close, idx_close, a.days, verbose=True)
        if res is None:
            skipped.append((t.name, "有效样本不足（<16 交易日或 <5 成分股）"))
            continue

        color = plot.color_for(len(results))
        name = t.name or t.raw
        if not a.no_spd:
            png = os.path.join(a.out, "%s_SPD流形图.png" % _safe_name(name))
            plot.plot_spd(res, name, png, name_map)
            print("  [saved] %s" % png, flush=True)
        results.append((name, color, res))
        done.append(name)

        zone = spd.classify(res["corr_c"][-1], res["h1n"][-1])
        print("  结论: corr=%.2f H1n=%.2f 熵=%.2f Hurst=%.2f → %s"
              % (res["corr_c"][-1], res["h1n"][-1], res["ent"][-1],
                 res["hurst_full"], plot.ZONE_LABEL[zone][0]), flush=True)

    if results and not a.no_phase:
        phase_png = os.path.join(a.out, "SPD流形二维相位图.png")
        plot.plot_phase(results, phase_png)
        print("\n[saved] %s" % phase_png, flush=True)

    print("\n========== 汇总 ==========")
    print("成功：%s" % ("、".join(done) if done else "无"))
    for raw, why in skipped:
        print("跳过：%s（%s）" % (raw, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
