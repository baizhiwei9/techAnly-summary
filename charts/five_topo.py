#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""five_topo · 五大宽基「纯拓扑结构」总览图（不掺缠论）
====================================================================
大佬 2026-11-14 20:08 要求：
  「之前不两个图吗？左图是 UMAP，右图是里奇曲率。你这个开一个我看不清。
    要把两个图都合到那个图上去，要不就单独发一个——缠论的是缠论的，拓扑图是拓扑图。
    还有我也不知道你是看多还看空，信息太乱。」

→ 本图只画拓扑，一个指数一行，**左 = UMAP 结构位置，右 = 里奇曲率联动网络**，
   全宽画布（不挤），每行左上角给一条**明确多空结论**（看多/偏多/偏空/看空 + 结构支持度）。

口径（全部复用库内实现，不重算）：
  · 相关矩阵 → 相关距离 → UMAP 2D          （MT.umap_embed）
  · 相关网络 → Ollivier-Ricci 曲率           （MT.ollivier_ricci）
  · 连通簇 Betti_0 / 闭环 Betti_1 / 结构分    （MT.corr_manifold_inputs / MT.hermer2_score）
  · 趋势层（含方向）                          （MT.trend_layer）

多空判定规则（写死、可复现）：
  方向票 = [近5日>0, 近10日>0, 现价>MA60] 三票
    3票 → 看多　　2票 → 偏多震荡　　1票 → 偏空震荡　　0票 → 看空
  结构支持度（只看几何，不含方向）：
    结构分 >= +0.50 → 结构支持（抱团聚合）
    结构分 <= -0.50 → 结构不支持（散开分化）
    其余            → 结构中性的

用法：
  python3 charts/five_topo.py --out reports/charts/five_topo/five_topo.png
  python3 charts/five_topo.py --panel /root/.cache/topo5 --days 60
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403

if not os.environ.get("DATA_HUB_PATH"):
    for _c in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..",
                            ".openclaw", "workspace", "optstack"),
               "/root/.openclaw/workspace/optstack"):
        if os.path.isdir(_c):
            os.environ["DATA_HUB_PATH"] = os.path.abspath(_c)
            break

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.lines import Line2D
import networkx as nx

import topology.manifold_topo as MT
from charts.five_index import INDICES, components, panel_from_csv_dir, index_daily

RED, GRN = "#d62728", "#2ca02c"
INK, MUTED, GRID = "#2b2b2b", "#7a7a7a", "#e6e6e6"
NAVY = "#0d2b5e"
BLUE, ORANGE = "#3b6fd4", "#e08a1e"


# ───────────── 版面常量（2026-11-14 大佬：图太窄、字看不清 → 往下垒、放大）─────────────
# 关键：字号/画布宽 的比值决定「显示时字有多大」。画布越窄、字号越大 → 字越大。
DEFAULT_W = 16.0        # 画布宽（英寸）
ROW_H = 7.6             # 每个指数一块（结论条 + 两张图）的高度（英寸）
DPI = 108               # 输出清晰度（16in×108 ≈ 1730px 宽）
FS = dict(
    title=19,           # 子图标题 ①②
    sub_name=30,        # 指数名
    sub_label=25,       # 多空标签
    struct=19.5,        # 结构分那行
    detail=17.5,        # 方向票那行
    legend=16.0,        # UMAP 图例
    choke=16.0,         # 咽喉清单
    mark=13,            # 咽喉圈内 ①②③
    fig_title=34,
    fig_note=18,
    fig_foot=15,
)

# ─────────────────────── 计算 ───────────────────────
def topo_calc(close, index_code=None, days=60):
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
    return dict(emb=emb, mom5=mom5, curv=curv, deg=deg, corr=corr, mkt=mkt,
                trend=trend, b0=b0, b1=b1, n=len(codes), codes=codes, days=days)


def verdict(trend, mkt):
    """明确多空结论：3 票制（近5日/近10日/vs MA60）。返回 (标签, 颜色, 明细行)。"""
    r5 = trend.get("r5", 0.0)
    r10 = trend.get("r10", 0.0)
    vs = trend.get("vs_ma60", 0.0)
    votes = [r5 > 0, r10 > 0, vs > 0]
    k = sum(votes)
    if k == 3:
        lab, col = "看多", RED
    elif k == 2:
        lab, col = "偏多震荡", "#e0703e"
    elif k == 1:
        lab, col = "偏空震荡", "#4a86c8"
    else:
        lab, col = "看空", GRN
    score = mkt.get("score", float("nan"))
    if score >= 0.5:
        sup, sup_c = "结构支持（抱团聚合）", "#f0f0f0"
    elif score <= -0.5:
        sup, sup_c = "结构不支持（散开分化）", "#f0f0f0"
    else:
        sup, sup_c = "结构中性", "#f0f0f0"
    detail = ("方向票 %d/3：近5日 %+.1f%%｜近10日 %+.1f%%｜vs MA60 %+.1f%%｜站上MA20 %.0f%%"
              % (k, r5, r10, vs, trend.get("above20", float("nan"))))
    return lab, col, detail, sup, sup_c


# ─────────────────────── 画图 ───────────────────────
def draw_row(fig, row_gs, ri, name, T, gs=None):
    emb, mom5, curv = T["emb"], T["mom5"], T["curv"]
    corr, mkt, trend = T["corr"], T["mkt"], T["trend"]
    codes = T["codes"]
    names = T.get("names") or {}
    n = len(codes)
    lab, col, detail, sup, _ = verdict(trend, mkt)

    # ═══ ① UMAP 结构位置（整行宽，占满，不再是窄条）═══
    ax = fig.add_subplot(row_gs[1, 0])
    ax.set_facecolor("#fcfcfe")
    combo = 0.6 * MT.zs(mom5) + 0.4 * MT.zs(curv)
    cls = np.where(combo >= 0.45, 0, np.where(combo <= -0.45, 1, 2))
    colmap = {0: "#2eb872", 1: "#e05252", 2: "#3b6fd4"}
    mr = pd.Series(mom5).rank(pct=True).values
    ax.scatter(emb[:, 0], emb[:, 1], s=70 + 520 * mr,
               c=[colmap[c] for c in cls], alpha=0.78,
               edgecolors="white", linewidths=0.9, zorder=3)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.margins(0.10)
    ax.set_title("① 结构位置（UMAP）· 气泡＝近5日动量", fontsize=FS["title"],
                 color=INK, pad=9, loc="left", weight="bold")
    ax.text(0.012, 0.03, "绿＝机会侧（动量强+不脆弱）\n红＝风险侧（动量弱或结构脆）\n蓝＝中性",
            transform=ax.transAxes, fontsize=FS["legend"], color="#3a3a3a", va="bottom",
            linespacing=1.7,
            bbox=dict(boxstyle="round,pad=0.45", fc="white", ec="#dedede", lw=1.0, alpha=0.95))

    # ═══ ② 里奇曲率联动网络（整行宽）═══
    ax2 = fig.add_subplot(row_gs[1, 1])
    ax2.set_facecolor("#fcfcfe")
    sim = np.clip(corr, 0, None).copy()
    np.fill_diagonal(sim, 0.0)
    G = nx.Graph()
    G.add_nodes_from(range(n))
    for i in range(n):
        for j in np.argsort(sim[i])[::-1][:5]:
            if i < j:
                G.add_edge(i, int(j), w=float(sim[i, j]))
    pos = nx.spring_layout(G, weight="w", seed=11, k=1.9 / np.sqrt(n), iterations=200)
    for u, v, dd in G.edges(data=True):
        a = float(np.clip((dd["w"] - 0.2) / 0.7, 0.08, 0.72))
        ax2.plot([pos[u][0], pos[v][0]], [pos[u][1], pos[v][1]],
                 color="#9aa7bb", lw=0.8 + 2.0 * max(0.0, dd["w"] - 0.3), alpha=a, zorder=1)
    rk = pd.Series(curv).rank(method="average").values
    rkn = (rk - 1) / max(1.0, (len(rk) - 1))
    sizes = 220 + 12 * T["deg"]
    for i in range(n):
        ax2.scatter([pos[i][0]], [pos[i][1]], s=sizes[i],
                    c=[MT.CURV_CMAP(rkn[i])], edgecolors="white", linewidths=1.1, zorder=3)
    order = np.argsort(curv)
    _top3 = list(order[:3])
    _marks = ["\u2460", "\u2461", "\u2462"]
    for r_, i in enumerate(_top3):
        ax2.scatter([pos[i][0]], [pos[i][1]], s=sizes[i] + 620, facecolors="none",
                    edgecolors=NAVY, linewidths=2.4, zorder=4)
        ax2.annotate(_marks[r_], (pos[i][0], pos[i][1]), textcoords="offset points",
                     xytext=(0, 0), fontsize=FS["mark"], color=NAVY, weight="bold",
                     ha="center", va="center", zorder=7)
    _lines = []
    for r_, i in enumerate(_top3):
        _lines.append("%s %s %+.2f" % (_marks[r_],
                                       names.get(codes[i], codes[i]), curv[i]))
    ph = ax2.text(0.012, 0.020, "咽喉（曲率最负·枢纽股）\n" + "\n".join(_lines),
                  transform=ax2.transAxes, fontsize=FS["choke"], color=NAVY,
                  va="bottom", ha="left", linespacing=1.7,
                  bbox=dict(boxstyle="round,pad=0.40", fc="white", ec="#d5d5d5",
                            lw=1.0, alpha=0.95), zorder=9)
    fig.canvas.draw()
    _r = fig.canvas.get_renderer()
    for _fs in (FS["choke"], FS["choke"] - 0.8, FS["choke"] - 1.6, FS["choke"] - 2.4,
                FS["choke"] - 3.2, FS["choke"] - 4.0, FS["choke"] - 4.8):
        _ab = ax2.get_window_extent(_r)
        _bb = ph.get_window_extent(_r)
        if _bb.x1 <= _ab.x1 - 1 and _bb.y0 >= _ab.y0 + 1 and _bb.y1 <= _ab.y1 - 1:
            break
        ph.set_fontsize(_fs)
        fig.canvas.draw()
        _r = fig.canvas.get_renderer()
    ax2.margins(0.14)
    ax2.set_xticks([]); ax2.set_yticks([])
    for sp in ax2.spines.values():
        sp.set_visible(False)
    ax2.set_title("② 联动网络（里奇曲率）· 越红越脆弱=咽喉", fontsize=FS["title"],
                  color=INK, pad=9, loc="left", weight="bold")

    # ═══ 行首结论条（占满整行，字号放大）═══
    axh = fig.add_subplot(row_gs[0, :])
    axh.set_axis_off()
    axh.set_xlim(0, 1); axh.set_ylim(0, 1)
    axh.text(0.0, 0.80, name, fontsize=FS["sub_name"], color=INK, weight="bold",
             va="center", ha="left")
    axh.text(0.34, 0.80, "　%s　" % lab, fontsize=FS["sub_label"], color="white",
             weight="bold", va="center", ha="left",
             bbox=dict(boxstyle="round,pad=0.40", fc=col, ec="none"))
    axh.text(0.0, 0.36,
             "结构分 %+.2f ｜ %s ｜ 簇 %s ｜ 闭环 %s ｜ 均曲率 %+.3f ｜ 样本 %d 只 · 近 %d 日"
             % (mkt.get("score", float("nan")), sup,
                "—" if T["b0"] < 0 else T["b0"], "—" if T["b1"] < 0 else T["b1"],
                float(np.mean(curv)), n, T.get("days", 60)),
             fontsize=FS["struct"], color="#2f2f2f", va="center", ha="left", weight="bold")
    axh.text(0.0, 0.04, detail, fontsize=FS["detail"], color="#555555",
             va="center", ha="left")


_NAMES_CACHE = {}


def _names(codes, tries=3):
    """股票简称映射：全进程缓存 + 3 次重试（tushare 偶发超时，缺名会退化成代码）。"""
    miss = [c for c in codes if c not in _NAMES_CACHE]
    for k in range(tries):
        if not miss:
            break
        try:
            got = MT.stock_names(miss) or {}
        except Exception:
            got = {}
        for c, v in got.items():
            if v and v != c:
                _NAMES_CACHE[c] = v
        miss = [c for c in codes if c not in _NAMES_CACHE]
        if miss:
            print("[five-topo][names] 第%d次取简称后仍缺 %d 个，重试" % (k + 1, len(miss)),
                  flush=True)
    return {c: _NAMES_CACHE.get(c, c) for c in codes}


def _audit_layout(fig):
    """版面自检：文字是否溢出所在坐标区、行首标题是否压到图。打印警告，不抛异常。"""
    if os.environ.get("TOPO_NO_AUDIT"):
        return
    try:
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        bad = 0
        for ax in fig.axes:
            ab = ax.get_window_extent(r)
            for t in ax.texts:
                if not t.get_text().strip():
                    continue
                b = t.get_window_extent(r)
                dx = b.x1 - ab.x1
                dy0 = b.y0 - ab.y0
                dy1 = b.y1 - ab.y1
                if dx > 1.0 or dy0 < -1.0 or dy1 > 1.0:
                    bad += 1
                    print("[five-topo][audit] 文字溢出: %r 右溢%.1fpx 下溢%.1f 上溢%.1f"
                          % (t.get_text()[:18], dx, dy0, dy1), flush=True)
        print("[five-topo][audit] 版面自检：%d 处溢出" % bad, flush=True)
    except Exception as e:
        print("[five-topo][audit] 跳过（%s）" % str(e)[:60], flush=True)


def build(out=None, panel_dir=None, days=60):
    rows = []
    for name, code in INDICES:
        hits = components(code)
        if panel_dir:
            dates, close = panel_from_csv_dir(panel_dir, hits, days + 1)
        else:
            from topology import data_loader as DL
            dates, close = DL.close_panel(hits, days)
            if close is None:
                raise RuntimeError("取不到成分股日线面板：%s" % name)
        T = topo_calc(close, code, days)
        T["names"] = _names(T["codes"])
        _o = np.argsort(T["curv"])[:3]
        print("[five-topo][choke] %s 咽喉TOP3: %s" % (
            name, " / ".join("%s %.3f" % (T["names"].get(T["codes"][i], T["codes"][i]),
                                          T["curv"][i]) for i in _o)), flush=True)
        rows.append((name, T))
        print("[five-topo] %s 完成（成分 %d · 簇%s 环%s 结构分%+0.2f）"
              % (name, T["n"], T["b0"], T["b1"], T["mkt"].get("score", 0)), flush=True)

    nrow = len(rows)
    fig_h = ROW_H * nrow + 1.1 * (nrow - 1) + 2.0
    fig = plt.figure(figsize=(DEFAULT_W, fig_h), facecolor="white")
    outer = fig.add_gridspec(nrow, 1, hspace=0.34,
                             left=0.014, right=0.986,
                             top=1 - 1.45 / fig_h, bottom=0.62 / fig_h)
    for i, (name, T) in enumerate(rows):
        sub = outer[i].subgridspec(2, 2, height_ratios=[0.30, 1.0],
                                   width_ratios=[1.0, 1.0],
                                   hspace=0.10, wspace=0.085)
        draw_row(fig, sub, i, name, T)

    fig.text(0.020, 1 - 0.30 / fig_h,
             "五大宽基 · 拓扑结构总览",
             fontsize=FS["fig_title"], color=NAVY, weight="bold", va="top")
    fig.text(0.020, 1 - 0.86 / fig_h,
             "每个指数一块：结论条在上，左 ① UMAP 结构位置 ／ 右 ② 里奇曲率联动网络",
             fontsize=FS["fig_note"], color=MUTED, va="top")
    fig.text(0.020, 0.22 / fig_h,
             "方向票＝近5日/近10日/vs MA60 三票制　｜　结构分只看「抱团还是散开」，不是涨跌预测"
             "　｜　窗口 %d 个交易日　｜　纯 Python · 0 token" % days,
             fontsize=FS["fig_foot"], color=MUTED, va="bottom")
    os.makedirs(os.path.dirname(out or "./x.png"), exist_ok=True)
    _audit_layout(fig)
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[five-topo] 输出 %s" % out, flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        WS, "reports", "charts", "five_topo",
        "五大宽基_拓扑结构_%s.png" % datetime.now().strftime("%Y%m%d")))
    ap.add_argument("--panel", default="")
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()
    build(a.out, a.panel or None, a.days)


if __name__ == "__main__":
    main()
