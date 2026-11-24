#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论·分型版 K线图生成器（mplfinance + 缠论示意图）—— 画图模块
=====================================================================
原 chanlun/fractal/chan_chart.py 的画图部分，统一收进 charts/（2026-11-14）。

把「某一级别的缠论买卖点」画成图片：K线 + 笔 + 中枢矩形 + 买点/卖点 + 副图。

副图按级别区分（2026-11-16 大佬澄清：「小时级别和周线级别你还是用那个偏向的 MACD，
我只是说你日线级别给我出图那会改一下子」）：
  · 日线       → 期权（VIX + SKEW 偏度）+ MF-DFA Δα，双 Y 轴
  · 小时线/周线 → MACD(10,20,5)，保持原样不动
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
from matplotlib.transforms import blended_transform_factory  # noqa: E402

# VWAP 标准差带（2026-11-15 大佬：「缠论的那图都要带上那个 VWAP，
# 我好像知道它到了几倍的标准差了」→ 图上直接标 σ 倍数）
# 计算源唯一：smc/smc_core.py:vwap_tracks()（滚动 win 根、成交量加权、返回 vwap+sigma）
try:
    from smc_core import vwap_tracks as _vwap_tracks  # noqa: E402
except Exception:                                     # 画图不应因 VWAP 缺失而整体失败
    _vwap_tracks = None

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
    cands.append(os.path.join(os.path.dirname(WS), "datahub-multi-tier"))
    cands.append(os.path.join(os.path.dirname(WS), "datahub-three-tier"))  # 兼容旧目录名
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


# ─────────── 多指数纵向堆叠（一指数一行，不压缩宽度） ───────────
def _paint_pcr_panel(ax, view, nv, pcr_series, f_tick, f_y):
    """薄面板：持仓量 PCR（认沽持仓量 ÷ 认购持仓量，剔到期≤5天合约）。

    数据源唯一：/root/.openclaw/workspace/bin/opt_pcr_oi.py（不顺写第二套口径）。
    画法：蓝紫实线 + 1.0 参考虚线（1.0 上方 = 认沽持仓多于认购，情绪偏空/对冲重）；
    右侧淡填充 = 近 60 日分位带（0~100% → 翻转成高度），不挤主线；
    右上角标最新值 + 60 日分位（文字锚在轴内，不跑出画布）。
    """
    x = np.arange(nv)
    dates = [str(v) for v in view["dt"].dt.strftime("%Y%m%d").values]
    vals = np.full(nv, np.nan)
    if pcr_series is not None and len(pcr_series):
        ser = pcr_series["PCR_OI"] if "PCR_OI" in pcr_series.columns else pcr_series.iloc[:, 0]
        idx = [str(i) for i in ser.index]
        ser = pd.Series(ser.values.astype(float), index=idx)
        al = ser.reindex(ser.index.union(pd.Index(dates))).ffill().reindex(dates)
        vals = al.values.astype(float)
    if not np.isfinite(vals).any():
        ax.text(0.5, 0.5, "持仓量 PCR 数据不可用", transform=ax.transAxes,
                ha="center", va="center", fontsize=f_tick, color="#888")
        ax.set_xticks([])
        return None
    # 纵轴范围：数据 + 1.0 参考线都要装得下，留 12% 空
    fin = vals[np.isfinite(vals)]
    lo = min(float(fin.min()), 1.0)
    hi = max(float(fin.max()), 1.0)
    pad = max((hi - lo) * 0.22, 0.02)
    ax.set_ylim(lo - pad, hi + pad * 1.5)
    ax.axhline(1.0, color="#999999", ls="--", lw=1.0, alpha=0.85, zorder=1)
    # 低于 1.0 的面积淡色（视觉上「认购占优 / 偏空对冲轻」一块阴影）
    ax.fill_between(x, ax.get_ylim()[0], vals, color=PCR_FILL, alpha=0.20,
                    lw=0, zorder=0)
    ax.plot(x, vals, color=PCR_LINE, lw=2.2, zorder=6, label="持仓量 PCR（剔到期≤5天）")
    ax.set_ylabel("持仓量 PCR", fontsize=f_y - 3, color=PCR_LINE)
    ax.tick_params(axis="y", colors=PCR_LINE, labelsize=f_tick - 1.5)
    ax.tick_params(axis="x", labelsize=f_tick - 1.0)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.45)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.text(-1.6, 1.0, " 1.0", fontsize=f_tick - 2.5, color="#777", va="center", ha="left")
    cur = float(fin[-1])
    w = fin[-min(60, len(fin)):]
    pct = round(float((w <= cur).sum()) / len(w) * 100) if len(w) else None
    lab = "持仓量PCR %.4f" % cur + ("　｜　60日分位 %d%%" % pct if pct is not None else "")
    ax.text(0.995, 0.90, lab, transform=ax.transAxes, ha="right", va="top",
            fontsize=f_tick - 0.5, color="white", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.26", fc=PCR_LINE, ec="white",
                      alpha=0.90, lw=0.8), zorder=12)
    globals().setdefault("_PCR_LAST", {})[id(ax)] = {"pcr_oi": cur, "pct": pct}
    return cur, pct


def _paint_row(fig, ax, axm, name, code, df, start, nv, geo, ck, zs, P, last_ts,
               fs=None, sub="vol", da_series=None, vol_series=None,
               da_ax=True, xtick_every=10, vol_mode="vix",
               pcr_ax=None, pcr_series=None):
    """在给定 axes 上画一个完整指数的一段行情（价格面板 + 副图）。

    `sub` 决定副图画什么：
      · "vol"（默认）  ：VIX + 偏度（左轴）／MF-DFA Δα（右轴）三条线，双 Y 轴
      · "macd"        ：MACD(10,20,5)
      · "kline"       ：日线 K 线图（K线+MA20）

    `da_series`：MF-DFA 滚动 Δα（列 da）；`vol_series`：期权波动率（列 vix/skew）。
    两者都按日期对齐到 view，副图用 vol_series 时一起画。
    `xtick_every`：价格面板横轴刻度间隔（根）；0 = 不显示。
    """
    fs = fs or {}
    f_title, f_zt, f_pt, f_tick, f_y = (fs.get("title", 14.0), fs.get("ztick", 10.5),
                                      fs.get("pt", 11.0), fs.get("tick", 9.5),
                                      fs.get("y", 11.0))
    view = df.iloc[start:]
    x = np.arange(nv)
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
        ax.vlines(i, l[i], h[i], color=cols[i], lw=0.9, zorder=3)
        y0, y1 = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - bw / 2, y0), bw, max(y1 - y0, rng * 0.0008),
                               facecolor=cols[i], edgecolor=cols[i], lw=0.5, zorder=4))
    # ── VWAP 标准差带（±1σ / ±2σ / ±3σ）─────────────────────────
    # 2026-11-15 大佬要求：缠论图必须带 VWAP，并且要能看出「到了几倍标准差」。
    # 画法：中线实线 + 每档虚线；现价所在档位加粗高亮，标题旁直接标 ±X.Xσ。
    vwap_sig = None
    if _vwap_tracks is not None:
        try:
            _vw = _vwap_tracks(df)
            _v = np.asarray(_vw["vwap"], dtype=float)[start:]
            _sg = np.asarray(_vw["sigma"], dtype=float)[start:]
            if np.isfinite(_v).any():
                ax.plot(x, _v, color="#8e44ad", lw=2.0, zorder=5.5,
                        label="VWAP 中线")
                for k, ls, col, lw in ((1, ":", "#e67e22", 1.0),
                                       (-1, ":", "#e67e22", 1.0),
                                       (2, "--", "#c0392b", 1.1),
                                       (-2, "--", "#c0392b", 1.1),
                                       (3, "-.", "#7f8c8d", 0.9),
                                       (-3, "-.", "#7f8c8d", 0.9)):
                    _b = _v + k * _sg
                    if not np.isfinite(_b).any():
                        continue
                    _hit = False
                    if k in (2, -2) and np.isfinite(_b[-1]) and np.isfinite(c[-1]):
                        _hit = abs(k - (c[-1] - _v[-1]) / _sg[-1]) < 0.5 if _sg[-1] > 0 else False
                    ax.plot(x, _b, color=col, lw=(lw * 2.2 if _hit else lw), ls=ls,
                            alpha=(0.95 if _hit else 0.55), zorder=(5.6 if _hit else 5.4),
                            label="%+dσ" % k)
                # 现价相对 VWAP 的 σ 倍数（画在图右上角）
                if np.isfinite(_v[-1]) and _sg[-1] > 0:
                    vwap_sig = (c[-1] - _v[-1]) / _sg[-1]
                    _col = ("#c0392b" if abs(vwap_sig) >= 2 else
                            ("#e67e22" if abs(vwap_sig) >= 1 else "#7f8c8d"))
                    ax.text(0.995, 0.975,
                            "现价距 VWAP %+.2fσ" % vwap_sig,
                            transform=ax.transAxes, ha="right", va="top",
                            fontsize=f_pt + 0.5, fontweight="bold", color="white",
                            bbox=dict(boxstyle="round,pad=0.34", fc=_col,
                                      ec="white", alpha=0.92, lw=1.0), zorder=12)
                    if abs(vwap_sig) >= 2:
                        ax.text(0.995, 0.885,
                                "⚠ 已到 %d 倍标准差（%s）" % (
                                    int(abs(vwap_sig)), "上轨·超买" if vwap_sig > 0 else "下轨·超卖"),
                                transform=ax.transAxes, ha="right", va="top",
                                fontsize=f_pt, color=_col, fontweight="bold",
                                bbox=dict(boxstyle="round,pad=0.28", fc="white",
                                          ec=_col, alpha=0.9, lw=0.9), zorder=12)
                ax.legend(loc="upper left", fontsize=f_tick - 0.5, framealpha=0.82,
                          ncol=4, borderpad=0.35, handlelength=1.6)
        except Exception as _e:
            print("[WARN] VWAP 叠加失败:", str(_e)[:100])

    sx, sy = [], []
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -2 <= xi <= nv + 2:
            sx.append(xi)
            sy.append(p["price"])
    if len(sx) >= 2:
        ax.plot(sx, sy, color="#1f4e79", lw=1.7, zorder=6)
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -1 <= xi <= nv:
            ax.scatter([xi], [p["price"]], s=32, zorder=7,
                       color=(RED if p["type"] == 1 else GRN),
                       marker=("v" if p["type"] == 1 else "^"))
    if zs and geo["zs_x"]:
        x0 = max(geo["zs_x"][0] - start, -0.5)
        x1 = geo["zs_x"][1] - start
        if x1 >= -1:
            ax.add_patch(Rectangle((x0, zs[0]), x1 - x0, zs[1] - zs[0],
                                   facecolor="#ffd24d", alpha=0.22,
                                   edgecolor="#b8860b", lw=1.6, zorder=3))
            ax.axhline(zs[1], color="#b8860b", lw=1.0, ls="--", alpha=0.75, zorder=4)
            ax.axhline(zs[0], color="#b8860b", lw=1.0, ls="--", alpha=0.75, zorder=4)
            ax.text(max(x0, 0), zs[1], " ZG %.2f" % zs[1], va="bottom", ha="left",
                    fontsize=f_zt, color="#8b6508", zorder=8)
            ax.text(max(x0, 0), zs[0], " ZD %.2f" % zs[0], va="top", ha="left",
                    fontsize=f_zt, color="#8b6508", zorder=8)
            ax.text(max(x0, 0) + 0.4, (zs[0] + zs[1]) / 2, "中枢", va="center",
                    ha="left", fontsize=f_zt + 3, color="#8b6508", alpha=0.85, zorder=8)
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
            xi = pt["orig"] - start
            if not (-1 <= xi <= nv):
                continue
            ax.scatter([xi], [pt["price"]], s=sz * 1.15, marker=mk, color=col,
                       edgecolor="white", linewidth=1.3, zorder=10)
            y0_, y1_ = ax.get_ylim()
            span = y1_ - y0_
            d = dy
            if dy < 0 and pt["price"] - span * 0.16 < y0_:
                d = 1
            elif dy > 0 and pt["price"] + span * 0.16 > y1_:
                d = -1
            # 贴近右边缘的标注会飘出画布（2026-11-14 自检发现）→ 水平方向往内推
            _xt = 0
            if xi > nv - nv * 0.18:
                _xt = -58
            elif xi < nv * 0.10:
                _xt = 58
            ax.annotate("%s %.2f（%d根前）" % (tag, price, b["age"]),
                        xy=(xi, pt["price"]),
                        xytext=(_xt, 34 * d), textcoords="offset points",
                        ha=("right" if _xt < 0 else ("left" if _xt > 0 else "center")),
                        va=("top" if d < 0 else "bottom"),
                        fontsize=f_pt, color=col, fontweight="bold", zorder=11,
                        bbox=dict(boxstyle="round,pad=0.3", fc="white",
                                  ec=col, alpha=0.94, lw=1.0))
    zpos = {"in": "中枢内", "above": "中枢上方", "below": "中枢下方"}.get(ck.get("zpos"), "-")
    ts_txt = last_ts.strftime("%Y-%m-%d")
    _sig_txt = ("   ｜   VWAP %+.2fσ" % vwap_sig) if vwap_sig is not None else ""
    # 2026-11-17：标题原来一整行（实测宽 1854px @150dpi > 画布 1800px），
    #   右侧 165px 被裁 → 拆两行，且字号略降，保证整句在画布内
    ax.set_title("%s（%s）· %s   收盘 %.2f\n阶段：%s   ｜   %s   ｜   背驰：%s%s"
                 % (name, code, ts_txt, P, ck.get("stage", ""), zpos,
                    ck.get("div") or "无", _sig_txt),
                 fontsize=f_title - 1.0, pad=9, loc="left")
    ax.set_ylabel("价格", fontsize=f_y)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.55)
    ax.set_xlim(-2, nv + 7)
    ax.tick_params(labelsize=f_tick)

    # ── 横轴刻度（大佬：「那个横轴一你得画上」）────────────────────
    _xlabels = view["dt"].dt.strftime("%m-%d").values
    if xtick_every and xtick_every > 0:
        _ti = list(range(0, nv, max(1, xtick_every)))
        ax.set_xticks(_ti)
        ax.set_xticklabels([_xlabels[i] for i in _ti], fontsize=f_tick)
        ax.tick_params(axis="x", labelrotation=0)
    else:
        plt.setp(ax.get_xticklabels(), visible=False)


    # ── 副图 ──────────────────────────────────────────────────────
    # 2026-11-24：默认 **不带 MACD**，副图 = 期权(VIX+偏度) + MF-DFA Δα
    #   （原 R_cascade 位替换为 MF-DFA 多分形谱宽 Δα，口径唯一 charts/phase_mfdfa）
    #   · "vol"  ← 默认：VIX + 偏度（左轴 0~130）／Δα（右轴自适应，深红粗线）
    #   · "macd" ：MACD(10,20,5) —— 传 with_macd=True 或 --sub macd
    #   · "kline"：日线 K 线（更早的临时方案，保留能力）
    if sub == "vol":
        _v, _s, _r = _paint_vol_panel(axm, view, nv, da_series, vol_series,
                                      f_tick, f_y, vol_mode=vol_mode)
        globals().setdefault("_VOL_LAST", {})[name] = {"vix": _v, "skew": _s, "da": _r}
    elif sub == "kline":
        _paint_kline_panel(axm, view, nv, f_tick, f_y)
    else:
        _paint_macd_panel(axm, df, start, nv, bw, f_tick, f_y)
        step = max(nv // 12, 1)
        idxs = list(range(0, nv, step))
        dates = view["dt"].dt.strftime("%m-%d").values
        axm.set_xticks(idxs)
        axm.set_xticklabels([dates[i] for i in idxs], fontsize=f_tick)
        axm.set_xlim(-2, nv + 7)

    # ── 第三块（很薄）：持仓量 PCR ───────────────────────────────
    # 2026-11-16 大佬：「如果你这个 PCR 是持仓量 PCR，那你在这个子图上再画一下子」
    if pcr_ax is not None:
        _ok = _paint_pcr_panel(pcr_ax, view, nv, pcr_series, f_tick, f_y)
        if _ok is not None:
            # 日期刻度只留最底层（价格面板 / 期权面板都不显示）
            plt.setp(ax.get_xticklabels(), visible=False)
            plt.setp(axm.get_xticklabels(), visible=False)
            axp_x = list(range(0, nv, max(nv // 12, 1)))
            _xl = view["dt"].dt.strftime("%m-%d").values
            pcr_ax.set_xticks(axp_x)
            pcr_ax.set_xticklabels([_xl[i] for i in axp_x], fontsize=f_tick)
            pcr_ax.set_xlim(-2, nv + 7)



def _paint_macd_panel(ax, df, start, nv, bw, f_tick, f_y):
    """副图：MACD(10,20,5) —— 小时线 / 周线继续用这个（2026-11-16 大佬澄清）。

    口径与原 `_paint_row` 里的 MACD 段**完全一致**（同一组常量，勿硬编码）：
    DIF = EMA(10) - EMA(20)，DEA = EMA(DIF, 5)，HIST = 2×(DIF-DEA)。
    """
    x = np.arange(nv)
    cl = df["close"].astype(float)
    dif = (cl.ewm(span=MACD_FAST, adjust=False).mean()
           - cl.ewm(span=MACD_SLOW, adjust=False).mean())
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    hist = 2 * (dif - dea)
    hh = hist.values[start:]
    ax.bar(x, hh, width=bw, color=[RED if v >= 0 else GRN for v in hh], alpha=0.7)
    ax.plot(x, dif.values[start:], color="#e67e22", lw=1.0, label="DIF")
    ax.plot(x, dea.values[start:], color="#2980b9", lw=1.0, label="DEA")
    ax.axhline(0, color="#999", lw=0.6)
    ax.set_ylabel("MACD(10,20,5)", fontsize=f_y - 1)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.5)
    ax.tick_params(labelsize=f_tick)
    ax.legend(loc="upper left", fontsize=f_tick, framealpha=0.85, ncol=2)


RCP_UP, RCP_DN = "#e74c3c", "#2ecc71"     # R_cascade 图里的涨/跌配色
RCP_MA = "#f39c12"                        # R_cascade 图里的 MA20 线（白底改成更深的橙）


def _paint_kline_panel(ax, view, nv, f_tick, f_y):
    """副图：R_cascade 口径的 K 线面板（K线 + MA20 + 月份竖线 + 现价）。

    取自 `misc/engine/rcascade.py::draw()` 的 axp 面板：
      · 影线 + 实体，UP/DN 两色（#e74c3c / #2ecc71）
      · MA20 虚线
      · 每月一条竖线
    唯一一处调整：涨跌配色按「收盘 vs 前一根收盘」（= 涨跌幅口径），
    不用 open/close，避免低开收高被画成红柱、和图外涨跌幅对不上
    （2026-11-06 大佬：「今天是跌的，你怎么还是向上的？」）。
    """
    o = view["open"].astype(float).values
    h = view["high"].astype(float).values
    l = view["low"].astype(float).values
    c = view["close"].astype(float).values
    x = np.arange(nv)
    prev = np.concatenate([[np.nan], c[:-1]])
    cols = [RCP_UP if (np.isfinite(prev[i]) and c[i] >= prev[i]) else RCP_DN
            for i in range(nv)]
    for i in range(nv):
        ax.plot([i, i], [l[i], h[i]], color=cols[i], lw=0.8, zorder=2)
        ax.add_patch(Rectangle((i - 0.32, min(o[i], c[i])), 0.64,
                               max(abs(c[i] - o[i]), 1e-9),
                               facecolor=cols[i], edgecolor=cols[i], lw=0.5, zorder=3))
    ma20 = view["close"].astype(float).rolling(20).mean().values
    if np.isfinite(ma20).any():
        ax.plot(x, ma20, color=RCP_MA, lw=1.2, ls="--", alpha=0.9, zorder=4,
                label="MA20")
        ax.legend(loc="upper left", fontsize=f_tick, framealpha=0.85)
    ax.set_ylabel("日线K线", fontsize=f_y - 1)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.5)
    ax.tick_params(labelsize=f_tick)
    # 月份竖线（与 R_cascade 图同口径）
    ticks, prev_m = [], None
    for k, dt in enumerate(view["dt"]):
        if dt.month != prev_m:
            ticks.append(k)
            prev_m = dt.month
    for t in ticks:
        ax.axvline(t, color="#7c8794", lw=0.7, ls="--", alpha=0.35, zorder=1)
    # 现价（贴右侧）
    if nv:
        ax.annotate("%.2f" % c[-1], xy=(nv - 1, c[-1]),
                    xytext=(4, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=f_tick,
                    color=cols[-1], fontweight="bold")


# ── MF-DFA Δα 叠加（双坐标轴，深红粗线）────────────────────────────
DA_LINE = "#8b0000"          # 深红（dark red）
DA_LINE_W = 3.4              # 粗线
VOL_VIX = "#1d4ed8"          # VIX 蓝
VOL_SKEW = "#b45309"         # 偏度 琥珀
VOL_YLIM = (0.0, 100.0)      # 左轴固定范围：偏度**近一年分位** 0~100（VIX 20~40 落在同一轴上）
SKEW_PCT_LINES = ((50, ":", "#94a3b8", "分位50"),
                  (80, "--", "#c2410c", "80 偏高"),
                  (95, "--", "#991b1b", "95 极危"))
DA_YLIM = (0.0, 2.0)         # 右轴**默认/兜底**范围（自适应，见 _paint_vol_panel）
DA_THR_PCT = 90              # Δα 告警分位：窗口内 90 分位画阈值线（与 rmt_rc 告警同口径）
PCR_LINE = "#6d28d9"         # 持仓量 PCR 主线（紫）
PCR_FILL = "#8b5cf6"         # 持仓量 PCR 淡填充


def _vol_series_for(name):
    """取该指数的 VIX / 偏度序列（唯一实现：misc/engine/opt_vol.py）。失败返回 None。"""
    try:
        _ENG = os.path.join(WS, "misc", "engine")
        if _ENG not in sys.path:
            sys.path.insert(0, _ENG)
        import opt_vol as OV
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] opt_vol 不可用：%s" % str(e)[:100], flush=True)
        return None
    try:
        df = OV.series_for(name)
        if df is None or not len(df):
            return None
        cols = [c for c in ("vix", "skew", "skew_pct", "atm_iv") if c in df.columns]
        out = df[cols].copy()
        if "atm_iv" in out.columns and "iv" not in out.columns:
            out["iv"] = out["atm_iv"]
        return out
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] %s VIX/偏度取数失败：%s" % (name, str(e)[:100]), flush=True)
        return None


def _da_series_for(name, code=None, df=None):
    """取该指数的 MF-DFA 滚动 Δα 序列。

    Δα **唯一实现**在 charts/phase_mfdfa.rolling_delta_alpha（N=250，多分形谱宽）；
    日线数据走 misc/engine/rcascade.py::load()（**只取数，不算 RC**）。
    优先复用已传入的日线 df，拿不到再去取。
    返回 DataFrame(index=YYYYMMDD str, col "da")；取不到返回 None（画图不能因此炸）。
    """
    try:
        from charts.phase_mfdfa import rolling_delta_alpha
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] phase_mfdfa 不可用：%s" % str(e)[:100], flush=True)
        return None

    close = None
    if df is not None and "close" in getattr(df, "columns", []):
        _dtc = "dt" if "dt" in df.columns else df.columns[0]
        try:
            close = pd.Series(df["close"].astype(float).values,
                              index=pd.to_datetime(df[_dtc].values))
        except Exception:                                        # noqa: BLE001
            close = None
    if close is None:
        try:
            _ENG = os.path.join(WS, "misc", "engine")
            if _ENG not in sys.path:
                sys.path.insert(0, _ENG)
            import rcascade as RC
        except Exception as e:                                   # noqa: BLE001
            print("[WARN] rcascade 不可用：%s" % str(e)[:100], flush=True)
            return None
        typ = "index_sz" if str(code or "").upper().endswith(".SZ") else "index_sh"
        num = str(code or "").split(".")[0]
        try:
            d, _src = RC.load(typ, num)
            if d is None or not len(d):
                return None
            close = d.set_index("dt")["close"].astype(float)
        except Exception as e:                                   # noqa: BLE001
            print("[WARN] %s Δα 日线取数失败：%s" % (name, str(e)[:100]), flush=True)
            return None
    try:
        da = rolling_delta_alpha(close, N=250)
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] %s Δα 计算失败：%s" % (name, str(e)[:100]), flush=True)
        return None
    if da is None or not len(da):
        return None
    out = pd.DataFrame({"da": da.astype(float)})
    out.index = [x.strftime("%Y%m%d") for x in pd.to_datetime(da.index)]
    return out


def _da_alert_item(name, da_df, threshold=DA_THR_PCT):
    """单指数 Δα 告警项：当前值 / 窗内 P-threshold / 当前分位 / 是否触发。

    与 charts/rmt_rc.py::build_alerts 同口径：**当前值在自身历史里的百分位 > 阈值**即告警。
    """
    if da_df is None or "da" not in getattr(da_df, "columns", []):
        return None
    s = pd.to_numeric(pd.Series(da_df["da"]), errors="coerce").dropna()
    if len(s) < 30:
        return None
    vals = s.values.astype(float)
    last = float(vals[-1])
    pct = float((vals <= last).mean() * 100.0)
    return dict(target=name, last=last,
                thr=float(np.percentile(vals, threshold)),
                pct=pct, hit=(pct > threshold))


def send_da_alert_email(items, chart=None, threshold=DA_THR_PCT, to=None, dry_run=False):
    """MF-DFA Δα 分位告警邮件（走库内统一 `misc/core/mailer.py`，可带图）。

    复用本机既有 **163 → QQ** 发信账号（mailer 在未显式配置时会自动回落，
    口令只从运行时读取，**不落库、不打印**）。发不出去只返回 False，不抛异常。
    """
    items = [x for x in (items or []) if x]
    hit = [x for x in items if x["hit"]]
    if not items:
        print("[alert] 无 Δα 数据，不发邮件", flush=True)
        return False
    if not hit:
        print("[alert] Δα 均未超 P%.0f，不发邮件" % threshold, flush=True)
        return False
    try:
        _CORE = os.path.join(WS, "misc", "core")
        if _CORE not in sys.path:
            sys.path.insert(0, _CORE)
        import mailer  # noqa: PLC0415
    except Exception as e:                                       # noqa: BLE001
        print("[alert] mailer 不可用，只打印告警：%s" % str(e)[:120], flush=True)
        return False

    lines = ["## 缠论分型版 · MF-DFA Δα 分位告警（阈值 P%.0f）" % threshold, "",
             "触发 %d 个（当前 Δα 处于自身历史高位）：" % len(hit), ""]
    for x in sorted(hit, key=lambda z: -z["pct"]):
        lines.append("- **%s** Δα=%.3f，处 %.0f%% 分位（P%.0f=%.3f）"
                     % (x["target"], x["last"], x["pct"], threshold, x["thr"]))
    lines += ["", "---", "", "### 全部读数", "",
              "| 标的 | Δα | 当前分位 | P%.0f |" % threshold, "|---|---|---|---|"]
    for x in sorted(items, key=lambda z: -z["pct"]):
        lines.append("| %s | %.3f | %.0f%% | %.3f |%s"
                     % (x["target"], x["last"], x["pct"], x["thr"],
                        " 🔴" if x["hit"] else ""))
    lines += ["", "> Δα = MF-DFA 多分形谱宽（N=250，scales 20/30/50/80/125，q=-4..4）；"
                  "分位 = 当前值在自身全样本中的百分位。"]

    subject = "🚨 MF-DFA Δα 分位告警 · %s · %d 项超 P%.0f" % (
        datetime.now().strftime("%Y-%m-%d"), len(hit), threshold)
    ok = mailer.send(subject, "\n".join(lines), to=to,
                     attach=chart if (chart and os.path.exists(chart)) else None,
                     dry_run=dry_run)
    print("[alert] mail %s" % ("ok" if ok else "fail"), flush=True)
    return ok


def _paint_vol_panel(ax, view, nv, da_series, vol_series, f_tick, f_y,
                     title_note="", vol_mode="vix"):
    """副图：MF-DFA Δα / 偏度 / VIX 三条线，**双 Y 轴**。

    2026-11-24：原「右轴 R_cascade」按大佬要求替换为 **MF-DFA 滚动 Δα**
    （多分形谱宽，唯一实现 charts/phase_mfdfa，N=250）。

    · 左轴 **固定 0~100**：偏度 **SKEW 指数**（BKM 严格口径：100 − 10×RNSkew，
                        固定带宽 0.85S~1.15S、≥8 档 OTM，档位不够为 NA）
                        取**近一年滚动分位** + VIX（30D ATM IV 插值）——同量纲共轴
    · 右轴（自适应）    ：Δα 多分形谱宽 —— 深红粗线，单独一根轴；
                        窗内 **90 分位** 画告警阈值线（与 rmt_rc 告警同口径）

    `with_macd`：副图带不带 MACD（默认 False）。
      False → 期权（VIX+偏度）+ MF-DFA Δα
      True  → MACD(10,20,5)
    返回最后一天的三元组，供标题/日志使用。
    """
    dates = [str(x) for x in view["dt"].dt.strftime("%Y%m%d").values]
    x = np.arange(nv)

    def _align(df, col):
        if df is None or not len(df) or col not in getattr(df, "columns", []):
            return np.full(nv, np.nan)
        ser = df[col]
        idx = [str(i) for i in ser.index]
        ser.index = idx
        out = ser.reindex(ser.index.union(pd.Index(dates))).ffill().reindex(dates)
        return out.values.astype(float)

    # 2026-11-16 大佬：「就是 VIX，如果你不好算的话，你可以把那个变成隐波」
    # → 支持 --vol-mode iv 用近月 ATM 隐含波动率代替 VIX（VIX 本身已能算，默认仍用 VIX）
    _col = "iv" if vol_mode == "iv" else "vix"
    vix = _align(vol_series, _col)
    if not np.isfinite(vix).any() and _col != "vix":
        vix = _align(vol_series, "vix")          # 隐波缺列时回退 VIX
    skew_pct = _align(vol_series, "skew_pct")
    da = _align(da_series, "da")

    # ── 左轴：**偏度近一年分位 0~100** + VIX ────────────────────────
    # 2026-11-17 大佬：「偏度的那个图你改一下子，因为我看出来都是 100 左右，
    #   变化不大。你偏度图能不能变成近一年的分位数？」
    # → 左轴改成 0~100 的**滚动分位**（252 交易日窗口，唯一实现在 opt_vol.py）。
    #   偏度原始值（100−10×RNSkew）只用来算分位，图上不再画绝对值。
    ax.plot(x, skew_pct, color=VOL_SKEW, lw=2.2,
            label="偏度分位（近一年 252 日滚动，0~100）", zorder=6)
    _vlabel = ("隐波 ATM IV（近月平值）" if vol_mode == "iv"
               else "VIX（30D，OI加权方差插值）")
    ax.plot(x, vix, color=VOL_VIX, lw=1.7, ls="--", label=_vlabel, zorder=5)
    # 关键刻度线：分位 50 中位 / 80 偏高 / 95 极危；VIX 20 / 40
    for yv, ls, col, lab in SKEW_PCT_LINES + ((20, ":", "#93c5fd", "VIX20"),
                                              (40, ":", "#3b82f6", "VIX40")):
        ax.axhline(yv, color=col, ls=ls, lw=0.9, alpha=0.55, zorder=1)
        # 标签放绘图区**内侧右端**：原来放 x=nv+6（画布外）会被右轴/R_cascade 压掉。
        # ⚠️ 必须用 blended 变换：x=轴比例(0.994)，y=**数据坐标**(0~100)；
        #    直接 transAxes 会把 y 当比例 → 跑到画布外（audit 实测报错）。
        ax.text(0.994, yv, lab, fontsize=f_tick - 2.5, color=col,
                alpha=0.9, va="center", ha="right", zorder=11,
                transform=blended_transform_factory(ax.transAxes, ax.transData))
    ax.set_ylim(*VOL_YLIM)          # (0, 100)
    ax.set_ylabel("偏度分位（近一年，0~100）／ VIX", fontsize=f_y - 2,
                  color=VOL_SKEW)
    ax.tick_params(axis="y", colors=VOL_SKEW, labelsize=f_tick - 0.5)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.45)
    ax.tick_params(labelsize=f_tick)

    # ── 右轴：MF-DFA 滚动 Δα（多分形谱宽，深红粗线）──
    # 2026-11-24 大佬：「R_cascade 那个位置替换成带 MFDFA 的」→ 右轴改画 Δα；
    #   口径唯一实现 charts/phase_mfdfa.rolling_delta_alpha(N=250)，
    #   阈值线与 charts/rmt_rc.py build_alerts 同口径（窗内 90 分位）。
    ax2 = ax.twinx()
    _da_axis = DA_YLIM
    _da_fin = da[np.isfinite(da)] if len(da) else np.array([])
    if _da_fin.size:
        _lo = float(np.min(_da_fin))
        _hi = float(np.max(_da_fin))
        _pad = max((_hi - _lo) * 0.08, 0.05)
        _da_axis = (max(0.0, _lo - _pad), max(_hi + _pad, _lo + 0.10))
        ax2.set_ylim(*_da_axis)
        ax2.plot(x, da, color=DA_LINE, lw=DA_LINE_W, alpha=0.92, zorder=9,
                 solid_capstyle="round",
                 label="MF-DFA Δα（右轴 %.2f~%.2f）" % _da_axis)
        # 90 分位告警阈值（与 charts/rmt_rc.py 的 90% 分位告警同口径）
        _da90 = float(np.percentile(_da_fin, DA_THR_PCT))
        _y0, _y1 = _da_axis
        _span = max(_y1 - _y0, 1e-6)
        if _y0 < _da90 < _y1:
            ax2.axhline(_da90, color=DA_LINE, ls="--", lw=0.9, alpha=0.4, zorder=1)
            # 标签放**绘图区内左侧**，靠近边界时翻到另一侧，避免出画布
            _va = "bottom" if (_da90 - _y0) / _span < 0.06 else "top"
            ax2.text(0.006, _da90, "Δα P%.0f %.2f" % (DA_THR_PCT, _da90),
                     fontsize=f_tick - 2, color=DA_LINE, alpha=0.8,
                     va=_va, ha="left", zorder=11,
                     transform=blended_transform_factory(ax2.transAxes, ax2.transData))
    ax2.set_ylabel("MF-DFA Δα（%.2f~%.2f）" % _da_axis if _da_fin.size
                   else "MF-DFA Δα", fontsize=f_y - 2, color=DA_LINE)
    ax2.tick_params(axis="y", colors=DA_LINE, labelsize=f_tick - 0.5)
    ax2.spines["right"].set_color(DA_LINE)
    ax2.spines["right"].set_linewidth(1.6)

    # 图例合并（左轴两条 + 右轴一条）
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=f_tick - 1,
              framealpha=0.85, ncol=3)

    # 横轴刻度
    _xlabels = view["dt"].dt.strftime("%m-%d").values
    step = max(nv // 12, 1)
    idxs = list(range(0, nv, step))
    ax.set_xticks(idxs)
    ax.set_xticklabels([_xlabels[i] for i in idxs], fontsize=f_tick)
    ax.set_xlim(-2, nv + 7)
    for t in idxs:
        ax.axvline(t, color="#7c8794", lw=0.7, ls="--", alpha=0.22, zorder=0)

    def _last(arr):
        a = arr[np.isfinite(arr)]
        return float(a[-1]) if len(a) else float("nan")
    return _last(vix), _last(skew_pct), _last(da)


def _audit_stack(fig):
    """版面自检：报「文字 bbox 与任何坐标轴区域重叠」以及「出画布」的文字。

    2026-11-14 大佬：「这个图和那个字都重合了」→ 加一个可复现的检查，
    不再靠肉眼。CHAN_AUDIT=1 时生效，只打印不抛异常。
    """
    try:
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        axes_boxes = []
        for ax in fig.axes:
            try:
                axes_boxes.append((ax, ax.get_window_extent(r)))
            except Exception:
                pass
        bad = 0
        fig_box = fig.bbox
        for ax in fig.axes:
            texts = [ax.title] if ax.title.get_text() else []
            # 2026-11-17 大佬：「图右侧的那个坐标我看不到了」
            #   → 轴标题/刻度标签也必须纳入「出画布」检查，否则右轴 ylabel 被裁了也报 0 处
            texts += [ax.xaxis.label, ax.yaxis.label]
            texts += [t for t in ax.texts]
            try:
                # 只把 y 轴刻度纳入**出画布**检查（右侧被裁就是这个）；
                # x 轴刻度不纳入重叠检查，否则和下方子图贴边会误报
                texts += list(ax.get_yticklabels())
            except Exception:
                pass
            for t in texts:
                if not t.get_text().strip():
                    continue
                try:
                    tb = t.get_window_extent(r)
                except Exception:
                    continue
                if (tb.x0 < fig_box.x0 - 2 or tb.x1 > fig_box.x1 + 2
                        or tb.y0 < fig_box.y0 - 2 or tb.y1 > fig_box.y1 + 2):
                    print("[audit] 出画布：%r" % t.get_text()[:40], flush=True)
                    bad += 1
                    continue
                for other, ob in axes_boxes:
                    if other is ax:
                        continue
                    # 孪生轴（twinx/twiny 共用同一块画布）不算「压到别的子图」
                    if (abs(ob.x0 - ax.get_window_extent(r).x0) < 2
                            and abs(ob.y0 - ax.get_window_extent(r).y0) < 2):
                        continue
                    ov = (min(tb.x1, ob.x1) - max(tb.x0, ob.x0)) > 4 and \
                         (min(tb.y1, ob.y1) - max(tb.y0, ob.y0)) > 4
                    if ov:
                        print("[audit] 文字压到别的子图：%r" % t.get_text()[:40], flush=True)
                        bad += 1
                        break
        # 图级文本（suptitle / 口径说明）的出画布检查：2026-11-17 实测
        # 「口径说明」曾宽 2765px > 画布 1200px，左右各被裁 780px，旧 audit 不报
        for t in ([fig._suptitle] if fig._suptitle else []) + list(fig.texts):
            if not t.get_text().strip():
                continue
            try:
                tb = t.get_window_extent(r)
            except Exception:
                continue
            if (tb.x0 < fig_box.x0 - 2 or tb.x1 > fig_box.x1 + 2
                    or tb.y0 < fig_box.y0 - 2 or tb.y1 > fig_box.y1 + 2):
                print("[audit] 图级文字出画布：%r" % t.get_text()[:40], flush=True)
                bad += 1
        # 子图主标题（ax.title）出画布检查：2026-11-17 实测标题宽 1854px > 画布 1800px
        # 右侧 165px（「背驰：无」）被裁，旧 audit 完全没报
        for ax in fig.axes:
            t = ax.title
            if not t.get_text().strip():
                continue
            try:
                tb = t.get_window_extent(r)
            except Exception:
                continue
            if tb.x0 < fig_box.x0 - 2 or tb.x1 > fig_box.x1 + 2:
                print("[audit] 子图标题出画布：%r" % t.get_text()[:40], flush=True)
                bad += 1
        # 文字 vs 文字（标题/口径说明会互相压）
        all_t = []
        for ax in fig.axes:
            if ax.title.get_text().strip():
                all_t.append(ax.title)
            all_t += [t for t in ax.texts if t.get_text().strip()]
        if fig._suptitle and fig._suptitle not in fig.texts:
            all_t.append(fig._suptitle)
        all_t += [t for t in fig.texts if t.get_text().strip()]
        boxes = []
        for t in all_t:
            try:
                boxes.append((t, t.get_window_extent(r)))
            except Exception:
                pass
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (t1, b1), (t2, b2) = boxes[i], boxes[j]
                if (min(b1.x1, b2.x1) - max(b1.x0, b2.x0)) > 4 and \
                   (min(b1.y1, b2.y1) - max(b1.y0, b2.y0)) > 4:
                    print("[audit] 文字互相重叠：%r ↔ %r"
                          % (t1.get_text()[:24], t2.get_text()[:24]), flush=True)
                    bad += 1
        # 标题 vs 子图
        for t in [fig._suptitle] if fig._suptitle else []:
            try:
                tb = t.get_window_extent(r)
            except Exception:
                continue
            for other, ob in axes_boxes:
                if (min(tb.x1, ob.x1) - max(tb.x0, ob.x0)) > 4 and \
                   (min(tb.y1, ob.y1) - max(tb.y0, ob.y0)) > 4:
                    print("[audit] 总标题压到子图", flush=True)
                    bad += 1
                    break
        print("[audit] 重叠/溢出 %d 处" % bad, flush=True)
    except Exception as e:
        print("[audit] 自检失败:", str(e)[:150], flush=True)


def _pcr_oi_series_for(name):
    """持仓量 PCR 序列（唯一实现：/root/.openclaw/workspace/bin/opt_pcr_oi.py）。

    返回 DataFrame(index=YYYYMMDD str, col "PCR_OI")；取不到返回 None。
    索引名 "创业板指" 等别名由 opt_pcr_oi.ALIAS 处理。
    """
    _ws = os.environ.get("PCR_OI_WS", "/root/.openclaw/workspace")
    _b = os.path.join(_ws, "bin")
    try:
        if _b not in sys.path:
            sys.path.insert(0, _b)
        import opt_pcr_oi as PO  # noqa: PLC0415
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] opt_pcr_oi 不可用：%s" % str(e)[:100], flush=True)
        return None
    try:
        seq = PO.pcr_series_for_index(name)
        if not seq:
            return None
        s = pd.Series([v for _d, v in seq],
                      index=[str(d) for d, _v in seq], dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        return pd.DataFrame({"PCR_OI": s})
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] %s 持仓量PCR取数失败：%s" % (name, str(e)[:100]), flush=True)
        return None


def draw_stack(pairs, out=None, n_show=160, row_h=7.8, width=12.0, dpi=150,
               with_da=True, sub=None, with_macd=False, vol_mode="vix",
               with_pcr=True, alert_sink=None, alert_thr=DA_THR_PCT):
    """多指数**纵向堆叠**：一个指数两块（上=缠论价格 / 下=VIX+偏度+MF-DFA Δα 双轴），往下垒。

    大佬 2026-11-14 23:13：「我就不明白，你就把这几个子图粘到一块就行了，为什么要压缩呀？
    …你要能力不行，还是恢复到以前，一个指数一个图吧。」

    → 上一版是 5 列并排（figsize 10.2*ncol × 11.2 英寸），每列被压成约 11:1 的窄条，
      字和图挤在一起。本函数改成「宽度不变、往下垒」：
      画布宽 width 英寸固定，每指数占 row_h 英寸高，单块内 价格:副图 = 2.9:1.35，
      子图宽高比 ≈ 1.5:1（接近单图 16×10 的观感），字号按单图比例放大。

    口径与 draw() 完全一致（同一 chan_geometry / 买卖点样式）。

    两层结构（2026-11-16 最终定稿）：
      ① 上：**原来的缠论图**（笔 / 中枢 ZG-ZD / 买卖点 / VWAP ±1σ2σ3σ），不加任何叠加线
      ② 下：**VIX + 偏度 + MF-DFA Δα 三条线，双 Y 轴**
             左轴（波动率点）：VIX（30D ATM IV 插值）、偏度（BKM 严格口径 SKEW 指数）
             右轴（多分形）  ：MF-DFA 滚动 Δα（谱宽，N=250，深红粗线；附 90 分位告警线）
    大佬原话：「算了算了，你不要这么整了，还是原来的是原来的，底下那坐标轴…
    还是双 Y 轴，一个是卡斯卡的，一个是偏度，一个是那个 VIX，就这仨在一块。
    就是，还是不要 MACD 了」
    """
    nrow = len(pairs)
    # 2026-11-16 大佬：「如果你这个 PCR 是持仓量 PCR，那你在这个子图上再画一下子」
    # → 每个指数再叠一块**很薄的第三块**：持仓量 PCR（dte≤5 剔除口径）
    fig = plt.figure(figsize=(width, row_h * nrow), facecolor="white")
    # 顶部留 1.15 英寸给「大标题 + 口径说明」，避免标题压第一行子图
    _h = row_h * nrow
    # 2026-11-17 大佬：「这个图右侧的那个坐标我看不到了，你把右侧的空白留大点」
    #   → right 0.988→0.925：右轴刻度数字 + 「R_cascade（0~2）」竖排标题实测要
    #     约 0.9 英寸，0.945 时标题右缘只剩 ~10px（几乎贴边），0.925 后留 ~55px。
    gs = fig.add_gridspec(nrow, 1, hspace=0.30,
                          left=0.062, right=0.925,
                          top=1 - 1.15 / _h,
                          bottom=0.030)
    globals()["_VOL_LAST"] = {}
    # 2026-11-16 大佬：「有个参数要传进去，就是带不带对应的 MACD。现在我就不需要你带了，
    # 也只要带期权和那个 CASC 的了」
    #   with_macd=False（默认）→ 副图 = 期权（VIX+偏度）+ MF-DFA Δα
    #   with_macd=True          → 副图 = MACD(10,20,5)
    #   sub 显式给值时优先（向后兼容 --sub kline 等）
    sub_mode = sub if sub else ("macd" if with_macd else "vol")
    vol_map, pcr_map = {}, {}
    for _n, _c in pairs:
        if sub_mode == "vol":
            _v = _vol_series_for(_n)
            if _v is not None:
                vol_map[_n] = _v
        if with_pcr:
            _p = _pcr_oi_series_for(_n)
            if _p is not None:
                pcr_map[_n] = _p

    for ri, (name, code) in enumerate(pairs):
        df = _index_day(code)
        if df is None or len(df) < 60:
            raise RuntimeError("%s(%s) 数据不足" % (name, code))
        # Δα 用**本行已取好的日线**现算（N=250，唯一实现 charts/phase_mfdfa），
        # 不再单独回源，避免重复取数；取不到就置 None，副图只画期权两条线。
        _da = (_da_series_for(name, code, df=df)
               if (with_da and sub_mode == "vol") else None)
        if alert_sink is not None and _da is not None:
            _item = _da_alert_item(name, _da, threshold=alert_thr)
            if _item is not None:
                alert_sink.append(_item)
        total = len(df)
        start = max(0, total - n_show)
        nv = min(n_show, total)
        geo = chan_geometry(df)
        ck = geo["ck"]
        zs = geo["zs"]
        P = float(df["close"].iloc[-1])
        last_ts = df["dt"].iloc[-1]
        _has_pcr = pcr_map.get(name) is not None
        if _has_pcr:
            sub = gs[ri, 0].subgridspec(3, 1, height_ratios=[2.9, 1.25, 0.72],
                                        hspace=0.10)
        else:
            sub = gs[ri, 0].subgridspec(2, 1, height_ratios=[2.9, 1.35], hspace=0.09)
        ax = fig.add_subplot(sub[0, 0])
        axm = fig.add_subplot(sub[1, 0], sharex=ax)
        axp = fig.add_subplot(sub[2, 0], sharex=ax) if _has_pcr else None
        _paint_row(fig, ax, axm, name, code, df, start, nv, geo, ck, zs, P, last_ts,
                   sub=sub_mode, da_series=_da,
                   vol_series=vol_map.get(name), vol_mode=vol_mode,
                   pcr_ax=axp, pcr_series=pcr_map.get(name))
    fig.suptitle("缠论·分型版 · 五大宽基日线（每指数一行）｜下：VIX／偏度分位／MF-DFA Δα 双轴 + 持仓量PCR",
                 fontsize=19,
                 y=1 - 0.42 / _h, va="center")
    # 2026-11-17：口径说明原来一整行长文本，实测宽 2765px > 画布 1200px，
    #   **左右两端各被裁掉 780px**（右侧坐标/文字「看不到」有一部分是它顶出来的）
    #   → 拆成两行、字号降到 13，确保整句落在画布内。
    fig.text(0.5, 1 - 0.80 / _h,
             "口径：分型→笔→中枢(ZG/ZD)→背驰　｜　★买点 ▼卖点　｜　"
             "黄框=中枢　蓝线=笔　紫线=VWAP　虚线=±1σ/2σ/3σ",
             ha="center", va="center", fontsize=13.5, color="#444444")
    fig.text(0.5, 1 - 0.99 / _h,
             "中：蓝=VIX(30D)　琥珀=偏度近一年分位(252日滚动)　深红=MF-DFA Δα(右轴，虚线=P90)　｜　"
             "底：持仓量PCR(剔≤5天，1.0 上方=认沽占优)",
             ha="center", va="center", fontsize=13.5, color="#444444")
    if os.environ.get("CHAN_AUDIT"):
        _audit_stack(fig)
    os.makedirs(OUT_DIR, exist_ok=True)
    if not out:
        d8 = datetime.now().strftime("%Y%m%d")
        out = os.path.join(OUT_DIR, "chan_stack_%s.png" % d8)
    fig.savefig(out, dpi=dpi, facecolor="white")
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
    ap.add_argument("--stack", default=None,
                    help="多指数**纵向堆叠**（一指数一行，不压缩宽度）：逗号分隔名称或代码")
    ap.add_argument("--stack-n", type=int, default=160,
                    help="堆叠图显示最近多少根K线")
    ap.add_argument("--row-h", type=float, default=7.6, help="堆叠图每行高度（英寸）")
    ap.add_argument("--alert-email", action="store_true",
                    help="堆叠图出完后：Δα 分位超阈值的指数发预警邮件（带图；走 163→QQ）")
    ap.add_argument("--alert-threshold", type=float, default=90.0,
                    help="Δα 分位告警阈值（默认 90）")
    ap.add_argument("--mail-to", default=None,
                    help="收件人（默认走 mailer 配置，本机=工作区 163→QQ 账号）")
    ap.add_argument("--email-dry-run", action="store_true",
                    help="配合 --alert-email：只打印（脱敏）不真发")
    ap.add_argument("--no-da", "--no-rc", dest="with_da", action="store_false",
                    help="副图不画 MF-DFA Δα（--no-rc 是旧名，保留兼容）")
    ap.set_defaults(with_da=True)
    ap.add_argument("--sub", default=None, choices=["vol", "macd", "kline"],
                    help="副图内容（一般不用给）：vol=VIX/偏度/MF-DFA Δα（默认）；macd；kline")
    ap.add_argument("--vol-mode", default="vix", choices=["vix", "iv"],
                    help="副图波动率用 VIX（默认）还是窄月 ATM 隐波")
    ap.add_argument("--pcr", dest="with_pcr", action="store_true",
                    help="堆叠图再叠一块薄面板：持仓量 PCR（默认开）")
    ap.add_argument("--no-pcr", dest="with_pcr", action="store_false",
                    help="不出持仓量 PCR 薄面板")
    ap.set_defaults(with_pcr=True)
    ap.add_argument("--macd", dest="with_macd", action="store_true",
                    help="副图带 MACD（默认不带，带期权+MF-DFA Δα）")
    ap.add_argument("--no-macd", dest="with_macd", action="store_false",
                    help="副图不带 MACD（默认，带期权+MF-DFA Δα）")
    ap.set_defaults(with_macd=False)
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

    if a.stack:
        pairs = []
        for seg in a.stack.split(","):
            seg = seg.strip()
            if not seg:
                continue
            r = _resolve_index(seg)
            if r is None:
                print("[ERR] 无法识别的指数：%s" % seg, flush=True)
                return 2
            pairs.append(r)
        if not pairs:
            print("[ERR] --stack 至少需要一个指数", flush=True)
            return 2
        _sink = []
        p = draw_stack(pairs, a.out, a.stack_n, row_h=a.row_h,
                       with_da=a.with_da, sub=a.sub,
                       with_macd=a.with_macd, vol_mode=a.vol_mode,
                       with_pcr=a.with_pcr,
                       alert_sink=_sink, alert_thr=a.alert_threshold)
        print("[OK]", p)
        if a.alert_email:
            send_da_alert_email(_sink, chart=p, threshold=a.alert_threshold,
                                to=a.mail_to, dry_run=a.email_dry_run)
        return 0

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
