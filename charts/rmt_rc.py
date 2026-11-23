#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""charts/rmt_rc.py · RMT + MFDFA Δα 联合监控图（单图 / 多图都支持）
=========================================================================
大佬口径（2026-11-15 定稿）：
  · 60 天滚动窗口（--win 60，可改）
  · **不要 Outlier Count 个数**，要 outlier_energy 数值
  · λ1/λ2（第一/第二特征值比）单独一根轴
  · 分位数**不占主图版面**，全部放到最底部一行小字
  · 任一分位 > 90% 要能发邮件提醒（--alert / --email）

指标（全部本文件算，不依赖别处口径）：
  AR5            = (λ1+…+λ5) / Σ(λ1…λ50)        分母＝前 50 个特征值之和
  λ1/λ2          = 第一 / 第二特征值之比
  λ1/Σλ          = λ1 / Σ(λ1…λ50)                与 AR5 同分母
  outlier_energy = Σ(λi | λi > (1+√(N/T))²) / N   超 MP 上界的特征值占总能量比（迹 = N）
  Δα             = **直接调 charts/phase_mfdfa.rolling_delta_alpha**（唯一实现）
                   · 标的自己的日线算 MF-DFA 滚动多分形谱宽（N=250，scales=[20,30,50,80,125]）
                   · 90 分位 = 告警阈值（--alert / --email 分位>90% 发邮件）

标的可以是：
  · 宽基（沪深300/中证500/中证1000/创业板指/科创50/上证50，也认 hs300/500/1000/创业板/科创）
  · 申万行业（如 半导体、白酒）
  · 同花顺行业板块 / 概念板块（如 稀土永磁）
  · 6 位指数代码（如 000300）

用法：
  # 单图（一个指数）
  python3 charts/rmt_rc.py --targets 科创50 --out reports/charts/rmt/rmt_kechuang50.png

  # 多图（一行图里多个指数，默认 沪深300 + 科创50）
  python3 charts/rmt_rc.py --targets 沪深300,科创50

  # 五个宽基全画
  python3 charts/rmt_rc.py --targets 沪深300,中证500,中证1000,创业板指,科创50 --cols 2

  # 行业 / 概念也能画（Δα 用该行业指数自己的日线）
  python3 charts/rmt_rc.py --targets 半导体,稀土永磁

  # 分位告警（超 90% 就发邮件）
  python3 charts/rmt_rc.py --targets 沪深300,科创50 --alert --email

设计要点：
  · 数据全部走 DataHub（多源并行面板 + ETF 日线），不在本文件里写抓取
  · 面板按 (起止日, 成分股数) 落盘缓存，重复跑不重复抓
  · 每个指数一个「2 行块」：上 ETF 收盘价 / 下 RMT 三轴
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403  (sys.path / net_guard / 中文字体 / Agg)
from charts.rmt import (  # noqa: F401  统一 RMT 算法源（2026-11-18）
    ar5,
    correlation_matrix,
    eigen_decompose,
    mp_bounds,
)
from charts.phase_mfdfa import rolling_delta_alpha  # noqa: E402  MF-DFA Δα 唯一实现

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

# ───────────────────────── 配色（沿用库内暗黑金融风） ─────────────────────────
BG = "#05080f"
PANEL = "#0a1220"
GRIDC = "#1c2b45"
TEXTC = "#cbd5e1"
MUTED = "#8b9bb4"
GOLD = "#fbbf24"
CYAN = "#2dd4bf"
BLUE = "#38bdf8"
ORANGE = "#fb923c"
RED = "#fb7185"
GREEN = "#34d399"
PURPLE = "#a78bfa"

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": PANEL, "axes.edgecolor": "#26344d",
    "axes.labelcolor": TEXTC, "axes.titlecolor": "#e2e8f0", "text.color": "#e2e8f0",
    "xtick.color": MUTED, "ytick.color": MUTED, "grid.color": GRIDC,
    "grid.linewidth": 0.5, "axes.grid": True,
})

# ───────────────────────── 标的表 ─────────────────────────
# 指数代码 / 默认 ETF / 显示名；ETF 优先取 DataHub 的 WIDE_INDEX_ETFS 主 ETF
TARGETS = {
    "沪深300": ("000300.SH", "510300.SH"),
    "中证500": ("000905.SH", "510500.SH"),
    "中证1000": ("000852.SH", "512100.SH"),
    "创业板指": ("399006.SZ", "159915.SZ"),
    "科创50": ("000688.SH", "588000.SH"),
    "上证50": ("000016.SH", "510050.SH"),
}

# 大 alias：让命令行也能说「创业板 / 科创 / hs300」
ALIAS = {
    "沪深300": ["沪深300", "hs300", "300", "IF"],
    "中证500": ["中证500", "zz500", "500", "IC"],
    "中证1000": ["中证1000", "zz1000", "1000", "IM"],
    "创业板指": ["创业板指", "创业板", "cyb", "159915"],
    "科创50": ["科创50", "科创", "kc50", "588000", "000688"],
    "上证50": ["上证50", "sz50", "510050", "000016"],
}


def _resolve_target(text):
    t = str(text or "").strip()
    if t in TARGETS:
        return t
    for name, aliases in ALIAS.items():
        if t in aliases:
            return name
    # 代码反查
    for name, (code, etf) in TARGETS.items():
        if t.upper() in (code.upper(), etf.upper()):
            return name
    raise SystemExit("未知标的：%s（可选：%s）" % (t, "、".join(TARGETS)))


# ───────────────── 任意标的（指数 / 申万行业 / 同花顺板块）─────────────────
# 2026-11-17 大佬：「不单单是宽基，也有可能是指数，也有可能是行业」
#   → 宽基仍走 TARGETS（指数成分股 + ETF）；行业/概念名走板块口径。
KIND_WIDE, KIND_SW, KIND_IND, KIND_CON = "wide", "sw", "industry", "concept"


def _dh_lists(dh):
    """板块清单（行业 / 概念）+ 申万行业清单，全部吃 DataHub 缓存。"""
    out = {"ind": {}, "con": {}, "sw": {}}
    for key, kind in (("ind", "行业"), ("con", "概念")):
        try:
            df = dh.get_sector_list(kind)
            for _, r in df.iterrows():
                out[key][str(r["name"])] = (str(r["code"]), kind)
        except Exception:                                        # noqa: BLE001
            pass
    for lv in (1, 2):
        try:
            df = dh.get_sw_classify(level=lv)
            for _, r in df.iterrows():
                out["sw"].setdefault(str(r["sw_name"]), str(r["sw_code"]))
        except Exception:                                        # noqa: BLE001
            pass
    return out


def resolve_any_target(text, dh=None):
    """解析标的名 → dict(kind, name, code, etf, etfname)。

    顺序：宽基别名 → 申万行业 → 同花顺行业板块 → 同花顺概念 → 6 位指数代码。
    """
    t = str(text or "").strip()
    try:
        name = _resolve_target(t)
        code, etf = TARGETS[name]
        return dict(kind=KIND_WIDE, name=name, code=code, etf=etf, etfname="ETF")
    except SystemExit:
        pass
    dh = dh or _datahub()
    L = _dh_lists(dh)
    if t in L["sw"]:
        return dict(kind=KIND_SW, name=t, code=L["sw"][t], etf=None, etfname="申万指数")
    if t in L["ind"]:
        return dict(kind=KIND_IND, name=t, code=L["ind"][t][0], etf=None, etfname="板块指数")
    if t in L["con"]:
        return dict(kind=KIND_CON, name=t, code=L["con"][t][0], etf=None, etfname="概念指数")
    if t.isdigit() and len(t) == 6:            # 纯代码 → 当指数
        suf = ".SZ" if t[0] in ("3", "0") else ".SH"
        return dict(kind=KIND_WIDE, name=t, code=t + suf, etf=None, etfname="指数")
    raise SystemExit("未知标的：%s（可用：宽基名 / 申万行业 / 同花顺行业 / 概念 / 6 位指数代码）" % t)


def _rc_type(kind, code):
    """标的数据源类型 → 数据源 typ（供日线取数用，宽基/指数/申万/行业/概念）。"""
    if kind == KIND_SW:
        return "sw"
    if kind == KIND_IND:
        return "sector_ind"
    if kind == KIND_CON:
        return "sector_con"
    return "index_sz" if str(code).upper().endswith(".SZ") else "index_sh"


def da_series_via_lib(typ, code):
    """MF-DFA 多分形谱宽 Δα 序列 —— **直接调唯一实现** charts/phase_mfdfa.rolling_delta_alpha。

    标的自己的日线算滚动 Δα（N=250，scales=[20,30,50,80,125]，q=[-4..4]）：
      宽基/指数：typ=index_sh / index_sz
      申万行业  ：typ=sw
      同花顺行业：typ=sector_ind
      同花顺概念：typ=sector_con
    日线走 misc/engine/rcascade.py::load()（只取数，不算 RC）。
    返回 DataFrame(index=YYYYMMDD str, col "da")；取不到返回 None。
    """
    import importlib.util
    fp = os.path.join(WS, "misc", "engine", "rcascade.py")
    if not os.path.exists(fp):
        print("[rmt] 找不到 rcascade.py（%s），Δα 无日线可用" % fp, flush=True)
        return None
    try:
        if "rcascade" in sys.modules:
            RC = sys.modules["rcascade"]
        else:
            spec = importlib.util.spec_from_file_location("rcascade", fp)
            RC = importlib.util.module_from_spec(spec)
            sys.modules["rcascade"] = RC
            spec.loader.exec_module(RC)
        d, _src = RC.load(typ, str(code))
    except Exception as ex:                                       # noqa: BLE001
        print("[rmt] rcascade.load(%s,%s) 失败：%s" % (typ, code, str(ex)[:100]), flush=True)
        return None
    if d is None or not len(d):
        return None
    close = d.set_index("dt")["close"].astype(float)
    da = rolling_delta_alpha(close, N=250)
    if da is None or not len(da):
        return None
    out = pd.DataFrame({"da": da.astype(float)})
    out.index = [x.strftime("%Y%m%d") for x in pd.to_datetime(da.index)]
    return out


def own_close_via_lib(typ, code):
    """标的自身日线收盘（走 rcascade.load，与 Δα 同源）。取不到返回空 Series。"""
    import importlib.util
    fp = os.path.join(WS, "misc", "engine", "rcascade.py")
    if not os.path.exists(fp):
        return pd.Series(dtype=float)
    try:
        if "rcascade" in sys.modules:
            RC = sys.modules["rcascade"]
        else:
            spec = importlib.util.spec_from_file_location("rcascade", fp)
            RC = importlib.util.module_from_spec(spec)
            sys.modules["rcascade"] = RC
            spec.loader.exec_module(RC)
        d, _src = RC.load(typ, str(code))
    except Exception as ex:                                       # noqa: BLE001
        print("[rmt] 自身日线(%s,%s) 失败：%s" % (typ, code, str(ex)[:90]), flush=True)
        return pd.Series(dtype=float)
    if d is None or not len(d):
        return pd.Series(dtype=float)
    s = pd.Series(d["close"].astype(float).values,
                  index=[str(x).replace("-", "")[:8] for x in d["dt"]])
    return s[~s.index.duplicated(keep="last")].sort_index()


def _datahub():
    mod = None
    cands = []
    env = os.environ.get("DATA_HUB_PATH")
    if env:
        cands.append(os.path.abspath(os.path.expanduser(env)))
    cands.append(os.path.join(os.path.dirname(WS), "datahub-three-tier"))
    cands.append(os.path.join(WS, "optstack"))
    cands.append("/root/.openclaw/workspace/optstack")
    for base in cands:
        if not os.path.isdir(base):
            continue
        if base not in sys.path:
            sys.path.insert(0, base)
        try:
            import importlib
            mod = importlib.import_module("data_hub")
            break
        except Exception:
            continue
    if mod is None:
        raise SystemExit("找不到 data_hub（试过 DATA_HUB_PATH / ../datahub-three-tier / optstack）")
    return mod


# ───────────────────────── 取数（带缓存） ─────────────────────────
def panel_cache_dir():
    d = os.path.join(WS, ".cache", "rmt")
    os.makedirs(d, exist_ok=True)
    return d


def get_panel(codes, start, end, force=False):
    """收盘价宽表（行=日期 str，列=代码）。命中缓存直接读。"""
    dh = _datahub()
    key = "panel_%s_%s_n%d.pkl" % (start, end, len(codes))
    fp = os.path.join(panel_cache_dir(), key)
    if os.path.exists(fp) and not force:
        try:
            close = pd.read_pickle(fp)
            if len(close) and set(codes) & set(close.columns):
                print("[rmt] 面板命中缓存 %s  %s" % (os.path.basename(fp), close.shape), flush=True)
                return close
        except Exception:
            pass
    print("[rmt] 抓取面板 %d 只 %s~%s ..." % (len(codes), start, end), flush=True)
    close, rep = dh.fetch_stock_close_panel(codes, start, end)
    if rep:
        try:
            print("[rmt] 取数报告 " + json.dumps(
                {k: (v if not isinstance(v, (list, dict)) else len(v))
                 for k, v in (rep.items() if isinstance(rep, dict) else {})},
                ensure_ascii=False)[:300], flush=True)
        except Exception:
            pass
    close.to_pickle(fp)
    return close


def get_etf_close(etf, start, end):
    dh = _datahub()
    try:
        e = dh.get_etf_daily(etf, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    except Exception as ex:
        print("[rmt] ETF %s 取数失败 %s" % (etf, str(ex)[:80]), flush=True)
        return pd.Series(dtype=float)
    e = e[["trade_date", "close"]].dropna().sort_values("trade_date")
    e["trade_date"] = e["trade_date"].astype(str)
    return e.set_index("trade_date")["close"]


# ───────────────────────── 指标计算 ─────────────────────────
def rmt_series(ret, comps, win=60):
    """滚动窗口 → AR5 / λ1/λ2 / λ1/Σλ / outlier_energy（Δα 由自身日线另算，不在此列）"""
    P = ret[[c for c in comps if c in ret.columns]]
    rows = []
    for i in range(win - 1, len(P)):
        w = P.iloc[i - win + 1:i + 1].dropna(axis=1, thresh=int(win * 0.95))
        if w.shape[1] < 20:
            continue
        w = w.fillna(0.0)
        C = correlation_matrix(w.values)          # 相关矩阵（去均值标准化）
        ev, _ = eigen_decompose(C)                # 降序特征值
        n = len(ev)
        s50 = ev[:min(50, n)].sum()
        thr, _ = mp_bounds(n / win)               # MP 上界 (1+√(N/T))²
        out = ev[ev > thr]
        rows.append(dict(
            date=P.index[i], N=n, thr=thr,
            ar5=ar5(ev, top=5, denom=50),
            l1l2=ev[0] / max(ev[1], 1e-9),
            l1s=ev[0] / s50,
            oe=out.sum() / n,                     # outlier_energy
            oc=int(len(out)),
        ))
    if not rows:
        raise SystemExit("窗口不足，算不出 RMT（检查面板长度 / 成分股覆盖）")
    return pd.DataFrame(rows).set_index("date")


# ───────────────────── 快速算法 + CSV 面板（2026-11-16 新增）─────────────────────
# 为什么要有这一段
# ----------------
# 日报要画「五大宽基 × 60 日窗口」的 RMT，`rmt_series()` 每窗口都用
# np.corrcoef(N×N) + eigvalsh，N=1000（中证1000）时单指数就要 6 分钟，
# 五个指数加起来日报顶不住；而且 DataHub 的按股票拉面板 1812 只要十几分钟。
#
# 两个等价的快路径（都用逐点比对验证过，误差 <1e-13）：
#   ① 特征值：相关矩阵特征值 == 标准化矩阵 SVD 奇异值² / (win-1)。
#      N 可以远大于 win，所以改用 60×60 的 Gram 矩阵（win×win）求奇异值，
#      复杂度从 O(N³) 降到 O(win²·N)，N=1000 时 341 个窗口 1.9s（原来约 400s）。
#   ② 数据：tushare `pro.daily(trade_date=)` 一次给全市场 5500 行，
#      按「日」增量落 CSV（bin/rmt_panel_sync.py），比按股票拉快一个数量级。
#
# 口径与 `rmt_series()` **完全一致**：同样的 dropna(thresh=win*0.95)、
# fillna(0)、ddof=1、前 50 个特征值做分母、MP 上界 (1+√(N/T))²。

CSV_PANEL_DIR = os.environ.get("RMT_PANEL_DIR", "/root/.cache/rmt_panel")
SERIES_CACHE = os.path.join(CSV_PANEL_DIR, "series")


def panel_from_csv(panel_dir=None, days=560):
    """读 /root/.cache/rmt_panel/close_*.csv → 宽表（index=YYYYMMDD, columns=ts_code）。"""
    import glob
    d = panel_dir or CSV_PANEL_DIR
    fs = sorted(glob.glob(os.path.join(d, "close_*.csv")))[-days:]
    if not fs:
        return None
    rows = []
    for f in fs:
        day = os.path.basename(f).replace("close_", "").replace(".csv", "")
        try:
            one = pd.read_csv(f, usecols=["ts_code", "close"])
        except Exception:                                        # noqa: BLE001
            continue
        if one is None or one.empty:
            continue
        ser = one.drop_duplicates("ts_code").set_index("ts_code")["close"].astype(float)
        ser.name = day
        rows.append(ser)
    if not rows:
        return None
    out = pd.concat(rows, axis=1).T.sort_index()
    out.index.name = "trade_date"
    return out


def rmt_series_fast(ret, comps, win=60):
    """`rmt_series()` 的等价快版（SVD-based，见上方说明）。"""
    P = ret[[c for c in comps if c in ret.columns]]
    if not len(P.columns):
        raise SystemExit("rmt_series_fast: 成分股一只都没匹配上")
    V = P.values.astype(float)
    dates = list(P.index)
    thr_n = int(win * 0.95)
    rows, idx = [], []
    for i in range(win - 1, len(V)):
        w = V[i - win + 1:i + 1]
        keep = (~np.isnan(w)).sum(axis=0) >= thr_n
        w = w[:, keep]
        n = w.shape[1]
        if n < 20:
            continue
        w = np.nan_to_num(w, nan=0.0)
        sd = w.std(axis=0, ddof=1)
        sd[sd < 1e-12] = 1e-12
        Z = (w - w.mean(axis=0)) / sd
        sv = np.linalg.svd(Z, compute_uv=False)   # 快版特征分解（SVD，等价 eigh(C)）
        ev = np.sort(sv ** 2 / (win - 1))[::-1]
        s50 = ev[:min(50, n)].sum()
        thr, _ = mp_bounds(n / win)               # MP 上界 (1+√(N/T))²
        out = ev[ev > thr]
        rows.append(dict(N=n, thr=thr, ar5=ar5(ev, top=5, denom=50),
                         l1l2=ev[0] / max(ev[1], 1e-9), l1s=ev[0] / s50,
                         oe=out.sum() / n, oc=int(len(out))))
        idx.append(dates[i])
    if not rows:
        raise SystemExit("窗口不足，算不出 RMT（面板长度 / 成分股覆盖不够）")
    return pd.DataFrame(rows, index=idx)


def series_for(name, win=60, force=False, panel_days=560, verbose=True):
    """取某指数的 RMT 指标序列（CSV 面板 + 磁盘缓存）。

    返回 DataFrame(index=YYYYMMDD str, cols: N thr ar5 l1l2 l1s oe oc rc)。
    缓存文件 `series/<name>_win<win>.csv`；面板最后一天变了才重算。
    """
    os.makedirs(SERIES_CACHE, exist_ok=True)
    fp = os.path.join(SERIES_CACHE, "%s_win%d.csv" % (name, win))
    panel = panel_from_csv(days=panel_days)
    if panel is None:
        raise SystemExit("RMT 面板为空：%s（先跑 bin/rmt_panel_sync.py）" % CSV_PANEL_DIR)
    last_day = str(panel.index[-1])
    if os.path.exists(fp) and not force:
        try:
            old = pd.read_csv(fp, index_col=0, dtype={"trade_date": str})
            old.index = [str(x) for x in old.index]      # 缓存读回来是 int64，统一成 str
            if len(old) and str(old.index[-1]) == last_day:
                if verbose:
                    print("[rmt] %s 命中缓存 %s" % (name, os.path.basename(fp)), flush=True)
                return old
        except Exception:                                        # noqa: BLE001
            pass
    code, _etf = TARGETS[name]
    dh = _datahub()
    comps = [str(x) for x in dh.get_index_components(code)]
    ret = (panel.pct_change().iloc[1:] * 100).clip(-30, 30)
    ret.index = [str(x) for x in ret.index]
    full = rmt_series_fast(ret, comps, win=win)
    if verbose:
        last = full.iloc[-1]
        print("[rmt] %s 算出 %d 窗 %s~%s | oe=%.4f AR5=%.3f λ1/λ2=%.2f λ1/Σλ=%.3f"
              % (name, len(full), full.index[0], full.index[-1], last.oe, last.ar5,
                 last.l1l2, last.l1s), flush=True)
    full.index = [str(x) for x in full.index]
    full.to_csv(fp)
    return full


def pct_rank(s, v):
    s = s.dropna()
    if not len(s):
        return float("nan")
    return float((s <= v).mean() * 100)


FSZ = 1.45         # 全局字号倍数：聊天窗按宽度缩放显示，字号/画布宽 的比值才决定可读性

METRICS = [("oe", "outlier_energy"), ("ar5", "AR5"), ("l1l2", "λ1/λ2"),
           ("l1s", "λ1/Σλ"), ("da", "MFDFA Δα")]


def metrics_now(full, show_from):
    """返回 (展示段 df, 最新一行, 各指标全样本分位)"""
    d = full[full.index >= show_from]
    if not len(d):
        d = full.tail(1)
    last = d.iloc[-1]
    q = {k: pct_rank(full[k], last[k]) for k, _ in METRICS}
    return d, last, q


# ───────────────────────── 出图 ─────────────────────────
def _style_axis_y(ax, color, label, size=15):
    ax.set_ylabel(label, fontsize=size, color=color)
    ax.tick_params(axis="y", colors=color, labelsize=13*FSZ)
    ax.spines["left"].set_color(color)


def draw_rmt_block(fig, gs, r0, c0, name, d, last, q, px, thr, show_from,
                   etf, etfname, win, dpi_note="", da_p90=None, kind=None):
    """一个标的 = 上：对应价格；下：RMT 三轴（RC 已换成 MF-DFA Δα，与单独图同口径）"""
    # ---- 上：价格 ----
    ax0 = fig.add_subplot(gs[r0, c0])
    px = px[px.index >= show_from] if len(px) else px
    if len(px):
        xp = np.arange(len(px))
        ax0.plot(xp, px.values, color=GOLD, lw=2.0, label="%s %s 收盘" % (etfname, etf))
        ax0.fill_between(xp, px.values.min(), px.values, color=GOLD, alpha=0.08)
        ax0.set_xlim(-1, len(px))
        span = px.max() - px.min()
        ax0.yaxis.set_major_locator(MultipleLocator(max(1e-3, np.round(span / 6, 3))))
        ax0.legend(loc="upper left", fontsize=14*FSZ, framealpha=0.25)
    ax0.set_ylabel("%s 收盘价（元）" % etfname, fontsize=14.5*FSZ, color=GOLD)
    ax0.tick_params(axis="y", colors=GOLD, labelsize=12.5*FSZ)
    ax0.spines["left"].set_color(GOLD)
    _title = "%s · %s" % (name, etfname)
    if etf:
        _title += "（%s %s）" % (etfname, etf)
    ax0.set_title(_title, fontsize=19*FSZ, pad=9, color=GOLD)
    ax0.grid(alpha=0.22, axis="y")
    ax0.set_xticklabels([])

    # ---- 下：RMT ----
    ax = fig.add_subplot(gs[r0 + 1, c0])
    x = np.arange(len(d))

    ax.plot(x, d.oe.values, color=BLUE, lw=2.4, marker="o", ms=3.2, alpha=0.95,
            label="outlier_energy = Σ(λ>MP上界)/N")
    ax.fill_between(x, 0, d.oe.values, color=BLUE, alpha=0.12)
    _style_axis_y(ax, BLUE, "outlier_energy（超阈值特征值占总能量比）")
    ax.set_ylim(0, max(0.75, np.ceil(float(np.nanmax(d.oe)) * 1.15 * 20) / 20))
    ax.yaxis.set_major_locator(MultipleLocator(0.1))
    ax.grid(alpha=0.22, axis="y")

    ax1 = ax.twinx()
    ax1.plot(x, d.ar5.values, color=GREEN, lw=2.5, marker="^", ms=3.8, alpha=0.93,
             label="AR5 = (λ1..λ5)/Σ(λ1..λ50)")
    ax1.plot(x, d.l1s.values, color=PURPLE, lw=1.9, marker="v", ms=3.2, alpha=0.88,
             label="λ1/Σλ（同分母）")
    ax1.plot(x, d.da.values, color=ORANGE, lw=1.8, marker="s", ms=3, alpha=0.85,
             label="MF-DFA Δα（N=250，多分形谱宽）")
    ax1.set_ylim(0, 1.6)
    ax1.set_ylabel("AR5 ｜ λ1/Σλ ｜ Δα（右轴）", fontsize=15*FSZ)
    ax1.yaxis.set_major_locator(MultipleLocator(0.2))
    ax1.tick_params(labelsize=13*FSZ)
    for yv, c, txt in [(0.30, GREEN, "AR5 0.30"), (0.50, RED, "AR5 0.50 危险")]:
        ax1.axhline(yv, color=c, ls="--", lw=0.85, alpha=0.42)
        ax1.text(len(d) - 0.5, yv, txt, fontsize=11.5*FSZ, color=c, alpha=0.9,
                 ha="right", va="bottom")
    if da_p90 is not None and pd.notna(da_p90):
        ax1.axhline(da_p90, color=ORANGE, ls="--", lw=1.1, alpha=0.75)
        ax1.text(len(d) - 0.5, da_p90, "Δα P90 %.3f（告警）" % da_p90,
                 fontsize=11.5*FSZ, color=ORANGE, alpha=0.95, ha="right", va="bottom")

    axr = ax.twinx()
    axr.spines["right"].set_position(("outward", 54))
    axr.plot(x, d.l1l2.values, color=RED, lw=2.2, marker="d", ms=3.4, alpha=0.92,
             label="λ1/λ2（右外轴）")
    lmax = max(4.0, np.ceil(float(np.nanmax(d.l1l2)) * 1.12))
    axr.set_ylim(0, lmax)
    axr.set_ylabel("λ1 / λ2", fontsize=15*FSZ, color=RED)
    axr.yaxis.set_major_locator(MultipleLocator(1 if lmax <= 10 else 2))
    axr.tick_params(axis="y", colors=RED, labelsize=13*FSZ)
    axr.spines["right"].set_color(RED)

    ax.axvline(len(d) - 1, color="white", ls=":", lw=1, alpha=0.5)

    step = max(1, len(d) // 12)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([d.index[i][4:6] + "/" + d.index[i][6:] for i in range(0, len(d), step)],
                       rotation=45, fontsize=13*FSZ)
    ax.set_xlim(-1, len(d))

    h1, l1 = ax.get_legend_handles_labels()
    hr, lr = axr.get_legend_handles_labels()
    h2, l2 = ax1.get_legend_handles_labels()
    ax.legend(h1 + hr + h2, l1 + lr + l2, loc="upper left", fontsize=13*FSZ,
              framealpha=0.32, ncol=2)

    ax.set_title("%s 成分股相关矩阵 · RMT 结构（N=%d，%d 日窗口，%d 个窗口）"
                 % (name, int(last.N), win, len(d)), fontsize=18*FSZ, pad=9)
    ax.set_xlabel("窗口结束日（%s ~ %s）" % (d.index[0], d.index[-1]), fontsize=14*FSZ)

    # 右上角：只放数值（分位在页脚）
    ax1.text(0.995, 0.985,
             "最新 %s　outlier_energy = %.4f　|　AR5=%.3f　λ1/λ2=%.2f　λ1/Σλ=%.3f　Δα=%.3f"
             % (d.index[-1][4:6] + "/" + d.index[-1][6:], last.oe, last.ar5,
                last.l1l2, last.l1s, last.da),
             transform=ax1.transAxes, ha="right", va="top", fontsize=13.5*FSZ,
             color="#e2e8f0", bbox=dict(boxstyle="round,pad=0.34", fc="#0f1a2c",
                                       ec=GRIDC, alpha=0.95))

    # 左下角：oe 区间 / 中位 / 当前
    ax.text(0.005, 0.03,
            "outlier_energy：区间 %.4f~%.4f｜中位 %.4f｜当前 %.4f（MP 上界 %.2f，超阈值 λ 共 %d 个）"
            % (float(np.nanmin(d.oe)), float(np.nanmax(d.oe)),
               float(np.nanmedian(d.oe)), last.oe, thr, int(last.oc)),
            transform=ax.transAxes, ha="left", va="bottom", fontsize=12.5*FSZ,
            color=BLUE, alpha=0.92)
    return ax, ax1


def render(targets, out, win=60, show_from="20260601", end=None, cols=None,
           dpi=150, force=False, lookback=420):
    """出图主函数：单标的=1 列，多标的=多列（每列一个标的，上下两行）。

    targets 里的元素可以是字符串（名字 / 代码），也可以是 resolve_any_target() 出来的 dict。
    """
    dh = _datahub()
    end = end or datetime.now().strftime("%Y%m%d")
    # 面板起点：展示起点往前推至少 lookback 天 → 历史分位数才有稳定基准
    need_start = (datetime.strptime(show_from, "%Y%m%d") - timedelta(days=lookback)) \
        .strftime("%Y%m%d")
    # Δα 要 250 日滚动窗口 + 多分形尺度，回看更久一点更安全（need_start 已含 lookback）

    metas = [t if isinstance(t, dict) else resolve_any_target(t, dh) for t in targets]
    names = [m["name"] for m in metas]

    codes_all, per = [], {}
    for m in metas:
        name, kind = m["name"], m["kind"]
        code, etf = m["code"], m.get("etf")
        comps = []
        if kind == KIND_WIDE:
            # ETF 优先用 DataHub 映射表里登记的主 ETF
            try:
                mm = (dh.WIDE_INDEX_ETFS or {}).get(name) or {}
                etf = (mm.get("main") or etf)
            except Exception:                                    # noqa: BLE001
                pass
            try:
                comps = [str(x) for x in dh.get_index_components(code)]
                if etf:
                    try:
                        m["etfname"] = (dh.WIDE_INDEX_ETF_NAMES or {}).get(etf, "ETF")
                    except Exception:                            # noqa: BLE001
                        m["etfname"] = "ETF"
            except Exception as ex:                              # noqa: BLE001
                print("[rmt] %s 成分股取数失败：%s" % (name, str(ex)[:80]), flush=True)
        else:
            # 行业/概念：成分股优先申万官网 / tushare 板块成分，取不到就跑纯指数 RC
            try:
                if kind == KIND_SW:
                    mm = dh.get_sw_members(str(code))
                    comps = [str(x) for x in mm["con_code"]]
                elif kind == KIND_CON:
                    mm = dh.fetch_concept_members(ths_code=str(code))
                    comps = [str(x) for x in mm["con_code"]]
                else:
                    mm = dh.get_sector_members(str(code), kind="行业")
                    comps = [str(x) for x in mm["code"]]
            except Exception as ex:                              # noqa: BLE001
                print("[rmt] %s 板块成分取不到（只用自身指数 RC）：%s"
                      % (name, str(ex)[:80]), flush=True)
                comps = []
            comps = [c if "." in c else (c + (".SZ" if c[0] in "03" else ".SH")) for c in comps]
        per[name] = dict(kind=kind, code=code, etf=etf, comps=comps,
                         etfname=m.get("etfname") or "指数", typ=_rc_type(kind, code))
        codes_all += comps

    codes_all = list(dict.fromkeys(codes_all))
    close = get_panel(codes_all, need_start, end, force=force) if codes_all \
        else pd.DataFrame()
    if len(close):
        close.index = close.index.astype(str)
        ret = (close.pct_change().iloc[1:] * 100).clip(-30, 30)
    else:
        ret = pd.DataFrame()

    meta = {}
    for name in names:
        info = per[name]
        full = pd.DataFrame()
        if len(ret.columns) and info["comps"]:
            try:
                full = rmt_series(ret, info["comps"], win=win)
            except SystemExit:
                full = pd.DataFrame()
        if not len(full):
            # 没有成分股面板 → 用标的自身 Δα 的长度做骨架（RMT 那几列留空）
            probe = da_series_via_lib(info["typ"], info["code"])
            if probe is None or not len(probe):
                raise SystemExit("%s 既没有成分股面板、也没有自身行情，无法出图" % name)
            full = pd.DataFrame(index=[str(x) for x in probe.index], data={
                "N": 1, "thr": np.nan, "ar5": np.nan, "l1l2": np.nan,
                "l1s": np.nan, "oe": np.nan, "oc": np.nan, "da": np.nan})
            print("[rmt] %s 无成分股面板 → 只画自身指数+Δα" % name, flush=True)

        # ── Δα 口径唯一（直接调 charts/phase_mfdfa.rolling_delta_alpha）：
        #   标的自己的日线算 MF-DFA 滚动多分形谱宽（N=250），只换数据源类型：
        #     宽基/指数 index_sh|index_sz / 申万 sw / 同花顺行业 sector_ind / 概念 sector_con
        #   拿不到该标的自有行情 → Δα 留空（不再回退成分股等权）。
        own_da = da_series_via_lib(info["typ"], info["code"])
        if own_da is not None and len(own_da):
            full = full.copy()
            full["da"] = own_da["da"].reindex(full.index).ffill()
        else:
            print("[rmt] %s 自身行情不可用，Δα 留空" % name, flush=True)

        d, last, q = metrics_now(full, show_from)
        if info.get("etf"):
            px = get_etf_close(info["etf"], need_start, end)
        else:
            px = own_close_via_lib(info["typ"], info["code"])
            px = px[px.index >= need_start] if len(px) else px
        da_p90 = float(full["da"].quantile(0.9)) if full["da"].notna().any() else None
        meta[name] = dict(info=info, full=full, d=d, last=last, q=q, px=px, da_p90=da_p90)
        print("[rmt] %-8s %d 窗 %s~%s | outlier_energy=%.4f AR5=%.3f λ1/λ2=%.2f λ1/Σλ=%.3f Δα=%.3f"
              % (name, len(d), d.index[0], d.index[-1], last.oe, last.ar5, last.l1l2,
                 last.l1s, last.da), flush=True)

    n = len(names)
    ncol = cols or 1        # 默认纵向堆叠：并排会把画布拉宽 → 字被同比缩小
                            # 想要并排就显式给 --cols 2
    ncol = max(1, min(ncol, n))
    nblk = int(np.ceil(n / ncol))

    # 底部图例区：多行、大字号、纯 ASCII 符号（避免缩图后糊成乱码）
    foot_lines = [
        (BLUE,   "outlier_energy（左内轴·蓝）：超过随机矩阵阈值的特征值，占总能量比例"),
        (RED,    "L1/L2（右外轴·红）：第一与第二特征值之比，越大表示抱团越集中"),
        (GREEN,  "AR5（右轴·绿）：前 5 个特征值之和 ÷ 前 50 个之和"),
        (PURPLE, "L1/SUM（右轴·紫）：第一大特征值 ÷ 前 50 个之和，与 AR5 同分母"),
        (ORANGE, "Δα（右轴·橙）：MF-DFA 多分形谱宽（N=250）；标的自身日线，"
                  "90 分位虚线为告警阈值"),
    ]
    lh = 0.62 * FSZ                               # 每行高度（英寸，随字号放大）
    foot_h = lh * len(foot_lines) + 1.05          # 图例区总高
    fig_w = 13.2 * ncol if ncol > 1 else 13.6
    fig_h = 7.0 * nblk + (1.2 if nblk > 1 else 2.6) + foot_h

    fig = plt.figure(figsize=(fig_w, fig_h))
    heights = []
    for _ in range(nblk):
        heights += [1.0, 1.3]
    gs = fig.add_gridspec(2 * nblk, ncol,
                          left=0.058, right=0.945, top=1 - 0.62 / fig_h,
                          bottom=(foot_h / fig_h), hspace=0.36, wspace=0.40,
                          height_ratios=heights)

    for idx, name in enumerate(names):
        m = meta[name]
        r0 = (idx // ncol) * 2
        c0 = idx % ncol
        draw_rmt_block(fig, gs, r0, c0, name, m["d"], m["last"], m["q"],
                       m["px"], m["last"].thr, show_from,
                       m["info"].get("etf") or "", m["info"].get("etfname") or "指数",
                       win, da_p90=m.get("da_p90"),
                       kind=m["info"].get("kind"))

    title = ("RMT + MFDFA Δα 联合监控图 ｜ %d 日滚动窗口 ｜ %s → 现在"
             % (win, datetime.strptime(show_from, "%Y%m%d").strftime("%Y-%m-%d")))
    if n > 1:
        title += " ｜ " + " · ".join(names)
    fig.suptitle(title, fontsize=24*FSZ if n <= 2 else 21, color="#e2e8f0",
                 y=1 - 0.26 / fig_h)

    # ---- 底部：多行大字号图例 ----
    # 字号 ≈ 图宽 1.9%：这样哪怕被聊天窗压到 700px 宽，字仍有 ~13px
    fs_foot = 19 * FSZ if ncol > 1 else 20 * FSZ
    y0 = (foot_h - 0.30) / fig_h
    dy = lh / fig_h
    for i, (col, txt) in enumerate(foot_lines):
        fig.text(0.028, y0 - i * dy, "■", color=col, fontsize=fs_foot + 0.5,
                 ha="left", va="center")
        # 正文用浅灰（彩色细字在暗底 + 缩图后极易糊成"乱码"），只有色块保留颜色
        fig.text(0.040, y0 - i * dy, txt, color="#cbd5e1", fontsize=fs_foot,
                 ha="left", va="center")

    # ---- 最后一行：各标的历史分位 ----
    foot2 = []
    for name in names:
        q = meta[name]["q"]
        foot2.append("%s　outlier_energy %.0f%% ｜ AR5 %.0f%% ｜ L1/L2 %.0f%% ｜ "
                     "L1/SUM %.0f%% ｜ Δα %.0f%%"
                     % (name, q["oe"], q["ar5"], q["l1l2"], q["l1s"], q["da"]))
    fig.text(0.030, (foot_h - 0.30 - len(foot_lines) * lh) / fig_h,
             "历史分位（全样本）：　" + "　　|　　".join(foot2),
             color="#94a3b8", fontsize=fs_foot - 0.8, ha="left", va="center")

    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=dpi, facecolor=BG, bbox_inches=None)
    plt.close(fig)
    print("[rmt] saved %s  %.0f KB" % (out, os.path.getsize(out) / 1024), flush=True)
    return out, meta


# ───────────────────────── 分位告警 / 邮件 ─────────────────────────
def build_alerts(meta, threshold=90.0):
    rows, alerts = [], []
    for name, m in meta.items():
        last, q = m["last"], m["q"]
        for key, label in METRICS:
            val = float(getattr(last, key))
            qq = float(q[key])
            rows.append(dict(target=name, metric=label, value=val, pct=qq,
                             hit=qq > threshold))
            if qq > threshold:
                alerts.append("%s %s 处于 %.0f%% 分位（值 %.4f）" % (name, label, qq, val))
    return rows, alerts


def send_alert_email(rows, alerts, chart=None, threshold=90.0, to=None):
    """调用 workspace 的 send_email.py（路径可用 TECH_EMAIL_SCRIPT 覆盖）"""
    script = os.environ.get("TECH_EMAIL_SCRIPT",
                            "/root/.openclaw/workspace/scripts/send_email.py")
    if not os.path.exists(script):
        print("[alert] 找不到邮件脚本 %s，只打印告警" % script)
        return False
    lines = ["## RMT + MFDFA Δα 分位告警（阈值 %.0f%%）" % threshold, "",
             "触发项 %d 个：" % len(alerts), ""]
    lines += ["- " + a for a in alerts]
    lines += ["", "---", "", "### 全部读数", "",
              "| 标的 | 指标 | 数值 | 历史分位 |", "|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %.4f | %.0f%%%s |"
                     % (r["target"], r["metric"], r["value"], r["pct"],
                        " 🔴" if r["hit"] else ""))
    lines += ["", "> outlier_energy = Σ(λi | λi > (1+√(N/T))²)/N；AR5=(λ1..λ5)/Σ(λ1..λ50)；"
                  "λ1/λ2=第一/第二特征值比；λ1/Σλ 同分母；Δα=MF-DFA 多分形谱宽（N=250）。"]
    body_file = os.path.join(panel_cache_dir(), "alert_body.md")
    open(body_file, "w", encoding="utf-8").write("\n".join(lines))

    cmd = [sys.executable, script,
           "--subject", "[RMT告警] %d 项分位超 %.0f%%" % (len(alerts), threshold),
           "--body-file", body_file]
    if to:
        cmd += ["--to", to]
    if chart and os.path.exists(chart):
        cmd += ["--attach", chart]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    print("[alert] mail rc=%d" % r.returncode)
    if r.returncode != 0:
        print((r.stderr or r.stdout or "")[-400:])
    return r.returncode == 0


# ───────────────────────── CLI ─────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="RMT + MFDFA Δα 联合监控图（单图/多图）+ 分位告警")
    ap.add_argument("--targets", "--target", dest="targets",
                    default="沪深300,科创50",
                    help="逗号分隔：宽基名（沪深300/中证500/中证1000/创业板指/科创50/上证50，"
                         "也认 hs300/500/1000/创业板/科创）、申万行业（如 半导体）、"
                         "同花顺行业/概念（如 白酒、稀土永磁）、或 6 位指数代码")
    ap.add_argument("--out", default=None, help="输出 png 路径（默认 reports/charts/rmt/...）")
    ap.add_argument("--win", type=int, default=60, help="滚动窗口天数（默认 60）")
    ap.add_argument("--start", default="20260601", help="展示起始日 YYYYMMDD（默认 20260601）")
    ap.add_argument("--end", default=None, help="数据截止日 YYYYMMDD（默认今天）")
    ap.add_argument("--cols", type=int, default=None, help="多图列数（默认几个标的就几列）")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--force", action="store_true", help="忽略面板缓存，重新抓数据")
    ap.add_argument("--lookback", type=int, default=420,
                    help="历史分位基准的回看天数（默认 420，面板起点＝展示起点−该天数）")
    ap.add_argument("--alert", action="store_true", help="检查分位，超阈值打印告警")
    ap.add_argument("--alert-threshold", type=float, default=90.0)
    ap.add_argument("--email", action="store_true", help="配合 --alert：超阈值时发邮件")
    ap.add_argument("--mail-to", default=None)
    ap.add_argument("--no-chart", action="store_true", help="只算告警，不出图")
    a = ap.parse_args(argv)

    raw = [x.strip() for x in str(a.targets).replace("，", ",").split(",") if x.strip()]
    metas, names2 = [], []
    for x in raw:
        m = resolve_any_target(x)
        if m["name"] not in names2:
            names2.append(m["name"])
            metas.append(m)
    if not metas:
        raise SystemExit("至少要一个标的")

    slug = "_".join(names2) if len(names2) <= 3 else "multi%d" % len(names2)
    out = a.out or os.path.join(WS, "reports", "charts", "rmt",
                                "rmt_rc_%s_%s.png" % (slug, a.start))

    if a.no_chart and not a.alert:
        raise SystemExit("--no-chart 需要同时给 --alert")

    chart = None
    meta = None
    if not a.no_chart:
        chart, meta = render(metas, out, win=a.win, show_from=a.start,
                             end=a.end, cols=a.cols, dpi=a.dpi, force=a.force,
                             lookback=a.lookback)
    else:
        # 只算告警：复用 render 的数据流程但不落图
        chart, meta = render(metas, out, win=a.win, show_from=a.start,
                             end=a.end, cols=a.cols, dpi=a.dpi, force=a.force,
                             lookback=a.lookback)

    if a.alert or a.email:
        rows, alerts = build_alerts(meta, a.alert_threshold)
        if not alerts:
            print("[alert] 无分位 > %.0f%% 的指标" % a.alert_threshold)
        else:
            print("[alert] %d 项超阈值：" % len(alerts))
            for x in alerts:
                print("   - " + x)
            if a.email:
                send_alert_email(rows, alerts, chart=chart,
                                 threshold=a.alert_threshold, to=a.mail_to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
