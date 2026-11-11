#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二批（B2 锚定 + B3 假三买修复）旧版/新版对比图
⚠️ 只读对比脚本：不修改实盘信号，不写状态文件。

用法:
  python -m chanlun_backtest.compare_v2_batch2
输出:
  reports/batch2_compare/<symbol>_<tf>.png
  reports/batch2_compare/summary.csv
  reports/batch2_compare/macd_summary.csv   (第三批 MACD 20/40/5 → 10/20/5 影响)
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib import font_manager

from chanlun_backtest import structure_indicators as si
from chanlun_backtest.structure_signals import detect_structure_signals
from chanlun_backtest.structure_signals_v2 import detect_structure_signals_v2

# ── 中文字体 ──
_FONTS = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
_fam = []
for f in _FONTS:
    if os.path.exists(f):
        try:
            font_manager.fontManager.addfont(f)
            _fam.append(font_manager.FontProperties(fname=f).get_name())
        except Exception:
            pass
matplotlib.rcParams["font.sans-serif"] = _fam + ["DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "reports", "batch2_compare")

COLOR = {
    "B1": "#1f77b4", "B2": "#ff7f0e", "B3": "#d62728",
    "S1": "#9467bd", "S2": "#8c564b", "S3": "#2ca02c",
}
BUY_KINDS = ("B1", "B2", "B3")
SELL_KINDS = ("S1", "S2", "S3")


def resample_weekly(df):
    d = df.copy()
    d["dt"] = pd.to_datetime(d["timestamp"], errors="coerce")
    d = d.dropna(subset=["dt"]).set_index("dt")
    a = d.resample("W-FRI").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open", "close"])
    a = a.reset_index().rename(columns={"dt": "timestamp"})
    a["timestamp"] = a["timestamp"].dt.strftime("%Y-%m-%d")
    return a


def calc_macd_old(close, fast=20, slow=40, signal=5):
    n = len(close)
    ema_f = np.zeros(n); ema_s = np.zeros(n)
    ema_f[0] = close[0]; ema_s[0] = close[0]
    af = 2.0 / (fast + 1); a_s = 2.0 / (slow + 1)
    for t in range(1, n):
        ema_f[t] = af * close[t] + (1 - af) * ema_f[t - 1]
        ema_s[t] = a_s * close[t] + (1 - a_s) * ema_s[t - 1]
    dif = ema_f - ema_s
    dea = np.zeros(n); dea[0] = dif[0]
    a_sig = 2.0 / (signal + 1)
    for t in range(1, n):
        dea[t] = a_sig * dif[t] + (1 - a_sig) * dea[t - 1]
    return dif, dea, 2.0 * (dif - dea)


def candles(ax, wdf, width=0.62):
    o = wdf["open"].values; h = wdf["high"].values
    l = wdf["low"].values; c = wdf["close"].values
    for i in range(len(wdf)):
        up = c[i] >= o[i]
        col = "#d62728" if up else "#2ca02c"   # A股红涨绿跌
        ax.plot([i, i], [l[i], h[i]], color=col, lw=0.55, zorder=2)
        body_low = min(o[i], c[i])
        body_h = max(abs(c[i] - o[i]), (h[i] - l[i]) * 0.012)
        ax.add_patch(Rectangle((i - width / 2, body_low), width, body_h,
                               facecolor=col, edgecolor=col, lw=0.4, zorder=3))


def draw_zhongshu(ax, df, start_idx, wdf_len):
    bi = df.attrs.get("bi_ends") or []
    for zs in df.attrs.get("zhongshus") or []:
        try:
            x0 = bi[zs.start_bi].kline_idx - start_idx
            x1 = bi[zs.end_bi].kline_idx - start_idx
        except Exception:
            continue
        if x1 < 0 or x0 > wdf_len - 1:
            continue
        x0 = max(x0, 0); x1 = min(x1, wdf_len - 1)
        if x1 <= x0:
            x1 = x0 + 1
        ax.add_patch(Rectangle((x0, zs.zd), x1 - x0, max(zs.zg - zs.zd, 1e-9),
                               facecolor="#f1c40f", alpha=0.13,
                               edgecolor="#b7950b", lw=0.6, zorder=1))


def plot_symbol(sym, name, tf, df, window):
    df = si.calculate_structure_indicators(df.copy())
    n = len(df)
    start = max(0, n - window)
    wdf = df.iloc[start:].reset_index(drop=True)
    base = detect_structure_signals(df)
    v2a = detect_structure_signals_v2(df, b1_mode="v1")
    v2b = detect_structure_signals_v2(df, b1_mode="remote")

    old = [{"idx": int(i), "kind": k, "level": float(l)}
           for i, k, _s, l in base]
    new_a = [dict(s) for s in v2a]
    new_b = [dict(s) for s in v2b]

    def in_win(sigs):
        return [s for s in sigs if s["idx"] >= start]

    old_w, a_w, b_w = in_win(old), in_win(new_a), in_win(new_b)

    def counts(sigs):
        c = {k: 0 for k in BUY_KINDS + SELL_KINDS}
        for s in sigs:
            if s["kind"] in c:
                c[s["kind"]] += 1
        return c

    co, ca, cb = counts(old_w), counts(a_w), counts(b_w)

    fig, axes = plt.subplots(3, 1, figsize=(16, 15), sharex=True)
    panels = [
        (axes[0], old_w, "①旧版 v1：B2=任意更高低点；B3=只看最后一个中枢", co),
        (axes[1], a_w, "②v2a：B2 锚定 v1 一买；B3 遍历中枢+递进校验+现价检查", ca),
        (axes[2], b_w, "③v2b：在 v2a 基础上，一买/一卖改用远程段背驰口径", cb),
    ]
    for ax, sigs, title, cc in panels:
        candles(ax, wdf)
        draw_zhongshu(ax, df, start, len(wdf))

        for s in sigs:
            xi = s["idx"] - start
            if xi < 0 or xi >= len(wdf):
                continue
            kind = s["kind"]
            if kind not in COLOR:
                continue
            if kind in BUY_KINDS:
                y = wdf["low"].iloc[xi] * 0.982
                ax.scatter([xi], [y], marker="^", s=95, color=COLOR[kind],
                           edgecolor="white", linewidth=0.6, zorder=6)
                ax.text(xi, y * 0.992, kind, ha="center", va="top",
                        fontsize=7.2, color=COLOR[kind], fontweight="bold", zorder=7)
            else:
                y = wdf["high"].iloc[xi] * 1.018
                ax.scatter([xi], [y], marker="v", s=75, color=COLOR[kind],
                           edgecolor="white", linewidth=0.6, zorder=6, alpha=0.85)
                ax.text(xi, y * 1.008, kind, ha="center", va="bottom",
                        fontsize=7.0, color=COLOR[kind], zorder=7)

        stat = "  ".join("%s=%d" % (k, cc[k]) for k in BUY_KINDS + SELL_KINDS)
        ax.set_title("%s（%s）%s ｜ %s ｜ 窗口内: %s" %
                     (name, sym, tf, title, stat), fontsize=12, loc="left")
        ax.set_ylabel("价格")
        ax.grid(alpha=0.18, lw=0.5)
        ax.set_xlim(-1, len(wdf))

        lo = wdf["low"].min(); hi = wdf["high"].max()
        pad = (hi - lo) * 0.09
        ax.set_ylim(lo - pad, hi + pad)

    # x 轴日期
    step = max(1, len(wdf) // 9)
    ticks = list(range(0, len(wdf), step))
    labels = [str(wdf["timestamp"].iloc[i])[:10] for i in ticks]
    axes[2].set_xticks(ticks)
    axes[2].set_xticklabels(labels, rotation=25, ha="right", fontsize=8)

    fig.suptitle("第二批对比｜%s %s｜最近 %d 根K线｜黄框=中枢" %
                 (name, tf, len(wdf)), fontsize=15, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    fn = os.path.join(OUT_DIR, "%s_%s.png" % (sym.replace(".", "_"), tf))
    fig.savefig(fn, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return co, ca, cb, len(old_w), len(a_w), len(b_w), fn


def macd_effect(df, tf):
    """同一 v1 逻辑下：旧 MACD 20/40/5 vs 新 MACD 10/20/5 的信号数量"""
    df_old = df.copy()
    orig = si.calc_macd
    try:
        si.calc_macd = calc_macd_old
        df_old = si.calculate_structure_indicators(df_old)
    finally:
        si.calc_macd = orig
    old_sigs = detect_structure_signals(df_old)
    df_new = si.calculate_structure_indicators(df.copy())
    new_sigs = detect_structure_signals(df_new)
    return len(old_sigs), len(new_sigs), old_sigs, new_sigs


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    from data_fetcher import get_index_daily

    symbols = [
        ("sh510300", "沪深300ETF"),
        ("sh510500", "中证500ETF"),
        ("sz159915", "创业板ETF"),
        ("sh588000", "科创50ETF"),
    ]

    rows = []
    macd_rows = []
    for sym, name in symbols:
        bars = get_index_daily(sym)
        if len(bars) < 200:
            print("跳过 %s：日线数据不足 (%d)" % (sym, len(bars)))
            continue
        df = pd.DataFrame(bars)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        # 用 timestamp 列名统一
        if "datetime" in df.columns:
            df = df.rename(columns={"datetime": "timestamp"})

        for tf, wdf, window in [("daily", df, 320),
                                ("weekly", resample_weekly(df), 160)]:
            if len(wdf) < 120:
                continue
            co, ca, cb, n_old, n_a, n_b, fn = plot_symbol(sym, name, tf, wdf, window)
            rows.append({
                "symbol": sym, "name": name, "tf": tf,
                "old_B1": co["B1"], "old_B2": co["B2"], "old_B3": co["B3"],
                "old_S1": co["S1"], "old_S2": co["S2"], "old_S3": co["S3"],
                "v2a_B1": ca["B1"], "v2a_B2": ca["B2"], "v2a_B3": ca["B3"],
                "v2a_S1": ca["S1"], "v2a_S2": ca["S2"], "v2a_S3": ca["S3"],
                "v2b_B1": cb["B1"], "v2b_B2": cb["B2"], "v2b_B3": cb["B3"],
                "v2b_S1": cb["S1"], "v2b_S2": cb["S2"], "v2b_S3": cb["S3"],
                "old_total": n_old, "v2a_total": n_a, "v2b_total": n_b,
                "chart": os.path.relpath(fn, os.path.dirname(OUT_DIR)),
            })
            print("[chart] %s %s  v1=%s  v2a=%s  v2b=%s  -> %s"
                  % (sym, tf, co, ca, cb, fn))

        # MACD 参数影响（全历史，v1 口径）
        mo, mn, _os, _ns = macd_effect(df, "daily")
        macd_rows.append({"symbol": sym, "name": name,
                          "old_macd_20405": mo, "new_macd_10205": mn})
        print("[macd] %s 20/40/5=%d  10/20/5=%d" % (sym, mo, mn))

    if rows:
        pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "summary.csv"),
                                  index=False, encoding="utf-8-sig")
    if macd_rows:
        pd.DataFrame(macd_rows).to_csv(os.path.join(OUT_DIR, "macd_summary.csv"),
                                       index=False, encoding="utf-8-sig")
    print("输出目录:", OUT_DIR)


if __name__ == "__main__":
    main()
