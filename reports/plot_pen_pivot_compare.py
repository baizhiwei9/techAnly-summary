#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""笔 / 中枢 引擎差异对比图（vendor 远程快照 vs 本地 structure_indicators）

⚠️ 2026-11-12 起线上 chan_smc_index 已改为本地引擎；本脚本的「远程」侧固定读
   chanlun_multiscale/chanlun_backtest/vendor/chan_smc_index.py，仅用于复现统一前差异。

用法（在含 data_fetcher 的工作目录或设置 DATA_FETCHER_PATH）：
    python3 reports/plot_pen_pivot_compare.py sh510300 --n 180 --out reports

输出：
    pen_compare_<sym>.png    上：远程笔（间隔≥3合并K线）｜下：本地笔（MIN_BI_GAP=4，≥5根）
    pivot_compare_<sym>.png  上：远程中枢（仅最近3段重叠）｜下：本地中枢（逐笔链式）
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib import font_manager

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "bin"))
sys.path.insert(0, os.path.join(REPO, "chanlun", "multiscale"))

import chan_smc_index as C                       # noqa: E402
import chanlun_backtest.structure_indicators as LI  # noqa: E402

# 远程引擎快照（vendor 逐字节副本，仅用于历史对比）；现行 chan_smc_index 已是本地口径
import importlib.util as _ilu
_vp = os.path.join(REPO, "chanlun", "multiscale", "chanlun_backtest", "vendor",
                   "chan_smc_index.py")
_spec = _ilu.spec_from_file_location("vendor_chan_smc_index", _vp)
R = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(R)

for _f in ("Noto Serif CJK SC", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "SimHei"):
    try:
        font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [_f]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False


def load(sym):
    try:
        from data_fetcher import get_index_daily
        rows = get_index_daily(sym)
        if rows:
            df = pd.DataFrame(rows)
            df = df.rename(columns={"datetime": "dt"})
            df["dt"] = pd.to_datetime(df["dt"])
            return df.sort_values("dt").reset_index(drop=True)
    except Exception as e:
        print("[WARN] data_fetcher:", e)
    tf, code = (sym, sym.upper()) if "." in sym else ("day", sym)
    d = C.fetch_daily(code) if "." in sym else None
    if d is None:
        raise SystemExit("取不到 %s 数据（需要 data_fetcher 或 $TECH_WS）" % sym)
    return d


def _vendor_mb_idx(bars):
    """与 vendor 远程 merge_bars 完全一致，额外记录原始极值索引。"""
    out = []   # [h, l, last_i, hi_i, lo_i]
    for i, (h, l) in enumerate(bars):
        h, l = float(h), float(l)
        if not out:
            out.append([h, l, i, i, i])
            continue
        ph, pl, _last, phi, plo = out[-1]
        if (h <= ph and l >= pl) or (h >= ph and l <= pl):
            up = len(out) < 2 or out[-1][0] >= out[-2][0]
            if up:
                nhi = i if h > ph else phi
                nlo = i if l > pl else plo
                out[-1] = [max(ph, h), max(pl, l), i, nhi, nlo]
            else:
                nhi = i if h < ph else phi
                nlo = i if l < pl else plo
                out[-1] = [min(ph, h), min(pl, l), i, nhi, nlo]
        else:
            out.append([h, l, i, i, i])
    return out


def remote_geometry(df):
    bars = [(float(h), float(l)) for h, l in zip(df["high"], df["low"])]
    mb = _vendor_mb_idx(bars)
    bi = R.build_bi(mb)
    segs = R.segs_from_bi(bi)

    def oi(midx, kind):
        return int(mb[midx][3] if kind == 1 else mb[midx][4])

    for sg in segs:
        sg["i0o"] = oi(sg["i0"], -1 if sg["up"] else 1)
        sg["i1o"] = oi(sg["i1"], 1 if sg["up"] else -1)
    pts = [{"orig": s["i0o"], "price": s["from"], "type": (-1 if s["up"] else 1)} for s in segs]
    if segs:
        pts.append({"orig": segs[-1]["i1o"], "price": segs[-1]["to"],
                    "type": (1 if segs[-1]["up"] else -1)})
    # 远程中枢：从最后往前，第一个 3 段重叠
    zs = None
    for i in range(len(segs) - 3, -1, -1):
        w = segs[i:i + 3]
        zg = min(x["hi"] for x in w)
        zd = max(x["lo"] for x in w)
        if zg > zd:
            zs = {"zd": zd, "zg": zg,
                  "x0": min(x["i0o"] for x in w), "x1": max(x["i1o"] for x in w)}
            break
    return {"mb": mb, "bi": bi, "segs": segs, "pts": pts, "zs": zs}


def local_geometry(df):
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    merged = LI.merge_klines(h, l)
    fr = LI.find_fractals(merged)
    ends = LI.build_bi(fr)
    zs = LI.detect_zhongshu(ends)
    pts = [{"orig": e.kline_idx, "price": e.price, "type": (1 if e.kind == "top" else -1)}
           for e in ends]
    zlist = []
    for z in zs:
        zlist.append({"zd": z.zd, "zg": z.zg,
                      "x0": ends[z.start_bi].kline_idx, "x1": ends[z.end_bi].kline_idx})
    return {"merged": merged, "fr": fr, "ends": ends, "zs": zs, "pts": pts, "zlist": zlist}


def candles(ax, df, start):
    v = df.iloc[start:].reset_index(drop=True)
    x = np.arange(len(v))
    o, h, l, c = (v[k].astype(float).values for k in ("open", "high", "low", "close"))
    up = c >= o
    ax.vlines(x, l, h, color="#888", lw=0.7, zorder=1)
    ax.bar(x, (c - o), bottom=o, width=0.6,
           color=np.where(up, "#e74c3c", "#2ecc71"), edgecolor="none", zorder=2)
    return x, v


def draw_pens(ax, pts, start, n, color, ls, label):
    # 两端各多留 20% 窗口，避免跨窗口的笔被截断
    use = [(p["orig"] - start, p["price"]) for p in pts
           if -0.2 * n <= p["orig"] - start <= 1.2 * n]
    ax.plot([p[0] for p in use], [p[1] for p in use], color=color, lw=1.6,
            ls=ls, marker="o", ms=2.5, label=label, zorder=4)
    return len([1 for x, _ in use if 0 <= x < n])


def draw_zones(ax, zones, start, n, color, label, colors=None):
    cnt = 0
    for i, z in enumerate(zones):
        x0, x1 = z["x0"] - start, z["x1"] - start
        if x1 < -5 or x0 > n + 5:
            continue
        x0, x1 = max(x0, -0.5), min(x1, n - 0.5)
        col = colors[i % len(colors)] if colors else color
        ax.add_patch(Rectangle((x0, z["zd"]), max(x1 - x0, 1), z["zg"] - z["zd"],
                               facecolor=col, alpha=0.28, edgecolor=col, lw=1.2,
                               zorder=3, label=(label if cnt == 0 else None)))
        ax.text(x1, z["zg"], " ZG %.2f" % z["zg"], fontsize=7, va="bottom", color=col)
        ax.text(x1, z["zd"], " ZD %.2f" % z["zd"], fontsize=7, va="top", color=col)
        cnt += 1
    return cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sym", nargs="?", default="sh510300")
    ap.add_argument("--n", type=int, default=180)
    ap.add_argument("--out", default=HERE)
    args = ap.parse_args()

    df = load(args.sym)
    # 指标用全量算，图上只画最近 n 根
    rem = remote_geometry(df)
    loc = local_geometry(df)
    start = max(0, len(df) - args.n)
    n = len(df) - start

    plt.close("all")
    fig, axes = plt.subplots(2, 1, figsize=(15, 10), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1]})
    for ax, engine, title, color, ls in (
        (axes[0], rem, "远程笔：间隔 ≥3 根合并K线，共 %d 笔" % (len(rem["bi"]) - 1), "#1f77b4", "-"),
        (axes[1], loc, "本地笔：MIN_BI_GAP=4（≥5 根合并K线），共 %d 笔" % (len(loc["ends"]) - 1), "#e67e22", "--"),
    ):
        candles(ax, df, start)
        cnt = draw_pens(ax, engine["pts"], start, n, color, ls, "笔")
        ax.set_title("%s ｜ 窗口内端点 %d 个" % (title, cnt), fontsize=11, loc="left")
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left", fontsize=9)
        ax.set_ylabel("价格")
    axes[1].set_xlabel("最近 %d 根日K（x=0 为窗口起点）" % n)
    fig.suptitle("%s 笔对比：同一段K线、两套成笔阈值" % args.sym, fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    p1 = os.path.join(args.out, "pen_compare_%s.png" % args.sym)
    fig.savefig(p1, dpi=150)
    print("saved", p1)

    plt.close("all")
    fig, axes = plt.subplots(2, 1, figsize=(15, 10), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1]})
    candles(axes[0], df, start)
    rz = [rem["zs"]] if rem["zs"] else []
    cr = draw_zones(axes[0], rz, start, n, "#1f77b4", "远程中枢")
    axes[0].set_title("远程中枢：只取最近 3 笔重叠，共 %d 个" % cr, fontsize=11, loc="left")
    axes[0].grid(alpha=0.25)
    axes[0].legend(loc="upper left", fontsize=9)
    axes[0].set_ylabel("价格")

    candles(axes[1], df, start)
    recent = [z for z in loc["zlist"] if z["x1"] >= start][-4:]
    cl = draw_zones(axes[1], recent, start, n, "#e67e22", "本地中枢",
                    colors=["#e67e22", "#9b59b6", "#16a085", "#c0392b"])
    axes[1].set_title("本地中枢：逐笔链式延伸，窗口内 %d 个（全量 %d 个）"
                      % (cl, len(loc["zlist"])), fontsize=11, loc="left")
    axes[1].grid(alpha=0.25)
    axes[1].legend(loc="upper left", fontsize=9)
    axes[1].set_xlabel("最近 %d 根日K（x=0 为窗口起点）" % n)
    fig.suptitle("%s 中枢对比：远程「最近3段」vs 本地「逐笔链式」" % args.sym, fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    p2 = os.path.join(args.out, "pivot_compare_%s.png" % args.sym)
    fig.savefig(p2, dpi=150)
    print("saved", p2)
    print("stats: remote pens=%d local pens=%d remote zs=%d local zs=%d"
          % (len(rem["bi"]) - 1, len(loc["ends"]) - 1, len(rz), len(loc["zlist"])))


if __name__ == "__main__":
    main()
