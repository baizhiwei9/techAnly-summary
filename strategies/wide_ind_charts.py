#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基行业监控 · 中枢突破/跌破 配图（程序生成，0 token）
================================================================================
大佬 2026-10-26 需求（原话）：
  「发图片配图。就比如说 5 大宽基指数是一张图，每个指数一个子图。如果它没有跌破或者
   没有突破，那子图里就不要有它。然后这个日线级别是一个子图，然后周线级别是一个图。
   日线级别是一个图，周线级别是个图。然后每个行业是一个子图。如果它没有突破或者
   没有跌破，那你就不用加到子图里边。我只关心中枢有没有突破，有没有跌破这个」

→ **4 张图**：
  ① `wide_week.png`  五大宽基 · 周线中枢（每个指数一个子图，只放有突破/跌破的）
  ② `wide_day.png`   五大宽基 · 日线中枢
  ③ `ind_week.png`   两个实盘所属行业 · 周线中枢
  ④ `ind_day.png`    两个实盘所属行业 · 日线中枢

每个子图：K线 + 笔 + 中枢黄框 + ZG/ZD 虚线 + 现价，标题写「名称 现价 状态（距 ZG/ZD x%）」
只画中枢位置（⭐跌破 / 🌙突破）；**中枢内的标的不进图**（用户只关心中枢破没破）。

用法（作为库）：
  import wide_ind_charts as WC
  charts = WC.build(res)      # res = wide_ind_report.collect() 的结果
  # → [{"lv":"week","kind":"wide","path":...,"n":3,"names":[...]}, ...]
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import socket
from datetime import datetime

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib import font_manager

for _f in ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
    if os.path.exists(_f):
        try:
            font_manager.fontManager.addfont(_f)
        except Exception:
            pass
plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "Noto Sans CJK JP", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import chan_chart as CH
import chan_smc_index as C
import weekly_zs as WZS
import y1_watch as Y1

OUT_DIR = os.path.join(WS, ".cache/charts")
os.makedirs(OUT_DIR, exist_ok=True)
VER = "缠论·分型版"
LV_TXT = {"day": "日线", "week": "周线"}
POS_TXT = {"above": "【突破中枢】", "below": "【跌破中枢】"}   # 图里不用 emoji（字体缺字形会变方框）
# 月亮：☾（U+263E）wqy-microhei + DejaVu 都有字形；🌙(U+1F319) 会被渲染成方框
Y1_MOON = "☾"

# 每个子图显示多少根K线
N_DEFAULT = {"week": 70, "day": 90}


def _weekly_df(daily):
    """日线 DataFrame（dt/open/high/low/close）→ 周线（W-FRI），只保留已收盘周"""
    d = daily.set_index("dt")
    w = pd.DataFrame({
        "open": d["open"].resample("W-FRI").first(),
        "high": d["high"].resample("W-FRI").max(),
        "low": d["low"].resample("W-FRI").min(),
        "close": d["close"].resample("W-FRI").last()}).dropna()
    w = w[w.index <= daily["dt"].max()].reset_index()
    return w


def _bars_for(kind, code_name, level):
    """取某个标的某个级别的 K 线 DataFrame（dt/open/high/low/close）"""
    if kind == "wide":
        daily = C.fetch_daily(code_name)          # code_name 用 ts_code
        if daily is None:
            return None
        daily = daily.rename(columns={"dt": "dt"})
    else:
        daily = WZS.fetch_sw_daily(code_name)     # code_name 用申万代码
    if daily is None or len(daily) < 80:
        return None
    if level == "week":
        w = _weekly_df(daily)
        return w if len(w) >= 25 else None
    return daily.reset_index(drop=True)


def _draw_one(ax, df, name, lv, close, extra=""):
    """单个标的的画法（K线 + 笔 + 中枢框 + 现价线）"""
    total = len(df)
    n = N_DEFAULT.get(lv, 80)
    geo = CH.chan_geometry(df)
    ck = geo["ck"]
    zs = geo["zs"]
    start = max(0, total - n)
    view = df.iloc[start:].reset_index(drop=True)
    nview = len(view)
    P = float(close if close is not None else view["close"].iloc[-1])

    # ⚠️ 2026-11-06 大佬：「今天是跌的，你怎么还是向上的？」
    # 原来按「收 vs 开」上色 → 低开高走也涂红，和涨跌幅相反。改按「收 vs 上一根收盘」。
    pcs = df["close"].astype(float).shift(1).values
    if "pre_close" in df.columns:
        _pc = df["pre_close"].astype(float).values
        pcs = np.where(np.isnan(_pc), pcs, _pc)

    # K线
    for i in range(nview):
        o, h, l, c = (float(view["open"].iloc[i]), float(view["high"].iloc[i]),
                      float(view["low"].iloc[i]), float(view["close"].iloc[i]))
        pc = pcs[start + i] if (start + i) < len(pcs) else np.nan
        up_ = (c >= pc) if pc == pc else (c >= o)
        col = "#d62728" if up_ else "#2ca02c"
        ax.plot([i, i], [l, h], color=col, lw=0.7, zorder=2)
        ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, max(abs(c - o), 1e-9),
                               facecolor=col, edgecolor=col, lw=0.5, zorder=3))

    # 笔
    sx, sy = [], []
    for p in geo["bi"]:
        x = p["orig"] - start
        if -2 <= x <= nview + 2:
            sx.append(x)
            sy.append(p["price"])
    if len(sx) >= 2:
        ax.plot(sx, sy, color="#1f4e79", lw=1.3, zorder=5)

    # 中枢
    if zs and geo["zs_x"]:
        x0 = max(geo["zs_x"][0] - start, -0.5)
        x1 = geo["zs_x"][1] - start
        if x1 >= -1:
            ax.add_patch(Rectangle((x0, zs[0]), max(x1 - x0, 1), zs[1] - zs[0],
                                   facecolor="#ffd24d", alpha=0.22,
                                   edgecolor="#b8860b", lw=1.1, zorder=4))
            ax.axhline(zs[1], color="#b8860b", lw=0.9, ls="--", alpha=0.85, zorder=4)
            ax.axhline(zs[0], color="#b8860b", lw=0.9, ls="--", alpha=0.85, zorder=4)
            ax.text(nview * 0.005, zs[1], " ZG %.2f" % zs[1], fontsize=8.5,
                    color="#8a6508", va="bottom", ha="left", zorder=10)
            ax.text(nview * 0.005, zs[0], " ZD %.2f" % zs[0], fontsize=8.5,
                    color="#8a6508", va="top", ha="left", zorder=10)
            # 把中枢上/下沿也纳入纵轴范围，保证看得见
            y0, y1 = ax.get_ylim()
            ax.set_ylim(min(y0, zs[0] - (y1 - y0) * 0.05),
                        max(y1, zs[1] + (y1 - y0) * 0.05))

    # 现价线
    ax.axhline(P, color="#333333", lw=1.0, ls=":", alpha=0.75, zorder=6)
    ax.text(nview * 0.995, P, "现价 %.2f " % P, fontsize=8.5, color="#222222",
            va="center", ha="right", zorder=10)

    # ── 一买预备：可能有一买机会（且 MA40 向上）→ 画圈 + 月亮 ──
    # 2026-10-26 大佬：「如果要出现一买的机会，并且 MA40 是向上，你也要提示可能有一买
    # 机会出现。然后图的时候，你在一买的机会那块画一个圈，或者标一个月亮」
    y1_txt = ""
    try:
        pre = Y1.y1_prepare(ck, view["close"].values, P)
        if pre.get("ok") and Y1.mark_on_ax(ax, geo["bi"], pre, start, nview, Y1_MOON):
            y1_txt = "  ◯可能一买(MA40↑)"
    except Exception as _e:
        print("[WARN] y1 mark %s: %s" % (name, str(_e)[:80]), flush=True)

    # 标题：名称 + 涨跌幅 + 状态 + 距离
    zpos = ck.get("zpos")
    tag = POS_TXT.get(zpos, "")
    # 涨跌幅（vs 上一根收盘），直接写标题，避免只看颜色误判
    _pcl = pcs[-1] if len(pcs) else np.nan
    pct_txt = ("%+.2f%%" % ((P / float(_pcl) - 1) * 100)) if (_pcl == _pcl and _pcl) else ""
    dist = ""
    if zs:
        if zpos == "below":
            dist = "低于 ZD %.2f%%" % ((P - zs[0]) / zs[0] * 100)
        elif zpos == "above":
            dist = "高于 ZG %.2f%%" % ((P - zs[1]) / zs[1] * 100)
    ax.set_title("%s  %.2f %s  %s%s%s%s" % (name, P, pct_txt, tag,
                                           "｜" if dist else "", dist, y1_txt),
                 fontsize=11.5, fontweight="bold", loc="left",
                 color="#b30000" if zpos == "below" else "#0b6e0b")
    ax.set_xlim(-1, nview)
    ax.set_xticks([])
    ax.grid(True, ls=":", color="#e8e8e8", lw=0.6)
    ax.tick_params(labelsize=8)
    if extra:
        ax.text(0.995, 0.06, extra, transform=ax.transAxes, fontsize=8.5,
                color="#555555", ha="right", va="bottom")


def _compose(items, lv, kind, title, out_path):
    """items = [(名字, df, close, extra)]，拼图；空则返回 None

    ⚠️ 2026-10-26：子图超过 8 个就改**双列**。原来 21 个子图竖排 = 5400+px 长条，
    手机/飞书里要滚半天根本看不清。
    """
    if not items:
        return None
    n = len(items)
    ncol = 1 if n <= 8 else (2 if n <= 24 else 3)
    nrow = (n + ncol - 1) // ncol
    hrow = 3.0 if ncol == 1 else 2.9
    w = 16 if ncol == 1 else (17 if ncol == 2 else 20)
    fig, axes = plt.subplots(nrow, ncol, figsize=(w, hrow * nrow),
                             gridspec_kw={"hspace": 0.60, "wspace": 0.10},
                             squeeze=False)
    flat = [ax for row in axes for ax in row]
    for ax in flat[n:]:                      # 多余空格子藏掉
        ax.set_axis_off()
    for ax, (name, df, close, extra) in zip(flat, items):
        try:
            _draw_one(ax, df, name, lv, close, extra)
        except Exception as e:
            ax.text(0.5, 0.5, "%s 画图失败：%s" % (name, str(e)[:40]),
                    ha="center", va="center")
            ax.set_axis_off()
    fig.suptitle("%s ｜ 级别：%s ｜ 版本：%s ｜ 只画有中枢突破/跌破 或 可能一买(MA40↑) 的"
                 % (title, LV_TXT.get(lv, lv), VER),
                 fontsize=15, fontweight="bold", y=0.997)
    fig.text(0.5, 0.981,
             "黄框=中枢　虚线=ZG/ZD　蓝线=笔　点线=现价　绿题=突破中枢　红题=跌破中枢　"
             "◯圈+月亮=可能一买（MA40↑）",
             ha="center", fontsize=10, color="#444444")
    fig.subplots_adjust(left=0.030 if ncol > 1 else 0.045, right=0.989,
                        top=0.925 if ncol == 1 else 0.958, bottom=0.008)
    fig.savefig(out_path, dpi=115, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return out_path


def build(res, only_signal=True, tag="", kinds=("wide", "ind")):
    """按报告数据出 4 张图。返回 list[dict(lv, kind, path, n, names)]

    res = wide_ind_report.collect() 的返回（含 wide / ports）
    only_signal=True → 只画 zpos 是 above/below 的（用户明确要求）
    """
    d8 = datetime.now().strftime("%Y%m%d")
    sfx = ("_" + tag) if tag else ""
    outs = []

    # ── ①② 五大宽基：周线 / 日线 各一张（kinds 不含 wide 时跳过）──
    wmap = {x[0]: x[1] for x in C.IDX}   # 名称 → ts_code
    for lv in (("week", "day") if "wide" in kinds else ()):
        items = []
        for w in res.get("wide") or []:
            a = w.get(lv) or {}
            if not a.get("ok"):
                continue
            # 2026-10-26：除了突破/跌破，**可能一买（MA40↑）的也进图**（大佬要圈/月亮）
            if only_signal and a.get("zpos") not in ("above", "below") \
                    and not (a.get("prep") or {}).get("ok"):
                continue
            df = _bars_for("wide", wmap.get(w["name"]), lv)
            if df is None:
                continue
            items.append((w["name"], df, w.get("close"), ""))
        p = os.path.join(OUT_DIR, "wide_%s%s_%s.png" % (lv, sfx, d8))
        got = _compose(items, lv, "wide", "五大宽基指数 · 中枢突破/跌破", p)
        if got:
            outs.append({"lv": lv, "kind": "wide", "path": got,
                         "n": len(items), "names": [x[0] for x in items]})

    # ── ③④ 两个实盘行业：周线 / 日线 各一张 ──
    for lv in (("week", "day") if "ind" in kinds else ()):
        items = []
        for port in res.get("ports") or []:
            for r in port.get("rows") or []:
                a = r.get(lv) or {}
                if not a.get("ok"):
                    continue
                # 2026-10-26：可能一买（MA40↑）的行业也进图（大佬要围圈/月亮）
                if only_signal and a.get("zpos") not in ("above", "below") \
                        and not (a.get("prep") or {}).get("ok"):
                    continue
                df = _bars_for("ind", r.get("code"), lv)
                if df is None:
                    continue
                nm = "%s·%s" % (port["label"], r["name"])
                extra = "仓%.1f%%" % (r.get("weight") or 0)
                items.append((nm, df, r.get("close"), extra))
        p = os.path.join(OUT_DIR, "ind_%s%s_%s.png" % (lv, sfx, d8))
        got = _compose(items, lv, "ind", "实盘所属行业 · 中枢突破/跌破", p)
        if got:
            outs.append({"lv": lv, "kind": "ind", "path": got,
                         "n": len(items), "names": [x[0] for x in items]})
    return outs


if __name__ == "__main__":
    import wide_ind_report as WIR
    res = WIR.collect()
    ch = build(res)
    for c in ch:
        print("[chart] %s · %s → %s（%d 个子图）" % (
            c["kind"], LV_TXT.get(c["lv"], c["lv"]), c["path"], c["n"]))
    if not ch:
        print("该级别无中枢突破/跌破，未出图")
