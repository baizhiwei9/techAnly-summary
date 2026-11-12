#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买点图：日线 / 小时线 / 缠论均线版 —— 三面板一张图
=====================================================================
给 bin/tech_buypoint.py 的个股买点出图（大佬 2026-11-09 21:00 要求：
「你这个日线版、小时线版还有均线版，这都是要带图的，都是子图放在一个图了」）

面板（从上到下）：
  ① 日线      K线 + 缠论分型版笔/中枢/买卖点（买点=绿月亮🌙 卖点=红星⭐）+ 量 + MACD
  ② 小时线60m  同上，只画缠论分型版（小时线只认三买 / 一买须金叉）
  ③ 缠论均线版  MA5/10/20/40/80/160 + 笔(MA5/10穿20) + 线段(MA20/40穿80) + 走势类型底色

实现复用 tech_summary 的 draw 逻辑与 chan_geometry，口径完全一致。
0 token，纯 matplotlib。
"""
from __future__ import annotations

import os
import socket
import sys

_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.text  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

def _repo_root(start):
    """向上探测仓库根：同时含 bin/ 与 core/ 的那一层（2026-11-12 目录整理后可任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "chanlun")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
# 顺序有讲究：最后插入的优先级最高 → bin/ 放最前 = 最低优先级，
# 保证 import 命中真实目录，而不是 bin/ 里的兼容 shim。
for _sd in ("bin", "", "misc/core", "chanlun/fractal", "chanlun/ma", "chanlun/multiscale",
            "chanlun/multiscale/vendor", "misc/report", "misc", "wavelet", "smc",
            "misc/strategies", "misc/tools"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# ── 出图清晰度（大佬 2026-11-11：图不清楚，要更高清）──
#   TECH_CHART_DPI     画布 DPI（默认 200；单张三面板用 CHART_DPI 默认 170）
#   放大字号的同时按 DPI 缩放线宽，保证 3000px 宽时线条不发虚
CHART_DPI = int(os.environ.get("CHART_DPI", "200"))
GRID_DPI = int(os.environ.get("TECH_CHART_DPI", "220"))


def _finalize_png(path):
    """存完转成 RGB（去 alpha）：体积更小、各端显示更稳（RGBA 在部分 IM 里会发灰）"""
    try:
        from PIL import Image
        im = Image.open(path)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im2 = im.convert("RGBA")
            bg.paste(im2, mask=im2.split()[-1])
            bg.save(path, "PNG", optimize=True)
        return path
    except Exception:
        return path


import chan_ma_trend_v2 as M          # noqa: E402
import tech_summary as TS            # noqa: E402
from tech_summary import (            # noqa: E402
    MOON, STAR, MA_PALETTE, MA_LW, RED, GRN, BLUE, ORANGE,
    glyph_marker, ma_structure, ma20_state,
)

CHART_DIR = os.path.join(WS, ".cache", "charts")
os.makedirs(CHART_DIR, exist_ok=True)


def _fmt_pct(v):
    return "%+.2f%%" % v


def _draw_kline_panel(ax, axV, axM, df, fx, n_show, title, kind="日线"):
    """K线 + 缠论分型版结构 + 量 + MACD，返回 today_pct"""
    total = len(df)
    start = max(0, total - n_show)
    view = df.iloc[start:].reset_index(drop=True)
    nv = len(view)
    x = np.arange(nv)
    o = view["open"].values.astype(float)
    h = view["high"].values.astype(float)
    l = view["low"].values.astype(float)
    c = view["close"].values.astype(float)
    prev_c = np.concatenate([[np.nan], c[:-1]])
    if start > 0:
        prev_c[0] = float(df["close"].iloc[start - 1])
    # ⚠️ 涨跌按 close vs 上一根 close 上色（MEMORY 硬规矩）
    up_col = [RED if (np.isfinite(prev_c[i]) and c[i] >= prev_c[i]) else GRN for i in range(nv)]
    today_pct = (c[-1] - prev_c[-1]) / prev_c[-1] * 100 if np.isfinite(prev_c[-1]) else 0.0

    bw = 0.62
    for i in range(nv):
        ax.vlines(x[i], l[i], h[i], color=up_col[i], lw=0.9, zorder=3)
        body_lo, body_hi = min(o[i], c[i]), max(o[i], c[i])
        if abs(body_hi - body_lo) < 1e-9:
            body_hi = body_lo + (h[i] - l[i]) * 0.001 + 1e-6
        ax.add_patch(Rectangle((x[i] - bw / 2, body_lo), bw, body_hi - body_lo,
                               facecolor=up_col[i], edgecolor=up_col[i], lw=0.5, zorder=4))

    # 缠论几何（用全量算，再按索引裁切）
    from chan_chart import chan_geometry
    geo = chan_geometry(df)
    bx, by = [], []
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -2 <= xi <= nv + 2:
            bx.append(xi)
            by.append(p["price"])
    if len(bx) >= 2:
        ax.plot(bx, by, color=BLUE, lw=1.5, zorder=6, label="缠论笔")
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -1 <= xi <= nv:
            ax.scatter([xi], [p["price"]], s=20, zorder=7,
                       color=(RED if p["type"] == 1 else GRN),
                       marker=("v" if p["type"] == 1 else "^"))

    # 中枢
    if geo["zs"] and geo["zs_x"]:
        zs0, zs1 = geo["zs"]
        x0 = max(geo["zs_x"][0] - start, -0.5)
        x1 = min(geo["zs_x"][1] - start, nv)
        if x1 >= -1 and x1 > x0:
            ax.add_patch(Rectangle((x0, zs0), x1 - x0, zs1 - zs0,
                                   facecolor="#ffd24d", alpha=0.25,
                                   edgecolor="#b8860b", lw=1.4, zorder=3))
            ax.axhline(zs1, color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
            ax.axhline(zs0, color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
            ax.text(max(x0, 0) + 0.5, (zs0 + zs1) / 2, "中枢 ZD %.2f~ZG %.2f" % (zs0, zs1),
                    fontsize=8, color="#8b6508", va="center", zorder=9)

    # 买卖点：买点=绿月牙 卖点=红星
    # ⚠️ 2026-11-09：买点必须过闸门（年龄/距离/MA20 方向），否则不画——
    #    风语筑事故：116 根前的一买 8.78 被画成当前买点，误导。
    BUY = {"一买": ("#0b6e0b", 460), "二买": ("#1a9850", 340),
           "三买": ("#2e9b4f", 300)}
    SELL = {"一卖": ("#8b0000", 420), "二卖": ("#d73027", 300)}
    _cl = float(df["close"].iloc[-1])
    _buy_ok = [x for x in TS.rank_buys(fx.get("buy_list") or [],
                                       TS.ma20_state(df), close=_cl) if x["accept"]]
    for lst, styles, dy, is_buy in ((_buy_ok, BUY, -1, True),
                                     (fx.get("sell_list") or [], SELL, 1, False)):
        for b in lst:
            if b["tag"] not in styles:
                continue
            if not (b.get("fresh") or b.get("sustained")):
                continue
            col, sz = styles[b["tag"]]
            cand = [p for p in geo["bi"] if p["type"] == (1 if not is_buy else -1)]
            if not cand:
                continue
            pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
            xi = pt["orig"] - start
            if not (-1 <= xi <= nv):
                continue
            ax.scatter([xi], [pt["price"]], s=sz, marker=(MOON if is_buy else STAR),
                       color=col, edgecolor=(col if is_buy else "white"),
                       lw=(1.9 if is_buy else 1.2), zorder=10)
            ax.annotate("%s %.2f\n%d根前" % (b["tag"], b["level"], b["age"]),
                        xy=(xi, pt["price"]), xytext=(0, 30 * dy),
                        textcoords="offset points", ha="center",
                        va=("top" if dy < 0 else "bottom"),
                        fontsize=8, color=col, fontweight="bold", zorder=11,
                        bbox=dict(boxstyle="round,pad=0.24", fc="white", ec=col,
                                  alpha=0.94, lw=0.9))

    ax.set_ylabel("价格", fontsize=9)
    ax.grid(True, ls=":", color="#dddddd", alpha=0.7)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9, ncol=2)
    ax.set_xlim(-1, nv)
    ax.set_title("%s ｜ 收 %.2f ｜ 今日 %s" % (title, c[-1], _fmt_pct(today_pct)),
                 fontsize=11.5, color="#222222", pad=7, loc="left")
    ax.tick_params(labelbottom=False, labelsize=7.8, pad=2)

    # 量
    axV.bar(x, view["volume"].values.astype(float), width=bw, color=up_col, alpha=0.75)
    axV.set_ylabel("量", fontsize=8)
    axV.grid(True, ls=":", color="#dddddd", alpha=0.5)
    axV.tick_params(labelbottom=False, labelsize=7.6, pad=2)

    # MACD(10,20,5)
    cl = df["close"].astype(float)
    dif = cl.ewm(span=10, adjust=False).mean() - cl.ewm(span=20, adjust=False).mean()
    dea = dif.ewm(span=5, adjust=False).mean()
    hist = 2 * (dif - dea)
    axM.bar(x, hist.values[start:],
            color=["#d62728" if v >= 0 else "#2ca02c" for v in hist.values[start:]],
            width=bw, alpha=0.7)
    axM.plot(x, dif.values[start:], color=ORANGE, lw=1.0, label="DIF")
    axM.plot(x, dea.values[start:], color="#2980b9", lw=1.0, label="DEA")
    axM.axhline(0, color="#888888", lw=0.7)
    axM.legend(loc="upper left", fontsize=7.5, framealpha=0.85, ncol=3)
    axM.set_ylabel("MACD", fontsize=8)
    axM.tick_params(labelsize=7.6, pad=2)
    step = max(1, nv // 12)
    ticks = list(range(0, nv, step))
    axM.set_xticks(ticks)
    axM.set_xticklabels([pd.Timestamp(view["dt"].iloc[i]).strftime("%m-%d") for i in ticks],
                        fontsize=7.8)
    return today_pct


def _draw_ma_panel(ax, df, n_ma=400):
    """缠论均线版：走势类型底色 + 线段 + 笔 + 6 条均线"""
    n = len(df)
    n_ma = min(max(n_ma, 60), n)
    off = n - n_ma
    view = df.iloc[off:].reset_index(drop=True)
    x = np.arange(n_ma)
    dd = M.add_ind(df)
    st = ma_structure(df)

    MODE_COL = {"trend_up": ("#ffe3e3", "上涨"), "trend_dn": ("#e0f2e4", "下跌"),
                "range": ("#efefef", "盘整")}
    MODE_TXT = {"trend_up": "#c62828", "trend_dn": "#1b7e32", "range": "#555555"}

    # ① 走势类型背景（最淡）
    if st:
        labeled = set()
        for i0, i1, kind in st["mode"]:
            x0, x1 = i0 - off, i1 - off
            if x1 < -1 or x0 > n_ma:
                continue
            x0, x1 = max(x0, -1), min(x1, n_ma)
            fc, lb = MODE_COL[kind]
            ax.axvspan(x0, x1, facecolor=fc, alpha=0.55, zorder=0, lw=0)
            if (x1 - x0) > n_ma * 0.10 and kind not in labeled:
                labeled.add(kind)
                ax.text((x0 + x1) / 2, 0.84, lb, transform=ax.get_xaxis_transform(),
                        ha="center", va="top", fontsize=8.5, color=MODE_TXT[kind],
                        fontweight="bold", zorder=3, alpha=0.55)

    # ② 收盘线（淡灰）
    ax.plot(x, view["close"].values.astype(float), color="#b0b0b0", lw=0.9,
            alpha=0.85, label="收盘", zorder=3)

    # ③ 均线：图上只画 MA10/20 + MA160（MA5 画着太乱，故不画；计算里 MA5 仍在用 —— 笔要双穿）
    for nn in (10, 20):
        col = "ma%d" % nn
        if col not in dd.columns:
            continue
        y = dd[col].values[off:]
        if np.all(~np.isfinite(y)):
            continue
        ax.plot(x, y, color=MA_PALETTE[nn], lw=0.8, alpha=0.55,
                label="MA%d" % nn, zorder=4)
    if "ma160" in dd.columns:
        y = dd["ma160"].values[off:]
        if np.any(np.isfinite(y)):
            ax.plot(x, y, color="#8e44ad", lw=1.3, alpha=0.55, ls="--",
                    label="MA160(大级别)", zorder=4)
            yv = y[-1]
            if np.isfinite(yv):
                ax.annotate("MA160 %.2f" % yv, xy=(n_ma - 0.6, yv), xytext=(6, 0),
                            textcoords="offset points", va="center", ha="left",
                            fontsize=7.4, color="#8e44ad", fontweight="bold",
                            annotation_clip=False, zorder=12)

    # ④ 中枢（线段三段重叠区）—— 黄框 + ZG/ZD 虚线
    if st and st.get("zs"):
        # 只给「最宽的 2 个」中枢打标签：多中枢相邻时文字会互相压
        _zs_sorted = sorted(range(len(st["zs"])),
                            key=lambda i: -(st["zs"][i]["i1"] - st["zs"][i]["i0"]))
        _zs_label_ok = set(_zs_sorted[:2])
        for _zi, zs in enumerate(st["zs"]):
            x0, x1 = zs["i0"] - off, zs["i1"] - off
            if x1 < -1 or x0 > n_ma:
                continue
            x0, x1 = max(x0, -0.5), min(x1, n_ma)
            if x1 <= x0:
                continue
            ax.add_patch(Rectangle((x0, zs["zd"]), x1 - x0, zs["zg"] - zs["zd"],
                                   facecolor="#ffd24d", alpha=0.30,
                                   edgecolor="#c07800", lw=2.2, zorder=6))
            ax.hlines([zs["zg"], zs["zd"]], x0, x1,
                      colors="#c07800", lw=1.4, ls="--", alpha=0.85, zorder=7)
            if (x1 - x0) > n_ma * 0.08 and _zi in _zs_label_ok:
                # 放框内靠上（va=top），避免和 ZG 线、上方走势类型底色文字叠在一起
                ax.text(x1 - 1.5, zs["zg"] - (zs["zg"] - zs["zd"]) * 0.08,
                        "中枢 %.0f~%.0f" % (zs["zd"], zs["zg"]),
                        fontsize=7.2, color="#8b6508", ha="right", va="top",
                        fontweight="bold", zorder=11,
                        bbox=dict(boxstyle="round,pad=0.14", fc="white",
                                  ec="#b8860b", lw=0.5, alpha=0.88))

    # ⑤ 线段（MA20/40 穿 MA80）—— 主结构，最深最粗
    if st and len(st["seg"]) >= 2:
        sx, sy = [], []
        for p in st["seg"]:
            xi = p["i"] - off
            if -2 <= xi <= n_ma + 2:
                sx.append(xi)
                sy.append(p["px"])
        if len(sx) >= 2:
            ax.plot(sx, sy, color="#0d2b45", lw=3.6, alpha=1.0, zorder=9,
                    label="线段(MA20/40穿MA80)")
            # 未完成的线段：最后极值 → 最新收盘 用虚线补齐
            _lp = st["seg"][-1]
            if _lp.get("live") and sx[-1] < n_ma - 1:
                ax.plot([sx[-1], n_ma - 1], [sy[-1], float(view["close"].iloc[-1])],
                        color="#0d2b45", lw=2.0, ls="--", alpha=0.75, zorder=9)
            for i in range(len(sx)):
                ax.scatter([sx[i]], [sy[i]], s=62, marker="s", color="#0d2b45",
                           edgecolor="white", lw=1.4, zorder=10)
                if i and abs(sx[i] - sx[i - 1]) > 2:
                    ax.annotate("线段", xy=((sx[i] + sx[i - 1]) / 2, (sy[i] + sy[i - 1]) / 2),
                                fontsize=7.0, color="#0d2b45", ha="center", va="center",
                                fontweight="bold", zorder=10,
                                bbox=dict(boxstyle="round,pad=0.16", fc="white",
                                          ec="#0d2b45", lw=0.6, alpha=0.88))

    # ⑥ 笔（MA5/10 穿 MA20）—— 次级结构
    if st and len(st["bi"]) >= 2:
        bx2, by2 = [], []
        for p in st["bi"]:
            xi = p["i"] - off
            if -2 <= xi <= n_ma + 2:
                bx2.append(xi)
                by2.append(p["px"])
        if len(bx2) >= 2:
            ax.plot(bx2, by2, color="#e8955a", lw=1.3, alpha=0.9, zorder=7,
                    label="笔(MA5/10穿MA20)")
            _bp = st["bi"][-1]
            if _bp.get("live") and bx2[-1] < n_ma - 1:
                ax.plot([bx2[-1], n_ma - 1], [by2[-1], float(view["close"].iloc[-1])],
                        color="#e8955a", lw=1.0, ls="--", alpha=0.7, zorder=7)
            for i in range(len(bx2)):
                ax.scatter([bx2[i]], [by2[i]], s=12, marker="o", color="#e8955a",
                           edgecolor="white", lw=0.5, zorder=8)

    # 技能核心结论（tradeable 一句话）+ 大级别 MA160（与 chan-ma-trend 技能完全同口径）
    try:
        tr = M.verdict(dd)
        lv = M.level_locate(dd)
        big = lv.get("big") or {}
        tcol = {"✅": "#1b7e32", "⛔": "#c62828", "🟡": "#b8860b", "⚪": "#666666"}
        cc2 = next((v for k, v in tcol.items() if tr.startswith(k)), "#333333")
        # ⚠️ 图内不能出现 emoji（无彩色字形 → 豆腐块），去前缀只靠颜色区分
        for _e in tcol:
            if tr.startswith(_e):
                tr = tr[len(_e):].strip()
                break
        _al = big.get("align") or "—"
        ax.text(0.008, 0.975, "%s\n大级别(MA160)：%s" % (tr, _al),
                transform=ax.transAxes, fontsize=8.6, color=cc2, fontweight="bold",
                va="top", ha="left", linespacing=1.35,
                bbox=dict(boxstyle="round,pad=0.26", fc="white", ec=cc2, alpha=0.95, lw=1.1))
        # ▶ 当前位置（笔上/笔下）+ 关键位
        wk = M.bi_walk(dd)
        if wk.get("ok"):
            wc = {"up": "#c62828", "dn": "#2ca02c", "mid": "#b8860b"}.get(wk["state"], "#333")
            lvt = " ｜ ".join("%s %.2f" % (k, v) for k, v in (wk.get("levels") or [])[:3])
            # 右对齐到右边，避免和左上结论框挤在中间（原来 ha=center 会撞）
            ax.text(0.995, 0.975, wk["where"] + (("\n" + lvt) if lvt else ""),
                    transform=ax.transAxes, fontsize=8.2, color=wc, fontweight="bold",
                    va="top", ha="right", linespacing=1.35,
                    bbox=dict(boxstyle="round,pad=0.26", fc="white", ec=wc, alpha=0.95, lw=1.1))
    except Exception:
        pass
    ax.set_ylabel("缠论·均线版", fontsize=9)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.6)
    ax.legend(loc="lower right", fontsize=7.4, framealpha=0.9, ncol=2)
    ax.set_xlim(-1, n_ma)
    # 说明行放**轴内底部**（顶部已被结论框占用，放顶上必压）
    ax.text(0.004, 0.022,
            "缠论·均线版（近 %d 根）｜ 笔=MA5/10双穿MA20 ｜ 线段=MA20/40双穿MA80 ｜ 底色=走势类型" % n_ma,
            transform=ax.transAxes, fontsize=8.6, color="#333333",
            ha="left", va="bottom", zorder=12,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.75))
    step = max(1, n_ma // 14)
    tk = list(range(step, n_ma, step))
    ax.set_xticks(tk)
    ax.set_xticklabels([pd.Timestamp(view["dt"].iloc[i]).strftime("%m-%d") for i in tk],
                       fontsize=7.6)
    ax.tick_params(axis="y", labelsize=7.6, pad=2)
    return st


def draw_buypoint_chart(disp, day, h1=None, out=None, n_day=120, n_h1=110, n_ma=400, dpi=None):
    """三面板买点图：日线 / 小时线 / 缠论均线版 → 出 PNG 路径"""
    d = day.copy()
    if "volume" not in d.columns:
        d["volume"] = d.get("vol", 0.0)
    fx_d = TS.chan_fx(d, "day")

    has_h1 = h1 is not None and len(h1) > 60
    # 日线：价/量/MACD + （小时线：价/量/MACD）+ 均线版
    heights = [2.8, 1.1, 1.0]
    if has_h1:
        heights = [2.8, 1.1, 1.0, 2.4, 1.0, 0.9]
    heights.append(2.6)
    nrows = len(heights)
    # 清晰度：画布从 16in 收到 11in（字号不变 → 相对字号大 1.45 倍），dpi 170→200
    fig = plt.figure(figsize=(11.0, 14.6 if has_h1 else 10.4), facecolor="white")
    gs = fig.add_gridspec(nrows, 1, height_ratios=heights, hspace=0.46)

    axP = fig.add_subplot(gs[0])
    axV = fig.add_subplot(gs[1], sharex=axP)
    axM = fig.add_subplot(gs[2], sharex=axP)
    pct_d = _draw_kline_panel(axP, axV, axM, d, fx_d, n_day,
                              "%s · 日线（缠论分型版）" % disp, "day")

    if has_h1:
        hh = h1.copy()
        if "volume" not in hh.columns:
            hh["volume"] = 0.0
        fx_h = TS.chan_fx(hh, "h1")
        axP2 = fig.add_subplot(gs[3])
        axV2 = fig.add_subplot(gs[4], sharex=axP2)
        axM2 = fig.add_subplot(gs[5], sharex=axP2)
        _draw_kline_panel(axP2, axV2, axM2, hh, fx_h, n_h1,
                          "%s · 小时线 60m（缠论分型版）" % disp, "h1")

    axA = fig.add_subplot(gs[nrows - 1])
    st = _draw_ma_panel(axA, d, n_ma)

    try:
        fig.canvas.draw()
    except Exception:
        pass
    _t1, _f1 = _fit_text(fig, "%s ｜ 买点图（日线优先 · 小时线次级 · 均线版结构）｜ 买点=绿月牙 卖点=红星"
                         % disp, 17.0, 10.4)
    fig.text(0.5, 0.988, _t1, fontsize=_f1, color="#111111", ha="center", va="top",
             fontweight="bold")
    _n1, _nf1 = _fit_text(fig, "K线上色 = 收盘 vs 前收　｜　日线/小时线：笔(蓝) 中枢(黄) 买卖点(月牙/星)"
                          "　｜　均线版：笔(深橙) 线段(深蓝) 走势类型底色", 11.5, 10.4)
    fig.text(0.5, 0.006, _n1, ha="center", va="bottom",
             fontsize=_nf1, color="#666666")

    if out is None:
        out = os.path.join(CHART_DIR, "buypoint_%s_%s.png"
                           % (disp.replace("/", "_"),
                              pd.Timestamp(d["dt"].iloc[-1]).strftime("%Y%m%d")))
    _debug_overlaps(fig, "panel")
    fig.savefig(out, dpi=(dpi or CHART_DPI), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    _finalize_png(out)
    return out


# ══════════════════════════════════════════════════════════════════════
#  多标的网格图：每只一个子图（日线 K 线 + 缠论分型版 + VWAP 六轨 + 量）
#  用于「多个行业合并后，选出有买点的前 N 只」一次性出图
# ══════════════════════════════════════════════════════════════════════
def _debug_overlaps(fig, tag="fig"):
    """TECH_GRID_DEBUG=1 时自检：文字互相重叠 / 顶出画布。0 成本（不设环境变量直接返回）"""
    if not os.environ.get("TECH_GRID_DEBUG"):
        return
    try:
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        Wf, Hf = fig.bbox.width, fig.bbox.height
        boxes = []
        for t in fig.findobj(matplotlib.text.Text):
            if not t.get_text().strip() or not t.get_visible():
                continue
            try:
                bb = t.get_window_extent(renderer=r)
            except Exception:
                continue
            if bb.width <= 1 or bb.height <= 1:
                continue
            boxes.append((t.get_text(), bb))
        bad = 0
        for a in range(len(boxes)):
            for b in range(a + 1, len(boxes)):
                A, B = boxes[a][1], boxes[b][1]
                ix = min(A.x1, B.x1) - max(A.x0, B.x0)
                iy = min(A.y1, B.y1) - max(A.y0, B.y0)
                if ix > 2 and iy > 2:
                    bad += 1
                    print("[OVERLAP] %-28s <-> %-28s dx=%.0f dy=%.0f"
                          % (boxes[a][0][:28], boxes[b][0][:28], ix, iy))
        oob = [(t, bb) for t, bb in boxes
               if bb.x1 > Wf - 4 or bb.x0 < 4 or bb.y1 > Hf - 4 or bb.y0 < 4]
        for t, bb in oob[:10]:
            print("[OOB] %-30s x0=%.0f x1=%.0f y0=%.0f y1=%.0f (fig %.0fx%.0f)"
                  % (t[:30], bb.x0, bb.x1, bb.y0, bb.y1, Wf, Hf))
        print("[dbg:%s] texts=%d overlaps=%d oob=%d" % (tag, len(boxes), bad, len(oob)))
    except Exception as e:
        print("[dbg] fail", e)


def _fit_text(fig, s, fs, max_in, min_fs=8.5):
    """把文字量宽：超宽先按比例缩字号，仍超宽就截断加省略号。

    大佬 2026-11-11：「图里边的这个字都重叠了」——根因就是文字宽度没管，
    长标题/长标注直接画出画布或压到隔壁列。这里统一做边界控制。
    """
    try:
        r = fig.canvas.get_renderer()
    except Exception:
        return s, fs
    def _w(txt, size):
        t = fig.text(0, 0, txt, fontsize=size)
        try:
            w = t.get_window_extent(renderer=r).width / fig.dpi
        finally:
            t.remove()
        return w
    if _w(s, fs) <= max_in:
        return s, fs
    fs2 = max(min_fs, fs * max_in / max(_w(s, fs), 1e-6))
    if _w(s, fs2) <= max_in:
        return s, fs2
    for keep in range(len(s) - 1, 4, -1):
        cand = s[:keep] + "…"
        if _w(cand, fs2) <= max_in:
            return cand, fs2
    return s[:4] + "…", fs2


def draw_stock_grid(picks, out_path, n_show=120, title=None, ncol=None, dpi=None,
                    max_items=20):
    """一张图最多 20 个子图（不拆页），每只股票一个子图：

        K线（涨跌上色）+ 缠论分型版（笔/中枢）+ VWAP 六轨 + 买点(月牙)/卖点(五角星)
        **每个子图下方固定一块标注**：买点什么价、卖点什么价、VWAP 位置

    大佬 2026-11-11 定稿：
      「一个图最多有 20 个子图就可以了。每个子图后边标识买卖点」
      → 不再分页（原来 4 只/页拆 5 张），一屏到底；子图下必须写清买卖点。

    ⚠️ 2026-11-11 23:xx 修「字重叠」：
      根因是标题/副标题/底部标注全用 transAxes 相对偏移，行距靠 h_pad 猜，
      字号一大就压在一起，长文字还会捅出画布 / 压到隔壁列。
      改法：**按英寸绝对布局**（每个子图独占 表头0.60" + 面板2.75" + 表脚0.92"），
      文字一律先量宽再决定缩字号或截断，不再用 tight_layout / bbox_inches=tight。

    picks: [{'name','code','df','pct','vw_z','ind','buys':[...],'sells':[...]}]
    """
    if not picks:
        return None
    picks = picks[:max_items]
    import smc_core as S
    from chan_chart import chan_geometry

    n = len(picks)
    # 布局：≤2 只 1 列；其余 2 列（20 只 = 2 列 × 10 行）
    ncol = ncol or (1 if n == 1 else 2)
    nrow = (n + ncol - 1) // ncol

    # ── 绝对尺寸（英寸）──
    COL_W = 6.6          # 单列宽
    M_L, M_R = 0.72, 0.34  # 左右留白（左侧放价格轴，右侧给贴边标注留余量）
    HEAD_H = 0.62        # 表头：两行标题
    PLOT_H = 2.78        # K线面板高
    FOOT_H = 0.96        # 表脚：两行（买点 / 卖点+VWAP），下面是 x 轴日期
    TOP_H = 0.62         # 总标题
    BOT_H = 0.52         # 图底说明
    ROW_H = HEAD_H + PLOT_H + FOOT_H
    fig_w = COL_W * ncol
    fig_h = TOP_H + ROW_H * nrow + BOT_H
    plot_w = COL_W - M_L - M_R

    fig = plt.figure(figsize=(fig_w, fig_h))
    try:
        fig.canvas.draw()
    except Exception:
        pass

    FS_TITLE, FS_SUB, FS_TICK, FS_NOTE = 17.5, 14.0, 11.5, 12.0
    FS_BUY, FS_SELL, FS_ZG = 14.5, 14.5, 13.0
    LW_K, LW_BI = 1.5, 3.0

    for k, s in enumerate(picks):
        r_, c_ = divmod(k, ncol)
        x0 = M_L + c_ * COL_W
        row_top = fig_h - TOP_H - r_ * ROW_H          # 本行顶边（离底部，英寸）
        plot_top = row_top - HEAD_H
        plot_bot = plot_top - PLOT_H
        ax = fig.add_axes([x0 / fig_w, plot_bot / fig_h,
                           plot_w / fig_w, PLOT_H / fig_h])

        d = s.get("df")
        if d is None or len(d) < 60:
            ax.text(.5, .5, "%s 取数失败" % s.get("name", ""), ha="center",
                    fontsize=FS_SUB, transform=ax.transAxes)
            ax.axis("off")
            continue
        total = len(d)
        start = max(0, total - n_show)
        view = d.iloc[start:].reset_index(drop=True)
        nv = len(view)
        x = np.arange(nv)
        o = view["open"].values.astype(float)
        h = view["high"].values.astype(float)
        lo = view["low"].values.astype(float)
        c = view["close"].values.astype(float)
        prev_c = np.concatenate([[np.nan], c[:-1]])
        if start > 0:
            prev_c[0] = float(d["close"].iloc[start - 1])
        cols = [RED if (np.isfinite(prev_c[i]) and c[i] >= prev_c[i]) else GRN for i in range(nv)]
        bw = 0.62
        for i in range(nv):
            ax.vlines(x[i], lo[i], h[i], color=cols[i], lw=LW_K, zorder=3)
            b0, b1 = min(o[i], c[i]), max(o[i], c[i])
            if abs(b1 - b0) < 1e-9:
                b1 = b0 + max((h[i] - lo[i]) * 1e-3, 1e-6)
            ax.add_patch(Rectangle((x[i] - bw / 2, b0), bw, b1 - b0,
                                   facecolor=cols[i], edgecolor=cols[i], lw=0.6, zorder=4))
        # ── VWAP 六轨（窗口 60）──
        try:
            vw = S.vwap_tracks(d)
            for key, col, lw_, ls, lab in (("up3", "#8e0000", 1.5, "-", "+3σ"),
                                           ("up2", "#c0392b", 1.4, "--", "+2σ"),
                                           ("up1", "#e08b7a", 1.2, ":", "+1σ"),
                                           ("vwap", "#111111", 2.5, "-", "VWAP"),
                                           ("dn1", "#1e8449", 1.4, "--", "−1σ"),
                                           ("dn2", "#0b4f2c", 1.5, "-", "−2σ")):
                v_ = vw[key][start:]
                if not np.all(np.isnan(v_)):
                    ax.plot(x, v_, color=col, lw=lw_, ls=ls, zorder=5,
                            label=lab if k == 0 else None)
        except Exception:
            pass
        # ── 缠论分型版：笔 / 中枢 ──
        geo = None
        try:
            geo = chan_geometry(d)
            bx, by = [], []
            for p in geo["bi"]:
                xi = p["orig"] - start
                if -2 <= xi <= nv + 2:
                    bx.append(xi)
                    by.append(p["price"])
            if len(bx) >= 2:
                ax.plot(bx, by, color=BLUE, lw=LW_BI, zorder=6,
                        label="缠论笔" if k == 0 else None)
            if geo["zs"] and geo["zs_x"]:
                zs0, zs1 = geo["zs"]
                x0z = max(geo["zs_x"][0] - start, -0.5)
                x1z = min(geo["zs_x"][1] - start, nv)
                if x1z > x0z:
                    ax.add_patch(Rectangle((x0z, zs0), x1z - x0z, zs1 - zs0,
                                           facecolor="#ffd24d", alpha=0.3,
                                           edgecolor="#c8961a", lw=1.6, zorder=3))
                    ax.axhline(zs1, color="#c8961a", lw=1.3, ls="--", alpha=0.9, zorder=4)
                    ax.axhline(zs0, color="#c8961a", lw=1.3, ls="--", alpha=0.9, zorder=4)
                    _zbx = dict(boxstyle="round,pad=0.16", fc="white", ec="none", alpha=0.82)
                    ax.annotate("ZG %.2f" % zs1, xy=(x0z + 1.0, zs1), xycoords="data",
                                xytext=(0, 2), textcoords="offset points", fontsize=FS_ZG,
                                color="#8b6508", fontweight="bold", va="bottom",
                                ha="left", zorder=9, bbox=_zbx)
                    ax.annotate("ZD %.2f" % zs0, xy=(x0z + 1.0, zs0), xycoords="data",
                                xytext=(0, -2), textcoords="offset points", fontsize=FS_ZG,
                                color="#8b6508", fontweight="bold", va="top",
                                ha="left", zorder=9, bbox=_zbx)
        except Exception:
            geo = None

        # ── 买点：月牙（只画过闸门的）；多个买点标签错位排，避免互相压 ──
        buy_txt = []
        try:
            _cl = float(d["close"].iloc[-1])
            _i_buy = 0
            for b in (s.get("buys") or TS.rank_buys(
                    TS.chan_fx(d, "day").get("buy_list") or [],
                    TS.ma20_state(d), close=_cl)):
                if not b.get("accept"):
                    continue
                buy_txt.append("%s %.2f" % (b.get("tag", "买点"), b.get("level", 0)))
                cand = [p for p in (geo or {"bi": []})["bi"] if p["type"] == -1]
                if not cand:
                    continue
                pt = min(cand, key=lambda q: abs(q["price"] - b["level"]))
                xi = pt["orig"] - start
                if not (-1 <= xi <= nv):
                    continue
                ax.scatter([xi], [pt["price"]], s=380, marker=MOON, color="#0b6e0b",
                           edgecolor="#0b6e0b", lw=2.0, zorder=10)
                _ha, _dx = ("right", -3) if xi > nv * 0.87 else \
                    (("left", 3) if xi < nv * 0.10 else ("center", 0))
                # 价格贴近底部（下方就是 x 轴日期）→ 标签改放上方，别压日期
                _y0, _y1 = ax.get_ylim()
                _rel = (pt["price"] - _y0) / max(_y1 - _y0, 1e-9)
                _up = _rel < 0.22
                ax.annotate("买 %.2f" % b["level"], xy=(xi, pt["price"]),
                            xytext=(_dx, (22 + 27 * _i_buy) if _up else (-22 - 27 * _i_buy)),
                            textcoords="offset points",
                            ha=_ha, va="bottom" if _up else "top",
                            fontsize=FS_BUY, color="#0b6e0b", fontweight="bold", zorder=11,
                            bbox=dict(boxstyle="round,pad=0.24", fc="white",
                                      ec="#0b6e0b", alpha=0.95, lw=1.1))
                _i_buy += 1
        except Exception:
            pass

        # ── 卖点：五角星（只画过闸门的）──
        sell_txt = []
        try:
            _cl = float(d["close"].iloc[-1])
            _i_sell = 0
            for b in (s.get("sells") or find_sells_for_chart(d)):
                if not b.get("accept"):
                    continue
                sell_txt.append("%s %.2f" % (b.get("tag", "卖点"), b.get("level", 0)))
                cand = [p for p in (geo or {"bi": []})["bi"] if p["type"] == 1]
                if not cand:
                    continue
                pt = min(cand, key=lambda q: abs(q["price"] - b["level"]))
                xi = pt["orig"] - start
                if not (-1 <= xi <= nv):
                    continue
                ax.scatter([xi], [pt["price"]], s=340, marker=STAR, color="#8b0000",
                           edgecolor="white", lw=1.6, zorder=10)
                _ha, _dx = ("right", -3) if xi > nv * 0.87 else \
                    (("left", 3) if xi < nv * 0.10 else ("center", 0))
                ax.annotate("卖 %.2f" % b["level"], xy=(xi, pt["price"]),
                            xytext=(_dx, 21 + 25 * _i_sell), textcoords="offset points",
                            ha=_ha, va="bottom",
                            fontsize=FS_SELL, color="#8b0000", fontweight="bold", zorder=11,
                            bbox=dict(boxstyle="round,pad=0.24", fc="white",
                                      ec="#8b0000", alpha=0.95, lw=1.1))
                _i_sell += 1
        except Exception:
            pass

        # ── 表头两行（绝对定位，量宽后再画，互不重叠）──
        pct = s.get("pct")
        if pct is None:
            pct = (c[-1] / prev_c[-1] - 1) * 100 if np.isfinite(prev_c[-1]) else 0.0
        t1 = "%s(%s)  %+.2f%%" % (s.get("name", ""), s.get("code", ""), pct)
        t1, f1 = _fit_text(fig, t1, FS_TITLE, plot_w)
        fig.text(x0 / fig_w, (plot_top + 0.31) / fig_h, t1, fontsize=f1,
                 fontweight="bold", ha="left", va="bottom", color="#111111")
        t2 = "%s ｜ VWAP %+.2fσ ｜ %s" % (s.get("fx_txt") or "-",
                                         s.get("vw_z", 0) or 0, s.get("ind", ""))
        t2, f2 = _fit_text(fig, t2, FS_SUB, plot_w)
        fig.text(x0 / fig_w, (plot_top + 0.055) / fig_h, t2, fontsize=f2,
                 ha="left", va="bottom", color="#333333")

        # ── 表脚两行：第1行买点（左），第2行卖点+VWAP（左），都不与 x 轴日期挤 ──
        bt = "、".join(buy_txt) if buy_txt else "无"
        st = "、".join(sell_txt) if sell_txt else "无"
        zz = s.get("vw_z")
        if zz is None:
            ztag = "VWAP -"
        elif zz >= 3:
            ztag = "VWAP %+.2fσ 全平区(+3σ)" % zz
        elif zz >= 2:
            ztag = "VWAP %+.2fσ 追高慎(+2σ减半区)" % zz
        elif zz >= 1:
            ztag = "VWAP %+.2fσ +1σ警戒" % zz
        elif zz <= -1:
            ztag = "VWAP %+.2fσ 已破−1σ止损" % zz
        elif zz <= -0.8:
            ztag = "VWAP %+.2fσ 贴近止损线" % zz
        elif zz < 0:
            ztag = "VWAP %+.2fσ 中线下方" % zz
        else:
            ztag = "VWAP %+.2fσ 中线上方" % zz
        l1 = "买点：%s" % bt
        l1, fl1 = _fit_text(fig, l1, FS_NOTE, plot_w)
        fig.text(x0 / fig_w, (plot_bot - 0.40) / fig_h, l1, fontsize=fl1,
                 color="#0b6e0b" if buy_txt else "#888888",
                 fontweight="bold" if buy_txt else "normal", ha="left", va="top")
        l2 = "卖点：%s ｜ %s" % (st, ztag)
        l2, fl2 = _fit_text(fig, l2, FS_NOTE, plot_w)
        fig.text(x0 / fig_w, (plot_bot - 0.68) / fig_h, l2, fontsize=fl2,
                 color="#8b0000" if sell_txt else "#666666",
                 fontweight="bold" if sell_txt else "normal", ha="left", va="top")

        # ── 价格轴上下留白：给「买/卖 xx.xx」标签腾地方，别掉到 x 轴日期上 ──
        try:
            y0_, y1_ = ax.get_ylim()
            rg = max(y1_ - y0_, 1e-9)
            ax.set_ylim(y0_ - rg * 0.17, y1_ + rg * 0.10)
        except Exception:
            pass

        ax.grid(True, ls=":", color="#dddddd", alpha=0.75)
        ax.tick_params(labelsize=FS_TICK, pad=3, length=2.5)
        if nv > 12:
            step = max(1, nv // 8)
            # 跳过第 0 根：最左日期标签会撞 y 轴刻度数字
            tks = list(range(step, nv, step))
            if tks:
                ax.set_xticks(tks)
                ax.set_xticklabels([pd.Timestamp(view["dt"].iloc[i]).strftime("%m-%d")
                                    for i in tks], fontsize=FS_TICK, rotation=0)
        ax.set_xlim(-1.2, nv + 0.8)
        ax.margins(x=0)
        if k == 0:
            ax.legend(loc="upper left", fontsize=FS_NOTE - 1.5, ncol=3,
                      framealpha=0.9, borderpad=0.4)

    # ── 总标题 / 图底说明 ──
    sup = title or ("买点个股结构图（缠论分型版 + VWAP 六轨）· %s"
                    % pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"))
    sup, fsup = _fit_text(fig, sup, 19.0, fig_w - 0.6)
    fig.text(0.5, 1 - 0.26 / fig_h, sup, fontsize=fsup, fontweight="bold",
             ha="center", va="center")
    note = ("绿色月牙=买点 / 红色五角星=卖点（缠论·分型版，均已过闸门） ｜ 黄框=中枢(ZG/ZD) ｜ "
            "黑/红/绿线=VWAP 中线/±1σ/±2σ")
    note, fn = _fit_text(fig, note, FS_NOTE, fig_w - 0.6)
    fig.text(0.5, 0.26 / fig_h, note, fontsize=fn, color="#444444",
             ha="center", va="center")

    _debug_overlaps(fig, "grid")
    fig.savefig(out_path, dpi=(dpi or GRID_DPI), facecolor="white")
    plt.close(fig)
    _finalize_png(out_path)
    return out_path


def find_sells_for_chart(d):
    """图上用的卖点（复用 tech_buypoint 的全中枢扫描，失败就返回空）"""
    try:
        import tech_buypoint as TB
        fx = TS.chan_fx(d, "day")
        cl = float(d["close"].iloc[-1])
        return TB.find_sells(d, {"sell_list": fx.get("sell_list") or []},
                             cl, TS.ma20_state(d)) or []
    except Exception:
        return []
