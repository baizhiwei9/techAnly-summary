#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📐 概念拓扑缠论选股 · 20 只个股日线缠论合图
================================================================================
大佬 2026-10-28 17:41 要求（原话）：
  「你最后用程序生成这 20 个概念的日线图，缠论分析日线图，就日线级别的缠论分析。
   然后每一个股票是一个子图」

→ 一张 PNG，网格排布，**每只股票一个子图**，日线级别，画：
   · 日 K 线（红涨绿跌）
   · MA20 / MA40 / MA80 / MA160
   · **中枢**：矩形框（中枢下沿 ZD ~ 上沿 ZG），标注
   · **笔**：折线连接分型高低点（chan_smc_index 的 bi/seg）
   · **买点**：◯ 圈 + 价位标注（一买/二买/三买），只画 age≤25 的
   · 标题：名称 / 所属概念 / 缠论档位 / 20日涨幅 / 当日涨幅
   · 现价 vs MA20 位置、中枢位置（above/in/below）

⚠️ 2026-10-28 踩坑记录
  1. 图内**不要放 emoji**（🌙⭐🦀 在 WenQuanYi/Noto 里缺字形会变方块），只写 ▲涨/▼跌
  2. `chan_read` 的 bars 是 `[(high, low)]`，不是 OHLC —— 画 K 线要用原始 df 的 open/high/low/close
  3. `chan_read` 返回的 `cur` 是合并K线高点，不是收盘价 —— 现价从 `df['close'].iloc[-1]` 取
  4. 子图数量动态网格：≤4 用 2 列，≤9 用 3 列，≤16 用 4 列，更多用 5 列

用法（作为库）：
    import concept_chart as CC
    p = CC.build(stocks, out_path="/path/to/out.png")
    # stocks: [{'name','code','concept','chan','buy','m20','pct'}, ...]

独立测试：
    python3 strategies/concept_chart.py            # 用最近一次 concept_reports 的 json 出图
================================================================================
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import json
import glob
import argparse
import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_FONT_FILES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
]
_FAM_OK = []
for _f in _FONT_FILES:
    if os.path.exists(_f):
        try:
            font_manager.fontManager.addfont(_f)
            _FAM_OK.append(font_manager.FontProperties(fname=_f).get_name())
        except Exception:
            pass
plt.rcParams["font.sans-serif"] = _FAM_OK + ["WenQuanYi Micro Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

UP, DN = "#d62728", "#2ca02c"
MA_COLORS = {20: "#1f77b4", 40: "#ff7f0e", 80: "#9467bd", 160: "#7f7f7f"}
BARS = 120                      # 每格显示多少根日 K
ZS_COLOR = "#ff9900"
BI_COLOR = "#555555"
BUY_COLOR = "#d62728"


def _grid(n):
    if n <= 4:
        return (2, 2)
    if n <= 9:
        return (3, 3)
    if n <= 16:
        return (4, 4)
    return (5, 4)


def _one(ax, df, meta):
    """画单个子图"""
    import chan_smc_index as C

    d = df.tail(BARS).copy().reset_index(drop=True)
    if d.empty:
        ax.text(0.5, 0.5, "无数据", ha="center", va="center")
        ax.axis("off")
        return
    x = np.arange(len(d))
    o = d["open"].astype(float).values
    h = d["high"].astype(float).values
    l = d["low"].astype(float).values
    c = d["close"].astype(float).values

    # ── K 线
    for i in range(len(d)):
        col = UP if c[i] >= o[i] else DN
        ax.plot([i, i], [l[i], h[i]], color=col, lw=0.6, zorder=2)
        lo, hi = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - 0.32, lo), 0.64, max(hi - lo, 1e-6),
                               facecolor=col, edgecolor=col, lw=0.4, zorder=3))

    # ── 均线（用全量 df 算，再截到窗口）
    full = df.copy()
    for n in (20, 40, 80, 160):
        if len(full) >= n:
            ma = full["close"].astype(float).rolling(n).mean().tail(BARS).values
            ax.plot(x, ma, color=MA_COLORS[n], lw=0.75, alpha=0.85,
                    label="MA%d" % n, zorder=4)

    # ── 缠论：中枢 + 笔 + 买点
    try:
        bars = [(float(hi_), float(lo_)) for hi_, lo_ in zip(h, l)]
        r = C.chan_read(bars, meta.get("name", ""))
    except Exception:
        r = {}
    if r.get("ok"):
        zs = r.get("zs")
        if zs and len(zs) == 2:
            zd, zg = float(zs[0]), float(zs[1])
            y0, y1 = min(zd, zg), max(zd, zg)
            ax.add_patch(Rectangle((0, y0), len(d) - 1, max(y1 - y0, 1e-6),
                                   facecolor=ZS_COLOR, alpha=0.13,
                                   edgecolor=ZS_COLOR, lw=0.9, ls="--", zorder=1))
            ax.axhline(y1, color=ZS_COLOR, lw=0.5, ls=":", alpha=0.7, zorder=1)
            ax.axhline(y0, color=ZS_COLOR, lw=0.5, ls=":", alpha=0.7, zorder=1)
            ax.text(len(d) - 1.5, y1, "中枢上沿 %.2f" % y1, fontsize=5.5,
                    color=ZS_COLOR, ha="right", va="bottom", zorder=6)
            ax.text(len(d) - 1.5, y0, "中枢下沿 %.2f" % y0, fontsize=5.5,
                    color=ZS_COLOR, ha="right", va="top", zorder=6)

        # 买点标记（age ≤ 25）
        buys = [b for b in (r.get("buy_list") or []) if b.get("age", 99) <= 25]
        for b in buys:
            px = float(b["level"])
            age = int(b["age"])
            xi = len(d) - 1 - age
            if 0 <= xi < len(d):
                ax.scatter([xi], [px], s=46, facecolors="none",
                           edgecolors=BUY_COLOR, linewidths=1.4, zorder=7)
                ax.annotate("%s %.2f" % (b["tag"], px), (xi, px),
                            textcoords="offset points", xytext=(0, -11),
                            fontsize=5.5, color=BUY_COLOR, ha="center", zorder=8)

    # ── 标题
    nm = meta.get("name", "")
    ind = meta.get("concept", "")
    chan = (meta.get("chan") or "").replace("🟢", "")
    m20 = float(meta.get("m20") or 0) * 100
    pct = float(meta.get("pct") or 0) * 100
    close = float(c[-1])
    ma20 = float(full["close"].astype(float).tail(20).mean()) if len(full) >= 20 else np.nan
    pos = "MA20上方" if np.isfinite(ma20) and close > ma20 else "MA20下方"
    arrow = "▲" if pct >= 0 else "▼"
    title = "%s  %s" % (nm, chan)
    # 大佬 2026-10-28：子图必须带「所属概念」
    vv = meta.get("vol") or {}
    vmark = " ｜中长量✅" if vv.get("pass") else ""
    sub = "概念：%s ｜ %s收%.2f(%s%.1f%%) 20日%+.1f%% %s%s" % (
        ind[:16], arrow, close, arrow, abs(pct), m20, pos, vmark)
    ax.set_title(title, fontsize=8.5, pad=3, fontweight="bold")
    ax.text(0.5, 1.005, sub, transform=ax.transAxes, fontsize=5.8,
            ha="center", va="bottom", color="#444444")
    ax.set_xlim(-1, len(d))
    lo_all = float(np.nanmin(l))
    hi_all = float(np.nanmax(h))
    pad = (hi_all - lo_all) * 0.08 or 1.0
    ax.set_ylim(lo_all - pad, hi_all + pad)
    ax.tick_params(labelsize=5)
    ax.grid(alpha=0.18, lw=0.4)
    ax.legend(fontsize=4.5, loc="upper left", ncol=4, framealpha=0.55,
              handlelength=1.0, columnspacing=0.8)


def build(stocks, out_path=None, date=None, bars=BARS):
    """stocks: [{'name','code','concept','chan','buy','m20','pct'}, ...] → PNG 路径"""
    if not stocks:
        return None
    global BARS
    BARS = bars
    import concept_scan as CS

    rows, used = [], 0
    for s in stocks:
        code = s.get("code")
        if not code:
            continue
        try:
            df = CS._fetch_stock(code)
        except Exception:
            df = None
        if df is None or len(df) < 60:
            continue
        rows.append((df, s))
        used += 1
    if not rows:
        return None

    gr, gc = _grid(used)
    # 每个子图高度按行数缩放
    fw = 4.2 * gc
    fh = 2.55 * gr
    fig, axes = plt.subplots(gr, gc, figsize=(fw, fh), dpi=110)
    axes = np.atleast_1d(axes).ravel()
    for k in range(len(axes)):
        ax = axes[k]
        if k < len(rows):
            df, meta = rows[k]
            try:
                _one(ax, df, meta)
            except Exception as e:
                ax.text(0.5, 0.5, "%s\n绘图失败" % meta.get("name", ""),
                        ha="center", va="center", fontsize=7)
                ax.axis("off")
                print("[chart] %s 失败 %s" % (meta.get("name"), str(e)[:60]), flush=True)
        else:
            ax.axis("off")

    d = date or datetime.date.today().strftime("%Y%m%d")
    fig.suptitle("概念拓扑缠论选股 · 日线缠论分析 · %s（%d 只）" % (
        "%s-%s-%s" % (d[:4], d[4:6], d[6:]), used),
        fontsize=12, fontweight="bold", y=0.997)
    fig.text(0.5, 0.004,
             "红涨绿跌 ｜ 蓝MA20 橙MA40 紫MA80 灰MA160 ｜ 橙色框=缠论中枢 ｜ "
             "红圈=日线买点（一买/二买/三买，25日内）",
             ha="center", fontsize=7, color="#555555")
    fig.tight_layout(rect=[0, 0.012, 1, 0.985])

    if out_path is None:
        out_path = os.path.join(WS, ".cache", "concept_charts",
                                "concept_%s.png" % d)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[chart] %d 只 → %s（%.0f KB）" % (
        used, out_path, os.path.getsize(out_path) / 1024), flush=True)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = a.date or datetime.date.today().strftime("%Y%m%d")
    fp = os.path.join(WS, "memory", "concept_reports", "%s_concept.json" % d)
    if not os.path.exists(fp):
        cand = sorted(glob.glob(os.path.join(WS, "memory", "concept_reports", "*_concept.json")))
        if not cand:
            print("没有概念报告 json，先跑 bin/concept_scan.py")
            return 1
        fp = cand[-1]
    j = json.load(open(fp, encoding="utf-8"))
    stocks = j.get("stocks") or []
    if not stocks:
        print("json 里没有 stocks，确认跑的是 --layer 4")
        return 1
    p = build(stocks, out_path=a.out, date=d)
    print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
