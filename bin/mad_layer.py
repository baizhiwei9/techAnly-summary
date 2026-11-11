#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAD 均线层（独立模块，不挂 tech_summary 主报告）
=====================================================================
口径与历史「MAD 均线指选股」的均线比价核心一致：

    MAD          = MA20 / MA200 - 1        （短中期均线对长期均线的比价，越大越强）
    通过条件     = 收盘 > MA40  且  MA40 五日斜率 > 0
    排名         = 按 MAD 降序（MAD 只做强弱排序，不参与是否通过的判定）

「比价 + 站上中期均线 + 中期均线向上」三件事合起来，才是这个口径想表达的东西：
只站上 MA40 但 MA40 还在下行 = 反弹，不算通过；MAD 再高但价格跌破 MA40 = 已走弱，也不算通过。

历史实现里 MA20/MA40/MA200 用的是「收盘价 × 复权因子」；本模块直接吃传入的收盘序列，
所以请用前复权/后复权一致的日线（datahub 返回的是不复权价，跨除权日需自行复权）。

用法：
    python3 bin/mad_layer.py --target 600519
    python3 bin/mad_layer.py --pool 沪深300,中证500,科创50,创业板
    python3 bin/mad_layer.py --target 600519 --json
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

# ---------------- 参数（与历史实现一致） ----------------
MA_SHORT, MA_MID, MA_LONG = 20, 40, 200
SLOPE_LAG = 5

ALIAS = {
    "沪深300": "SH510300", "中证500": "SH510500", "中证1000": "SH512100",
    "科创50": "SH588000", "创业板": "SZ159915", "上证50": "SH510050",
    "恒生科技": "SH513180",
}
DEFAULT_POOL = ["沪深300", "中证500", "中证1000", "科创50", "创业板", "上证50", "恒生科技"]


def to_symbol(target):
    t = str(target).strip()
    if t in ALIAS:
        return ALIAS[t], False, t
    if re.fullmatch(r"\d{6}\.(SH|SZ|CSI)", t.upper()):
        return t.upper(), True, t.upper()
    if re.fullmatch(r"(sh|sz)\d{6}", t, re.I):
        return t.upper(), False, t.upper()
    if re.fullmatch(r"\d{6}", t):
        return ("SH" if t[0] in "569" else "SZ") + t, False, t
    raise SystemExit("无法识别的标的：%s（支持中文宽基名 / sh510300 / 600519 / 000905.SH）" % target)


def load(target, days=600):
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
    return d.dropna(subset=["close"]).sort_values("dt").reset_index(drop=True), name


def layer(df):
    """MAD 均线单标的一层；返回 dict"""
    c = pd.Series(df["close"].values, index=pd.to_datetime(df["dt"])).dropna()
    n = len(c)
    if n < MA_LONG:
        return {"status": "insufficient_history", "obs": n,
                "mad": None, "close": round(float(c.iloc[-1]), 4) if n else None,
                "ma20": None, "ma40": None, "ma200": None,
                "above_ma40": None, "ma40_up_lag5": None, "pass_filter": False,
                "reason": "历史不足 %d 根（现 %d 根）" % (MA_LONG, n)}

    ma20 = float(c.iloc[-MA_SHORT:].mean())
    ma40 = float(c.iloc[-MA_MID:].mean())
    ma200 = float(c.iloc[-MA_LONG:].mean())
    ma40_lag = float(c.iloc[-(MA_MID + SLOPE_LAG):-SLOPE_LAG].mean())
    px = float(c.iloc[-1])

    mad = ma20 / ma200 - 1.0
    above = bool(px > ma40)
    up = bool(ma40 > ma40_lag)
    passed = above and up

    if not above:
        reason = "收盘跌破 MA40（%.2f < %.2f）" % (px, ma40)
    elif not up:
        reason = "MA40 五日斜率向下（%.2f → %.2f）" % (ma40_lag, ma40)
    else:
        reason = "站上 MA40 且 MA40 向上，MAD=%.2f%%" % (mad * 100)

    return {"status": "ok", "obs": n, "last_date": str(c.index[-1].date()),
            "close": round(px, 4), "ma20": round(ma20, 4), "ma40": round(ma40, 4),
            "ma200": round(ma200, 4), "mad": round(mad, 6),
            "mad_pct": round(mad * 100, 2),
            "close_over_ma40_pct": round((px / ma40 - 1.0) * 100, 2),
            "ma40_slope_pct_lag5": round((ma40 / ma40_lag - 1.0) * 100, 2),
            "above_ma40": above, "ma40_up_lag5": up,
            "pass_filter": passed, "reason": reason}


def scan(pool, days=600):
    """批量跑（默认宽基池），按 MAD 降序返回明细表"""
    recs = []
    for t in pool:
        try:
            df, name = load(t, days)
            r = layer(df)
        except Exception as e:  # noqa: BLE001
            recs.append({"target": t, "status": "error", "reason": str(e)})
            continue
        r["target"] = name
        recs.append(r)
    ok = [r for r in recs if r.get("status") == "ok"]
    bad = [r for r in recs if r.get("status") != "ok"]
    ok.sort(key=lambda x: x["mad"], reverse=True)
    for i, r in enumerate(ok, 1):
        r["rank"] = i
    return ok + bad


def main():
    ap = argparse.ArgumentParser(description="MAD 均线层（独立模块）")
    ap.add_argument("--target", help="单标的：中文宽基名 / sh510300 / 600519 / 000905.SH")
    ap.add_argument("--pool", help="批量标的，逗号分隔；不给 --target 时默认跑 7 只宽基")
    ap.add_argument("--n", type=int, default=600, help="取多少根日线（默认 600）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    a = ap.parse_args()

    if a.target:
        df, name = load(a.target, a.n)
        r = layer(df)
        r["target"] = name
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print("=" * 62)
            print("MAD 均线  %s   数据截至 %s" % (name, r.get("last_date", "-")))
            print("=" * 62)
            print("结论      %s" % ("通过" if r.get("pass_filter") else "未通过"))
            print("依据      %s" % r["reason"])
            for k, lab in (("close", "收盘"), ("ma20", "MA20"), ("ma40", "MA40"), ("ma200", "MA200"),
                           ("mad_pct", "MAD(%)"), ("close_over_ma40_pct", "距MA40(%)"),
                           ("ma40_slope_pct_lag5", "MA40斜率(%)")):
                print("%-10s %s" % (lab, r.get(k)))
            print("=" * 62)
        return

    pool = [x.strip() for x in a.pool.split(",") if x.strip()] if a.pool else DEFAULT_POOL
    rows = scan(pool, a.n)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    print("=" * 78)
    print("MAD 均线 · 批量（按 MAD 降序，通过条件：收>MA40 且 MA40 五日斜率>0）")
    print("=" * 78)
    print("%-3s %-10s %-9s %-8s %-8s %-9s %-6s %s" %
          ("#", "标的", "收盘", "MAD%", "距MA40%", "MA40斜率%", "通过", "说明"))
    for r in rows:
        if r.get("status") != "ok":
            print("%-3s %-10s %s" % ("-", r["target"], r["reason"]))
            continue
        print("%-3d %-10s %-9.3f %-8.2f %-8.2f %-9.2f %-6s %s" %
              (r["rank"], r["target"], r["close"], r["mad_pct"],
               r["close_over_ma40_pct"], r["ma40_slope_pct_lag5"],
               "是" if r["pass_filter"] else "否", r["reason"]))
    print("=" * 78)
    print("通过 %d / %d" % (sum(1 for r in rows if r.get("pass_filter")), len(rows)))


if __name__ == "__main__":
    main()