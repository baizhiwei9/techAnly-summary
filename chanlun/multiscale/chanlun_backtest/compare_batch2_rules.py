#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二批（B2/B3）规则差异逐条对比出图（2026-11-11）

每条差异一张图，上下两栏：
  上 = 旧版口径，下 = 新版口径；虚线标出「只在一边出现」的信号。

输出:
  reports/batch2_rules/diff_<n>_<rule>.png
  reports/batch2_rules/diff_summary.csv

⚠️ 只读对比：不改实盘信号、不写状态文件。
"""
import os
import sys
import csv
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from chanlun_backtest import structure_indicators as si
from chanlun_backtest.structure_signals import detect_structure_signals
from chanlun_backtest.structure_signals_v2 import detect_structure_signals_v2
from chanlun_backtest.compare_v2_batch2 import (
    resample_weekly, candles, draw_zhongshu, COLOR, BUY_KINDS,
)

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "reports", "batch2_rules")

# 每条差异：规则说明 + 代表标的 + 信号类型 + 新版口径
CARDS = [
    dict(
        n=1, key="second_buy", kind="B2", variant="v2a",
        symbol="sh510300", name="沪深300ETF", tf="daily",
        desc="二买：旧版=任意更高的低点都算 → 新版=一买后第一个回踩，且不破一买低点",
    ),
    dict(
        n=2, key="second_sell", kind="S2", variant="v2a",
        symbol="sh510300", name="沪深300ETF", tf="daily",
        desc="二卖：旧版=任意更低的高点都算 → 新版=一卖后第一个反抽，且不破一卖高点",
    ),
    dict(
        n=3, key="third_buy", kind="B3", variant="v2a",
        symbol="sh510300", name="沪深300ETF", tf="weekly",
        desc="三买：旧版只看最后一个中枢 → 新版遍历所有中枢 + 中枢递进校验 + 现价必须站上 ZG",
    ),
    dict(
        n=4, key="first_buy_remote", kind="B1", variant="v2b",
        symbol="sz159915", name="创业板ETF", tf="daily",
        desc="一买：上一版 v2a 沿用旧背驰口径 → v2b 改用远程「段背驰」口径",
    ),
    dict(
        n=5, key="first_sell_remote", kind="S1", variant="v2b",
        symbol="sh510300", name="沪深300ETF", tf="daily",
        desc="一卖：上一版 v2a 沿用旧背驰口径 → v2b 改用远程「段背驰」口径",
    ),
]


def _load_bars(symbols):
    from data_fetcher import get_index_daily
    out = {}
    for sym in symbols:
        bars = get_index_daily(sym)
        if len(bars) < 200:
            print("跳过 %s：日线数据不足 (%d)" % (sym, len(bars)))
            continue
        df = pd.DataFrame(bars)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        if "datetime" in df.columns:
            df = df.rename(columns={"datetime": "timestamp"})
        out[sym] = df
    return out


def _as_dicts(signals):
    """v1 元组 / v2 dict 统一成 {idx, kind, level}"""
    out = []
    for s in signals:
        if isinstance(s, dict):
            out.append({"idx": int(s["idx"]), "kind": s["kind"],
                        "level": float(s.get("level") or 0.0)})
        else:
            i, k, _stop, lvl = s
            out.append({"idx": int(i), "kind": k, "level": float(lvl)})
    return out


def _window(signals, start):
    return [s for s in signals if s["idx"] >= start]


def _plot_card(card, df, tf_df, window, old_sigs, new_sigs,
               old_label, new_label):
    start = max(0, len(tf_df) - window)
    wdf = tf_df.iloc[start:].reset_index(drop=True)
    old_w = _window(old_sigs, start)
    new_w = _window(new_sigs, start)
    kind = card["kind"]

    old_keys = {(s["kind"], s["idx"]) for s in old_w if s["kind"] == kind}
    new_keys = {(s["kind"], s["idx"]) for s in new_w if s["kind"] == kind}
    old_only = sorted(i for k, i in old_keys - new_keys)
    new_only = sorted(i for k, i in new_keys - old_keys)

    fig, axes = plt.subplots(2, 1, figsize=(15, 11), sharex=True)
    for ax, sigs, label, color in [
        (axes[0], old_w, old_label, "#555555"),
        (axes[1], new_w, new_label, "#c0392b"),
    ]:
        candles(ax, wdf)
        draw_zhongshu(ax, df, start, len(wdf))

        for s in sigs:
            xi = s["idx"] - start
            if xi < 0 or xi >= len(wdf):
                continue
            k = s["kind"]
            if k not in COLOR:
                continue
            if k in BUY_KINDS:
                y = wdf["low"].iloc[xi] * 0.982
                ax.scatter([xi], [y], marker="^", s=110, color=COLOR[k],
                           edgecolor="white", linewidth=0.7, zorder=6)
                ax.text(xi, y * 0.990, k, ha="center", va="top", fontsize=8,
                        color=COLOR[k], fontweight="bold", zorder=7)
            else:
                y = wdf["high"].iloc[xi] * 1.018
                ax.scatter([xi], [y], marker="v", s=90, color=COLOR[k],
                           edgecolor="white", linewidth=0.7, zorder=6)
                ax.text(xi, y * 1.010, k, ha="center", va="bottom", fontsize=8,
                        color=COLOR[k], zorder=7)

        # 只在另一边出现的点：同一根K线上画圈
        for xi_abs in (old_only if ax is axes[0] else new_only):
            xi = xi_abs - start
            if 0 <= xi < len(wdf):
                yy = wdf["low"].iloc[xi] * 0.955 if ax is axes[0] else wdf["high"].iloc[xi] * 1.045
                ax.scatter([xi], [yy], marker="o", s=210, facecolors="none",
                           edgecolors=color, linewidths=1.4, linestyle="--", zorder=8)
                ax.axvline(xi, color=color, lw=0.8, ls=":", alpha=0.55, zorder=4)

        n_kind = sum(1 for s in sigs if s["kind"] == kind)
        ax.set_title("%s｜%s=%d" % (label, kind, n_kind), fontsize=12, loc="left")
        ax.set_ylabel("价格")
        ax.grid(alpha=0.18, lw=0.5)
        ax.set_xlim(-1, len(wdf))

        lo = wdf["low"].min(); hi = wdf["high"].max()
        pad = (hi - lo) * 0.10
        ax.set_ylim(lo - pad, hi + pad)

    step = max(1, len(wdf) // 10)
    ticks = list(range(0, len(wdf), step))
    axes[1].set_xticks(ticks)
    axes[1].set_xticklabels([str(wdf["timestamp"].iloc[i])[:10] for i in ticks],
                            rotation=25, ha="right", fontsize=8)

    note = "旧版独有 %d 个%s；新版独有 %d 个%s" % (
        len(old_only), kind, len(new_only), kind)
    if old_only or new_only:
        note += "｜旧：%s｜新：%s" % (
            ",".join(str(i) for i in old_only) or "-",
            ",".join(str(i) for i in new_only) or "-")
    fig.text(0.008, 0.012, note, fontsize=9, color="#333333")

    legend = [
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#1f77b4",
               markersize=9, label="买点"),
        Line2D([0], [0], marker="v", color="w", markerfacecolor="#9467bd",
               markersize=8, label="卖点"),
        Line2D([0], [0], marker="o", color="w", markeredgecolor="#555555",
               markersize=11, markeredgewidth=1.4, label="旧版独有"),
        Line2D([0], [0], marker="o", color="w", markeredgecolor="#c0392b",
               markersize=11, markeredgewidth=1.4, label="新版独有"),
    ]
    axes[0].legend(handles=legend, loc="upper left", fontsize=8, ncol=4,
                   framealpha=0.85)

    fig.suptitle("第二批差异 %d｜%s\n%s %s｜最近 %d 根K线｜黄框=中枢" % (
        card["n"], card["desc"], card["name"], card["tf"], len(wdf)),
        fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0.025, 1, 0.94])

    fn = os.path.join(OUT_DIR, "diff_%d_%s.png" % (card["n"], card["key"]))
    fig.savefig(fn, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return {
        "diff_no": card["n"],
        "rule": card["key"],
        "kind": kind,
        "symbol": card["symbol"],
        "name": card["name"],
        "tf": card["tf"],
        "old_label": old_label,
        "new_label": new_label,
        "old_count": sum(1 for s in old_w if s["kind"] == kind),
        "new_count": sum(1 for s in new_w if s["kind"] == kind),
        "old_only_idx": " ".join(str(i) for i in old_only),
        "new_only_idx": " ".join(str(i) for i in new_only),
        "chart": os.path.relpath(fn, os.path.dirname(OUT_DIR)),
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    symbols = sorted({c["symbol"] for c in CARDS})
    bars = _load_bars(symbols)

    # 每个标的只算一次指标 / 三套信号
    cache = {}
    for sym, df in bars.items():
        cache[sym] = {
            "daily": si.calculate_structure_indicators(df.copy()),
            "weekly": si.calculate_structure_indicators(resample_weekly(df)),
        }

    rows = []
    for card in CARDS:
        if card["symbol"] not in cache:
            print("跳过差异 %d：%s 无数据" % (card["n"], card["symbol"]))
            continue
        df = cache[card["symbol"]][card["tf"]]

        v1 = _as_dicts(detect_structure_signals(df))
        v2a = _as_dicts(detect_structure_signals_v2(df, b1_mode="v1"))
        v2b = _as_dicts(detect_structure_signals_v2(df, b1_mode="remote"))

        if card["variant"] == "v2b":
            old, new = v2a, v2b
            old_label, new_label = "v2a（沿用旧背驰）", "v2b（远程段背驰）"
        else:
            old, new = v1, v2a
            old_label, new_label = "旧版 v1", "新版 v2a"

        window = 320 if card["tf"] == "daily" else 160
        row = _plot_card(card, df, df, window, old, new, old_label, new_label)
        rows.append(row)
        print("[diff %d] %s %s %s  %s=%d -> %d  %s" % (
            card["n"], card["symbol"], card["tf"], card["kind"],
            card["kind"], row["old_count"], row["new_count"], row["chart"]))

    csv_path = os.path.join(OUT_DIR, "diff_summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("\n汇总: %s" % csv_path)


if __name__ == "__main__":
    main()
