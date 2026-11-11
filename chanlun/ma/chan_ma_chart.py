#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论·均线版 单图（趋势浪子原文口径）—— 只画均线版一层，图大、结构清楚
=====================================================================
大佬 2026-11-11：「你出一版缠论的均线版画图，我看看对不对」

口径（与 bin/chan_ma_trend_v2.py 完全一致，不另写一套）：
  笔     = MA5 / MA10 双穿 MA20        （A0）
  线段   = MA20 / MA40 双穿 MA80        （A1，本级别）
  大级别 = 价格 vs MA160
  中枢   = 线段连续三段重叠区（ZD~ZG）
  走势类型 = 相邻两摆「高低点同时抬高/降低」→ 上涨/下跌，其余盘整

图面元素：
  · K 线（涨跌按 close vs 前一根 close 上色 —— MEMORY 硬规矩）
  · MA10 / MA20 / MA160 三条均线
  · 笔（细橙线 + 圆点）、线段（粗深蓝线 + 方点）
  · 中枢（黄色框 + ZG/ZD 虚线）
  · 走势类型底色带（红=上涨 / 绿=下跌 / 灰=盘整）
  · 左上/右上/中上角标：可交易性 / 当前走势+笔线段数 / 当前位置+关键位

用法：
  python3 bin/chan_ma_chart.py --target 中证500
  python3 bin/chan_ma_chart.py --target 600519 --n 300
  python3 bin/chan_ma_chart.py --target 中证500 --push      # 出完推 QQ+飞书
"""
from __future__ import annotations

import argparse
import os
import sys

# ── 全局硬规矩：禁 IPv6（容器 IPv6 不通会静默卡死）──
import socket

_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

def _repo_root(start):
    """向上探测仓库根：同时含 bin/ 与 core/ 的那一层（2026-11-12 目录整理后可任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "core")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
# 顺序有讲究：最后插入的优先级最高 → bin/ 放最前 = 最低优先级，
# 保证 import 命中真实目录，而不是 bin/ 里的兼容 shim。
for _sd in ("bin", "", "core", "chanlun/fractal", "chanlun/ma", "chanlun/multiscale",
            "chanlun/multiscale/vendor", "report", "misc", "wavelet", "smc"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

_FONT_FILES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
_FAM_OK = []
for _f in _FONT_FILES:
    if os.path.exists(_f):
        try:
            font_manager.fontManager.addfont(_f)
            _FAM_OK.append(font_manager.FontProperties(fname=_f).get_name())
        except Exception:
            pass
matplotlib.rcParams["font.sans-serif"] = _FAM_OK + ["WenQuanYi Micro Hei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

import tech_summary as T          # noqa: E402  复用 ma_structure / 数据装载 / 口径
import chan_ma_trend_v2 as M      # noqa: E402

OUT_DIR = os.path.join(WS, ".cache/charts")
RED, GRN = "#d62728", "#2ca02c"

MODE_FILL = {"trend_up": "#ffe0e0", "trend_dn": "#e2f4e6", "range": "#f0f0f0"}
MODE_TEXT = {"trend_up": ("#c62828", "上涨趋势"),
             "trend_dn": ("#1b7e32", "下跌趋势"),
             "range": ("#5f5f5f", "盘整")}


def build(target="中证500", level="day", n=400):
    """装载数据并算均线版结构"""
    tg = T.resolve(target)
    df = T.load_day(tg)
    if df is None or len(df) < 170:
        raise SystemExit("[ERR] %s 数据不足（%s 根）" % (target, 0 if df is None else len(df)))
    dd = M.add_ind(df)
    st = T.ma_structure(df)
    if st is None:
        raise SystemExit("[ERR] 均线版结构算不出（样本不足 170 根）")
    return tg, df, dd, st


def draw(tg, df, dd, st, out, n_show=400):
    total = len(df)
    start = max(0, total - n_show)
    view = df.iloc[start:].reset_index(drop=True)
    nv = len(view)
    x = np.arange(nv)
    o = view["open"].values.astype(float)
    h = view["high"].values.astype(float)
    l = view["low"].values.astype(float)
    c = view["close"].values.astype(float)

    # 涨跌按 close vs 前一根 close（MEMORY 硬规矩）
    prev_c = np.concatenate([[np.nan], c[:-1]])
    if start > 0:
        prev_c[0] = float(df["close"].iloc[start - 1])
    cols = [RED if (np.isfinite(prev_c[i]) and c[i] >= prev_c[i]) else GRN for i in range(nv)]
    last_pct = (c[-1] - prev_c[-1]) / prev_c[-1] * 100

    fig = plt.figure(figsize=(18, 10.5), facecolor="white")
    gs = fig.add_gridspec(2, 1, height_ratios=[5.6, 1.0], hspace=0.06)
    ax = fig.add_subplot(gs[0])
    axV = fig.add_subplot(gs[1], sharex=ax)

    # ── ① 走势类型底色带（最底层）──
    labeled = set()
    for i0, i1, kind in st["mode"]:
        x0, x1 = i0 - start, i1 - start
        if x1 < -1 or x0 > nv:
            continue
        x0, x1 = max(x0, -1), min(x1, nv)
        ax.axvspan(x0, x1, facecolor=MODE_FILL[kind], alpha=0.85, zorder=0, lw=0)
        if (x1 - x0) > nv * 0.07 and len(labeled) < 8:
            labeled.add(kind)
            col, lb = MODE_TEXT[kind]
            ax.text((x0 + x1) / 2, 0.995, lb, transform=ax.get_xaxis_transform(),
                    ha="center", va="top", fontsize=9, color=col,
                    fontweight="bold", alpha=0.75, zorder=2)

    # ── ② K 线 ──
    bw = 0.62
    rng = max(h.max() - l.min(), 1e-9)
    for i in range(nv):
        ax.vlines(i, l[i], h[i], color=cols[i], lw=0.9, zorder=3)
        y0, y1 = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - bw / 2, y0), bw, max(y1 - y0, rng * 0.0008),
                               facecolor=cols[i], edgecolor=cols[i], lw=0.5, zorder=4))
    axV.bar(x, view["volume"].values.astype(float), width=bw, color=cols, alpha=0.72)

    # ── ③ 均线：MA10 / MA20 / MA160 ──
    for nn, col, lw, ls, lab in ((10, "#ff9f43", 1.3, "-", "MA10"),
                                 (20, "#2e86de", 1.6, "-", "MA20"),
                                 (160, "#8e44ad", 1.8, "--", "MA160(大级别)")):
        key = "ma%d" % nn
        if key not in dd.columns:
            continue
        y = dd[key].values[start:]
        if np.all(~np.isfinite(y)):
            continue
        ax.plot(np.arange(len(y)), y, color=col, lw=lw, ls=ls, alpha=0.85,
                label=lab, zorder=5)
        yv = y[-1] if len(y) else np.nan
        if np.isfinite(yv):
            ax.annotate("%s %.2f" % (lab.split("(")[0], yv), xy=(nv - 0.5, yv),
                        xytext=(7, 0), textcoords="offset points", va="center", ha="left",
                        fontsize=8, color=col, fontweight="bold",
                        annotation_clip=False, zorder=14)

    # ── ④ 中枢：线段连续三段重叠区 ──
    for zs in st.get("zs") or []:
        x0, x1 = zs["i0"] - start, zs["i1"] - start
        if x1 < -1 or x0 > nv:
            continue
        x0, x1 = max(x0, -0.5), min(x1, nv)
        if x1 <= x0:
            continue
        ax.add_patch(Rectangle((x0, zs["zd"]), x1 - x0, zs["zg"] - zs["zd"],
                               facecolor="#ffd24d", alpha=0.28,
                               edgecolor="#c07800", lw=2.0, zorder=6))
        ax.hlines([zs["zg"], zs["zd"]], x0, x1, colors="#c07800",
                  lw=1.3, ls="--", alpha=0.9, zorder=7)
        if (x1 - x0) > nv * 0.05:
            ax.text((x0 + x1) / 2, zs["zg"], "中枢 %.0f~%.0f" % (zs["zd"], zs["zg"]),
                    fontsize=8, color="#8b6508", ha="center", va="bottom",
                    fontweight="bold", zorder=12,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              ec="#b8860b", lw=0.6, alpha=0.9))

    # ── ⑤ 线段（粗深蓝，主结构）──
    sx, sy = [], []
    for p in st["seg"]:
        xi = p["i"] - start
        if -2 <= xi <= nv + 2:
            sx.append(xi)
            sy.append(p["px"])
    if len(sx) >= 2:
        ax.plot(sx, sy, color="#0d2b45", lw=4.0, alpha=1.0, zorder=9,
                label="线段 = MA20/40 双穿 MA80")
        lp = st["seg"][-1]
        if lp.get("live") and sx[-1] < nv - 1:
            ax.plot([sx[-1], nv - 1], [sy[-1], float(c[-1])], color="#0d2b45",
                    lw=2.2, ls="--", alpha=0.8, zorder=9)
            ax.annotate("未完成线段", xy=(nv - 1, float(c[-1])), xytext=(-8, 8),
                        textcoords="offset points", fontsize=8, color="#0d2b45",
                        ha="right", fontweight="bold", zorder=13,
                        bbox=dict(boxstyle="round,pad=0.16", fc="white",
                                  ec="#0d2b45", lw=0.6, alpha=0.9))
        for i in range(len(sx)):
            ax.scatter([sx[i]], [sy[i]], s=90, marker="s", color="#0d2b45",
                       edgecolor="white", lw=1.6, zorder=10)
            ax.annotate("%.1f" % sy[i], (sx[i], sy[i]), xytext=(0, -14 if st["seg"][
                min(i, len(st["seg"]) - 1)]["dir"] == "up" else 14),
                textcoords="offset points", ha="center",
                fontsize=7.6, color="#0d2b45", fontweight="bold", zorder=13)

    # ── ⑥ 笔（细橙，次级结构）──
    bx, by = [], []
    for p in st["bi"]:
        xi = p["i"] - start
        if -2 <= xi <= nv + 2:
            bx.append(xi)
            by.append(p["px"])
    if len(bx) >= 2:
        ax.plot(bx, by, color="#e8955a", lw=1.6, alpha=0.95, zorder=7,
                label="笔 = MA5/10 双穿 MA20")
        bp = st["bi"][-1]
        if bp.get("live") and bx[-1] < nv - 1:
            ax.plot([bx[-1], nv - 1], [by[-1], float(c[-1])], color="#e8955a",
                    lw=1.2, ls="--", alpha=0.75, zorder=7)
        for i in range(len(bx)):
            ax.scatter([bx[i]], [by[i]], s=26, marker="o", color="#e8955a",
                       edgecolor="white", lw=0.7, zorder=8)

    # ── ⑦ 角标：可交易性 / 当前走势 / 当前位置 ──
    tr = M.verdict(dd)
    tcol = {"✅": "#1b7e32", "⛔": "#c62828", "🟡": "#b8860b", "⚪": "#666666"}
    c1 = next((v for k, v in tcol.items() if tr.startswith(k)), "#333333")
    tr_plain = tr
    for e in tcol:
        if tr_plain.startswith(e):
            tr_plain = tr_plain[len(e):].strip()
            break
    lv = M.level_locate(dd)
    big = lv.get("big") or {}
    ax.text(0.006, 0.982, tr_plain, transform=ax.transAxes, fontsize=12, color=c1,
            fontweight="bold", va="top", ha="left", zorder=15,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=c1, lw=1.4, alpha=0.95))
    ax.text(0.006, 0.918, "大级别(MA160)：%s" % (big.get("align") or "—"),
            transform=ax.transAxes, fontsize=9.5, color="#8e44ad",
            fontweight="bold", va="top", ha="left", zorder=15,
            bbox=dict(boxstyle="round,pad=0.24", fc="white", ec="#8e44ad",
                      lw=0.9, alpha=0.92))

    if st["mode"]:
        cur = st["mode"][-1][2]
        cc, cl = MODE_TEXT[cur]
        ax.text(0.994, 0.982,
                "当前走势：%s ｜ 笔 %d ｜ 线段 %d 段 ｜ 中枢 %d"
                % (cl, len(st["bi"]), max(len(st["seg"]) // 2, 1), len(st.get("zs") or [])),
                transform=ax.transAxes, fontsize=10.5, color=cc, fontweight="bold",
                va="top", ha="right", zorder=15,
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=cc, lw=1.4, alpha=0.95))

    wk = M.bi_walk(dd)
    if wk.get("ok"):
        wc = {"up": "#c62828", "dn": "#2ca02c", "mid": "#b8860b"}.get(wk["state"], "#333")
        lv_txt = " ｜ ".join("%s %.2f" % (k, v) for k, v in (wk.get("levels") or [])[:3])
        ax.text(0.5, 0.982, wk["where"] + (("   " + lv_txt) if lv_txt else ""),
                transform=ax.transAxes, fontsize=9.8, color=wc, fontweight="bold",
                va="top", ha="center", zorder=15,
                bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=wc, lw=1.2, alpha=0.95))

    # 排列态
    try:
        al = M.ma_alignment(dd)
        atxt = {"多头排列": ("多头排列 MA5>10>20>40>80>160", RED),
                "空头排列": ("空头排列 MA5<10<20<40<80<160", GRN)}.get(
            al.get("state"), (al.get("state", ""), "#666666"))
        if atxt[0]:
            ax.text(0.006, 0.028,
                    atxt[0] + ("（已 %d 根）" % al["bars"] if al.get("bars") else ""),
                    transform=ax.transAxes, fontsize=10, color=atxt[1], fontweight="bold",
                    va="bottom", ha="left", zorder=15,
                    bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=atxt[1],
                              lw=1.2, alpha=0.95))
    except Exception:
        pass

    name = tg.get("disp") or tg.get("name") if isinstance(tg, dict) else str(tg)
    ax.set_title("缠论 · 均线版（趋势浪子原文口径）｜%s ｜近 %d 根 ｜ 笔=MA5/10 双穿 MA20 ｜ "
                 "线段=MA20/40 双穿 MA80 ｜ 底色=走势类型 ｜ 黄框=中枢"
                 % (name, nv), fontsize=13, pad=10)
    ax.set_ylabel("价格", fontsize=10)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.55)
    ax.legend(loc="lower right", fontsize=9, framealpha=0.93, ncol=2)
    ax.set_xlim(-1.5, nv + 12)
    axV.set_ylabel("成交量", fontsize=9)
    axV.grid(True, ls=":", color="#cccccc", alpha=0.5)
    for a in (ax, axV):
        a.tick_params(labelsize=8.5)

    # x 轴日期刻度（load_day 出来的列名是 dt）
    step = max(nv // 10, 1)
    idxs = list(range(0, nv, step))
    dcol = "dt" if "dt" in view.columns else ("trade_date" if "trade_date" in view.columns else None)
    if dcol:
        dates = pd.to_datetime(view[dcol]).dt.strftime("%Y-%m-%d").values
        axV.set_xticks(idxs)
        axV.set_xticklabels([dates[i] for i in idxs], rotation=0, fontsize=8.5)
        plt.setp(ax.get_xticklabels(), visible=False)
    else:
        axV.set_xlabel("K 线序号")

    fig.savefig(out, dpi=135, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out, last_pct


def main():
    ap = argparse.ArgumentParser(description="缠论均线版单图")
    ap.add_argument("--target", "--index", dest="target", default="中证500")
    ap.add_argument("--level", default="day")
    ap.add_argument("--n", type=int, default=400, help="图上显示最近多少根（判定用全量）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--push", action="store_true", help="推 QQ + 飞书")
    a = ap.parse_args()

    tg, df, dd, st = build(a.target, a.level, a.n)
    os.makedirs(OUT_DIR, exist_ok=True)
    d8 = pd.Timestamp.now().strftime("%Y%m%d")
    name = tg.get("disp") or tg.get("name") if isinstance(tg, dict) else str(tg)
    out = a.out or os.path.join(OUT_DIR, "ma_%s_%s_%s.png" % (name, a.level, d8))
    out, pct = draw(tg, df, dd, st, out, n_show=a.n)
    print("[图] %s" % out)
    print("[%s] 现价 %.2f ｜ 涨跌 %+.2f%% ｜ 笔 %d ｜ 线段 %d ｜ 走势类型 %d 段 ｜ 中枢 %d"
          % (name, float(df["close"].iloc[-1]), pct, len(st["bi"]), len(st["seg"]),
             len(st["mode"]), len(st.get("zs") or [])))

    if a.push:
        sys.path.insert(0, os.path.join(WS, "bin"))
        import push_image
        txt = "📐 缠论·均线版｜%s｜%s\n现价 %.2f（%+.2f%%）\n笔 %d ｜ 线段 %d ｜ 中枢 %d" % (
            name, d8, float(df["close"].iloc[-1]), pct, len(st["bi"]),
            len(st["seg"]), len(st.get("zs") or []))
        ok = push_image.push([out], text=txt)
        print("[push] %s" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
