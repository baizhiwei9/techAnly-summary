#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""five_index · 五大宽基「缠论买卖点 + 拓扑结构」合并总览图
====================================================================
大佬 2026-11-14 要求：
  「日报里不分析 5 个指数嘛…你再带上这 5 个指数的拓扑结构图，把它们放一张图上」

每行一个指数（沪深300 / 中证500 / 中证1000 / 创业板指 / 科创50）：
  左：缠论·分型版（K线 + 笔 + 中枢 ZG/ZD + 一/二/三买卖点 + 阶段/背驰）
  右：拓扑结构（成分股 UMAP 2D 结构位置 + 连通簇/闭环/平均曲率/结构分 + 趋势层）

口径全部复用库里既有实现，**不在本文件重算任何结构**：
  · 缠论：charts.chan_fractal.chan_geometry → chanlun/fractal/chan_smc_index
  · 拓扑：topology/manifold_topo（corr → Ollivier-Ricci 曲率 → UMAP → Betti → hermer2 结构分）
  · 趋势层：manifold_topo.trend_layer（含方向，与结构分物理隔离）

取数：
  · 指数日线：data_hub `index_daily`（缺则 tushare）
  · 成分股收盘面板：`--panel` 给的全市场逐日 CSV 目录（close_YYYYMMDD.csv，列 ts_code,close）
                     ，没给就直接问 data_hub

用法：
  python3 charts/five_index.py --out reports/charts/five_index/five_20261114.png
  python3 charts/five_index.py --panel /root/.cache/topo5 --days 60 --n 120
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403  (仓库根/sys.path/net_guard/中文字体/Agg)

# 数据层定位：本机 data_hub 在 workspace/optstack（不在本仓库内）。
# 未显式给 DATA_HUB_PATH 时按已知位置兜底，避免退化成老链路直连 tushare（慢且易超时）。
if not os.environ.get("DATA_HUB_PATH"):
    for _c in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..",
                            ".openclaw", "workspace", "optstack"),
               "/root/.openclaw/workspace/optstack"):
        if os.path.isdir(_c):
            os.environ["DATA_HUB_PATH"] = os.path.abspath(_c)
            break

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

from charts.chan_fractal import chan_geometry, BUY_STYLE, SELL_STYLE
import topology.manifold_topo as MT

RED, GRN = "#d62728", "#2ca02c"
INK, MUTED, GRID = "#2b2b2b", "#7a7a7a", "#e6e6e6"
TITLE_RED = "#d92b2b"
NAVY = "#0d2b5e"

INDICES = [("沪深300", "000300.SH"), ("中证500", "000905.SH"),
           ("中证1000", "000852.SH"), ("创业板指", "399006.SZ"),
           ("科创50", "000688.SH")]


# ───────────────────────── 取数 ─────────────────────────
def _datahub():
    mod = None
    cands = []
    env = os.environ.get("DATA_HUB_PATH")
    if env:
        cands.append(os.path.abspath(os.path.expanduser(env)))
    cands.append(os.path.join(os.path.dirname(WS), "datahub-three-tier"))
    cands.append(os.path.join(WS, "optstack"))
    for base in cands:
        if not os.path.isdir(base):
            continue
        if base not in sys.path:
            sys.path.insert(0, base)
        try:
            import importlib
            mod = importlib.import_module("data_hub")
            break
        except Exception:
            continue
    return mod


def index_daily(code, days=400):
    """指数日线 → DataFrame[dt, open, high, low, close, volume]，升序。"""
    mod = _datahub()
    if mod is not None:
        try:
            d = mod.get_data("index_daily", ts_code=code)
            if d is not None and len(d):
                d = d.copy()
                d["dt"] = pd.to_datetime(d["trade_date"].astype(str))
                if "vol" in d.columns:
                    d = d.rename(columns={"vol": "volume"})
                if "volume" not in d.columns:
                    d["volume"] = 0.0
                return d.sort_values("dt").reset_index(drop=True)
        except Exception as e:
            print("[five] index_daily(data_hub) %s 失败 %s" % (code, str(e)[:60]), flush=True)
    import chan_smc_index as C
    return C.fetch_daily(code)


def components(code):
    mod = _datahub()
    d = mod.get_data("index_components", index_code=code)
    if isinstance(d, (list, tuple)):
        return [str(x) for x in d]
    col = "con_code" if "con_code" in d.columns else "ts_code"
    return [str(x) for x in d[col].tolist()]


def panel_from_csv_dir(dirpath, codes, n_days=61):
    """全市场逐日 CSV（close_YYYYMMDD.csv: ts_code,close）→ 收盘面板。"""
    fs = sorted(glob.glob(os.path.join(dirpath, "close_*.csv")))
    if len(fs) < n_days + 1:
        raise RuntimeError("面板 CSV 不足：%d 个 < %d" % (len(fs), n_days + 1))
    fs = fs[-(n_days + 1):]
    want = set(codes)
    frames = []
    for f in fs:
        d8 = os.path.basename(f)[6:14]
        d = pd.read_csv(f, usecols=["ts_code", "close"])
        d = d[d.ts_code.isin(want)]
        s = d.set_index("ts_code")["close"]
        s.name = d8
        frames.append(s)
    panel = pd.concat(frames, axis=1).T          # 行=日期，列=代码
    panel = panel.dropna(axis=1, how="any")
    return list(panel.index), panel


# ───────────────────────── 缠论面板 ─────────────────────────
def draw_chan(ax, df, name, code, n_show):
    total = len(df)
    start = max(0, total - n_show)
    view = df.iloc[start:].reset_index(drop=True)
    nv = len(view)
    geo = chan_geometry(df)
    ck, zs = geo["ck"], geo["zs"]

    o = view["open"].astype(float).values
    h = view["high"].astype(float).values
    l = view["low"].astype(float).values
    c = view["close"].astype(float).values
    prev = np.concatenate([[np.nan], c[:-1]])
    if start > 0:
        prev[0] = float(df["close"].iloc[start - 1])
    cols = [RED if (np.isfinite(prev[i]) and c[i] >= prev[i]) else GRN for i in range(nv)]
    bw, rng = 0.62, max(h.max() - l.min(), 1e-9)
    for i in range(nv):
        ax.vlines(i, l[i], h[i], color=cols[i], lw=0.7, zorder=3)
        y0, y1 = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - bw / 2, y0), bw, max(y1 - y0, rng * 0.0008),
                               facecolor=cols[i], edgecolor=cols[i], lw=0.4, zorder=4))
    # 笔
    sx, sy = [], []
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -2 <= xi <= nv + 2:
            sx.append(xi)
            sy.append(p["price"])
    if len(sx) >= 2:
        ax.plot(sx, sy, color="#1f4e79", lw=1.3, zorder=6)
    # 中枢
    if zs and geo["zs_x"]:
        x0 = max(geo["zs_x"][0] - start, -0.5)
        x1 = geo["zs_x"][1] - start
        if x1 >= -1:
            ax.add_patch(Rectangle((x0, zs[0]), x1 - x0, zs[1] - zs[0],
                                   facecolor="#ffd24d", alpha=0.22,
                                   edgecolor="#b8860b", lw=1.2, zorder=3))
            ax.axhline(zs[1], color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
            ax.axhline(zs[0], color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
            ax.text(max(x0, 0) + 0.5, (zs[0] + zs[1]) / 2, "中枢", va="center",
                    ha="left", fontsize=9, color="#8b6508", alpha=0.9, zorder=8)
    # 买卖点（只标新鲜/长期有效的，最多各 2 个，避免糊）
    for kind, lst, styles, dy in (("买", ck.get("buy_list") or [], BUY_STYLE, -1),
                                  ("卖", ck.get("sell_list") or [], SELL_STYLE, 1)):
        shown = 0
        for b in lst:
            tag = b["tag"]
            if tag not in styles or shown >= 2:
                continue
            mk, col, sz = styles[tag]
            cand = [p for p in geo["bi"] if p["type"] == (1 if kind == "卖" else -1)]
            if not cand:
                continue
            pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
            xi = pt["orig"] - start
            if not (-1 <= xi <= nv):
                continue
            ax.scatter([xi], [pt["price"]], s=sz * 0.55, marker=mk, color=col,
                       edgecolor="white", linewidth=0.9, zorder=10)
            ax.annotate("%s%.0f" % (tag, b["level"]), xy=(xi, pt["price"]),
                        xytext=(0, 22 * dy), textcoords="offset points",
                        ha="center", va=("top" if dy < 0 else "bottom"),
                        fontsize=8.5, color=col, fontweight="bold", zorder=11,
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=col,
                                  alpha=0.9, lw=0.7))
            shown += 1

    zpos = {"in": "中枢内", "above": "中枢上方", "below": "中枢下方"}.get(ck.get("zpos"), "无中枢")
    P = float(c[-1])
    last_ts = view["dt"].iloc[-1].strftime("%Y-%m-%d")
    ax.set_title("%s（%s） %s  收盘 %.2f ｜ %s ｜ 背驰：%s"
                 % (name, code, last_ts, P, ck.get("stage", ""), ck.get("div") or "无"),
                 fontsize=11.5, color=INK, pad=6, loc="left")
    ax.set_ylabel("价格", fontsize=9)
    ax.grid(True, ls=":", color=GRID, alpha=0.6)
    ax.set_xlim(-2, nv + 1)
    ax.tick_params(labelsize=8)
    step = max(nv // 8, 1)
    idxs = list(range(0, nv, step))
    ax.set_xticks(idxs)
    ax.set_xticklabels([view["dt"].dt.strftime("%m-%d").iloc[i] for i in idxs], fontsize=8)
    return ck, zpos


# ───────────────────────── 拓扑面板 ─────────────────────────
def topology_calc(close, index_code=None, days=60):
    close = close.dropna(axis=1, how="any")
    codes = list(close.columns)
    ret = np.log(close / close.shift(1)).dropna()
    corr = np.nan_to_num(ret.corr().values, nan=0.0)
    mom5 = (close.iloc[-1] / close.iloc[-6] - 1).values * 100
    curv, deg, G, ec = MT.ollivier_ricci(corr, k=5)
    mf = MT.corr_manifold_inputs(close, k=5)
    b0, b1 = mf["b0"], mf["b1"]
    half = len(ret) // 2
    b0p, _a, _b = MT._betti0_of_window(ret.iloc[:half])
    b0n, _c, _d = MT._betti0_of_window(ret.iloc[half:])
    if b0p < 0 or b0n < 0:
        b0p = b0n = None
    curv_mean = float(np.mean(curv))
    mkt = MT.hermer2_score(b0, b1, curv_mean, b0p, b0n)
    try:
        trend = MT.trend_layer(close, index_code=index_code)
    except Exception:
        trend = MT.trend_layer(close)
    dmat = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
    np.fill_diagonal(dmat, 0.0)
    emb = MT.umap_embed(dmat, n_neighbors=10, min_dist=0.3)
    return dict(emb=emb, mom5=mom5, curv=curv, mkt=mkt, trend=trend,
                b0=b0, b1=b1, n=len(codes), corr=corr, days=days,
                n_ret=len(ret))


def draw_topo(ax, name, T):
    emb, mom5, curv = T["emb"], T["mom5"], T["curv"]
    mkt, trend = T["mkt"], T["trend"]
    combo = 0.6 * MT.zs(mom5) + 0.4 * MT.zs(curv)
    cls = np.where(combo >= 0.45, 0, np.where(combo <= -0.45, 1, 2))
    colmap = {0: "#2eb872", 1: "#e05252", 2: "#3b6fd4"}
    colors = [colmap[c] for c in cls]
    mr = pd.Series(mom5).rank(pct=True).values
    size = 12 + 110 * mr
    ax.scatter(emb[:, 0], emb[:, 1], s=size, c=colors, alpha=0.72,
               edgecolors="white", linewidths=0.5, zorder=3)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_facecolor("#fbfbfd")

    score = mkt.get("score", float("nan"))
    verdict = mkt.get("verdict", "")
    txt = ("结构分（不含方向）%+.2f  %s\n"
           "连通簇 %s ｜ 闭环 %s ｜ 平均脆弱度 %.2f\n"
           "样本 %d 只 · 近 %d 个交易日"
           % (score, verdict,
              "—" if T["b0"] < 0 else T["b0"],
              "—" if T["b1"] < 0 else T["b1"],
              float(np.mean(curv)), T["n"], T.get("days", 60)))
    ax.set_title("拓扑结构 · %s" % name, fontsize=11.5, color=INK, pad=6, loc="left")
    ax.text(0.012, 0.03, txt, transform=ax.transAxes, fontsize=8.6, color="#3a3a3a",
            va="bottom", ha="left", linespacing=1.55,
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#dcdcdc", lw=0.8, alpha=0.95))
    tt = MT.trend_layer_text(trend).replace("趋势层（含方向）：", "")
    ax.text(0.012, 0.985, "趋势层（含方向）：" + tt, transform=ax.transAxes,
            fontsize=8.2, color=NAVY, va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.3", fc="#f2f6fd", ec="#c8d8f0",
                      lw=0.7, alpha=0.95))


# ───────────────────────── 主流程 ─────────────────────────
def build(out=None, panel_dir=None, days=60, n_show=120):
    rows = []
    for name, code in INDICES:
        df = index_daily(code)
        hits = components(code)
        if panel_dir:
            dates, close = panel_from_csv_dir(panel_dir, hits, days + 1)
        else:
            from topology import data_loader as DL
            dates, close = DL.close_panel(hits, days)
            if close is None:
                raise RuntimeError("取不到成分股日线面板：%s" % name)
        tp = topology_calc(close, code, days)
        rows.append((name, code, df, tp))
        print("[five] %s 缠论+拓扑 完成（成分 %d）" % (name, tp["n"]), flush=True)

    nrow = len(rows)
    fig = plt.figure(figsize=(24.0, 4.05 * nrow + 1.5), facecolor="white")
    gs = fig.add_gridspec(nrow, 2, width_ratios=[2.35, 1.0],
                          hspace=0.42, wspace=0.16,
                          left=0.035, right=0.985, top=0.945, bottom=0.045)
    for i, (name, code, df, tp) in enumerate(rows):
        ax1 = fig.add_subplot(gs[i, 0])
        ck, zpos = draw_chan(ax1, df, name, code, n_show)
        ax2 = fig.add_subplot(gs[i, 1])
        draw_topo(ax2, name, tp)

    tail = rows[-1][2]["dt"].iloc[-1].strftime("%Y-%m-%d")
    fig.text(0.035, 0.982, "五大宽基 · 缠论买卖点 + 拓扑结构 一图总览（%s）" % tail,
             fontsize=25, color=TITLE_RED, weight="bold")
    fig.text(0.035, 0.958,
             "左＝缠论·分型版（包含处理→分型→笔→中枢 ZG/ZD→背驰；★买点 ▼卖点，黄框＝最近中枢）　｜　"
             "右＝成分股拓扑（UMAP 结构位置 ｜ 颜色＝动能×脆弱度，气泡＝近5日动量）",
             fontsize=12.5, color=MUTED)
    fig.text(0.035, 0.012,
             "图例：★ 买点（一买/二买/三买）　▼ 卖点（一卖/二卖/三卖）　黄框＝最近中枢　蓝线＝笔　"
             "｜　拓扑右栏：结构分只看相关矩阵几何（不含价格方向），方向看「趋势层」那一行。"
             "数据：DataHub / tushare。",
             fontsize=11, color=MUTED)
    os.makedirs(os.path.dirname(out or "./x.png"), exist_ok=True)
    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[five] 输出 %s" % out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        WS, "reports", "charts", "five_index",
        "五大宽基_缠论买卖点+拓扑_%s.png" % datetime.now().strftime("%Y%m%d")))
    ap.add_argument("--panel", default="", help="全市场逐日 CSV 目录（close_YYYYMMDD.csv）")
    ap.add_argument("--days", type=int, default=60, help="拓扑收益窗口（交易日）")
    ap.add_argument("--n", type=int, default=120, help="缠论图显示最近多少根日K")
    a = ap.parse_args()
    build(a.out, a.panel or None, a.days, a.n)


if __name__ == "__main__":
    main()
