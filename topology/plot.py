#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plot.py · 拓扑流形画图（暗黑金融科幻风）
========================================
两个入口：
  plot_spd(res, name, out_path, name_map)      单标的四合一 SPD 流形图
  plot_phase(results, out_path, corr_th, h1_th) 多标的二维相位图（叠一张）

results = [(display_name, color, res_dict), ...]；res_dict 来自 spd.compute()。
纯 matplotlib + numpy 计算，非 AI 生成。
"""
from __future__ import annotations

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.colors import Normalize, LinearSegmentedColormap
from matplotlib.patches import Patch

# 中文字体（无则回退）
plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 暗黑金融科幻配色
BG = "#05080f"
PANEL = "#0a1220"
CYAN = "#2dd4bf"
BLUE = "#38bdf8"
ORANGE = "#fb923c"
RED = "#fb7185"
PURPLE = "#a78bfa"
GOLD = "#fbbf24"
GREEN = "#34d399"
GRIDC = "#1c2b45"
TEXTC = "#cbd5e1"
MUTED = "#8b9bb4"

plt.rcParams.update({
    "figure.facecolor": BG,
    "axes.facecolor": PANEL,
    "axes.edgecolor": "#26344d",
    "axes.labelcolor": TEXTC,
    "axes.titlecolor": "#e2e8f0",
    "text.color": "#e2e8f0",
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "grid.color": GRIDC,
    "grid.linewidth": 0.5,
    "axes.grid": True,
    "figure.dpi": 110,
})

ZONE_COLORS = {"red": RED, "yellow": GOLD, "blue": BLUE}
ZONE_LABEL = {
    "chance": ("机会区", "分化 · 结构解冻"),
    "risk":   ("风险区", "同步 · 锁死"),
    "bottom": ("磨底区", "分化 · 环未重生"),
    "alert":  ("警惕区", "同步 · 环存(伪分化)"),
}

_PALETTE = ["#fbbf24", "#2dd4bf", "#38bdf8", "#fb7185", "#a78bfa",
            "#34d399", "#fb923c", "#60a5fa", "#f472b6", "#a3e635"]


def color_for(i):
    return _PALETTE[i % len(_PALETTE)]


# ───────────────────────── 单标的四合一 SPD 流形图 ─────────────────────────
def plot_spd(res, name, out_path, name_map=None):
    """画单标的四合一图：流形曲面 / TDA 尖刺 / 指数收盘 / 指标面板。"""
    from . import spd as _spd
    name_map = name_map or {}
    dates = res["dates"]
    n_days = len(dates)
    W, m = res["W"], res["m"]
    x = np.arange(n_days)
    xw = np.arange(W - 1, n_days)
    idx_close = res.get("idx_close")

    fig = plt.figure(figsize=(17.5, 11.5))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1.0], hspace=0.28, wspace=0.22,
                          left=0.06, right=0.97, top=0.93, bottom=0.13)

    # 左上：SPD 测地距离曲面
    ax1 = fig.add_subplot(gs[0, 0], projection="3d")
    ax1.set_facecolor(PANEL)
    ax1.set_axis_off()
    ax1.view_init(elev=26, azim=-60)
    d1, d2 = res["eig_ref"][0], res["eig_ref"][1]
    gx = np.linspace(-0.97, 0.97, 70)
    gy = np.linspace(-0.97, 0.97, 70)
    GX, GY = np.meshgrid(gx, gy)
    GZ = np.sqrt((np.log(d1 + GX * d1) - np.log(d1)) ** 2 +
                 (np.log(d2 + GY * d2) - np.log(d2)) ** 2)
    ax1.plot_surface(GX, GY, GZ, cmap="viridis", alpha=0.82, linewidth=0,
                     antialiased=True, rcount=70, ccount=70)
    rim = GZ.max() * 1.02
    ax1.plot(GX[0], GY[0], np.full_like(GX[0], rim), color=ORANGE, lw=1.2, alpha=0.55)
    ax1.plot(GX[-1], GY[-1], np.full_like(GX[-1], rim), color=ORANGE, lw=1.2, alpha=0.55)
    ax1.plot(GX[:, 0], GY[:, 0], np.full_like(GX[:, 0], rim), color=ORANGE, lw=1.2, alpha=0.55)
    ax1.plot(GX[:, -1], GY[:, -1], np.full_like(GX[:, -1], rim), color=ORANGE, lw=1.2, alpha=0.55)
    pts = _spd.stock_2d_embedding(res["ret"].values)
    dev = res["dev"]
    top3 = set(np.argsort(dev)[::-1][:3].tolist())
    for i in range(len(pts)):
        c = ORANGE if i in top3 else CYAN
        ax1.plot([pts[i, 0], pts[i, 0]], [pts[i, 1], pts[i, 1]], [0, dev[i] * 2.2],
                 color=c, lw=1.0, alpha=0.6)
    ax1.set_title("SPD 流形 · 测地距离曲面", pad=8)

    # 右上：TDA 尖刺地形 + H1 发光环
    ax2 = fig.add_subplot(gs[0, 1], projection="3d")
    ax2.set_facecolor(PANEL)
    ax2.set_axis_off()
    ax2.view_init(elev=30, azim=-55)
    from ripser import Rips
    rips = Rips(maxdim=1, verbose=False)
    pts_full = _spd.stock_2d_embedding(res["ret"].values)
    h1_now = _spd.h1_lifetime(_spd.subsample(pts_full, 200), rips)
    H1_norm = h1_now / (res["h1"].max() + 1e-12)
    bins = 20
    hist, xe, ye = np.histogram2d(pts_full[:, 0], pts_full[:, 1], bins=bins)
    hist = np.log(hist.astype(float) + 1)
    hist = hist / hist.max()
    cx = (xe[:-1] + xe[1:]) / 2
    cy = (ye[:-1] + ye[1:]) / 2
    dx = (xe[1] - xe[0]) * 0.85
    dy = (ye[1] - ye[0]) * 0.85
    norm = Normalize(hist.min(), hist.max())
    cmap = LinearSegmentedColormap.from_list("spikes", ["#1e3a8a", "#3b82f6", "#fb923c"])
    for i in range(bins):
        for j in range(bins):
            ax2.bar3d(cx[j], cy[i], 0, dx, dy, hist[i, j],
                      color=cmap(norm(hist[i, j])), alpha=0.85, shade=True)
    rx = np.std(pts_full[:, 0]) * 1.9
    ry = np.std(pts_full[:, 1]) * 1.9
    t = np.linspace(0, 2 * np.pi, 200)
    zring = hist.max() * 0.82
    for k, (scale, alpha) in enumerate([(1.0, 0.18), (0.93, 0.35), (0.86, 0.9)]):
        ax2.plot(pts_full[:, 0].mean() + rx * scale * np.cos(t),
                 pts_full[:, 1].mean() + ry * scale * np.sin(t),
                 np.full_like(t, zring + k * 0.02),
                 color="#67e8f9", lw=2.5, alpha=alpha * (0.25 + 0.75 * H1_norm))
    ax2.set_title("TDA 尖刺地形 · H1 环", pad=8)

    # 左下：指数收盘 + 分区
    ax3 = fig.add_subplot(gs[1, 0])
    for i0, i1, st in _zone_spans(res["states"], m, n_days):
        ax3.axvspan(i0 - 0.5, i1 + 0.5, color=[RED, GOLD, BLUE][st], alpha=0.16, lw=0)
    if idx_close is not None and len(idx_close) == n_days + 1:
        ax3.plot(x, idx_close.values[1:], color=GOLD, lw=1.1)
    ax3.set_xlim(-1, n_days)
    ax3.set_title("%s 收盘 · 结构信号分区" % name, pad=8)
    ax3.set_xticks(x[::10])
    ax3.set_xticklabels([d[4:] for d in dates[::10]], rotation=0)
    ax3.grid(alpha=0.5)
    ax3.legend(handles=[
        Patch(color=RED, alpha=0.3, label="红区=指数风险(锁死)"),
        Patch(color=BLUE, alpha=0.3, label="蓝区=成分机会(分化)"),
        Patch(color=GOLD, alpha=0.3, label="黄区=卡滞"),
    ], loc="upper left", framealpha=0.25, fontsize=9)

    # 右下：里奇 + 测地 ｜ H1 + 相关性
    ax4 = fig.add_subplot(gs[1, 1])
    for i0, i1, st in _zone_spans(res["states"], m, n_days):
        ax4.axvspan(i0 - 0.5, i1 + 0.5, color=[RED, GOLD, BLUE][st], alpha=0.16, lw=0)
    l1, = ax4.plot(xw, res["ricci"], color=PURPLE, lw=1.6, label="里奇曲率(左)")
    l2, = ax4.plot(xw, res["g"], color=ORANGE, lw=1.6, label="SPD测地距离(左)")
    ax4.set_ylabel("里奇 / 测地距离")
    ax4.set_xlim(-1, n_days)
    ax4t = ax4.twinx()
    l3, = ax4t.plot(xw, res["h1"], color=CYAN, lw=1.6, label="H1寿命(右)")
    l4, = ax4t.plot(xw, res["corr"], color=GREEN, lw=1.6, label="平均相关性(右)")
    ax4t.set_ylabel("H1 寿命 / 平均相关")
    ax4t.grid(False)
    ax4.set_title("流形与拓扑指标", pad=8)
    ax4.legend(handles=[l1, l2, l3, l4], loc="upper left", framealpha=0.25, fontsize=9)
    ax4.set_xticks(xw[::6])
    ax4.set_xticklabels([dates[i][4:] for i in range(W - 1, n_days)][::6])

    # 底部结论
    state_now = res["states"][-1]
    state_txt = ["流动/堵塞：指数风险（锁死）", "卡滞（磨底假反弹）", "再生/分化：成分股机会"][state_now]
    top3_idx = np.argsort(res["dev"])[::-1][:3]
    comps = res["comps"]
    top3_txt = "、".join(name_map.get(comps[i], comps[i]) for i in top3_idx)
    fig.text(0.06, 0.035,
             "结论：当前处于「%s」｜ 最近%d天 测地距离=%.3f H1寿命=%.3f 平均相关=%.3f 熵=%.3f Hurst=%.3f\n"
             "机会方向(蓝区偏离度Top3)：%s"
             % (state_txt, n_days, res["g"][-1], res["h1"][-1], res["corr"][-1],
                res["ent"][-1], res["hurst_full"], top3_txt),
             fontsize=11, color="#e2e8f0", ha="left", va="bottom")

    fig.suptitle("%s · SPD流形 × TDA × 里奇曲率（%d天窗口）" % (name, n_days),
                 fontsize=15, color="#e2e8f0", y=0.98)
    fig.savefig(out_path, dpi=115, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _zone_spans(states, m, n_days):
    per = np.empty(n_days, dtype=int)
    for e in range(n_days):
        per[e] = states[min(e, m - 1)]
    spans = []
    i = 0
    while i < n_days:
        j = i
        while j < n_days and per[j] == per[i]:
            j += 1
        spans.append((i, j - 1, per[i]))
        i = j
    return spans


# ───────────────────────── 多标的二维相位图 ─────────────────────────
def plot_phase(results, out_path, corr_th=0.50, h1_th=0.50):
    """多标的二维相位图：横轴同步度 corr，纵轴拓扑再生度 H1（归一化）。"""
    from . import spd as _spd
    fig = plt.figure(figsize=(16.5, 9.2))
    gs = fig.add_gridspec(1, 2, width_ratios=[2.1, 1.0],
                          left=0.055, right=0.985, top=0.88, bottom=0.10, wspace=0.14)

    ax = fig.add_subplot(gs[0, 0])
    ax.set_facecolor(PANEL)
    ax.axhspan(0, h1_th, color=RED, alpha=0.12)
    ax.axhspan(0, h1_th, xmin=0, xmax=corr_th, color=GOLD, alpha=0.12)
    ax.axhspan(h1_th, 1, xmin=corr_th, color=ORANGE, alpha=0.10)
    ax.axhspan(h1_th, 1, xmin=0, xmax=corr_th, color=BLUE, alpha=0.13)
    ax.text(0.02, 0.97, "机会区\n分化 · 环重生", transform=ax.transAxes, va="top", ha="left",
            fontsize=12, color="#7dd3fc", fontweight="bold")
    ax.text(0.98, 0.03, "风险区\n同步 · 锁死", transform=ax.transAxes, va="bottom", ha="right",
            fontsize=12, color="#fda4af", fontweight="bold")
    ax.text(0.02, 0.03, "磨底区\n分化 · 环未重生", transform=ax.transAxes, va="bottom", ha="left",
            fontsize=10, color="#fcd34d")
    ax.text(0.98, 0.97, "警惕区\n同步 · 环存", transform=ax.transAxes, va="top", ha="right",
            fontsize=10, color="#fdba74")
    ax.axhline(h1_th, color=GRIDC, lw=1, ls="--")
    ax.axvline(corr_th, color=GRIDC, lw=1, ls="--")

    for name, color, res in results:
        corr, h1n = res["corr_c"], res["h1n"]
        ax.plot(corr, h1n, color=color, lw=1.6, alpha=0.85, zorder=3)
        ax.scatter(corr, h1n, s=26, c=color, alpha=0.55, zorder=3)
        k = min(3, len(corr) - 1)
        if k >= 1:
            ax.annotate("", xy=(corr[-1], h1n[-1]), xytext=(corr[-k - 1], h1n[-k - 1]),
                        arrowprops=dict(arrowstyle="-|>", color=color, lw=1.6, alpha=0.9), zorder=5)
        ax.scatter([corr[-1]], [h1n[-1]], s=190, c=color, marker="*",
                   edgecolors="white", linewidths=0.8, zorder=6)
        ax.annotate("%s·今" % name, xy=(corr[-1], h1n[-1]), xytext=(6, 8),
                    textcoords="offset points", color=color, fontsize=10.5,
                    fontweight="bold", zorder=7)

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("同步度 ＝ 成分股平均两两相关 corr　　　　向右 → 越同步（锁死·系统性风险）", fontsize=11)
    ax.set_ylabel("拓扑再生度 ＝ H1 环寿命（归一化）　　　　向上 → 环越活（分化·机会）", fontsize=11)
    ax.set_xticks(np.arange(0, 1.01, 0.1))
    ax.set_yticks(np.arange(0, 1.01, 0.1))
    ax.tick_params(labelsize=9)
    ax.set_title("SPD 流形二维相位图 · 60天窗口滚动", fontsize=13, pad=10)
    handles = [Line2D([], [], color=color, lw=2, label=name) for name, color, _ in results]
    handles.append(Line2D([], [], marker="*", color="white", markerfacecolor=GOLD,
                          markersize=15, label="当前窗口", linestyle="None"))
    ax.legend(handles=handles, loc="lower right", framealpha=0.25, fontsize=9)

    # 右栏：上=H1 机理，下=当前读数
    gsr = gs[0, 1].subgridspec(2, 1, height_ratios=[1.05, 1.0], hspace=0.30)
    tax = fig.add_subplot(gsr[0])
    rax = fig.add_subplot(gsr[1])
    rax.set_facecolor(PANEL)
    rax.set_xticks([]); rax.set_yticks([])
    for s in ("top", "right", "bottom", "left"):
        rax.spines[s].set_visible(False)
    rax.set_xlim(0, 1); rax.set_ylim(0, 1)
    rax.set_title("当前读数（最近 60 天窗口）", fontsize=11, pad=6)

    # H1 机理小图
    tax.set_facecolor(PANEL)
    tax.set_xticks([]); tax.set_yticks([])
    for s in ("top", "right", "bottom", "left"):
        tax.spines[s].set_visible(False)
    tax.set_xlim(-2.75, 2.75); tax.set_ylim(-1.55, 1.55)
    rng = np.random.default_rng(42)
    blob = rng.normal(0, 0.32, size=(46, 2))
    tax.scatter(blob[:, 0] - 1.45, blob[:, 1], s=15, c=RED, alpha=0.72, edgecolors="none")
    tax.annotate("", xy=(-1.45, -1.05), xytext=(-1.45, -0.62),
                 arrowprops=dict(arrowstyle="->", color="#fda4af", lw=1.1))
    tax.text(-1.45, -1.28, "同步·锁死", fontsize=8.5, color="#fda4af", ha="center", fontweight="bold")
    tax.text(-1.45, -1.47, "H1 环塌缩(短)", fontsize=7.5, color=MUTED, ha="center")
    ang = rng.uniform(0, 2 * np.pi, 48)
    rad = 0.62 + rng.normal(0, 0.09, 48)
    ring = np.column_stack([rad * np.cos(ang), rad * np.sin(ang)])
    tax.scatter(ring[:, 0] + 1.45, ring[:, 1], s=15, c=BLUE, alpha=0.78, edgecolors="none")
    t = np.linspace(0, 2 * np.pi, 200)
    tax.plot(1.45 + 0.62 * np.cos(t), 0.62 * np.sin(t), color="#67e8f9", lw=1.3, ls="--", alpha=0.9)
    tax.annotate("", xy=(1.45, 0.78), xytext=(1.45, 0.42),
                 arrowprops=dict(arrowstyle="->", color="#7dd3fc", lw=1.1))
    tax.text(1.45, 0.98, "分化·结构解冻", fontsize=8.5, color="#7dd3fc", ha="center", fontweight="bold")
    tax.text(1.45, 1.17, "H1 环重生(长)", fontsize=7.5, color=MUTED, ha="center")
    tax.text(0, 1.47, "成分股收益点云 · 中间有洞 = 市场有独立方向 = 机会",
             fontsize=8.2, color=TEXTC, ha="center")
    tax.set_title("为什么用 H1 环寿命？", fontsize=11, pad=6)

    y = 0.94
    for name, color, res in results:
        zone = _spd.classify(res["corr_c"][-1], res["h1n"][-1], corr_th, h1_th)
        zname, zdesc = ZONE_LABEL[zone]
        zcolor = _zone_color(zone)
        rax.text(0.02, y, name, fontsize=12, color=color, fontweight="bold", va="center")
        rax.text(0.42, y, zname, fontsize=12, color=zcolor, fontweight="bold", va="center")
        y -= 0.066
        rax.text(0.02, y, zdesc, fontsize=8.5, color=MUTED, va="center")
        y -= 0.056
        for label, key, bc in (("同步度 corr", "corr_c", ORANGE), ("H1环寿命", "h1n", BLUE)):
            v = float(np.clip(res[key][-1], 0, 1))
            rax.text(0.02, y, label, fontsize=8.5, color=TEXTC, va="center")
            rax.add_patch(plt.Rectangle((0.40, y - 0.014), 0.46, 0.028, facecolor=GRIDC, alpha=0.5, lw=0))
            rax.add_patch(plt.Rectangle((0.40, y - 0.014), 0.46 * v, 0.028, facecolor=bc, alpha=0.85, lw=0))
            rax.text(0.885, y, "%.2f" % v, fontsize=8, color=TEXTC, va="center")
            y -= 0.050
        rax.text(0.02, y, "谱熵=%.2f   Hurst=%.2f" % (res["ent"][-1], res["hurst_full"]),
                 fontsize=8, color=MUTED, va="center")
        y -= 0.078

    fig.text(0.055, 0.035,
             "读法：点落象限 = 当前状态；轨迹方向 = 趋势（往左上 = 转机会，往右下 = 转风险）。"
             "H1 环寿命 = 成分股收益点云里「洞」的持久度：有洞 = 市场存在独立方向 = 分化机会，无洞 = 全市场同步 = 系统性风险。",
             fontsize=10, color="#94a3b8")
    fig.suptitle("SPD 流形二维化 —— 机会 / 风险 相位图", fontsize=16, color="#e2e8f0", y=0.97)
    fig.savefig(out_path, dpi=115, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _zone_color(zone):
    return {"chance": BLUE, "risk": RED, "bottom": GOLD, "alert": ORANGE}[zone]


# ───────────────────────── 多标的合并总览图 ─────────────────────────
def plot_multi(results, out_path, name_map=None):
    """多标的合并到一张图：每个标的占一行子图，纵向堆叠、横向对比。

    与 plot_phase（所有标的混到一张相位图）互补——这个满足「每个指数一个子图」：
      每行画三条 0~1 可直接跨标的对比的信号：
        同步度 corr（绿）＝ 越高越锁死（风险）
        H1 环寿命（青）＝ 越高越分化（机会）
        谱熵 ent（紫）＝ 越高越结构化
      叠加红/黄/蓝结构分区背景，当前窗口打星，左侧标名称、右侧标当前结论象限。
    单标的也兼容（画一行），但单标的默认走 plot_spd 更丰富。
    """
    from . import spd as _spd
    n = len(results)
    if n == 0:
        return None

    fig_h = 2.55 * n + 3.8
    fig, axes = plt.subplots(n, 1, figsize=(17.5, fig_h), sharex=True, squeeze=False)
    axes = axes[:, 0]

    last_dates = None
    for ax, (name, color, res) in zip(axes, results):
        dates = res["dates"]
        last_dates = dates
        n_days = len(dates)
        W, m = res["W"], res["m"]
        x = np.arange(n_days)
        xw = np.arange(W - 1, n_days)

        # 结构分区背景：红=锁死(风险) 黄=卡滞 蓝=分化(机会)
        for i0, i1, st in _zone_spans(res["states"], m, n_days):
            ax.axvspan(i0 - 0.5, i1 + 0.5, color=[RED, GOLD, BLUE][st], alpha=0.14, lw=0)

        # 三条 0~1 信号
        ax.plot(xw, res["corr_c"], color=GREEN, lw=1.5, alpha=0.92, label="同步度 corr")
        ax.plot(xw, res["h1n"], color=CYAN, lw=1.5, alpha=0.92, label="H1 环寿命")
        ax.plot(xw, np.clip(res["ent"], 0, 1), color=PURPLE, lw=1.2, alpha=0.85,
                label="谱熵 ent")

        # 0.5 分界参考线
        ax.axhline(0.5, color=GRIDC, lw=0.8, ls="--", alpha=0.6)

        # 当前窗口星标
        ax.scatter([xw[-1]], [res["corr_c"][-1]], s=110, c=color, marker="*",
                   edgecolors="white", linewidths=0.8, zorder=6)

        # 左侧名称 + 右侧当前结论
        zone = _spd.classify(res["corr_c"][-1], res["h1n"][-1])
        zname, zdesc = ZONE_LABEL[zone]
        zcolor = _zone_color(zone)
        ax.set_ylabel(name, rotation=0, ha="right", va="center",
                      fontsize=12, color=color, fontweight="bold", labelpad=6)
        ax.text(1.006, 0.70, zname, transform=ax.transAxes, fontsize=12.5,
                color=zcolor, fontweight="bold", va="center")
        ax.text(1.006, 0.48, zdesc, transform=ax.transAxes, fontsize=8.5,
                color=MUTED, va="center")
        ax.text(1.006, 0.26, "corr=%.2f  H1=%.2f" % (res["corr_c"][-1], res["h1n"][-1]),
                transform=ax.transAxes, fontsize=8.5, color=TEXTC, va="center")

        ax.set_ylim(0, 1)
        ax.set_xlim(-1, n_days)
        ax.grid(alpha=0.4)

    # 共享 x 轴：只在最底行画日期刻度
    if last_dates is not None:
        n_days = len(last_dates)
        x = np.arange(n_days)
        axes[-1].set_xticks(x[::10])
        axes[-1].set_xticklabels([d[4:] for d in last_dates[::10]])
    axes[-1].legend(loc="upper left", framealpha=0.25, fontsize=9, ncol=3)

    fig.text(0.006, 0.52, "↑ 拓扑再生（H1 环寿命 / 谱熵）　　↓ 同步锁死（平均相关 corr）",
             rotation=90, fontsize=10, color=MUTED, va="center", ha="center")
    fig.text(0.03, 0.975, "多标的 SPD 流形结构总览 · 每行一个标的 · 右栏 = 当前结论",
             fontsize=13, color="#e2e8f0", va="center")
    fig.text(0.03, 0.012,
             "读法：绿线(同步度)↑且青线(H1)↓ = 转风险；绿线↓且青线↑ = 转机会。"
             "红/黄/蓝底 = 结构分区（锁死 / 卡滞 / 分化）。星号 = 当前窗口。",
             fontsize=10, color="#94a3b8")
    fig.savefig(out_path, dpi=115, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return out_path
