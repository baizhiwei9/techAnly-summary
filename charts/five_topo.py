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
  · Betti 连通簇分组（β0/β1 口径的子结构关系）  （本文件 _betti_components，面板⑥）
  · 趋势层（含方向）                          （MT.trend_layer）

多空判定规则（写死、可复现）：
  方向票 = [近5日>0, 近10日>0, 现价>MA60] 三票
    3票 → 看多　　2票 → 偏多震荡　　1票 → 偏空震荡　　0票 → 看空
  结构支持度（只看几何，不含方向）：
    结构分 >= +0.50 → 结构支持（抱团聚合）
    结构分 <= -0.50 → 结构不支持（散开分化）
    其余            → 结构中性的

用法：
  python3 charts/five_topo.py                                   # 默认五大宽基
  python3 charts/five_topo.py --targets 科创50                    # 单指数
  python3 charts/five_topo.py --targets 沪深300,科创50          # 指定指数
  python3 charts/five_topo.py --targets 行业:电子,概念:人工智能 # 行业 / 概念 / 板块
  python3 charts/five_topo.py --panel /root/.cache/topo5 --days 60

「拓扑结构图」是独立程序：输入任意 指数/行业/概念/板块，一个标的画一行，
凑成一张总览图。晚上任务只需 `--targets 沪深300,中证500,中证1000,创业板指,科创50`。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

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
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
import networkx as nx

import topology.manifold_topo as MT
from topology import data_loader as DL
from topology import targets as TT

# 默认标的：晚上任务传五大宽基（本程序对任意 指数/行业/概念/板块 通用）
DEFAULT_TARGETS = ["沪深300", "中证500", "中证1000", "创业板指", "科创50"]

# ── 基础配色 ──
RED, GRN = "#d62728", "#2ca02c"
INK, MUTED, GRID = "#2b2b2b", "#7a7a7a", "#e6e6e6"
NAVY = "#0d2b5e"
BLUE, ORANGE = "#3b6fd4", "#e08a1e"

# ── 撕裂 / 聚合 配色 ──
TEAR_C = "#d9480f"     # 撕裂（橙红）
TEAR_C2 = "#f08a35"
MID_C = "#b9b9b9"
HUG_C2 = "#5b8fd6"     # 偏聚合（浅蓝）
HUG_C = "#1f4e9c"      # 聚合（深蓝）
UP_C = "#c0392b"
DN_C = "#2e7d32"
BLOC_CMAP = ["#2f6fb5", "#e08a1e", "#3f9c5a", "#a4569c", "#c0453f", "#5f9ea0"]
GRIDC = GRID

# ───────────── 版面常量 ─────────────
DEFAULT_W = 20.5        # 画布宽（英寸）——5 个面板并排需要更宽
ROW_H = 10.0            # 每个标的占一行（结论条 + ①~⑤ 五个面板 + ⑥ Betti 分组条）的高度（英寸）
DPI = 104
FS = dict(
    sub_name=30, sub_label=21, struct=18.5, detail=16.0,
    title=17.5, p_title=16.5, p_tick=11.5, legend=13.0, bloc=12.5,
    choke=12.5, mark=11.5,
    quad_title=21, quad_tick=13, quad_lab=19, quad_txt=13.5, quad_pt=17,
    fig_title=34, fig_note=18, fig_foot=14.5,
)

def panel_from_csv_dir(dirpath, codes, n_days=61):
    """全市场逐日 CSV（close_YYYYMMDD.csv: ts_code,close）→ 收盘面板。

    自包含实现（不再依赖 charts.five_index，避免拖进缠论画图链）。
    """
    import glob
    fs = sorted(glob.glob(os.path.join(dirpath, "close_*.csv")))
    if len(fs) < n_days + 1:
        raise RuntimeError("面板 CSV 不足：%d 个 < %d" % (len(fs), n_days + 1))
    fs = fs[-(n_days + 1):]
    want = set(str(c) for c in codes)
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

def _betti_components(corr, c_min):
    """Betti 口径下的「连通簇」分组（与 β0 同阈值：corr ≥ c_min 的边连通）。

    相关距离 d=√(2(1-corr)) 的 Betti 阈值 r 等价于相关阈值 c_min=1−r²/2：
    两条边在半径 r 内相连 ⟺ 相关 ≥ c_min。返回 (comps, label)：
      comps = 成员下标列表（按大小降序），label = 每只所属簇号。
    """
    n = corr.shape[0]
    if not np.isfinite(c_min) or n == 0:
        return [], np.zeros(n, dtype=int)
    Gc = nx.Graph()
    Gc.add_nodes_from(range(n))
    iu = np.triu_indices(n, 1)
    for i, j in zip(iu[0], iu[1]):
        if corr[i, j] >= c_min:
            Gc.add_edge(int(i), int(j))
    comps = sorted(nx.connected_components(Gc), key=len, reverse=True)
    label = np.zeros(n, dtype=int)
    for ci, comp in enumerate(comps):
        for i in comp:
            label[i] = ci
    return comps, label


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

    # 阵营划分 / 簇内簇间相关（撕裂度的硬数字 + 阵营块图用的标签）
    diag = MT.structure_diag(close, corr, k_cluster=5)

    # 聚合度 A（0~100，**绝对口径**，可跨标的直接比）
    #   A = 100 × [ 0.5×min(平均相关/0.45, 1) + 0.5×(1 − min(簇内−簇间)/0.35, 1) ]
    iu = np.triu_indices(corr.shape[0], 1)
    sync_abs = float(corr[iu].mean())
    A = 100.0 * (0.5 * float(np.clip(sync_abs / 0.45, 0.0, 1.0))
                 + 0.5 * (1.0 - float(np.clip(diag["gap"] / 0.35, 0.0, 1.0))))
    M = 0.4 * trend.get("r5", 0.0) + 0.3 * trend.get("r10", 0.0) + 0.3 * trend.get("vs_ma60", 0.0)

    gt = _gap_trend(ret, diag["lab"])

    # Betti 子结构：β0=连通簇（阈值半径下互不连通的组），β1=独立闭环。
    # 与 manifold_topo 的 betti_numbers 完全同阈值，直接在相关矩阵上做连通分量。
    betti_c_min = 1.0 - mf["thresh"] ** 2 / 2.0
    betti_comps, betti_label = _betti_components(corr, betti_c_min)

    return dict(emb=emb, mom5=mom5, curv=curv, deg=deg, corr=corr, mkt=mkt,
                trend=trend, b0=b0, b1=b1, n=len(codes), codes=codes, days=days,
                diag=diag, gap_trend=gt, sync_abs=sync_abs,
                agg=float(A), mom=float(M),
                betti_comps=betti_comps, betti_label=betti_label,
                betti_c_min=betti_c_min)

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

def _gap_trend(ret, lab, win=20, step=3, n_pts=22):
    """滚动「阵营内相关 / 阵营间相关」走势 —— 看撕裂是在加剧还是在收敛。

    阵营标号固定用全窗口那次聚类的结果（不逐窗重聚类），
    这样两条线的变化只反映「同一批人抱得更紧还是散得更开」，口径稳定可复现。
    """
    n = len(ret)
    lab = np.asarray(lab)
    ins, outs, xs = [], [], []
    iu = np.triu_indices(len(lab), 1)
    same = lab[iu[0]] == lab[iu[1]]
    for end in range(n, win, -step):
        w = ret.iloc[end - win:end]
        c = np.nan_to_num(w.corr().values, nan=0.0)
        v = c[iu]
        if (same).sum() < 3 or (~same).sum() < 3:
            continue
        ins.append(float(v[same].mean()))
        outs.append(float(v[~same].mean()))
        xs.append(end)
        if len(xs) >= n_pts:
            break
    return xs[::-1], ins[::-1], outs[::-1]

def regime(A, M):
    """结构 × 动量 → 一句人话。返回 (结构词, 结构色, 机会风险词, 机会风险色, 建议)。"""
    if A >= 45:
        s_word, s_col = ("结构聚合", HUG_C) if A >= 58 else ("偏聚合", HUG_C2)
    else:
        s_word, s_col = ("结构撕裂", TEAR_C) if A < 32 else ("偏撕裂", TEAR_C2)
    if A >= 45 and M > 0:
        return s_word, s_col, "顺势机会", HUG_C, "一起涨 · 指数和龙头都跟得上"
    if A >= 45 and M <= 0:
        return s_word, s_col, "系统性风险", TEAR_C, "一起跌 · 没地方躲，先减仓"
    if A < 45 and M > 0:
        return s_word, s_col, "轮动机会", HUG_C2, "只有少数阵营在拉 · 重个股轻指数"
    return s_word, s_col, "弱势分化", TEAR_C2, "指数没戏 · 等结构收敛再动手"

def verdict_line(T):
    d, tr = T["diag"], T["trend"]
    s_word, s_col, o_word, o_col, adv = regime(T["agg"], T["mom"])
    detail = ("聚合度 %.0f/100 ｜ 簇内相关 %.2f · 簇间相关 %.2f（差 %+.2f）｜ 同步度历史 %d%% 分位"
              "　‖　动量 近5日 %+.1f%% · 近10日 %+.1f%% · vs MA60 %+.1f%% · 站上MA20 %.0f%%"
              % (T["agg"], d["w_in"], d["w_out"], d["gap"], round(d["sync_pct"]),
                 tr.get("r5", 0), tr.get("r10", 0), tr.get("vs_ma60", 0),
                 tr.get("above20", float("nan"))))
    return s_word, s_col, o_word, o_col, adv, detail

def draw_gauge(ax, T):
    d = T["diag"]
    A = T["agg"]
    ax.set_xlim(-11, 111); ax.set_ylim(0, 10)
    ax.axis("off")

    # 渐变尺身
    grad = np.linspace(0, 1, 512).reshape(1, -1)
    cmap = LinearSegmentedColormap.from_list(
        "tear_hug", [TEAR_C, TEAR_C2, MID_C, HUG_C2, HUG_C])
    ax.imshow(grad, extent=(0, 100, 5.0, 7.4), aspect="auto", cmap=cmap,
              interpolation="bilinear", zorder=1)
    ax.add_patch(Rectangle((0, 5.0), 100, 2.4, fill=False, ec="#cfcfcf", lw=1.2, zorder=2))

    # 分档刻度
    for x in (0, 25, 45, 60, 75, 100):
        ax.plot([x, x], [4.6, 5.0], color="#8a8a8a", lw=1.1, zorder=3)
        ax.text(x, 3.9, "%d" % x, ha="center", va="top", fontsize=FS["p_tick"] - 1.5, color=MUTED)
    ax.text(0, 8.0, "← 撕裂\n阵营各走各的", ha="left", va="bottom",
            fontsize=FS["p_tick"] - 0.5, color=TEAR_C, weight="bold", linespacing=1.35)
    ax.text(100, 8.0, "聚合 →\n一起涨一起跌", ha="right", va="bottom",
            fontsize=FS["p_tick"] - 0.5, color=HUG_C, weight="bold", linespacing=1.35)
    # 中性带
    ax.annotate("", xy=(38, 9.0), xytext=(52, 9.0),
                arrowprops=dict(arrowstyle="<->", color="#9a9a9a", lw=1.4))
    ax.text(45, 9.15, "中性带", ha="center", va="bottom", fontsize=FS["p_tick"], color=MUTED)

    # 游标
    ax.annotate("", xy=(A, 7.7), xytext=(A, 10.9),
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=2.2,
                                shrinkA=0, shrinkB=0), zorder=6)
    ax.plot([A, A], [5.0, 7.4], color="white", lw=2.4, zorder=5)
    ax.text(A, 2.6, "聚合度 %.0f" % A, ha="center", va="top", fontsize=FS["p_title"],
            color=INK, weight="bold")

    # 三个硬数字（transAxes 定位，任何宽度都不越界）
    ax.text(0.5, 0.055,
            "全样本平均相关 %.2f\n簇内 %.2f · 簇间 %.2f · 差 %+.2f"
            % (T.get("sync_abs", float("nan")), d["w_in"], d["w_out"], d["gap"]),
            transform=ax.transAxes, ha="center", va="bottom", linespacing=1.5,
            fontsize=FS["detail"] - 7, color="#4a4a4a")
    ax.set_title("② 结构刻度尺 · 现在站在哪一档（绝对口径，可跨指数比）",
                 fontsize=FS["p_title"] - 1.5, color=INK, pad=8, loc="left",
                 weight="bold")

def draw_bloc_matrix(ax, T):
    """阵营 × 阵营 的平均相关矩阵（**方块图**，不是 300×300 像素级热图）。

    为什么换成方块图：原来那张 300 只股票的相关热图，缩到手机上看就是一片红，
    肉眼根本没法比「哪块深哪块浅」。现在每个阵营只占一格，格子里直接写均值：
      · 对角线 = 阵营内部相关（同伙抱得多紧）
      · 非对角 = 阵营之间相关（不同伙连不连）
    撕裂 → 对角深、非对角几乎发白（格子之间断崖）；
    聚合 → 除对角外整片都偏红（谁跟谁都连着）。
    """
    corr, lab = T["corr"], T["diag"]["lab"]
    n = corr.shape[0]
    sizes = np.bincount(lab)
    order = np.argsort(sizes)[::-1]
    nb = len(order)

    M = np.zeros((nb, nb))
    for a_i, ba in enumerate(order):
        for b_i, bb in enumerate(order):
            ia = np.where(lab == ba)[0]
            ib = np.where(lab == bb)[0]
            if a_i == b_i:
                sub = corr[np.ix_(ia, ia)]
                iu = np.triu_indices(len(ia), 1)
                M[a_i, b_i] = float(sub[iu].mean()) if len(ia) > 1 else np.nan
            else:
                M[a_i, b_i] = float(corr[np.ix_(ia, ib)].mean())

    ax.set_facecolor("white")
    im = ax.imshow(M, cmap="RdBu_r", vmin=-0.10, vmax=0.75, origin="upper",
                   interpolation="nearest", zorder=1)

    for i in range(nb):
        for j in range(nb):
            v = M[i, j]
            if not np.isfinite(v):
                continue
            diag = (i == j)
            ax.text(j, i, "%.2f" % v, ha="center", va="center",
                    fontsize=FS["bloc"] + (1.5 if diag else 0),
                    color=("white" if v > 0.42 else "#2b2b2b"),
                    weight="bold" if diag else "normal", zorder=4)

    # 阵营占比标签（行、列各一份）
    labels = []
    for a_i, b in enumerate(order):
        pct = 100.0 * sizes[b] / n
        labels.append("阵营%d\n%d%%" % (a_i + 1, round(pct)))
    ax.set_xticks(range(nb)); ax.set_yticks(range(nb))
    ax.set_xticklabels(labels, fontsize=FS["bloc"] - 3.5, color="#4a4a4a")
    ax.set_yticklabels(labels, fontsize=FS["bloc"] - 3.5, color="#4a4a4a")
    ax.tick_params(length=0, pad=2)
    for sp in ax.spines.values():
        sp.set_color("#d0d0d0")

    ax.set_title("① 阵营块图 · 对角=同伙抱多紧 / 非对角=不同伙连不连",
                 fontsize=FS["p_title"] - 1.5, color=INK, pad=8, loc="left", weight="bold")

def draw_gap_trend(ax, T):
    """两条线：阵营内相关（同伙抱多紧）/ 阵营间相关（不同伙连不连）。

    线距越宽 = 撕裂越强；两条线一起抬高 = 全局抱团；
    线距在收窄 = 正在从撕裂走向聚合（结构收敛，风险在积累）。
    """
    xs, ins, outs = T.get("gap_trend") or ([], [], [])
    ax.set_facecolor("white")
    if len(xs) < 3:
        ax.text(0.5, 0.5, "数据不足", transform=ax.transAxes, ha="center", va="center",
                fontsize=FS["legend"], color=MUTED)
        ax.set_xticks([]); ax.set_yticks([])
        return

    x = np.arange(len(xs))
    ax.fill_between(x, outs, ins, color=TEAR_C2, alpha=0.18, zorder=1)
    ax.plot(x, ins, color=HUG_C, lw=3.0, marker="o", ms=3.6, zorder=3, label="阵营内相关")
    ax.plot(x, outs, color=TEAR_C, lw=3.0, marker="o", ms=3.6, zorder=3, label="阵营间相关")
    ax.set_xlim(-0.5, len(xs) - 0.5)

    lo = min(min(outs), min(ins)); hi = max(max(ins), max(outs))
    pad = max(0.05, (hi - lo) * 0.32)
    ax.set_ylim(lo - pad, hi + pad)
    ax.grid(color=GRIDC, lw=0.7, alpha=0.9)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#c8c8c8")
    ax.tick_params(colors=MUTED, labelsize=FS["p_tick"] - 1)
    ax.set_xticks([0, len(xs) - 1])
    ax.set_xticklabels(["60 日前", "今天"])
    ax.set_title("④ 阵营内 / 阵营间相关走势 · 两线距离＝撕裂程度",
                 fontsize=FS["p_title"] - 1.5, color=INK, pad=8, loc="left", weight="bold")
    ax.legend(loc="upper left", frameon=True, framealpha=0.93, fontsize=FS["legend"] - 2,
              edgecolor="#e0e0e0", handlelength=1.6)

    k = min(5, len(xs) - 1)
    d_out = outs[-1] - outs[-1 - k]
    d_in = ins[-1] - ins[-1 - k]
    d_gap = (ins[-1] - outs[-1]) - (ins[-1 - k] - outs[-1 - k])
    arrow = "↗ 撕裂加剧" if d_gap > 0.02 else ("↘ 结构收敛" if d_gap < -0.02 else "→ 基本持平")
    col = TEAR_C if d_gap > 0.02 else (HUG_C if d_gap < -0.02 else MUTED)
    ax.text(0.5, 0.035, "近 %d 窗口 %s　内 %+.3f / 间 %+.3f / 差 %+.3f"
            % (k, arrow, d_in, d_out, d_gap),
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=FS["legend"] - 5.5, color=col, weight="bold",
            bbox=dict(boxstyle="round,pad=0.32", fc="white", ec="#dedede", lw=1.0,
                      alpha=0.95), zorder=8)

def draw_network(ax, T, max_n=260):
    corr, curv, lab = T["corr"], T["curv"], T["diag"]["lab"]
    codes = T["codes"]
    names = T.get("names") or {}
    n = corr.shape[0]

    # 大指数降采样：每阵营取与阵营中心最强的若干只
    sel = np.arange(n)
    if n > max_n:
        sel = []
        for b in np.unique(lab):
            mem = np.where(lab == b)[0]
            k = max(2, int(round(len(mem) * max_n / n)))
            sub = corr[np.ix_(mem, mem)]
            s = sub.mean(axis=1)
            sel.extend(mem[np.argsort(s)[::-1][:k]].tolist())
        sel = np.asarray(sorted(sel))
    lab_s = lab[sel]
    cs = corr[np.ix_(sel, sel)]
    cur_s = curv[sel]

    G = nx.Graph()
    G.add_nodes_from(range(len(sel)))
    k = 4
    sim = np.clip(cs, 0, None).copy()
    np.fill_diagonal(sim, 0.0)
    for i in range(len(sel)):
        for j in np.argsort(sim[i])[::-1][:k]:
            if i < j:
                G.add_edge(i, int(j), w=float(sim[i, j]))

    it = 90 if len(sel) > 120 else 200
    pos = nx.spring_layout(G, weight="w", seed=11, k=2.1 / np.sqrt(len(sel)), iterations=it)

    # 边：灰=阵营内，红=跨阵营
    for u, v, dd in G.edges(data=True):
        cross = lab_s[u] != lab_s[v]
        a = float(np.clip((dd["w"] - 0.15) / 0.7, 0.10, 0.75))
        ax.plot([pos[u][0], pos[v][0]], [pos[u][1], pos[v][1]],
                color=(TEAR_C2 if cross else "#a8b2c0"),
                ls="--" if cross else "-",
                lw=(1.4 if cross else 0.9) + 1.8 * max(0.0, dd["w"] - 0.35),
                alpha=(0.85 if cross else a), zorder=(2 if cross else 1))

    # 节点：填充=阵营，外圈=曲率符号
    sizes = 110 + 300 * pd.Series(cur_s).rank(pct=True).values
    for i in range(len(sel)):
        fill = BLOC_CMAP[lab_s[i] % len(BLOC_CMAP)]
        ring = TEAR_C if cur_s[i] <= -0.15 else ("#7fb3e8" if cur_s[i] > 0.02 else "white")
        ax.scatter([pos[i][0]], [pos[i][1]], s=sizes[i], c=[fill],
                   edgecolors=ring, linewidths=1.9, zorder=4)

    # 咽喉 TOP3 标注
    order = np.argsort(cur_s)[:3]
    for r, i in enumerate(order):
        nm = names.get(codes[sel[i]], codes[sel[i]])
        ax.annotate("%s %.2f" % (nm, cur_s[i]), pos[i], textcoords="offset points",
                    xytext=(0, 15), fontsize=FS["legend"] - 1.5, color=NAVY,
                    ha="center", weight="bold", zorder=8,
                    path_effects=[matplotlib.patheffects.withStroke(linewidth=3.2, foreground="white")])

    ax.margins(0.13)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("③ 联动网络 · 填充色＝阵营，圈色＝曲率（红=负/易传导，蓝=正/内部紧）",
                 fontsize=FS["p_title"], color=INK, pad=8, loc="left", weight="bold")

    handles = [Patch(fc=BLOC_CMAP[b % len(BLOC_CMAP)], ec="white",
                     label="阵营%d（%d 只）" % (b + 1, int((lab_s == b).sum())))
               for b in np.unique(lab_s)]
    handles.append(Line2D([], [], color=TEAR_C2, ls="--", lw=2.0, label="跨阵营连线"))
    ax.legend(handles=handles, loc="lower left", frameon=True, framealpha=0.92,
              fontsize=FS["legend"] - 1.5, handletextpad=0.4, borderpad=0.5,
              edgecolor="#e0e0e0", ncol=1)

def draw_quadrant(fig, gs, rows):
    ax = fig.add_subplot(gs)
    ax.set_facecolor("white")
    As = [r[1]["agg"] for r in rows]
    Ms = [r[1]["mom"] for r in rows]
    x0, x1 = 8.0, 82.0
    ypad = max(3.0, 1.35 * max(abs(min(Ms)), abs(max(Ms))))
    y0, y1 = -ypad, ypad

    XC = 45.0
    # 四象限底色
    ax.add_patch(Rectangle((XC, 0), x1 - XC, y1, fc=HUG_C, alpha=0.055, zorder=0))
    ax.add_patch(Rectangle((XC, y0), x1 - XC, -y0, fc=TEAR_C, alpha=0.075, zorder=0))
    ax.add_patch(Rectangle((x0, 0), XC - x0, y1, fc=HUG_C2, alpha=0.055, zorder=0))
    ax.add_patch(Rectangle((x0, y0), XC - x0, -y0, fc=TEAR_C2, alpha=0.045, zorder=0))

    ax.axvline(XC, color="#9a9a9a", lw=1.3, ls="--", zorder=1)
    ax.axhline(0, color="#9a9a9a", lw=1.3, ls="--", zorder=1)

    qs = [
        (XC + (x1 - XC) * 0.5, y1 - ypad * 0.16, "顺势机会", "聚合 + 上行", HUG_C, "一起涨，指数/龙头跟得上"),
        (XC + (x1 - XC) * 0.5, y0 + ypad * 0.16, "系统性风险", "聚合 + 下行", TEAR_C, "一起跌，没地方躲"),
        (x0 + (XC - x0) * 0.5, y1 - ypad * 0.16, "轮动机会", "撕裂 + 上行", HUG_C2, "少数阵营在拉，重个股"),
        (x0 + (XC - x0) * 0.5, y0 + ypad * 0.16, "弱势分化", "撕裂 + 下行", TEAR_C2, "指数没戏，等收敛"),
    ]
    for cx, cy, t1, t2, col, t3 in qs:
        ax.text(cx, cy, t1, ha="center", va="center", fontsize=FS["quad_lab"],
                color=col, weight="bold", alpha=0.92, zorder=2)
        # 上行象限说明放下方，下行象限说明放上方（都在象限内部，不出界）
        ty = cy - ypad * 0.30 if cy > 0 else cy + ypad * 0.30
        ax.text(cx, ty, t2 + " · " + t3, ha="center", va="center",
                fontsize=FS["quad_txt"], color="#5a5a5a", alpha=0.9, zorder=2)

    # 五个指数
    for name, T in rows:
        x, y = T["agg"], T["mom"]
        _, _, o_word, o_col, adv = regime(x, y)
        ax.scatter([x], [y], s=430, c=[o_col], edgecolors="white", linewidths=2.4, zorder=5)
        ax.annotate("%s\n%s · 动量%+.1f" % (name, o_word, y), (x, y),
                    textcoords="offset points", xytext=(0, 25),
                    fontsize=FS["quad_pt"] - 3, color=INK, ha="center", weight="bold",
                    zorder=6,
                    path_effects=[matplotlib.patheffects.withStroke(linewidth=4, foreground="white")])

    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_xlabel("结构聚合度  A  （← 撕裂　　聚合 →）", fontsize=FS["quad_tick"] + 3, color=INK)
    ax.set_ylabel("动量分  M  （%组合：0.4×近5日 + 0.3×近10日 + 0.3×vsMA60）",
                  fontsize=FS["quad_tick"] + 1, color=INK)
    ax.tick_params(colors=MUTED, labelsize=FS["quad_tick"])
    ax.grid(color=GRIDC, lw=0.7, alpha=0.9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c8c8c8")
    ax.set_title("结构 × 动量 四象限 · 机会还是风险，看它落在哪一块",
                 fontsize=FS["quad_title"], color=NAVY, pad=10, loc="left", weight="bold")

# ═══════════════════════ 画：UMAP 结构位置 ═══════════════════════
def draw_umap(ax, T):
    """UMAP 结构位置：气泡＝近5日动量，颜色＝机会/风险侧（动量强+不脆弱=绿）。"""
    emb, mom5, curv = T["emb"], T["mom5"], T["curv"]
    ax.set_facecolor("white")
    combo = 0.6 * MT.zs(mom5) + 0.4 * MT.zs(curv)
    cls = np.where(combo >= 0.45, 0, np.where(combo <= -0.45, 1, 2))
    colmap = {0: "#2eb872", 1: "#e05252", 2: "#3b6fd4"}
    mr = pd.Series(mom5).rank(pct=True).values
    ax.scatter(emb[:, 0], emb[:, 1], s=40 + 300 * mr,
               c=[colmap[c] for c in cls], alpha=0.95,
               edgecolors="white", linewidths=0.7, zorder=3)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.margins(0.10)
    ax.set_title("① 结构位置（UMAP）· 气泡＝近5日动量", fontsize=FS["p_title"] - 1.5,
                 color=INK, pad=8, loc="left", weight="bold")
    ax.text(0.012, 0.03, "绿＝机会侧（动量强+不脆弱）\n红＝风险侧（动量弱或结构脆）\n蓝＝中性",
            transform=ax.transAxes, fontsize=FS["legend"] - 3.5, color="#3a3a3a",
            va="bottom", linespacing=1.6,
            bbox=dict(boxstyle="round,pad=0.32", fc="white", ec="#dedede", lw=1.0, alpha=0.95))


# ═══════════════════════ 画：子结构（Betti 分组）═══════════════════════
def draw_betti(ax, T):
    """⑥ 子结构（Betti 分组）：β0 连通簇 + β1 闭环 —— 把「谁跟谁是一伙」画出来。

    与 manifold_topo 的 betti_numbers 完全同阈值：相关距离 d=√(2(1-corr))，
    阈值 r=d>0 的 35 分位。每个 β0 连通簇一个颜色 + 虚线外接圈，游离单股直接标名字；
    β1 闭环数单独标注（独立方向环）。
    """
    emb = T["emb"]
    codes = T["codes"]
    names = T.get("names") or {}
    b0, b1 = T["b0"], T["b1"]
    comps, blabel = T["betti_comps"], T["betti_label"]
    c_min = T["betti_c_min"]
    ax.set_facecolor("white")

    if not len(comps) or b0 < 0:
        ax.text(0.5, 0.5, "Betti 不可算（成分不足 / ripser 失败）",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=FS["legend"], color=MUTED)
        ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        return

    # 点：按连通簇着色（簇色沿用 BLOC_CMAP，但含义是 Betti 连通簇，靠标题/图例区分）
    cols = [BLOC_CMAP[int(blabel[i]) % len(BLOC_CMAP)] for i in range(len(emb))]
    ax.scatter(emb[:, 0], emb[:, 1], s=34, c=cols, alpha=0.92,
               edgecolors="white", linewidths=0.7, zorder=3)

    # 虚线外接圈 + 簇标签（多成员簇画圈，游离单股直接标名字）
    for ci, comp in enumerate(comps):
        idx = sorted(comp)
        col = BLOC_CMAP[ci % len(BLOC_CMAP)]
        if len(idx) == 1:
            i = idx[0]
            ax.annotate(names.get(codes[i], codes[i]), (emb[i, 0], emb[i, 1]),
                        textcoords="offset points", xytext=(0, 11),
                        fontsize=FS["legend"] - 2.5, color=col, ha="center", weight="bold",
                        zorder=6,
                        path_effects=[matplotlib.patheffects.withStroke(linewidth=3.0,
                                                                        foreground="white")])
            continue
        pts = emb[idx]
        c = pts.mean(0)
        r = max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])) * 0.55 + 0.16
        ax.add_patch(plt.Circle(c, r, color=col, alpha=0.05, lw=1.5, ls="--",
                                fill=True, zorder=0))
        ax.text(c[0], c[1] + r + 0.12, "簇%d · %d只" % (ci + 1, len(idx)),
                fontsize=FS["legend"] - 2.0, color=col, ha="center", weight="bold",
                zorder=7,
                path_effects=[matplotlib.patheffects.withStroke(linewidth=3.0,
                                                                foreground="white")])

    # 底部：β0/β1 含义（子结构关系的硬数字）
    ax.text(0.5, 0.015,
            "β0 连通簇 = %d（分裂成 %d 伙）｜β1 闭环 = %d（内部独立方向环）｜阈值 corr≥%.2f"
            % (b0, b0, b1, c_min),
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=FS["legend"] - 3.5, color="#0d2b5e", weight="bold",
            bbox=dict(boxstyle="round,pad=0.32", fc="white", ec="#d5d5d5", lw=1.0,
                      alpha=0.95), zorder=8)

    # 图例：每个连通簇的成员（簇太多时只列前 6 个，其余并入「其他」）
    handles = []
    for ci, comp in enumerate(comps[:6]):
        idx = sorted(comp)
        top = "、".join(names.get(codes[i], codes[i]) for i in idx[:2])
        if len(idx) > 2:
            top += "…"
        handles.append(Line2D([], [], marker="o", ls="", ms=9,
                              mfc=BLOC_CMAP[ci % len(BLOC_CMAP)], mec="white",
                              label="簇%d（%d）：%s" % (ci + 1, len(idx), top)))
    if len(comps) > 6:
        handles.append(Line2D([], [], marker="o", ls="", ms=9, mfc="none", mec="none",
                              label="…共 %d 簇" % len(comps)))
    if handles:
        ax.legend(handles=handles, loc="lower left", frameon=True, framealpha=0.92,
                  fontsize=FS["legend"] - 3.0, handletextpad=0.35, borderpad=0.5,
                  edgecolor="#e0e0e0", ncol=1)

    ax.margins(0.14)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("⑥ 子结构（Betti 分组）· β0＝几伙人 / β1＝几个独立闭环",
                 fontsize=FS["p_title"], color=INK, pad=8, loc="left", weight="bold")


# ═══════════════════════ 行 ═══════════════════════
def draw_row(fig, sub, name, T):
    """一行＝结论条 + 5 个面板 + ⑥ Betti 分组条：① UMAP ② 阵营块图 ③ 刻度尺 ④ 联动网络 ⑤ 阵营内外走势"""
    s_word, s_col, o_word, o_col, adv, detail = verdict_line(T)

    axh = fig.add_subplot(sub[0, :])
    axh.set_axis_off()
    axh.set_xlim(0, 1); axh.set_ylim(0, 1)
    axh.text(0.0, 0.78, name, fontsize=FS["sub_name"], color=INK, weight="bold",
             va="center", ha="left")
    axh.text(0.115, 0.78, "　%s　" % s_word, fontsize=FS["sub_label"], color="white",
             weight="bold", va="center", ha="left",
             bbox=dict(boxstyle="round,pad=0.42", fc=s_col, ec="none"))
    axh.text(0.245, 0.78, "　%s　" % o_word, fontsize=FS["sub_label"], color="white",
             weight="bold", va="center", ha="left",
             bbox=dict(boxstyle="round,pad=0.42", fc=o_col, ec="none"))
    _ts = T.get("tear_state")
    if _ts:
        axh.text(0.385, 0.78, "　%s　" % _ts[0], fontsize=FS["sub_label"], color="white",
                 weight="bold", va="center", ha="left",
                 bbox=dict(boxstyle="round,pad=0.42", fc=_ts[1], ec="none"))
    axh.text(0.545, 0.78, adv, fontsize=FS["struct"], color=INK, weight="bold",
             va="center", ha="left")
    axh.text(0.0, 0.20, detail, fontsize=FS["detail"], color="#555555",
             va="center", ha="left")

    draw_umap(fig.add_subplot(sub[1, 0]), T)
    draw_bloc_matrix(fig.add_subplot(sub[1, 1]), T)
    draw_gauge(fig.add_subplot(sub[1, 2]), T)
    draw_network(fig.add_subplot(sub[1, 3]), T)
    draw_gap_trend(fig.add_subplot(sub[1, 4]), T)
    draw_betti(fig.add_subplot(sub[2, :]), T)


_NAMES_CACHE = {}

def _fiedler_gap(corr: np.ndarray):
    """谱二分（Fiedler 向量）→ 簇内-簇间相关差 gap（撕裂度）+ sync（同步度）。

    纯 numpy，不依赖 sklearn。与群云盘 topo_v2/topo_core.py 的 fiedler_split 同口径：
      · sync = 平均两两相关（越高越抱团/聚集）
      · gap  = 簇内平均相关 − 簇间平均相关（越大越撕裂，阵营分明）
    """
    n = corr.shape[0]
    D = np.sqrt(np.maximum(2 * (1 - corr), 0))
    np.fill_diagonal(D, 0.0)
    J = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * J @ (D ** 2) @ J
    w, V = np.linalg.eigh(B)
    f = V[:, np.argsort(w)[::-1][1]]
    lab = (f > 0).astype(int)
    frac = max(lab.mean(), 1 - lab.mean())
    if frac > 0.85:                      # 不平衡 → 用中位数强制平衡
        lab = (f > np.median(f)).astype(int)
    a, b = lab == 0, lab == 1
    iu = np.triu_indices(n, 1)
    sync = float(corr[iu].mean())
    if a.sum() and b.sum():
        tear = float(corr[np.ix_(a, b)].mean())
        w_in = np.r_[corr[np.ix_(a, a)][np.triu_indices(a.sum(), 1)],
                     corr[np.ix_(b, b)][np.triu_indices(b.sum(), 1)]]
        gap = float(w_in.mean() - tear) if len(w_in) else 0.0
    else:
        gap = 0.0
    return sync, gap

def _rolling_sync_gap(close: pd.DataFrame, win: int = 60, step: int = 5):
    """滚动窗口的 (sync, gap) 序列，用于跨指数池化做历史分位。

    返回 shape (m, 2) 的 np.ndarray；样本不足返回 (0, 2)。
    """
    ret = close.pct_change().iloc[1:].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    R = ret.values
    m = R.shape[0] - win + 1
    if m < 1:
        return np.zeros((0, 2), dtype=float)
    out = []
    for w in range(0, m, step):
        c = np.nan_to_num(np.corrcoef(R[w:w + win], rowvar=False), nan=0.0)
        out.append(_fiedler_gap(c))
    return np.array(out, dtype=float)

def _ricci_flow(close: pd.DataFrame, k: int = 5, win: int = None, step: int = None):
    """里奇流（Ricci flow）：滚动窗口的平均 Ollivier-Ricci 曲率序列。

    单快照里奇曲率只回答「当前脆不脆」；里奇流补上时间维度 ——
    逐窗算平均曲率，看「曲率如何随时间流动」：
      曲线向上 → 里奇流收敛（结构聚合）；向下 → 里奇流发散（结构撕裂）。
    窗口默认取收益样本的 1/3（至少 20）、步长取窗口 1/6，控制算量。
    """
    ret = np.log(close / close.shift(1)).dropna()
    T = ret.shape[0]
    if T < 16:
        return np.array([], dtype=float)
    win = int(np.clip(win or max(20, T // 3), 10, T))
    step = int(max(1, step or max(1, win // 6)))
    vals = []
    for w in range(0, T - win + 1, step):
        c = np.nan_to_num(np.corrcoef(ret.iloc[w:w + win].values, rowvar=False), nan=0.0)
        curv, _, _, _ = MT.ollivier_ricci(c, k=k)
        vals.append(float(np.mean(curv)))
    return np.array(vals, dtype=float)

def _tear_state(sync: float, gap: float, sync_pct: float, gap_pct: float):
    """五态判定（撕裂 / 聚集 / 抱团分化 / 散乱 / 过渡），topo_v2 口径。"""
    if gap_pct >= 75 and sync_pct <= 50:
        return "撕裂", "#e05252"
    if sync_pct >= 75 and gap_pct <= 50:
        return "聚集", "#e08a1e"
    if sync_pct >= 75 and gap_pct >= 75:
        return "抱团分化", "#8e6fd4"
    if sync_pct <= 50 and gap_pct <= 50:
        return "散乱", "#8a8a8a"
    return "过渡", "#2e9cc8"

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

def _resolve_targets(raws):
    """原始输入 → Target 列表（支持 指数/行业/概念/板块，可逗号/显式前缀）。"""
    idx_boards = DL.board_list("行业")
    cpt_boards = DL.board_list("概念")
    out = []
    for raw in raws:
        t = TT.resolve(raw, idx_boards, cpt_boards)
        out.append(t)
    return out

def build(out=None, panel_dir=None, days=60, targets=None):
    rows = []
    skipped = []
    tg = []                      # (name, sync_gap_seq) 供跨标的池化分位
    for t in targets or []:
        if t.kind in ("stock", "unknown"):
            skipped.append((t.raw, "无法识别 / 个股无成分股"))
            continue
        name = t.name or t.raw
        hits = DL.components(t)
        if not hits:
            skipped.append((name, "取不到成分股"))
            continue
        index_code = t.code if t.kind == "index" else None
        close_long = None
        if panel_dir:
            dates, close = panel_from_csv_dir(panel_dir, hits, days + 1)
        else:
            # 撕裂/聚集判别需要更长历史做分位：取 ~250 天；原图仍只画最后 days 天
            dates, close_long = DL.close_panel(hits, max(days, 250))
            if close_long is None:
                skipped.append((name, "成分股日线面板取数失败"))
                continue
            close = close_long.tail(days + 1)
        T = topo_calc(close, index_code, days)
        T["names"] = _names(T["codes"])
        if close_long is not None and len(close_long) >= 40:
            T["sg"] = _rolling_sync_gap(close_long, win=min(60, days), step=5)
            tg.append((name, T["sg"]))
            # 阵营内外相关走势用更长历史，曲线才有形状
            T["gap_trend"] = _gap_trend(
                np.log(close_long / close_long.shift(1)).dropna(), T["diag"]["lab"])
        T["flow"] = _ricci_flow(close, k=5)
        _o = np.argsort(T["curv"])[:3]
        print("[five-topo][choke] %s 咽喉TOP3: %s" % (
            name, " / ".join("%s %.3f" % (T["names"].get(T["codes"][i], T["codes"][i]),
                                          T["curv"][i]) for i in _o)), flush=True)
        s_word, _, o_word, _, _ = regime(T["agg"], T["mom"])
        print("[five-topo] %s 成分 %d · 聚合度 %.0f（%s）· 动量 %+.2f（%s）· 簇内%.2f 簇间%.2f"
              % (name, T["n"], T["agg"], s_word, T["mom"], o_word,
                 T["diag"]["w_in"], T["diag"]["w_out"]), flush=True)
        rows.append((name, T))

    for raw, why in skipped:
        print("[five-topo][skip] %s：%s" % (raw, why), flush=True)

    if not rows:
        raise RuntimeError("没有任何可画的标的")

    # ── 跨标的池化：五态「撕裂/聚集」判定 ──
    if tg:
        pool_sync = np.concatenate([sg[:, 0] for _, sg in tg if len(sg)])
        pool_gap = np.concatenate([sg[:, 1] for _, sg in tg if len(sg)])
        for name, T in rows:
            sg = T.get("sg")
            if sg is None or len(sg) == 0:
                T["tear_state"] = None
                continue
            sync_now, gap_now = float(sg[-1, 0]), float(sg[-1, 1])
            sp = float((pool_sync < sync_now).mean() * 100)
            gp = float((pool_gap < gap_now).mean() * 100)
            T["tear_state"] = _tear_state(sync_now, gap_now, sp, gp)
            print("[five-topo][tear] %s SYNC %.3f(%.0f%%) GAP %.3f(%.0f%%) → %s"
                  % (name, sync_now, sp, gap_now, gp, T["tear_state"][0]), flush=True)

    nrow = len(rows)
    quad_h = 7.6
    fig_h = quad_h + ROW_H * nrow + 0.85 * (nrow - 1) + 2.6
    fig = plt.figure(figsize=(DEFAULT_W, fig_h), facecolor="white")
    outer = fig.add_gridspec(nrow + 1, 1,
                             height_ratios=[quad_h] + [ROW_H] * nrow,
                             hspace=0.30, left=0.020, right=0.982,
                             top=1 - 1.55 / fig_h, bottom=0.60 / fig_h)
    draw_quadrant(fig, outer[0], rows)

    for i, (name, T) in enumerate(rows):
        sub = outer[i + 1].subgridspec(3, 5, height_ratios=[0.26, 1.0, 0.58],
                                       width_ratios=[1.16, 0.96, 0.84, 1.20, 0.96],
                                       hspace=0.13, wspace=0.16)
        draw_row(fig, sub, name, T)

    fig.text(0.020, 1 - 0.30 / fig_h,
             "拓扑结构总览 · 结构状态（撕裂 ↔ 聚合）× 动量　｜　时间窗口 %d 个交易日" % days,
             fontsize=FS["fig_title"], color=NAVY, weight="bold", va="top")
    fig.text(0.020, 1 - 0.86 / fig_h,
             "先看顶上四象限定「机会还是风险」，再逐行看「结构到底撕裂到什么程度」："
             "① UMAP 结构位置 ② 阵营块图（对角=同伙/非对角=不同伙） ③ 聚合度刻度尺 "
             "④ 联动网络（里奇曲率+里奇流） ⑤ 阵营内外相关走势 ⑥ 子结构（Betti 分组：β0/β1 连通簇）　"
             "（窗口默认 60，可用 --days 20/40/60/120 切换）",
             fontsize=FS["fig_note"], color=MUTED, va="top")
    fig.text(0.020, 0.20 / fig_h,
             "聚合度 A = 100×[0.5×min(平均相关/0.45,1) + 0.5×(1−min(簇内−簇间)/0.35,1)]　｜　"
             "动量 M = 0.4×近5日 + 0.3×近10日 + 0.3×vsMA60　｜　"
             "撕裂/聚集五态＝谱二分簇内-簇间落差 × 同步度（全标的 × 滚动窗池化分位）　｜　"
             "方向票＝近5日/近10日/vs MA60 三票制　｜　窗口 %d 个交易日　｜　纯 Python · 0 token" % days,
             fontsize=FS["fig_foot"], color=MUTED, va="bottom")
    os.makedirs(os.path.dirname(out or "./x.png"), exist_ok=True)
    _audit_layout(fig)
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[five-topo] 输出 %s" % out, flush=True)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="",
                    help="逗号分隔，指数/行业/概念/板块（默认五大宽基）；"
                         "行业/概念可加前缀：行业:电子、概念:人工智能")
    ap.add_argument("--out", default=os.path.join(
        WS, "reports", "charts", "five_topo",
        "拓扑结构_%s.png" % datetime.now().strftime("%Y%m%d")))
    ap.add_argument("--panel", default="")
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()

    raws = [s.strip() for s in a.targets.split(",") if s.strip()] or DEFAULT_TARGETS
    targets = _resolve_targets(raws)
    for raw, t in zip(raws, targets):
        print("[five-topo][resolve] %s → %s(%s)" % (raw, t.name, t.kind), flush=True)
    build(a.out, a.panel or None, a.days, targets)

if __name__ == "__main__":
    main()
