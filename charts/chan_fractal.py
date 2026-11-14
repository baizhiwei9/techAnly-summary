#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论·分型版 K线图生成器（mplfinance + 缠论示意图）—— 画图模块
=====================================================================
原 chanlun/fractal/chan_chart.py 的画图部分，统一收进 charts/（2026-11-14）。

把「某一级别的缠论买卖点」画成图片：K线 + 笔 + 中枢矩形 + 买点/卖点 + MACD。
口径（与 chan_signal_monitor / chan_smc_index 完全一致）
  版本：缠论·分型版（包含处理→顶底分型→笔→中枢ZG/ZD→背驰）
  级别：--level h1  → 小时线60m
        --level day → 日线

用法：
  python3 charts/chan_fractal.py --index 沪深300
  python3 charts/chan_fractal.py --index 科创50 --n 90 --out /tmp/a.png
  python3 charts/chan_fractal.py --compare 上证50,沪深300          # 多指数并排对比（日线）
  python3 charts/chan_fractal.py --compare 000016.SH,000300.SH
"""
import os as _os, sys as _sys
if __package__ in (None, ""):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403  (仓库根/sys.path/网络出口/中文字体/Agg)

import argparse  # noqa: E402
import re  # noqa: E402
from datetime import datetime  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import chan_smc_index as C  # noqa: E402
from chan_smc_index import MACD_FAST, MACD_SLOW, MACD_SIGNAL  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

OUT_DIR = os.path.join(WS, ".cache/charts")
LV_TXT = {"h1": "小时线60m", "day": "日线"}
VER = "缠论·分型版"
RED, GRN = "#d62728", "#2ca02c"


# ─────────── 缠论：带原始索引的合并K线 ───────────
# 几何计算全部复用 chan_smc_index（本地分型版唯一口径），
# 不再在画图层维护第二套包含/分型/成笔逻辑。
def chan_geometry(hb):
    """从 OHLC 算出一整套可绘图几何：笔端点 / 中枢 / 买卖点"""
    bars = list(zip(hb["high"].astype(float).values,
                    hb["low"].astype(float).values,
                    hb["close"].astype(float).values))
    mb = C.merge_bars(bars)
    bi = C.build_bi(mb)
    segs = C.segs_from_bi(bi, mb)
    # 笔端点（原始K线索引：顶取 hi_i，底取 lo_i）
    pts = [{"orig": (mb[i][3] if t == 1 else mb[i][4]),
            "type": int(t), "price": float(px)} for t, px, i in bi]
    # 中枢：本地逐笔链式，取最近一个
    zs, zs_x = None, None
    zlist = C.zhongshu_from_segs(segs)
    if zlist:
        z = zlist[-1]
        zs = (round(z["zd"], 2), round(z["zg"], 2))
        e = min(z["end"], len(segs) - 1)
        zs_x = (segs[z["i"]]["i0o"], segs[e]["i1o"])
    # 买卖点（复用 chan_smc_index 的正式判定，保证和图一致）
    ck = C.chan_read(bars, "h1")
    return {"bars": bars, "mb": mb, "bi": pts, "segs": segs,
            "zs": zs, "zs_x": zs_x, "ck": ck}


# 买卖点样式（matplotlib marker 必须用符号名：买点="*"，卖点="v"，不能用中文 ★ ▼）
BUY_STYLE = {"一买": ("*", "#0b6e0b", 420), "二买": ("*", "#1a9850", 300),
             "三买": ("*", "#4da34d", 220)}
SELL_STYLE = {"一卖": ("v", "#8b0000", 260), "二卖": ("v", "#d73027", 200),
              "三卖": ("v", "#f46d43", 160)}


# ─────────── 画图 ───────────
def draw(name, level="h1", n=120, out=None, show_macd=True):
    import mplfinance as mpf  # 惰性导入：只有单图模式才依赖 mplfinance
    if level == "h1":
        # ① data_hub `minute_bar`（要 volume；C.fetch_60 只返回 OHLC 四列）
        df = None
        try:
            import dh_bridge as _DHB
            d = _DHB.minute_bar(C.IDX_SH[name], freq="60", n=400, kind="index")
            if d is not None and len(d) > 40:
                df = d.rename(columns={"dt": "Date"})
        except Exception:                                        # noqa: BLE001
            df = None
        # ② 老链路兜底：minute_hub（原样保留）
        if df is None:
            import minute_hub as mh
            df = mh.get_kline(C.IDX_SH[name], "60", n=400, kind="index",
                              cache=True, cache_ttl_min=10)
            if df is not None:
                df = df.rename(columns={"day": "Date"})
                if "volume" not in df.columns:
                    df["volume"] = 0.0
    else:
        df = C.fetch_daily(dict((x[0], x[1]) for x in C.IDX)[name])
        if df is not None:
            df = df.rename(columns={"dt": "Date"})
            if "volume" not in df.columns:
                df["volume"] = df.get("vol", 0.0)
    if df is None or len(df) < 60:
        raise RuntimeError("%s 数据不足" % name)

    df = df[["Date", "open", "high", "low", "close", "volume"]].copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    # 图上只画最近 n 根，但**缠论用全量K线算**（不然笔/中枢会变），再按索引裁切
    total = len(df)
    full_geo = chan_geometry(df)
    start = max(0, total - n)
    view = df.iloc[start:].reset_index(drop=True)
    view = view.set_index("Date")          # mplfinance 要求 DatetimeIndex
    nview = len(view)

    def vx(orig_i):
        """原始索引 → 视图内的 x（mplfinance 用 0..N-1 整数位）"""
        return orig_i - start

    geo = chan_geometry(df)
    ck = geo["ck"]
    zs = geo["zs"]
    P = float(view["close"].iloc[-1])
    last_ts = view.index[-1]

    # MACD(10,20,5)：与 chan_smc_index.macd_hist 同口径（常量复用，勿硬编码）
    c = df["close"].astype(float)
    dif = (c.ewm(span=MACD_FAST, adjust=False).mean()
           - c.ewm(span=MACD_SLOW, adjust=False).mean())
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    hist = (2 * (dif - dea))
    macd_df = pd.DataFrame({"DIF": dif.values[start:], "DEA": dea.values[start:],
                            "HIST": hist.values[start:]}, index=view.index)

    aps = [
        mpf.make_addplot(macd_df["DIF"], panel=2, color="#e67e22", width=0.9, ylabel="MACD"),
        mpf.make_addplot(macd_df["DEA"], panel=2, color="#2980b9", width=0.9),
        mpf.make_addplot(macd_df["HIST"], panel=2, type="bar", color="#95a5a6", alpha=0.55),
    ]

    mc = mpf.make_marketcolors(up="#d62728", down="#2ca02c", edge="inherit",
                               wick="inherit", volume="in")
    style = mpf.make_mpf_style(base_mpf_style="yahoo", marketcolors=mc,
                               rc={"font.sans-serif": matplotlib.rcParams["font.sans-serif"],
                                   "axes.unicode_minus": False},
                               gridstyle=":", gridcolor="#dddddd")
    fig, axlist = mpf.plot(
        view, type="candle", style=style,
        volume=True, addplot=aps if show_macd else None,
        panel_ratios=(6, 1.6, 3) if show_macd else (6, 2),
        figsize=(16, 10), returnfig=True, xrotation=0,
        title="",
    )
    ax = axlist[0]

    # ── 笔（折线）──
    seg_x, seg_y = [], []
    for p in geo["bi"]:
        x = vx(p["orig"])
        if -2 <= x <= nview + 2:
            seg_x.append(x)
            seg_y.append(p["price"])
    if len(seg_x) >= 2:
        ax.plot(seg_x, seg_y, color="#1f4e79", lw=1.5, zorder=6, label="笔")

    # 分型端点
    for p in geo["bi"]:
        x = vx(p["orig"])
        if -1 <= x <= nview:
            ax.scatter([x], [p["price"]], s=26, zorder=7,
                       color=("#d62728" if p["type"] == 1 else "#2ca02c"),
                       marker=("v" if p["type"] == 1 else "^"))

    # ── 中枢矩形 ──
    if zs and geo["zs_x"]:
        x0, x1 = vx(geo["zs_x"][0]), vx(geo["zs_x"][1])
        x0 = max(x0, -0.5)
        if x1 >= -1:
            ax.add_patch(Rectangle((x0, zs[0]), x1 - x0, zs[1] - zs[0],
                                   facecolor="#ffd24d", alpha=0.22,
                                   edgecolor="#b8860b", lw=1.4, zorder=3))
            ax.axhline(zs[1], color="#b8860b", lw=0.9, ls="--", alpha=0.75, zorder=4)
            ax.axhline(zs[0], color="#b8860b", lw=0.9, ls="--", alpha=0.75, zorder=4)
            ax.text(max(x0, 0), zs[1], " ZG %.2f" % zs[1], va="bottom", ha="left",
                    fontsize=9, color="#8b6508", zorder=8)
            ax.text(max(x0, 0), zs[0], " ZD %.2f" % zs[0], va="top", ha="left",
                    fontsize=9, color="#8b6508", zorder=8)
            ax.text(max(x0, 0) + 0.4, (zs[0] + zs[1]) / 2, "中枢", va="center",
                    ha="left", fontsize=12, color="#8b6508", alpha=0.85, zorder=8)

    # ── 买点 / 卖点 ──（样式见模块级 BUY_STYLE / SELL_STYLE）
    for kind, lst, styles, dy in (("买", ck.get("buy_list") or [], BUY_STYLE, -1),
                                  ("卖", ck.get("sell_list") or [], SELL_STYLE, 1)):
        for b in lst:
            tag = b["tag"]
            if tag not in styles:
                continue
            mk, col, sz = styles[tag]
            # 找最近的笔端点作为 x 位置
            price = b["level"]
            cand = [p for p in geo["bi"] if p["type"] == (1 if kind == "卖" else -1)]
            if not cand:
                continue
            pt = min(cand, key=lambda p: abs(p["price"] - price))
            x = vx(pt["orig"])
            if not (-1 <= x <= nview):
                continue
            ax.scatter([x], [pt["price"]], s=sz, marker=mk, color=col,
                       edgecolor="white", linewidth=1.2, zorder=10)
            # 标注自动避让：默认买点标下方、卖点标上方；贴到图边就翻到另一侧
            y0, y1 = ax.get_ylim()
            span = y1 - y0
            d = dy
            if dy < 0 and pt["price"] - span * 0.14 < y0:
                d = 1          # 太低 → 改标上方
            elif dy > 0 and pt["price"] + span * 0.14 > y1:
                d = -1         # 太高 → 改标下方
            ax.annotate("%s %.2f\n(%d根前)" % (tag, price, b["age"]),
                        xy=(x, pt["price"]),
                        xytext=(0, 30 * d), textcoords="offset points",
                        ha="center", va=("top" if d < 0 else "bottom"),
                        fontsize=9, color=col, fontweight="bold", zorder=11,
                        bbox=dict(boxstyle="round,pad=0.28", fc="white",
                                  ec=col, alpha=0.92, lw=0.9))

    # ── 标题 / 说明 ──
    stage = ck.get("stage", "")
    div = ck.get("div") or "无"
    zpos_txt = {"in": "中枢内", "above": "中枢上方", "below": "中枢下方"}.get(ck.get("zpos"), "-")
    lv = LV_TXT.get(level, level)
    ts_txt = last_ts.strftime("%Y-%m-%d %H:%M") if level == "h1" else last_ts.strftime("%Y-%m-%d")
    fig.suptitle("%s · %s ｜ 级别：%s ｜ 版本：%s" % (name, ts_txt, lv, VER),
                 fontsize=16, fontweight="bold", y=0.975)
    sub = ("收盘 %.2f ｜ 阶段：%s ｜ 中枢位置：%s ｜ 中枢 %.2f~%.2f ｜ 背驰：%s"
           % (P, stage, zpos_txt, zs[0], zs[1], div)) if zs else \
          ("收盘 %.2f ｜ 阶段：%s ｜ 背驰：%s" % (P, stage, div))
    fig.text(0.5, 0.938, sub, ha="center", fontsize=11.5)
    fig.text(0.5, 0.912,
             "口径：包含处理→顶底分型→笔→中枢(ZG/ZD)→背驰　｜　★买点 ▼卖点　黄框=最近中枢　蓝线=笔",
             ha="center", fontsize=9.5, color="#555555")

    ax.set_ylabel("价格")
    if show_macd:
        axlist[-2].set_ylabel("MACD")
    ax.legend(loc="upper left", fontsize=9, framealpha=0.85)

    os.makedirs(OUT_DIR, exist_ok=True)
    if not out:
        d8 = datetime.now().strftime("%Y%m%d")
        out = os.path.join(OUT_DIR, "chan_%s_%s_%s.png" % (name, level, d8))
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


# ─────────── 多指数并排对比 ───────────
# chan_smc_index.IDX 只收 5 个宽基；这里补充常见宽基的名称→ts_code。
INDEX_EXTRA = {"上证50": "000016.SH", "深证50": "399850.SZ"}
_INDEX_NAMES = dict((x[0], x[1]) for x in C.IDX)
_INDEX_NAMES.update(INDEX_EXTRA)


def _resolve_index(raw):
    """指数名/代码 → (显示名, ts_code)。识别不了返回 None。"""
    s = str(raw or "").strip()
    if not s:
        return None
    # 纯 6 位代码：先反查已知表定交易所，查不到再按代码段猜
    if re.match(r"^\d{6}$", s):
        for name, tc in _INDEX_NAMES.items():
            if tc.split(".")[0] == s:
                return (name, tc)
        suffix = "SH" if s.startswith(("5", "6", "9")) else "SZ"
        return (s, "%s.%s" % (s, suffix))
    # 带后缀代码
    m = re.match(r"^(\d{6})\.(SH|SZ)$", s.upper())
    if m:
        return (s, "%s.%s" % (m.group(1), m.group(2)))
    # 名称
    if s in _INDEX_NAMES:
        return (s, _INDEX_NAMES[s])
    return None


def _norm_day_df(df):
    """把各来源的日线统一成 dt/open/high/low/close/volume（升序）。"""
    if df is None or not len(df):
        return None
    df = df.copy()
    if "trade_date" in df.columns and "dt" not in df.columns:
        df = df.rename(columns={"trade_date": "dt"})
    if "volume" not in df.columns:
        df["volume"] = df.get("vol", 0.0)
    df["dt"] = pd.to_datetime(df["dt"])
    return df.sort_values("dt").reset_index(drop=True)


def _local_datahub():
    """本地 data_hub 直连（无 dh_bridge 的开发环境验证用）。失败返回 None。"""
    import importlib
    cands = []
    env = os.environ.get("DATA_HUB_PATH")
    if env:
        cands.append(os.path.abspath(os.path.expanduser(env)))
    cands.append(os.path.join(os.path.dirname(WS), "datahub-three-tier"))
    for base in cands:
        if not os.path.isdir(base):
            continue
        if base not in sys.path:
            sys.path.insert(0, base)
        try:
            return importlib.import_module("data_hub")
        except Exception:
            continue
    return None


def _index_day(ts_code):
    """指数日线：优先 chan_smc_index.fetch_daily（dh_bridge→tushare→缓存），
    失败回退本地 data_hub 直连。返回规范化后的 df，取不到返回 None。"""
    df = None
    try:
        df = _norm_day_df(C.fetch_daily(ts_code))
    except Exception:
        df = None
    if df is not None and len(df) > 100:
        return df
    try:
        mod = _local_datahub()
        if mod is not None:
            d = _norm_day_df(mod.get_data("index_daily", ts_code=ts_code,
                                          start_date="20240101", end_date="20261114"))
            if d is not None and len(d) > 100:
                return d
    except Exception:
        pass
    return df if (df is not None and len(df) > 60) else None


def draw_compare(pairs, out=None, n_show=160):
    """多指数并排对比：每列一个指数（上=价格面板，下=MACD 面板）。

    pairs = [(显示名, ts_code), ...]；缠论判定用全量K线，图上只裁最近 n_show 根。
    口径与 draw() 完全一致（同一 chan_geometry / MACD 常量 / 买卖点样式）。
    """
    ncol = len(pairs)
    fig = plt.figure(figsize=(10.2 * ncol, 11.2), facecolor="white")
    gs = fig.add_gridspec(2, ncol, height_ratios=[3.0, 1.0],
                          hspace=0.10, wspace=0.14, left=0.05, right=0.96,
                          top=0.90, bottom=0.07)

    for ci, (name, code) in enumerate(pairs):
        df = _index_day(code)
        if df is None or len(df) < 60:
            raise RuntimeError("%s(%s) 数据不足" % (name, code))
        total = len(df)
        start = max(0, total - n_show)
        view = df.iloc[start:].reset_index(drop=True)
        nv = len(view)
        x = np.arange(nv)
        geo = chan_geometry(df)
        ck = geo["ck"]
        zs = geo["zs"]
        P = float(view["close"].iloc[-1])
        last_ts = view["dt"].iloc[-1]

        def vx(oi):
            return oi - start

        # ── 价格面板 ──
        ax = fig.add_subplot(gs[0, ci])
        o = view["open"].astype(float).values
        h = view["high"].astype(float).values
        l = view["low"].astype(float).values
        c = view["close"].astype(float).values
        prev = np.concatenate([[np.nan], c[:-1]])
        if start > 0:
            prev[0] = float(df["close"].iloc[start - 1])
        cols = [RED if (np.isfinite(prev[i]) and c[i] >= prev[i]) else GRN for i in range(nv)]
        bw = 0.62
        rng = max(h.max() - l.min(), 1e-9)
        for i in range(nv):
            ax.vlines(i, l[i], h[i], color=cols[i], lw=0.8, zorder=3)
            y0, y1 = min(o[i], c[i]), max(o[i], c[i])
            ax.add_patch(Rectangle((i - bw / 2, y0), bw, max(y1 - y0, rng * 0.0008),
                                   facecolor=cols[i], edgecolor=cols[i], lw=0.5, zorder=4))
        # 笔
        sx, sy = [], []
        for p in geo["bi"]:
            xi = vx(p["orig"])
            if -2 <= xi <= nv + 2:
                sx.append(xi)
                sy.append(p["price"])
        if len(sx) >= 2:
            ax.plot(sx, sy, color="#1f4e79", lw=1.5, zorder=6)
        for p in geo["bi"]:
            xi = vx(p["orig"])
            if -1 <= xi <= nv:
                ax.scatter([xi], [p["price"]], s=22, zorder=7,
                           color=(RED if p["type"] == 1 else GRN),
                           marker=("v" if p["type"] == 1 else "^"))
        # 中枢
        if zs and geo["zs_x"]:
            x0 = max(vx(geo["zs_x"][0]), -0.5)
            x1 = vx(geo["zs_x"][1])
            if x1 >= -1:
                ax.add_patch(Rectangle((x0, zs[0]), x1 - x0, zs[1] - zs[0],
                                       facecolor="#ffd24d", alpha=0.22,
                                       edgecolor="#b8860b", lw=1.4, zorder=3))
                ax.axhline(zs[1], color="#b8860b", lw=0.9, ls="--", alpha=0.75, zorder=4)
                ax.axhline(zs[0], color="#b8860b", lw=0.9, ls="--", alpha=0.75, zorder=4)
                ax.text(max(x0, 0), zs[1], " ZG %.2f" % zs[1], va="bottom", ha="left",
                        fontsize=9, color="#8b6508", zorder=8)
                ax.text(max(x0, 0), zs[0], " ZD %.2f" % zs[0], va="top", ha="left",
                        fontsize=9, color="#8b6508", zorder=8)
                ax.text(max(x0, 0) + 0.4, (zs[0] + zs[1]) / 2, "中枢", va="center",
                        ha="left", fontsize=12, color="#8b6508", alpha=0.85, zorder=8)
        # 买卖点
        for kind, lst, styles, dy in (("买", ck.get("buy_list") or [], BUY_STYLE, -1),
                                      ("卖", ck.get("sell_list") or [], SELL_STYLE, 1)):
            for b in lst:
                tag = b["tag"]
                if tag not in styles:
                    continue
                mk, col, sz = styles[tag]
                price = b["level"]
                cand = [p for p in geo["bi"] if p["type"] == (1 if kind == "卖" else -1)]
                if not cand:
                    continue
                pt = min(cand, key=lambda p: abs(p["price"] - price))
                xi = vx(pt["orig"])
                if not (-1 <= xi <= nv):
                    continue
                ax.scatter([xi], [pt["price"]], s=sz, marker=mk, color=col,
                           edgecolor="white", linewidth=1.2, zorder=10)
                y0_, y1_ = ax.get_ylim()
                span = y1_ - y0_
                d = dy
                if dy < 0 and pt["price"] - span * 0.14 < y0_:
                    d = 1
                elif dy > 0 and pt["price"] + span * 0.14 > y1_:
                    d = -1
                ax.annotate("%s %.2f" % (tag, price), xy=(xi, pt["price"]),
                            xytext=(0, 28 * d), textcoords="offset points",
                            ha="center", va=("top" if d < 0 else "bottom"),
                            fontsize=9, color=col, fontweight="bold", zorder=11,
                            bbox=dict(boxstyle="round,pad=0.26", fc="white",
                                      ec=col, alpha=0.92, lw=0.9))

        # 标题
        zpos = {"in": "中枢内", "above": "中枢上方", "below": "中枢下方"}.get(ck.get("zpos"), "-")
        ts_txt = last_ts.strftime("%Y-%m-%d")
        ax.set_title("%s（%s）· %s\n收盘 %.2f ｜ 阶段：%s ｜ %s ｜ 背驰：%s"
                     % (name, code, ts_txt, P, ck.get("stage", ""), zpos,
                        ck.get("div") or "无"),
                     fontsize=12.5, pad=10)
        ax.set_ylabel("价格", fontsize=10)
        ax.grid(True, ls=":", color="#cccccc", alpha=0.55)
        ax.set_xlim(-2, nv + 1)
        ax.tick_params(labelsize=8.5)

        # ── MACD 面板 ──
        axm = fig.add_subplot(gs[1, ci], sharex=ax)
        cl = df["close"].astype(float)
        dif = cl.ewm(span=MACD_FAST, adjust=False).mean() \
            - cl.ewm(span=MACD_SLOW, adjust=False).mean()
        dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
        hist = 2 * (dif - dea)
        hh = hist.values[start:]
        axm.bar(x, hh, width=bw, color=[RED if v >= 0 else GRN for v in hh], alpha=0.7)
        axm.plot(x, dif.values[start:], color="#e67e22", lw=0.9, label="DIF")
        axm.plot(x, dea.values[start:], color="#2980b9", lw=0.9, label="DEA")
        axm.axhline(0, color="#999", lw=0.6)
        axm.set_ylabel("MACD(10,20,5)", fontsize=9)
        axm.grid(True, ls=":", color="#cccccc", alpha=0.5)
        axm.tick_params(labelsize=8.5)
        axm.legend(loc="upper left", fontsize=8, framealpha=0.8, ncol=2)
        step = max(nv // 10, 1)
        idxs = list(range(0, nv, step))
        dates = view["dt"].dt.strftime("%m-%d").values
        axm.set_xticks(idxs)
        axm.set_xticklabels([dates[i] for i in idxs], rotation=0, fontsize=8.5)
        plt.setp(ax.get_xticklabels(), visible=False)

    fig.suptitle("缠论·分型版 对比 ｜ 口径：包含处理→顶底分型→笔→中枢(ZG/ZD)→背驰 ｜ "
                 "★买点 ▼卖点 ｜ 黄框=最近中枢 蓝线=笔", fontsize=14, y=0.975)
    fig.text(0.5, 0.925,
             "各指数最近 %d 根日K并排对比（判定用全量K线，图上只裁最近 %d 根）"
             % (n_show, n_show), ha="center", fontsize=10, color="#555555")

    os.makedirs(OUT_DIR, exist_ok=True)
    if not out:
        d8 = datetime.now().strftime("%Y%m%d")
        out = os.path.join(OUT_DIR, "chan_compare_%s_%s.png"
                           % ("_".join(n for n, _ in pairs), d8))
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="中证500", choices=[x[0] for x in C.IDX])
    ap.add_argument("--level", default="h1", choices=["h1", "day"])
    ap.add_argument("--n", type=int, default=120, help="图上显示最近多少根K线（判定仍用全量）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--all", action="store_true", help="5 指数小时线各出一张")
    ap.add_argument("--compare", default=None,
                    help="多指数并排对比（日线）：逗号分隔的名称或代码，如 上证50,沪深300")
    ap.add_argument("--compare-n", type=int, default=160,
                    help="对比图显示最近多少根K线（判定仍用全量）")
    a = ap.parse_args()

    if a.all:
        paths = []
        for nm, _c in C.IDX:
            try:
                p = draw(nm, a.level, a.n)
                print("[OK]", p, flush=True)
                paths.append(p)
            except Exception as e:
                print("[ERR]", nm, str(e)[:200], flush=True)
        return 0 if paths else 1

    if a.compare:
        pairs = []
        for seg in a.compare.split(","):
            seg = seg.strip()
            if not seg:
                continue
            r = _resolve_index(seg)
            if r is None:
                print("[ERR] 无法识别的指数：%s" % seg, flush=True)
                return 2
            pairs.append(r)
        if len(pairs) < 2:
            print("[ERR] 对比至少需要两个指数", flush=True)
            return 2
        p = draw_compare(pairs, a.out, a.compare_n)
        print("[OK]", p)
        return 0

    p = draw(a.index, a.level, a.n, a.out)
    print("[OK]", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
