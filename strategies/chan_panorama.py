#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五指数缠论全景图（一张图搞定）—— 程序生成，0 token
=====================================================================
一图 5 行（沪深300 / 中证500 / 中证1000 / 创业板指 / 科创50），每行一个小 K 线图：
  K线 + 笔 + 中枢黄框 + 买卖点标记 + 左侧标注当前阶段/位置

级别：小时线60m（默认）或 日线（--level day）
版本：缠论·分型版（包含处理→顶底分型→笔→中枢ZG/ZD→背驰）

用法：
  python3 strategies/chan_panorama.py                 # 小时线，输出并打印路径
  python3 strategies/chan_panorama.py --level day     # 日线
  python3 strategies/chan_panorama.py --push          # 生成后推 QQ 私聊 + 飞书私聊
  python3 strategies/chan_panorama.py --n 80 --push
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, socket, argparse
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

# 中文字体：直接注册 .ttc 文件（按 family 名 findfont 会失败）
for _f in ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
    if os.path.exists(_f):
        try:
            font_manager.fontManager.addfont(_f)
        except Exception:
            pass
matplotlib.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "Noto Sans CJK JP", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

import chan_chart as CH
import chan_smc_index as C
import y1_watch as Y1

OUT_DIR = os.path.join(WS, ".cache/charts")
LV_TXT = {"h1": "小时线60m", "day": "日线"}
VER = "缠论·分型版"
# 月亮符号：wqy-microhei 没字形 🌙(U+1F319)，用 ☾(U+263E)（DejaVu 有）
Y1_MOON = "☾"


def get_df(name, level):
    if level == "h1":
        import minute_hub as mh
        df = mh.get_kline(C.IDX_SH[name], "60", n=400, kind="index",
                          cache=True, cache_ttl_min=10)
        if df is not None:
            df = df.rename(columns={"day": "Date"})
    else:
        df = C.fetch_daily(dict((x[0], x[1]) for x in C.IDX)[name])
        if df is not None:
            df = df.rename(columns={"dt": "Date"})
    if df is None:
        return None
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def draw_panorama(level="h1", n=90, out=None, names=None, title_extra=""):
    """画多指数纵向拼图（默认全部 5 个；names 可指定子集）

    2026-10-24 用户要求：「小时级别有买点有卖点的，你都合成一张图片，发一次图片就行了」
    → names 只传「该级别有买卖点」的指数，一张图搞定这个级别。
    """
    all_names = [x[0] for x in C.IDX]
    names = names or all_names
    names = [n for n in names if n in all_names]
    if not names:
        return None
    fig, axes = plt.subplots(len(names), 1, figsize=(17, 3.0 * len(names)),
                             gridspec_kw={"hspace": 0.55})
    if len(names) == 1:
        axes = [axes]

    header_done = False
    for ax, name in zip(axes, names):
        df = get_df(name, level)
        if df is None or len(df) < 60:
            ax.text(0.5, 0.5, "%s 数据不足" % name, ha="center", va="center")
            ax.set_axis_off()
            continue

        total = len(df)
        geo = CH.chan_geometry(df)
        ck = geo["ck"]
        zs = geo["zs"]
        start = max(0, total - n)
        view = df.iloc[start:].reset_index(drop=True)
        nview = len(view)
        P = float(view["close"].iloc[-1])

        # ⚠️ 2026-11-06 大佬：「今天是跌的，你怎么还是向上的？」
        # 原来按「收盘 vs 开盘」上色 → 低开收高也是红，看起来在涨，和涨跌幅相反。
        # 改成按「收盘 vs 上一根收盘」（= 涨跌幅口径），先算好 prevclose 序列。
        pcs = df["close"].astype(float).shift(1).values
        if "pre_close" in df.columns:      # 日线 tushare 自带昨收，优先用
            _pc = df["pre_close"].astype(float).values
            pcs = np.where(np.isnan(_pc), pcs, _pc)

        # ── K线（自己画，比 mplfinance 更适合多子图）──
        for i in range(nview):
            o, h, l, c = (float(view["open"].iloc[i]), float(view["high"].iloc[i]),
                          float(view["low"].iloc[i]), float(view["close"].iloc[i]))
            pc = pcs[start + i] if (start + i) < len(pcs) else np.nan
            up_ = (c >= pc) if pc == pc else (c >= o)   # 无昨收时才退回 c vs o
            col = "#d62728" if up_ else "#2ca02c"
            ax.plot([i, i], [l, h], color=col, lw=0.7, zorder=2)
            ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, max(abs(c - o), 1e-9),
                                   facecolor=col, edgecolor=col, lw=0.5, zorder=3))

        # ── 笔 ──
        sx, sy = [], []
        for p in geo["bi"]:
            x = p["orig"] - start
            if -2 <= x <= nview + 2:
                sx.append(x)
                sy.append(p["price"])
        if len(sx) >= 2:
            ax.plot(sx, sy, color="#1f4e79", lw=1.4, zorder=5)

        # ── 中枢 ──
        zs_txt = "-"
        if zs and geo["zs_x"]:
            x0 = geo["zs_x"][0] - start
            x1 = geo["zs_x"][1] - start
            x0 = max(x0, -0.5)
            if x1 >= -1:
                ax.add_patch(Rectangle((x0, zs[0]), x1 - x0, zs[1] - zs[0],
                                       facecolor="#ffd24d", alpha=0.25,
                                       edgecolor="#b8860b", lw=1.2, zorder=4))
                ax.axhline(zs[1], color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
                ax.axhline(zs[0], color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
                zs_txt = "%.2f~%.2f" % zs

        # ── 买卖点 ──
        pts_txt = []
        BSTY = {"一买": ("*", "#0b6e0b", 380), "二买": ("*", "#1a9850", 280),
                "三买": ("*", "#4da34d", 200)}
        SSTY = {"一卖": ("v", "#8b0000", 240), "二卖": ("v", "#d73027", 190),
                "三卖": ("v", "#f46d43", 150)}
        for kind, lst, sty in (("买", ck.get("buy_list") or [], BSTY),
                               ("卖", ck.get("sell_list") or [], SSTY)):
            for b in lst:
                tag = b["tag"]
                if tag not in sty:
                    continue
                mk, col, sz = sty[tag]
                cand = [p for p in geo["bi"] if p["type"] == (-1 if kind == "买" else 1)]
                if not cand:
                    continue
                pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
                x = pt["orig"] - start
                if not (-1 <= x <= nview):
                    continue
                ax.scatter([x], [pt["price"]], s=sz, marker=mk, color=col,
                           edgecolor="white", linewidth=1.0, zorder=9)
                if b.get("fresh"):
                    pts_txt.append("%s %.2f(%d根前)" % (tag, b["level"], b["age"]))

        # ── 一买预备：可能有一买机会（且 MA40 向上）→ 画圈 + 月亮 ──
        # 2026-10-26 大佬：「如果要出现一买的机会，并且 MA40 是向上，你也要提示可能
        # 有一买机会出现。然后图的时候，你在一买的机会那块画一个圈，或者标一个月亮」
        try:
            pre = Y1.y1_prepare(ck, view["close"].values, P)
            if pre.get("ok") and Y1.mark_on_ax(ax, geo["bi"], pre, start, nview, Y1_MOON):
                pts_txt.append("可能一买(MA40↑) 前低%.2f" % pre["prev_low"])
        except Exception as _e:
            print("[WARN] y1 mark %s: %s" % (name, str(_e)[:80]), flush=True)

        # ── 左侧信息栏 ──
        y0, y1 = ax.get_ylim()
        span = y1 - y0
        ax.set_ylim(y0 - span * 0.10, y1 + span * 0.10)
        zpos = {"in": "中枢内", "above": "中枢上方", "below": "中枢下方"}.get(ck.get("zpos"), "-")
        # 涨跌幅（vs 上一根收盘）——直接写在信息栏，避免只看颜色误判
        _pc_last = pcs[-1] if len(pcs) else np.nan
        if _pc_last == _pc_last and _pc_last:
            _pct = (P / float(_pc_last) - 1) * 100
            pct_txt = "收 %.2f (%+.2f%%)" % (P, _pct)
        else:
            pct_txt = "收 %.2f" % P
        info = "%s\n%s\n%s\n中枢 %s\n%s" % (
            name, pct_txt, ck.get("stage", "")[:14], zs_txt,
            ("\n".join(pts_txt[:2]) if pts_txt else "无新鲜买卖点"))
        ax.text(1.008, 0.5, info, transform=ax.transAxes, fontsize=10,
                va="center", ha="left", linespacing=1.55,
                bbox=dict(boxstyle="round,pad=0.4", fc="#f8f8f8", ec="#cccccc", lw=0.8))
        # 中枢位置小标签
        ax.text(0.005, 0.95, zpos, transform=ax.transAxes, fontsize=9,
                color="#b8860b", va="top", ha="left", fontweight="bold")

        ax.set_xlim(-1, nview)
        ax.set_xticks([])
        ax.grid(True, ls=":", color="#e5e5e5", lw=0.6)
        ax.set_ylabel("价", fontsize=9)

        if not header_done:
            ts_txt = (view["Date"].iloc[-1].strftime("%Y-%m-%d %H:%M") if level == "h1"
                      else view["Date"].iloc[-1].strftime("%Y-%m-%d"))
            scope = "五指数" if len(names) == len(all_names) else "%d指数（有信号）" % len(names)
            fig.suptitle("%s缠论 · %s ｜ 级别：%s ｜ 版本：%s%s"
                         % (scope, ts_txt, LV_TXT.get(level, level), VER, title_extra),
                         fontsize=17, fontweight="bold", y=0.995)
            fig.text(0.5, 0.978,
                     "口径：包含处理→顶底分型→笔→中枢(ZG/ZD)→背驰　｜　★买点 ▼卖点　"
                     "◯圈+月亮 = 可能一买(MA40↑)　黄框=中枢　蓝线=笔",
                     ha="center", fontsize=10.5, color="#444444")
            header_done = True

    fig.subplots_adjust(left=0.05, right=0.86, top=0.945, bottom=0.02)
    os.makedirs(OUT_DIR, exist_ok=True)
    if not out:
        d8 = datetime.now().strftime("%Y%m%d")
        out = os.path.join(OUT_DIR, "panorama_%s_%s.png" % (level, d8))
    fig.savefig(out, dpi=120, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="h1", choices=["h1", "day"])
    ap.add_argument("--n", type=int, default=90, help="每个指数显示最近多少根K线")
    ap.add_argument("--out", default=None)
    ap.add_argument("--push", action="store_true", help="生成后推 QQ+飞书私聊")
    ap.add_argument("--qq-only", action="store_true")
    ap.add_argument("--feishu-only", action="store_true")
    a = ap.parse_args()

    p = draw_panorama(a.level, a.n, a.out)
    print("[OK]", p)
    if a.push:
        import push_image as PI
        txt = "五指数缠论全景 · %s · %s" % (LV_TXT.get(a.level), VER)
        ok = PI.push(p, text=txt, qq=not a.feishu_only, fs=not a.qq_only)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
