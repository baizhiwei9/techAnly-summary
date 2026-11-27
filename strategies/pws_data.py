#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
相变异常检测系统 · 第 1 层：数据层 (Data Layer)
=============================================
职责：只负责取数、缓存、校验。不做任何指标计算。
输出：统一宽表 (date × code)，以及指数价格表。

设计原则
--------
1. **单向依赖**：本层不 import 上层任何模块；上层只通过本层函数取数
2. **落盘缓存**：所有原始数据落 .cache/pws/raw/，重复调用不重复抓
3. **可降级**：TuShare 代理失败 → 直连 → datahub → akshare
4. **幂等更新**：`ensure(to=date)` 只补缺失日期

用法
----
    import pws_data as D
    D.ensure('20261103')                    # 补齐到指定日
    px = D.index_px('000852.SH')            # 指数日线 DataFrame
    ret = D.market_ret('20250301','20261103')  # 全市场收益宽表
    codes = D.pool('000688.SH')             # 成分股/板块池
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import time
import json
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

# 容器 IPv6 不通 → 强制 IPv4
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WS = os.environ.get("OPENCLAW_WORKSPACE") or __import__("ocsecrets").workspace()
RAW = os.path.join(WS, '.cache', 'pws', 'raw')
os.makedirs(RAW, exist_ok=True)

_TS = None


def pro(retries=5):
    """TuShare 实例（代理优先，失败回落直连）"""
    global _TS
    if _TS is not None:
        return _TS
    import tushare_init as T
    last = None
    for i in range(retries):
        try:
            p = T.get_pro()
            p.index_daily(ts_code='000300.SH', start_date='20260101', end_date='20260105')
            _TS = p
            return p
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    # 直连兜底
    try:
        import tushare_init as T
        p = T.get_pro(token=T.DIRECT_TOKEN, http_url='')
        _TS = p
        return p
    except Exception:
        raise last


# ---------------- 指数日线 ----------------
# 五大宽基指数（大佬 2026-11-03 定）：沪深300 / 中证500 / 中证1000 / 科创50 / 创业板指
IDX_NAME = {
    '000300.SH': '沪深300',
    '000905.SH': '中证500',
    '000852.SH': '中证1000',
    '000688.SH': '科创50',
    '399006.SZ': '创业板指',
}
IDX_FILE = {k: k.replace('.', '_') for k in IDX_NAME}


def index_px(ts_code, start='20200101', end=None, refresh=False):
    """指数日线（trade_date/open/high/low/close/vol），带缓存"""
    end = end or time.strftime('%Y%m%d')
    fp = os.path.join(RAW, f'idx_{IDX_FILE[ts_code]}.csv')
    if os.path.exists(fp) and not refresh:
        df = pd.read_csv(fp, dtype={'trade_date': str})
        if len(df) and df.trade_date.max() >= end:
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['ret'] = df.close.pct_change()
            return df[(df.trade_date >= start) & (df.trade_date <= end)].reset_index(drop=True)
    # 增量补齐
    have = pd.DataFrame()
    if os.path.exists(fp):
        have = pd.read_csv(fp, dtype={'trade_date': str})
    s0 = start
    if len(have):
        s0 = max(start, str(int(have.trade_date.max()) + 1))
    parts = [have] if len(have) else []
    if s0 <= end:
        # 统一走 datahub 三级灾备（2026-11-03 大佬要求：取数一律放 DataHub，不要各处自己拉）
        d = None
        try:
            import datahub
            rows = datahub.index_daily(ts_code, end, days=1600)
            if rows:
                d = pd.DataFrame(rows, columns=['trade_date', 'open', 'high', 'low', 'close', 'vol'])
        except Exception as e:
            print(f'[data] datahub 失败 {ts_code}: {e}', file=sys.stderr)
        if d is not None and len(d):
            parts.append(d[d.trade_date.astype(str) >= s0])
        else:
            # datahub 全挂才退回 TuShare 直取（应急兜底）
            try:
                p = pro()
                d2 = p.index_daily(ts_code=ts_code, start_date=s0, end_date=end)
                if d2 is not None and len(d2):
                    parts.append(d2)
            except Exception as e:
                print(f'[data] tushare 也失败 {ts_code}: {e}', file=sys.stderr)
    df = pd.concat(parts) if parts else pd.DataFrame()
    if df.empty:
        return None
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.drop_duplicates('trade_date').sort_values('trade_date').reset_index(drop=True)
    df.to_csv(fp, index=False)
    df['close'] = df.close.astype(float)
    df['ret'] = df.close.pct_change()
    return df[(df.trade_date >= start) & (df.trade_date <= end)].reset_index(drop=True)


def trade_dates(start, end, ref='000852.SH'):
    df = index_px(ref, start, end)
    return [] if df is None else df.trade_date.tolist()


# ---------------- 全市场收益面板 ----------------
MKT_FP = os.path.join(RAW, 'mkt_ret.csv')


def _daily_pct(d, retries=6):
    for i in range(retries):
        try:
            q = pro()
            df = q.daily(trade_date=d)
            if df is not None and len(df):
                return df.set_index('ts_code')['pct_chg']
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def market_ret(start, end, workers=5, progress=None):
    """全市场日收益宽表 (index=date, columns=ts_code)，单位 %"""
    have = pd.DataFrame()
    if os.path.exists(MKT_FP):
        have = pd.read_csv(MKT_FP, index_col=0)
        have.index = have.index.astype(str)
        have = have[(have.index >= start) & (have.index <= end)]
    dates = trade_dates(start, end)
    todo = [d for d in dates if d not in have.index]
    if todo:
        res = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_daily_pct, d): d for d in todo}
            n = 0
            for f in as_completed(futs):
                d = futs[f]
                try:
                    s = f.result()
                except Exception:
                    s = None
                if s is not None:
                    res[d] = s
                n += 1
                if progress and n % 25 == 0:
                    progress(n, len(todo))
                if len(res) >= 60:
                    tmp = pd.DataFrame(res).T
                    allp = pd.concat([have, tmp]) if len(have) else tmp
                    allp = allp[~allp.index.duplicated(keep='last')].sort_index()
                    allp.to_csv(MKT_FP)
                    have = allp; res = {}
        if res:
            tmp = pd.DataFrame(res).T
            allp = pd.concat([have, tmp]) if len(have) else tmp
            allp = allp[~allp.index.duplicated(keep='last')].sort_index()
            allp.to_csv(MKT_FP)
            have = allp
    return have.sort_index()


# ---------------- 成分池 ----------------
KC_CACHE = os.path.join(RAW, 'pool_kc_all.json')


def pool(index_code, date=None):
    """成分池
    000852.SH → 指数成分（index_weight）
    399006.SZ → 指数成分
    000688.SH → 科创板全市场 688（用户 2026-11-03 指定，科创50 成分太小）
    """
    date = date or time.strftime('%Y%m%d')
    if index_code == '000688.SH':
        # 科创板全市场，7 天缓存
        if os.path.exists(KC_CACHE):
            age = time.time() - os.path.getmtime(KC_CACHE)
            if age < 7 * 86400:
                return json.load(open(KC_CACHE))
        p = pro()
        st = p.stock_basic(exchange='', list_status='L', fields='ts_code')
        codes = st[st.ts_code.str.startswith('688')].ts_code.tolist()
        json.dump(codes, open(KC_CACHE, 'w'))
        return codes
    p = pro()
    for back in (0, 5, 15, 30, 60):
        d0 = (pd.Timestamp(date) - pd.Timedelta(days=back)).strftime('%Y%m%d')
        try:
            w = p.index_weight(index_code=index_code, start_date=d0, end_date=date)
            if w is not None and len(w):
                return w[w.trade_date == w.trade_date.max()].con_code.tolist()
        except Exception:
            time.sleep(2)
    return []


def ensure(end, start='20250401', workers=5, verbose=True):
    """一键补齐所有数据到 end"""
    for code in IDX_NAME:
        df = index_px(code, start, end)
        if verbose and df is not None:
            print(f'  指数 {IDX_NAME[code]:<8} {len(df):>4} 日  {df.trade_date.iloc[0]}~{df.trade_date.iloc[-1]}')
    m = market_ret(start, end, workers,
                   progress=(lambda a, b: print(f'  全市场 {a}/{b}')) if verbose else None)
    if verbose:
        print(f'  全市场面板 {m.shape[0]} 日 × {m.shape[1]} 股')
    for code in IDX_NAME:
        pl = pool(code, end)
        if verbose:
            print(f'  池 {IDX_NAME[code]:<8} {len(pl):>4} 只')
    return True


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--end', default=time.strftime('%Y%m%d'))
    ap.add_argument('--start', default='20250401')
    a = ap.parse_args()
    print(f'[数据层] ensure {a.start} ~ {a.end}')
    ensure(a.end, a.start)
    print('[数据层] 完成')
