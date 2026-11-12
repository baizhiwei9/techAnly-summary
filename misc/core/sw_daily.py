#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""申万行业指数日线取数（纯数据层，库内共享）

从 weekly_zs.py 抽出来的 **纯取数函数**，没有任何推送 / 报告 / 状态逻辑。
库内 tech_buypoint.py（行业分析）用它；库外的周报 / 行业监控也 import 这里，
方向是「应用 → 库」，库不依赖任何对外推送的应用。

用法：
    from sw_daily import fetch_sw_daily, sw_l2_map, sw_l3_map, stock_sw
    df = fetch_sw_daily("801010.SI")        # 申万行业指数日线（dt/open/high/low/close）

三级降级（2026-11-07）：
    L1 akshare index_hist_sw → 直连申万，最稳最快
    L2 东财行业板块日线       → 网页接口
    L3 tushare sw_daily       → 有权限时最准，但会限流，垫底
"""
from __future__ import annotations

import os
import sys
import json
import time
import socket
import datetime

# 全局硬规矩：禁 IPv6（容器 IPv6 不通会静默卡死）


def _repo_root(start):
    """向上探测仓库根（同时含 chanlun/ 与 misc/ 的那层）"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "chanlun")) and os.path.isdir(os.path.join(d, "misc")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.abspath(start))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
for _sd in ("misc/core", "misc/engine", "chanlun/fractal", "chanlun/ma", "smc", "misc"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# 网络出口策略：默认 IPv4；东财行情族走 IPv6（2026-11-12 东财 K 线仅 IPv6 可达）
# ⚠️ 必须放在 sys.path 建好之后，否则 import net_guard 会失败、退回一刀切禁 IPv6
try:
    from net_guard import install as _net_install
    _net_install()
except Exception:
    _orig_gai = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import pandas as pd

# 缓存目录（和旧 weekly_zs 保持一致，已有 25MB 缓存不用重建）
CACHE = os.path.join(WS, ".cache", "weekly_zs")
SWDIR = os.path.join(CACHE, "sw_daily")
L2_CODE_CACHE = os.path.join(CACHE, "sw_l2.json")
MEMBER_CACHE = os.path.join(CACHE, "sw_member.json")
for _p in (CACHE, SWDIR):
    os.makedirs(_p, exist_ok=True)


def log(*a):
    print(*a, flush=True)


# ────────────────────────── 映射表 ──────────────────────────
def sw_l2_map():
    """申万二级 名称→代码；本地缓存 30 天，缺失自动重建"""
    if os.path.exists(L2_CODE_CACHE):
        try:
            m = json.load(open(L2_CODE_CACHE, encoding="utf-8"))
            if len(m) > 100 and (time.time() - os.path.getmtime(L2_CODE_CACHE)) / 86400 < 30:
                return m
        except Exception:
            pass
    m = {}
    try:
        p = os.path.join(WS, ".cache/longxia/sw_classify_L2.json")
        if os.path.exists(p):
            m = json.load(open(p, encoding="utf-8"))
    except Exception:
        m = {}
    if len(m) < 100:
        try:
            from tushare_init import ts_call
            df = ts_call("index_classify", level="L2", src="SW2021")
            if df is not None and len(df):
                m = {str(r["index_code"]): r["industry_name"] for _, r in df.iterrows()}
        except Exception as e:
            log("[WARN] index_classify L2 失败 %s" % str(e)[:100])
    if m:
        json.dump(m, open(L2_CODE_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return m


def sw_l3_map():
    p = os.path.join(WS, ".cache/longxia/sw_classify_L3.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}


_MEMBER = {}
if os.path.exists(MEMBER_CACHE):
    try:
        _MEMBER = json.load(open(MEMBER_CACHE, encoding="utf-8"))
        _MEMBER = {k: tuple(v) for k, v in _MEMBER.items()}
    except Exception:
        _MEMBER = {}


def _save_member():
    try:
        json.dump({k: list(v) for k, v in _MEMBER.items()},
                  open(MEMBER_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def stock_sw(ts_code):
    """个股 → 申万 (l2_name, l3_name)；A股用 tushare index_member_all，失败返回 (None, None)"""
    if ts_code in _MEMBER:
        return _MEMBER[ts_code]
    res = (None, None)
    try:
        from tushare_init import ts_call
        df = ts_call("index_member_all", ts_code=ts_code)
        if df is not None and len(df):
            r = None
            if "is_new" in df.columns:
                cur = df[df["is_new"].astype(str).str.upper().eq("Y")]
                r = cur.iloc[0] if len(cur) else df.iloc[-1]
            else:
                r = df.iloc[-1]
            res = (str(r.get("l2_name") or "") or None, str(r.get("l3_name") or "") or None)
        time.sleep(0.35)
    except Exception as e:
        log("[WARN] index_member_all %s: %s" % (ts_code, str(e)[:80]))
        time.sleep(0.8)
    if res[0]:
        _MEMBER[ts_code] = res
        _save_member()
    return res


# ────────────────────────── 取数 ──────────────────────────
def _sw_path(code):
    return os.path.join(SWDIR, "%s.csv" % code)


def _ak_timeout(fn, *a, timeout=15, **k):
    """akshare 调用带硬超时（超时立刻切下一层，不干等）"""
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(1)
    f = ex.submit(fn, *a, **k)
    try:
        r = f.result(timeout=timeout)
        ex.shutdown(wait=False)
        return r
    except Exception:
        ex.shutdown(wait=False)
        return None


def _sw_name(code):
    """申万代码 → 行业名（给 L2 东财兜底用）"""
    try:
        m = json.load(open(os.path.join(WS, ".cache", "sw_l2_name.json"), encoding="utf-8"))
        return m.get(code)
    except Exception:
        pass
    try:
        mm = sw_l2_map()
        return mm.get(code)
    except Exception:
        return None


def fetch_sw_daily(code, start="20200101", ttl_min=None):
    """申万行业指数日线（dt, open/high/low/close），按 mtime 做增量缓存

    ⚠️ 2026-10-26 修：原来按「末行日期距今 ≤4 天」判缓存新鲜 → 周一会读到上周五的缓存，
    盘中/收盘后都拿不到当日数据。改成 **mtime TTL**（默认 180 分钟，可用 SWD_TTL_MIN 覆盖），
    过期就增量补最新。
    """
    p = _sw_path(code)
    today8 = datetime.date.today().strftime("%Y%m%d")
    ttl = float(os.environ.get("SWD_TTL_MIN", "180")) if ttl_min is None else ttl_min
    old = None
    if os.path.exists(p):
        try:
            old = pd.read_csv(p, parse_dates=["dt"])
            if old is not None and len(old) > 100:
                age_min = (time.time() - os.path.getmtime(p)) / 60.0
                if age_min <= ttl:
                    return old
        except Exception:
            old = None
    beg = start
    if old is not None and len(old) > 100:
        beg = (old["dt"].max() - pd.Timedelta(days=7)).strftime("%Y%m%d")
    df = None
    layer = None

    # ── L1 akshare（申万代码去掉 .SI 后缀）
    try:
        import akshare as ak
        sym = code.split(".")[0]
        a = _ak_timeout(ak.index_hist_sw, symbol=sym, period="day", timeout=20)
        if a is not None and len(a):
            a = a.rename(columns={"日期": "dt", "收盘": "close", "开盘": "open",
                                  "最高": "high", "最低": "low"})
            a["dt"] = pd.to_datetime(a["dt"])
            df = a[["dt", "open", "high", "low", "close"]]
            layer = "L1-akshare"
    except Exception as e:
        log("[sw] %s L1 akshare 失败 %s" % (code, str(e)[:60]))

    # ── L2 东财行业板块（按名称匹配）
    if df is None or not len(df):
        try:
            import akshare as ak
            nm = _sw_name(code)
            if nm:
                a = _ak_timeout(ak.stock_board_industry_hist_em, symbol=nm,
                                period="日k", start_date="20200101",
                                end_date=today8, timeout=20)
                if a is not None and len(a):
                    a = a.rename(columns={"日期": "dt", "收盘": "close", "开盘": "open",
                                          "最高": "high", "最低": "low"})
                    a["dt"] = pd.to_datetime(a["dt"])
                    df = a[["dt", "open", "high", "low", "close"]]
                    layer = "L2-eastmoney"
        except Exception as e:
            log("[sw] %s L2 东财失败 %s" % (code, str(e)[:60]))

    # ── L3 tushare（垫底，8s 硬超时）
    if df is None or not len(df):
        try:
            from tushare_init import ts_call
            df = ts_call("sw_daily", ts_code=code, start_date=beg, end_date=today8)
            if df is not None and len(df):
                layer = "L3-tushare"
                df = df.rename(columns={"trade_date": "dt"})
        except Exception:
            pass

    if df is None or not len(df):
        return old
    if layer:
        log("[sw] %s ← %s（%d 行）" % (code, layer, len(df)))
    df["dt"] = pd.to_datetime(df["dt"])
    df = df[["dt", "open", "high", "low", "close"]].sort_values("dt")
    if old is not None and len(old):
        df = pd.concat([old, df]).drop_duplicates("dt", keep="last").sort_values("dt")
    df = df.reset_index(drop=True)
    try:
        df.to_csv(p, index=False)
    except Exception:
        pass
    return df


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="申万行业指数日线取数（自检）")
    ap.add_argument("--code", default="801010.SI", help="申万行业代码")
    a = ap.parse_args()
    d = fetch_sw_daily(a.code)
    if d is None:
        print("取数失败")
    else:
        print("%s：%d 行，%s ~ %s" % (a.code, len(d), d["dt"].min().date(), d["dt"].max().date()))
        print(d.tail(3).to_string(index=False))
