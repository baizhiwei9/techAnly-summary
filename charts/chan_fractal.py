#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论·分型版 K线图生成器（mplfinance + 缠论示意图）—— 画图模块
=====================================================================
原 chanlun/fractal/chan_chart.py 的画图部分，统一收进 charts/（2026-11-14）。

把「某一级别的缠论买卖点」画成图片：K线 + 笔 + 中枢矩形 + 买点/卖点 + 副图。

副图按级别区分（2026-11-16 大佬澄清：「小时级别和周线级别你还是用那个偏向的 MACD，
我只是说你日线级别给我出图那会改一下子」）：
  · 日线       → 该指数日线 K 线（K线+MA20），叠 RMT 结构层（60 日滚动窗口）
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

# VWAP 标准差带（2026-11-15 大佬：「缠论的那图都要带上那个 VWAP，
# 我好像知道它到了几倍的标准差了」→ 图上直接标 σ 倍数）
# 计算源唯一：smc/smc_core.py:vwap_tracks()（滚动 win 根、成交量加权、返回 vwap+sigma）
try:
    from smc_core import vwap_tracks as _vwap_tracks  # noqa: E402
except Exception:                                     # 画图不应因 VWAP 缺失而整体失败
    _vwap_tracks = None

# RMT 指标（2026-11-16 大佬：第三层要「RMT 矩阵那套」——λ 比值 / 能量比 / RC）
# 计算口径唯一在 charts/rmt_rc.py；这里只取序列来画，不另算一套。
try:
    from charts import rmt_rc as _RMT
except Exception:                                    # 画图不应因 RMT 缺失整体失败
    try:
        import rmt_rc as _RMT                     # 直接以 charts/ 为 sys.path 时
    except Exception:
        _RMT = None

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


# ─────────── 多指数纵向堆叠（一指数一行，不压缩宽度） ───────────
def _paint_row(fig, ax, axm, name, code, df, start, nv, geo, ck, zs, P, last_ts,
               fs=None, sub="vol", rc_series=None, vol_series=None,
               rc_ax=True, xtick_every=10):
    """在给定 axes 上画一个完整指数的一段行情（价格面板 + 副图）。

    `sub` 决定副图画什么：
      · "vol"（默认）  ：VIX + 偏度（左轴）／R_cascade（右轴）三条线，双 Y 轴
      · "macd"        ：MACD(10,20,5)
      · "kline"       ：日线 K 线图（K线+MA20）

    `rc_series`：R_cascade（列 RC）；`vol_series`：期权波动率（列 vix/skew）。
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
    ax.set_title("%s（%s）· %s   收盘 %.2f   ｜   阶段：%s   ｜   %s   ｜   背驰：%s%s"
                 % (name, code, ts_txt, P, ck.get("stage", ""), zpos,
                    ck.get("div") or "无", _sig_txt),
                 fontsize=f_title, pad=9, loc="left")
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


    # ── 副图（2026-11-16 最终定稿，大佬两次改口后的结论）─────────────
    # 「底下那坐标轴…还是双 Y 轴，一个是卡斯卡的，一个是偏度，一个是那个 VIX，
    #   就这仨在一块。就是，还是不要 MACD 了」
    #   · "vol"  ← 日报用这个：VIX + 偏度（左轴）／R_cascade（右轴，深红粗线）
    #   · "macd" ：MACD(10,20,5)（保留能力，--sub macd 才用）
    #   · "kline"：日线 K 线（更早的临时方案，保留能力）
    if sub == "vol":
        _v, _s, _r = _paint_vol_panel(axm, view, nv, rc_series, vol_series,
                                      f_tick, f_y)
        globals().setdefault("_VOL_LAST", {})[name] = {"vix": _v, "skew": _s, "rc": _r}
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


# ── R_cascade 叠加（双坐标轴，深红粗线）────────────────────────────
RC_LINE = "#8b0000"          # 深红（dark red）
RC_LINE_W = 3.4              # 粗线
VOL_VIX = "#1d4ed8"          # VIX 蓝
VOL_SKEW = "#b45309"         # 偏度 琥珀
VOL_YLIM = (0.0, 130.0)      # 左轴固定范围（偏度 SKEW 100 中轴 + VIX 20~40）
RC_YLIM = (0.0, 2.0)         # 右轴固定范围（R_cascade 0.75~1.2 阈值都装得下）


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
        return df[["vix", "skew"]]
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] %s VIX/偏度取数失败：%s" % (name, str(e)[:100]), flush=True)
        return None


def _rc_series_for(name, code=None, df=None):
    """取该指数的 R_cascade 序列（唯一实现在 misc/engine/rcascade.py）。

    返回 DataFrame(index=YYYYMMDD str, col "RC")；取不到返回 None（画图不能因此炸）。
    """
    try:
        _ENG = os.path.join(WS, "misc", "engine")
        if _ENG not in sys.path:
            sys.path.insert(0, _ENG)
        import rcascade as RC
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] rcascade 不可用：%s" % str(e)[:100], flush=True)
        return None
    typ = "index_sz" if str(code or "").upper().endswith(".SZ") else "index_sh"
    num = str(code or "").split(".")[0]
    try:
        d = RC.compute(typ, num)
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] %s R_cascade 取数失败：%s" % (name, str(e)[:100]), flush=True)
        return None
    out = pd.DataFrame({"RC": d["RC"].astype(float)})
    out.index = [str(x).replace("-", "")[:8] for x in d.index]
    return out


def _paint_vol_panel(ax, view, nv, rc_series, vol_series, f_tick, f_y, title_note=""):
    """副图：R_cascade / 偏度 / VIX 三条线，**双 Y 轴**（2026-11-16 终稿）。

    大佬原话：「底下那坐标轴…还是双 Y 轴，一个是卡斯卡的，一个是偏度，
    一个是那个 VIX，就这仨在一块。就是，还是不要 MACD 了」

    · 左轴 **固定 0~130**：偏度 **SKEW 指数**（100 中轴，>110 危险、>120 极危）
                        + VIX（30D ATM IV 插值，20~40 区间）——同量纲共轴
    · 右轴 **固定 0~2**  ：R_cascade（std20/std60）—— 深红粗线，单独一根轴
    大佬 2026-11-16：「左坐标轴 1~120 左右（偏度>110 危险）…VIX 在 20~40 之间…
    卡斯卡的是 0.5~1.5，右坐标轴 0~2 差不多就能显示出来」
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

    vix = _align(vol_series, "vix")
    skew = _align(vol_series, "skew")
    rc = _align(rc_series, "RC")

    # ── 左轴：VIX + 偏度（**固定 0~130**，大佬 2026-11-16 指定）─────────
    # 「左坐标轴是 1~120 左右，因为偏度大于 110 就是危险了…VIX 是 20 到 40 之间…
    #   这样应该也能显示」
    ax.plot(x, skew, color=VOL_SKEW, lw=2.0,
            label="偏度 SKEW 指数（100 中轴，>110 危险）", zorder=6)
    ax.plot(x, vix, color=VOL_VIX, lw=1.9, ls="--",
            label="VIX（30D ATM IV 插值）", zorder=5)
    # 关键刻度线：偏度 100 中性 / 110 危险 / 120 极危；VIX 20 / 40
    for yv, ls, col, lab in ((100, ":", "#94a3b8", "偏度100 中性"),
                             (110, "--", "#c2410c", "110 危险"),
                             (120, "--", "#991b1b", "120 极危"),
                             (20, ":", "#93c5fd", "VIX20"),
                             (40, ":", "#3b82f6", "VIX40")):
        ax.axhline(yv, color=col, ls=ls, lw=0.9, alpha=0.55, zorder=1)
        ax.text(nv + 6, yv, lab, fontsize=f_tick - 2, color=col, alpha=0.85,
                va="center", ha="right")
    ax.set_ylim(*VOL_YLIM)          # (0, 130)
    ax.set_ylabel("偏度 SKEW ／ VIX（0~130）", fontsize=f_y - 2, color=VOL_SKEW)
    ax.tick_params(axis="y", colors=VOL_SKEW, labelsize=f_tick - 0.5)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.45)
    ax.tick_params(labelsize=f_tick)

    # ── 右轴：R_cascade（深红粗线，**固定 0~2**，大佬指定）──
    ax2 = ax.twinx()
    if np.isfinite(rc).any():
        ax2.plot(x, rc, color=RC_LINE, lw=RC_LINE_W, alpha=0.92, zorder=9,
                 solid_capstyle="round", label="R_cascade（右轴 0~2）")
        ax2.set_ylim(*RC_YLIM)       # (0, 2.0)
        for yv, ls in ((1.2, "--"), (0.8, "--"), (0.75, "-.")):
            ax2.axhline(yv, color=RC_LINE, ls=ls, lw=0.8, alpha=0.32, zorder=1)
        # 0.8 与 0.75 在 0~2 轴上只差 2.5% 高度 → 标签合并一行，避免撞字
        for yv, lab, va in ((1.2, "1.2 危险", "bottom"), (0.775, "0.8 安全 / 0.75 深坑", "top")):
            ax2.text(-1, yv, " " + lab, fontsize=f_tick - 2, color=RC_LINE,
                     alpha=0.75, va=va, ha="left")
        ax2.set_ylim(*RC_YLIM)
    ax2.set_ylabel("R_cascade（0~2）", fontsize=f_y - 2, color=RC_LINE)
    ax2.tick_params(axis="y", colors=RC_LINE, labelsize=f_tick - 0.5)
    ax2.spines["right"].set_color(RC_LINE)
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
    return _last(vix), _last(skew), _last(rc)


# ── 第三层：RMT（随机矩阵）结构指标 ────────────────────────────────
RMT_BLUE = "#2563eb"      # outlier_energy（左轴）
RMT_GREEN = "#15803d"     # AR5
RMT_PURPLE = "#7c3aed"    # λ1/Σλ
RMT_ORANGE = "#ea580c"    # RC
RMT_RED = "#b91c1c"       # λ1/λ2（外右轴）


def _paint_rmt_panel(ax, name, view, nv, f_tick, f_y, win=60):
    """第三层：该指数成分股相关矩阵的 RMT 结构（60 日滚动窗口）。

    口径全部来自 `charts/rmt_rc.py::series_for()`（唯一实现，本文件不另算）：
      · outlier_energy = Σ(λi | λi > (1+√(N/T))²) / N   ← 左轴，能量比
      · AR5            = (λ1..λ5) / Σ(λ1..λ50)          ← 右轴
      · λ1/Σλ          = λ1 / Σ(λ1..λ50)                ← 右轴（同分母）
      · RC             = σ20 / σ60（波动率级联比）        ← 右轴
      · λ1/λ2          = 第一 / 第二特征值比              ← 外右轴
    日期轴与上方 K 线共用（同一批 view 日期），RMT 缺的日子前向填充。
    """
    if _RMT is None:
        ax.text(0.5, 0.5, "RMT 模块不可用", ha="center", va="center",
                fontsize=f_y, color="#999999", transform=ax.transAxes)
        ax.set_axis_off()
        return
    try:
        full = _RMT.series_for(name, win=win, verbose=False)
    except Exception as e:                                     # noqa: BLE001
        ax.text(0.5, 0.5, "RMT 数据不可用：%s" % str(e)[:60], ha="center", va="center",
                fontsize=f_tick, color="#b91c1c", transform=ax.transAxes)
        ax.set_axis_off()
        return

    dates = [str(x) for x in view["dt"].dt.strftime("%Y%m%d").values]
    cols = {k: full[k].reindex(full.index.union(pd.Index(dates))).ffill()
            .reindex(dates).values.astype(float)
            for k in ("oe", "ar5", "l1s", "rc", "l1l2")}
    x = np.arange(nv)

    # 左轴：outlier_energy（柱 + 阴影，最直观的「能量比」）
    oe = cols["oe"]
    lo_oe = float(np.nanmin(oe)) if np.isfinite(oe).any() else 0.0
    base = min(0.0, lo_oe * 1.05)
    ax.bar(x, oe - base, bottom=base, width=0.72, color=RMT_BLUE, alpha=0.30, lw=0)
    ax.plot(x, oe, color=RMT_BLUE, lw=1.5, zorder=4)
    ax.set_ylabel("outlier_energy", fontsize=f_y - 2, color=RMT_BLUE)
    ax.tick_params(axis="y", colors=RMT_BLUE, labelsize=f_tick - 0.5)
    hi_oe = float(np.nanmax(oe)) if np.isfinite(oe).any() else 0.6
    ax.set_ylim(base, hi_oe * 1.12 if hi_oe > base else base + 0.1)

    # 右轴：AR5 / λ1/Σλ / RC
    ax1 = ax.twinx()
    ax1.plot(x, cols["ar5"], color=RMT_GREEN, lw=1.7, label="AR5=(λ1..λ5)/Σ(λ1..λ50)")
    ax1.plot(x, cols["l1s"], color=RMT_PURPLE, lw=1.3, ls="--", label="λ1/Σλ")
    ax1.plot(x, cols["rc"], color=RMT_ORANGE, lw=1.3, label="RC=σ20/σ60")
    ax1.set_ylim(0, max(1.6, float(np.nanmax(cols["rc"])) * 1.05))
    ax1.set_ylabel("AR5 ｜ λ1/Σλ ｜ RC", fontsize=f_y - 2, color=RMT_GREEN)
    ax1.tick_params(axis="y", colors=RMT_GREEN, labelsize=f_tick - 0.5)
    for yv, c in ((1.0, RMT_ORANGE), (1.2, "#c2410c")):
        ax1.axhline(yv, color=c, ls=":", lw=0.8, alpha=0.55)

    # 外右轴：λ1/λ2
    ax2 = ax1.twinx()
    ax2.spines["right"].set_position(("outward", 42))
    ax2.plot(x, cols["l1l2"], color=RMT_RED, lw=1.2, alpha=0.85, label="λ1/λ2")
    lmax = max(4.0, float(np.nanmax(cols["l1l2"])) * 1.1) if np.isfinite(cols["l1l2"]).any() else 4.0
    ax2.set_ylim(0, lmax)
    ax2.set_ylabel("λ1/λ2", fontsize=f_y - 2, color=RMT_RED)
    ax2.tick_params(axis="y", colors=RMT_RED, labelsize=f_tick - 0.5)
    ax2.spines["right"].set_color(RMT_RED)

    # 最新读数
    if np.isfinite(oe[-1]):
        ax.text(0.995, 0.955,
                "RMT(%d日窗) 最新 %s ｜ oe=%.4f  AR5=%.3f  λ1/λ2=%.2f  λ1/Σλ=%.3f  RC=%.2f"
                % (win, dates[-1][4:6] + "/" + dates[-1][6:], oe[-1], cols["ar5"][-1],
                   cols["l1l2"][-1], cols["l1s"][-1], cols["rc"][-1]),
                transform=ax.transAxes, ha="right", va="top", fontsize=f_tick - 0.5,
                color="#334155",
                bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#cbd5e1", alpha=0.9))

    hs, ls = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(hs + h2, ls + l2, loc="upper left", fontsize=f_tick - 1.5,
               framealpha=0.85, ncol=2)
    ax.set_ylabel("RMT结构", fontsize=f_y - 1)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.45)
    ax.tick_params(labelsize=f_tick)
    ax.set_xlim(-2, nv + 7)
    step = max(nv // 12, 1)
    idxs = list(range(0, nv, step))
    ax.set_xticks(idxs)
    ax.set_xticklabels([dates[i][4:6] + "/" + dates[i][6:] for i in idxs], fontsize=f_tick)
    for t in idxs:
        ax.axvline(t, color="#7c8794", lw=0.7, ls="--", alpha=0.25, zorder=0)


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
            texts += [t for t in ax.texts]
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


def draw_stack(pairs, out=None, n_show=160, row_h=7.8, width=12.0, dpi=150,
               with_rc=True, sub="vol"):
    """多指数**纵向堆叠**：一个指数两块（上=缠论价格 / 下=VIX+偏度+R_cascade 双轴），往下垒。

    大佬 2026-11-14 23:13：「我就不明白，你就把这几个子图粘到一块就行了，为什么要压缩呀？
    …你要能力不行，还是恢复到以前，一个指数一个图吧。」

    → 上一版是 5 列并排（figsize 10.2*ncol × 11.2 英寸），每列被压成约 11:1 的窄条，
      字和图挤在一起。本函数改成「宽度不变、往下垒」：
      画布宽 width 英寸固定，每指数占 row_h 英寸高，单块内 价格:副图 = 2.9:1.35，
      子图宽高比 ≈ 1.5:1（接近单图 16×10 的观感），字号按单图比例放大。

    口径与 draw() 完全一致（同一 chan_geometry / 买卖点样式）。

    两层结构（2026-11-16 最终定稿）：
      ① 上：**原来的缠论图**（笔 / 中枢 ZG-ZD / 买卖点 / VWAP ±1σ2σ3σ），不加任何叠加线
      ② 下：**VIX + 偏度 + R_cascade 三条线，双 Y 轴**
             左轴（波动率点）：VIX（30D ATM IV 插值）、偏度（25Δ Put−Call）
             右轴（比值）    ：R_cascade（std20/std60，深红粗线）
    大佬原话：「算了算了，你不要这么整了，还是原来的是原来的，底下那坐标轴…
    还是双 Y 轴，一个是卡斯卡的，一个是偏度，一个是那个 VIX，就这仨在一块。
    就是，还是不要 MACD 了」
    """
    nrow = len(pairs)
    fig = plt.figure(figsize=(width, row_h * nrow), facecolor="white")
    # 顶部留 1.15 英寸给「大标题 + 口径说明」，避免标题压第一行子图
    _h = row_h * nrow
    gs = fig.add_gridspec(nrow, 1, hspace=0.30,
                          left=0.052, right=0.988,
                          top=1 - 1.15 / _h,
                          bottom=0.030)
    globals()["_VOL_LAST"] = {}
    sub_mode = "vol" if sub == "vol" else sub
    rc_map, vol_map = {}, {}
    for _n, _c in pairs:
        if sub == "vol":
            _v = _vol_series_for(_n)
            if _v is not None:
                vol_map[_n] = _v
        if with_rc:
            _r = _rc_series_for(_n, _c)
            if _r is not None:
                rc_map[_n] = _r

    for ri, (name, code) in enumerate(pairs):
        df = _index_day(code)
        if df is None or len(df) < 60:
            raise RuntimeError("%s(%s) 数据不足" % (name, code))
        total = len(df)
        start = max(0, total - n_show)
        nv = min(n_show, total)
        geo = chan_geometry(df)
        ck = geo["ck"]
        zs = geo["zs"]
        P = float(df["close"].iloc[-1])
        last_ts = df["dt"].iloc[-1]
        sub = gs[ri, 0].subgridspec(2, 1, height_ratios=[2.9, 1.35], hspace=0.09)
        ax = fig.add_subplot(sub[0, 0])
        axm = fig.add_subplot(sub[1, 0], sharex=ax)
        _paint_row(fig, ax, axm, name, code, df, start, nv, geo, ck, zs, P, last_ts,
                   sub=sub_mode, rc_series=rc_map.get(name),
                   vol_series=vol_map.get(name))
    fig.suptitle("缠论·分型版 · 五大宽基日线（每指数一行）｜下：VIX／偏度／R_cascade 双轴",
                 fontsize=21,
                 y=1 - 0.42 / _h, va="center")
    fig.text(0.5, 1 - 0.86 / _h,
             "口径：包含处理→顶底分型→笔→中枢(ZG/ZD)→背驰　｜　★买点 ▼卖点　"
             "黄框=最近中枢　蓝线=笔　紫线=VWAP中线　虚线=±1σ/±2σ/±3σ　｜　"
             "下：蓝=VIX(30D ATM IV)　琥珀虚线=偏度25Δ　深红粗线=R_cascade(右轴)",
             ha="center", va="center", fontsize=14.5, color="#444444")
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
    ap.add_argument("--no-rc", action="store_true",
                    help="副图不画 R_cascade")
    ap.add_argument("--sub", default="vol", choices=["vol", "macd", "kline"],
                    help="副图内容：vol=VIX/偏度/R_cascade（默认）；macd；kline")
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
        p = draw_stack(pairs, a.out, a.stack_n, row_h=a.row_h,
                       with_rc=not a.no_rc, sub=a.sub)
        print("[OK]", p)
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
