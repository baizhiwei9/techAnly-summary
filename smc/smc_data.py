#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 数据获取引擎
================================================================
多级回退抓取 1H 母数据（及 day/week 派生需要的高级别），落盘缓存：
  宽基 ETF : 腾讯 mkline(60m,320根) -> 新浪K线 -> akshare -> tushare fund_daily(日线补长历史)
  期货     : 新浪 futures_zh_minute_sina(60m,1023根) -> 新浪 1min 分时聚合 -> akshare daily(长历史)

缓存：smc/cache/{code}_60m.csv / _1d.csv
"""
from __future__ import annotations
import os
import socket

# 网络出口策略：默认 IPv4；东财行情族走 IPv6（2026-11-12 东财 K 线仅 IPv6 可达）
# net_guard 在 misc/core/，import 前先把候选目录塞进 sys.path
import sys as _sys
import os as _os
for _guess in (
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "misc", "core"),
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "misc", "core"),
        _os.path.dirname(_os.path.abspath(__file__)),
):
    _g = _os.path.normpath(_guess)
    if _os.path.isdir(_g) and _g not in _sys.path:
        _sys.path.insert(0, _g)
try:
    from net_guard import install as _net_install
    _net_install()
except Exception:
    _orig_gai = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]
import time
import sys
from datetime import datetime


import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smc_config import CACHE, INSTRUMENTS

# ────────────────────── 取数统一（2026-11-13）──────────────────────
# 大佬定：「技术分析这个玩意你不要自己取数了，就用 DataHub 来取数」。
# 本模块只保留 data_hub 取不到的（期货分钟/期货日线），其余走 dh_bridge。
# 硬约束：data_hub 只读；每个点失败一律降级老链路，算法一行不改。
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "misc", "core"))
    import dh_bridge as _DHB
except Exception:                                              # noqa: BLE001
    _DHB = None

# ── 东财开关（2026-11-14 大佬定：默认一个东财请求都不发，它那边一直限流）──
_ALLOW_EM = os.environ.get("TECH_ALLOW_EM", "0").strip().lower() in ("1", "true", "yes", "on")


def _log(m):
    print(f"[smc_data] {m}", flush=True)


def _drop_inprogress(d, tf="60m"):
    """丢弃「正在形成」的最后一根 K 线。

    ⚠️ 2026-10-08 重大 bug：新浪/akshare 对期货 60m 会把**当前正在走的那根**
    标成该 K 线的**结束时间**（如 09:30 时返回一根 `10:00` 的 bar，实际只走了
    几分钟）。旧代码把它当已完结 bar 用/缓存 → 事件时间戳出现「未来时间」（用户
    看到 3 月的开仓/平仓，其实是错位）。
    此处：若最后一根 bar 的时间戳 > 当前时间 → 判定未完结，丢弃。"""
    if d is None or len(d) == 0:
        return d
    now = pd.Timestamp(datetime.now())
    try:
        last = pd.Timestamp(d["dt"].iloc[-1])
    except Exception:
        return d
    if last > now + pd.Timedelta(seconds=5):
        d = d.iloc[:-1].reset_index(drop=True)
        _log(f"丢弃未完结 bar {last}（当前 {now:%H:%M}）")
    return d


def _norm(df, tcol="t"):
    if df is None or len(df) == 0:
        return None
    d = df.copy()
    if "day" in d.columns and tcol not in d.columns:
        tcol = "day"
    if tcol not in d.columns:
        for c in ("datetime", "dt", "date", "t"):
            if c in d.columns:
                tcol = c
                break
    d = d.rename(columns={tcol: "dt"})
    d["dt"] = pd.to_datetime(d["dt"])
    for c in ["open", "high", "low", "close", "volume"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[["dt", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    return d.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)


# ── 宽基 ETF 分钟线（60m / 30m）──────────────────────────────
def _etf_min(code, per, n=320):
    """先取更长的新浪 K 线(datalen 1023)，再回退腾讯 320 根。"""
    from minute_hub import tx_kline, sina_kline_v2, sina_kline
    for fn, tag, nl in ((sina_kline_v2, "SINA", 1023), (tx_kline, "TX", n), (sina_kline, "AK", None)):
        try:
            if fn is sina_kline:
                d = fn(code, per)
            elif fn is sina_kline_v2:
                d = fn(code, per, datalen=nl)
            else:
                d = fn(code, per, nl)
            d = _norm(d)
            if d is not None and len(d) > 100:
                _log(f"{code} {per}m via {tag} rows={len(d)}")
                return d
        except Exception as e:
            _log(f"{code} {tag} {per}m 失败: {str(e)[:60]}")
    return None


def _etf_60m(code, n=320):
    return _etf_min(code, "60", n)


def _etf_30m(code):
    return _etf_min(code, "30", 320)


# ── 期货分钟线（60m / 30m）──────────────────────────────────
def _fut_min(code, per):
    """期货分钟线：新浪 akshare（带重试）→ minute_hub → 磁盘缓存兑底。

    ⚠️ 2026-10-08：新浪 `futures_zh_minute_sina` 偶发 RemoteDisconnected，
    旧代码只试一次就放弃 → 整轮不更新、还把上一次的陈旧数据当成新数据。
    现在重试 2 次；仍失败则**不返回 None**（由 get() 回退到缓存）。

    取数（2026-11-14 补齐）：**data_hub `minute_bar(kind="fut")`** 优先
        （data_hub 2026-11-14 修了 `_norm_code` 期货 bug，新增 `_norm_fut_code`，
         'IF0' 不再被补成 'shif0'；实测 IF0 60m 1023 根正常）
        → 失败降级新浪 akshare → minute_hub（原链路一行未改）
    """
    # ⓪ data_hub 期货分钟
    try:
        if _DHB is not None:
            d = _DHB.minute_bar(code, per, 1500, "fut")
            if d is not None and len(d) > 100:
                d = _drop_inprogress(d, per)
                if d is not None and len(d) > 100:
                    _log(f"{code} {per}m via data_hub rows={len(d)}")
                    return d
    except Exception as e:                                       # noqa: BLE001
        _log(f"{code} {per}m data_hub失败: {str(e)[:60]}")

    last_err = None
    for attempt in range(2):
        try:
            import akshare as ak
            d = _norm(ak.futures_zh_minute_sina(symbol=code, period=per))
            d = _drop_inprogress(d, per)
            if d is not None and len(d) > 100:
                _log(f"{code} {per}m via SINA rows={len(d)}")
                return d
        except Exception as e:
            last_err = str(e)[:60]
            time.sleep(1.0)
    _log(f"{code} 新浪{per}m失败({last_err})")
    try:
        from minute_hub import get_kline
        d = _norm(get_kline(code, per, kind="fut", cache=False))
        d = _drop_inprogress(d, per)
        if d is not None and len(d) > 100:
            return d
    except Exception as e:
        _log(f"{code} minute_hub失败: {str(e)[:60]}")
    return None


def _fut_60m(code):
    return _fut_min(code, "60")


def _fut_30m(code):
    return _fut_min(code, "30")


# ── 日线（补长历史，用于 day/week 级别回测与长周期 bias）─────
def _etf_daily(code):
    """ETF/股票日线（长历史）。股票/ETF 交易级别=日线，需尽量长的历史。

    取数（2026-11-13 统一）：**data_hub `etf_daily`** 优先 → 老链路兜底
    （tushare fund_daily → akshare fund_etf_hist_sina，默认零东财）。
    ⚠️ etf_daily 给 trade_date/vol 字段，需映射成 dt/volume；不复权（adjust=""），
       与原 tushare fund_daily 口径一致，不影响 SMC 结构判定。
    """
    ts = code[2:] + (".SH" if code.startswith("sh") else ".SZ")
    # ① data_hub（ETF 日线，不复权，口径与原 fund_daily 一致）
    if _DHB is not None:
        try:
            d = _DHB.etf_daily(ts, start="20180101")
            if d is not None and len(d) > 100:
                _log(f"{code} 日线 ← data_hub etf_daily rows={len(d)}")
                return d[["dt", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
        except Exception as e:                                 # noqa: BLE001
            _log(f"{code} data_hub etf_daily 失败 {str(e)[:50]} → 降级老链路")
    # ② 老链路兜底（原样保留）
    return _etf_daily_local(code)


def _etf_daily_local(code):
    """老链路兜底：tushare fund_daily → akshare fund_etf_hist_sina（默认零东财）

    2026-11-14：akshare 这一层从东财 `fund_etf_hist_em` 换成新浪 `fund_etf_hist_sina`
      （无日期参数，全量下发，本地按 start 切片）；东财版保留但只在 TECH_ALLOW_EM=1 时走。
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
    try:
        from tushare_init import ts_call
        ts = code[2:] + (".SH" if code.startswith("sh") else ".SZ")
        d = None
        for _try in range(2):            # tushare 偶发 RemoteDisconnected，重试一次
            d = ts_call("fund_daily", ts_code=ts, start_date="20180101")
            if d is not None and len(d) > 0:
                break
            time.sleep(1)
        if d is not None and len(d) > 0:
            d = d.rename(columns={"trade_date": "dt", "vol": "volume"})
            d["dt"] = pd.to_datetime(d["dt"])
            if "volume" not in d.columns:
                d["volume"] = float("nan")
            return d[["dt", "open", "high", "low", "close", "volume"]].sort_values("dt").reset_index(drop=True)
    except Exception as e:
        _log(f"{code} tushare 日线失败: {str(e)[:60]}")
    try:
        import akshare as ak
        d = _norm(ak.fund_etf_hist_sina(symbol=code), tcol="date")
        if d is None or not len(d):
            # 东财分支（默认关闭，TECH_ALLOW_EM=1 才进）
            if _ALLOW_EM:
                d = _norm(ak.fund_etf_hist_em(symbol=code[2:], period="daily",
                                              start_date="20180101", end_date="20991231",
                                              adjust=""), tcol="日期")
        if d is not None and len(d):
            d = d[d["dt"] >= pd.to_datetime("20180101")].reset_index(drop=True)
            _log(f"{code} 日线 via akshare(新浪) rows={len(d)}")
        return d
    except Exception as e:
        _log(f"{code} akshare 日线失败: {str(e)[:60]}")
        return None


def _fut_daily(code):
    """期货主力连续日线（新浪）。

    取数（2026-11-14 补齐）：**data_hub `futures_main_daily`** 优先（本轮新增
        data_type，走新浪 `futures_main_sina`，实测 IF0 451 根 / AG0 290 根）
        → 失败降级 akshare `futures_zh_daily_sina`（原链路一行未改）。
    """
    try:
        if _DHB is not None:
            d = _DHB.futures_main_daily(code)
            if d is not None and len(d) > 100:
                dd = d.rename(columns={"trade_date": "dt", "vol": "volume"})
                if "dt" not in dd.columns:
                    for c in ("date", "trade_date"):
                        if c in dd.columns:
                            dd = dd.rename(columns={c: "dt"})
                            break
                if "volume" not in dd.columns and "vol" in dd.columns:
                    dd = dd.rename(columns={"vol": "volume"})
                _log(f"{code} 日线 via data_hub rows={len(dd)}")
                return dd
    except Exception as e:                                       # noqa: BLE001
        _log(f"{code} 日线 data_hub失败: {str(e)[:60]}")
    try:
        import akshare as ak
        d = _norm(ak.futures_zh_daily_sina(symbol=code), tcol="date")
        return d
    except Exception as e:
        _log(f"{code} 期货日线失败: {str(e)[:60]}")
        return None


# ── 对外接口 ────────────────────────────────────────────────
def _merge_cache(fn, d):
    """把新抓到的行情并入已有缓存（并集去重，新数据覆盖同日K线）。

    ⚠️ 2026-10-08 重大 bug：新浪期货 60m 只返回**滚动最近 ~1023 根**，
    旧代码是「抓到什么就落盘成什么」→ 每跑一轮窗口就向前滑动、起点上移，
    回测需要重算导致早期事件被当成新事件重复推送（用户看到的「玉米开仓连着
    发好几条」+「时间是 1、2 月」就是这个原因）。
    改为并集保留：历史只增不减 → 回测起点固定 → 事件不重复。"""
    if d is None or len(d) == 0:
        return d
    d = d.copy()
    d["dt"] = pd.to_datetime(d["dt"])
    if os.path.exists(fn):
        try:
            old = pd.read_csv(fn)
            old["dt"] = pd.to_datetime(old["dt"])
            n_old = len(old)
            d = pd.concat([old, d], ignore_index=True)
            d = d.drop_duplicates(subset="dt", keep="last").sort_values("dt").reset_index(drop=True)
            if len(d) > n_old + 1:
                _log(f"缓存合并 新增 {len(d) - n_old} 根（现有 {len(d)}）")
        except Exception:
            pass
    return d


# ── 对外接口 ────────────────────────────────────────────────
def get(code, kind, freq="60m", refresh=False):
    """取某个标的的 60m / 30m / 1d 数据（带磁盘缓存，当日已抓过则复用）。
    ⚠️ 股票/ETF 交易级别=日线（freq=1d），期货=h1（freq=60m）。
    刷新时**并入**旧缓存（不截断历史），保证回测起点稳定。"""
    fn = os.path.join(CACHE, f"{code}_{freq}.csv")
    if not refresh and os.path.exists(fn):
        try:
            d = pd.read_csv(fn)
            d["dt"] = pd.to_datetime(d["dt"])
            return d
        except Exception:
            pass
    if freq == "60m":
        d = _etf_60m(code) if kind == "stock" else _fut_60m(code)
    elif freq == "30m":
        d = _etf_30m(code) if kind == "stock" else _fut_30m(code)
    elif freq == "1d":
        d = _etf_daily(code) if kind == "stock" else _fut_daily(code)
    else:
        raise ValueError(freq)
    # 抓取失败 → 回退磁盘缓存（绝不让整轮因单个品种网络抖动而丢数据）
    if (d is None or len(d) == 0) and os.path.exists(fn):
        try:
            d = pd.read_csv(fn)
            d["dt"] = pd.to_datetime(d["dt"])
            _log(f"{code} {freq} 抓取失败 → 用缓存 rows={len(d)}")
        except Exception:
            pass
    d = _merge_cache(fn, d)
    if d is not None and len(d):
        d.to_csv(fn, index=False)
    return d


def fetch_all(refresh=False, quiet=False):
    """按各标的自己的交易级别抓母数据：股票/ETF 抓 1d，期货抓 60m（另抓 30m/1d 备用）。"""
    from smc_config import level_of, base_freq
    out = {}
    for it in INSTRUMENTS:
        r = {}
        fqs = ("1d", "30m", "60m") if level_of(it) == "day" else ("60m", "30m", "1d")
        for fq in fqs:
            try:
                d = get(it["code"], it["kind"], fq, refresh=refresh)
            except Exception as e:
                d = None
                _log(f"{it['code']} {fq} 异常: {str(e)[:50]}")
            r[fq] = 0 if d is None else len(d)
        out[it["code"]] = r
        if not quiet:
            print(f"  {it['name']:10s} {it['code']:8s} 60m={r['60m']:5d} 30m={r['30m']:5d} 1d={r['1d']:5d}")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    print("=== SMC 数据获取 ===")
    fetch_all(refresh=a.refresh)
