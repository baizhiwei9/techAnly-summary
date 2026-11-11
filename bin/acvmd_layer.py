#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AC1-VMD 趋势层（独立模块，不挂 tech_summary 主报告）
=====================================================================
口径与历史 AC_VMD 指数日报 / 技术分析宽基汇总的 AC1-VMD 一致：

    regime = good  当且仅当  AC1(20) > 0.05  且  20 日实现波动率 < 全样本 70 分位
    VMD     K=4, alpha=2000, 取低序 2 个模态合成趋势，取末 5 点斜率 k
    多：regime=good 且 k>0 且 close>MA40 且 MA40 五日斜率>0
    空：regime=bad 或 k<0
    其余：震荡

三层含义：AC1 判「当前是不是趋势市」，VMD 判「低频趋势朝哪走」，MA40 判「价格站没站在中期均线上」。

用法：
    python3 bin/acvmd_layer.py --target 中证500
    python3 bin/acvmd_layer.py --target 600519 --n 800
    python3 bin/acvmd_layer.py --target 科创50 --json

注意：波动率分位用的是全样本分位（实时口径，与历史日报一致）。
      若要拿去做回测，须改成「截至当日」的 expansion 分位，否则是前视。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# ── 全局硬规矩：禁 IPv6（容器 IPv6 不通会静默卡死）──
import socket

_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))

import datahub as DH  # noqa: E402

try:
    from vmdpy import VMD  # noqa: E402
except Exception:  # noqa: BLE001
    VMD = None

# ---------------- 参数（与历史实现一致，改这里即改口径） ----------------
AC1_WINDOW = 20
AC1_GOOD_MIN = 0.05
VOL_PCT = 0.70
VMD_WINDOW = 60
VMD_K = 4
VMD_ALPHA = 2000
VMD_DC = 0
VMD_INIT = 1
VMD_TOL = 1e-7
VMD_N_LOW = 2
VMD_SLOPE_N = 5
MA40 = 40
SLOPE_LAG = 5

# 中文宽基名 → ETF（与 AC_VMD 日报的标的池一致）
ALIAS = {
    "沪深300": "SH510300", "中证500": "SH510500", "中证1000": "SH512100",
    "科创50": "SH588000", "创业板": "SZ159915", "上证50": "SH510050",
    "恒生科技": "SH513180",
}


def to_symbol(target):
    """目标 → datahub 符号；返回 (symbol, is_index, 展示名)"""
    t = str(target).strip()
    if t in ALIAS:
        return ALIAS[t], False, t
    if re.fullmatch(r"\d{6}\.(SH|SZ|CSI)", t.upper()):
        return t.upper(), True, t.upper()
    if re.fullmatch(r"(sh|sz)\d{6}", t, re.I):
        s = t.upper()
        return s, False, s
    if re.fullmatch(r"\d{6}", t):
        head = t[0]
        pre = "SH" if head in "569" else "SZ"
        return pre + t, False, t
    raise SystemExit("无法识别的标的：%s（支持中文宽基名 / sh510300 / 600519 / 000905.SH）" % target)


def load(target, days=800):
    """取日线（自动多源回退），返回 DataFrame[dt,open,high,low,close,volume]"""
    sym, is_idx, name = to_symbol(target)
    try:
        rows = DH.index_daily(sym, days=days) if is_idx else DH.daily(sym, days=days)
    except ImportError:
        # 本机没装 akshare/tushare 时，直接走 web 源（腾讯/东财），不依赖 L1/L2
        rows = DH.daily(sym, days=days, layers=("web",))
    if not rows:
        raise RuntimeError("取数失败：%s" % sym)
    d = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
    d["dt"] = pd.to_datetime(d["dt"], format="%Y%m%d", errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["close"]).sort_values("dt").reset_index(drop=True)
    return d, name


def ac1(r, window=AC1_WINDOW):
    """lag-1 自相关（窗口末端 window 个收益率）"""
    r = pd.Series(r).dropna().values
    if len(r) < window:
        return float("nan")
    r = r[-window:]
    r1, r2 = r[:-1], r[1:]
    s1, s2 = r1.std(ddof=1), r2.std(ddof=1)
    if s1 == 0 or s2 == 0 or not np.isfinite(s1) or not np.isfinite(s2):
        return 0.0
    return float(np.mean((r1 - r1.mean()) * (r2 - r2.mean())) / (s1 * s2))


def vmd_slope(close):
    """末 VMD_WINDOW 根价格做 VMD，低序 2 模态合成趋势的末段斜率"""
    if VMD is None:
        return float("nan")
    sig = np.asarray(pd.Series(close).dropna().values[-VMD_WINDOW:], dtype=float)
    if len(sig) < VMD_K + 2:
        return float("nan")
    try:
        u, _, _ = VMD(sig, alpha=VMD_ALPHA, tau=0.0, K=VMD_K, DC=VMD_DC, init=VMD_INIT, tol=VMD_TOL)
    except Exception:  # noqa: BLE001
        return float("nan")
    u = np.asarray(u)
    n_use = min(VMD_N_LOW, u.shape[0])
    trend = np.sum(u[:n_use], axis=0)
    y = trend[-VMD_SLOPE_N:]
    if len(y) < 2:
        return float("nan")
    return float((y[-1] - y[0]) / max(len(y) - 1, 1))


def layer(df):
    """AC1-VMD 单标的一层；返回 dict（view 取 多/空/震荡）"""
    c = pd.Series(df["close"].values, index=pd.to_datetime(df["dt"]))
    if len(c) < VMD_WINDOW + 5:
        return {"view": "震荡", "reason": "数据不足（< %d 根）" % (VMD_WINDOW + 5),
                "ac1": None, "k": None, "close": None, "ma40": None, "vol_ok": None}

    ret = c.pct_change()
    a1 = ac1(ret)
    vol20 = ret.rolling(20).std()
    v_now = vol20.iloc[-1]
    v_th = vol20.quantile(VOL_PCT)
    vol_ok = bool(np.isfinite(v_now) and np.isfinite(v_th) and v_now < v_th)
    reg_good = bool(np.isfinite(a1) and a1 > AC1_GOOD_MIN and vol_ok)

    k = vmd_slope(c)
    ma40 = c.rolling(MA40).mean()
    p = float(c.iloc[-1])
    ma40_now = float(ma40.iloc[-1]) if np.isfinite(ma40.iloc[-1]) else float("nan")
    ma40_lag = ma40.iloc[-1 - SLOPE_LAG] if len(ma40) > SLOPE_LAG else np.nan
    ma40_slope = float(ma40.iloc[-1] - ma40_lag) if np.isfinite(ma40_lag) else float("nan")

    out = {
        "ac1": round(a1, 4) if np.isfinite(a1) else None,
        "vol20": round(float(v_now), 6) if np.isfinite(v_now) else None,
        "vol_th": round(float(v_th), 6) if np.isfinite(v_th) else None,
        "vol_ok": vol_ok,
        "regime": "good" if reg_good else "bad",
        "k": round(k, 6) if np.isfinite(k) else None,
        "close": round(p, 4),
        "ma40": round(ma40_now, 4) if np.isfinite(ma40_now) else None,
        "ma40_slope": round(ma40_slope, 4) if np.isfinite(ma40_slope) else None,
    }

    if reg_good and np.isfinite(k) and k > 0 and np.isfinite(ma40_now) and p > ma40_now \
            and np.isfinite(ma40_slope) and ma40_slope > 0:
        out["view"] = "多"
        out["reason"] = "regime=good，VMD趋势上行(k=%.5f)，收>MA40" % k
    elif (not reg_good) or (np.isfinite(k) and k < 0):
        out["view"] = "空"
        out["reason"] = "AC1/波动率regime=bad" if not reg_good else "VMD趋势下行(k=%.5f)" % k
    else:
        out["view"] = "震荡"
        out["reason"] = "ac1=%s, k=%s" % (out["ac1"], out["k"])
    return out


def main():
    ap = argparse.ArgumentParser(description="AC1-VMD 趋势层（独立模块）")
    ap.add_argument("--target", required=True, help="中文宽基名 / sh510300 / 600519 / 000905.SH")
    ap.add_argument("--n", type=int, default=800, help="取多少根日线（默认 800）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    a = ap.parse_args()

    if VMD is None:
        raise SystemExit("缺依赖 vmdpy：pip install vmdpy")

    df, name = load(a.target, a.n)
    r = layer(df)
    r.update({"target": name, "last_date": str(df["dt"].iloc[-1].date()), "bars": len(df)})

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return

    print("=" * 62)
    print("AC1-VMD  %s   数据截至 %s（%d 根）" % (name, r["last_date"], r["bars"]))
    print("=" * 62)
    print("结论      %s" % r["view"])
    print("依据      %s" % r["reason"])
    print("AC1(20)   %s" % r["ac1"])
    print("regime    %s（vol20=%s，阈值=%s）" % (r["regime"], r["vol20"], r["vol_th"]))
    print("VMD 斜率  %s" % r["k"])
    print("收盘/MA40 %s / %s（MA40 五日斜率 %s）" % (r["close"], r["ma40"], r["ma40_slope"]))
    print("=" * 62)


if __name__ == "__main__":
    main()