#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 长历史数据层（独立缓存，不动 pws/raw）
三指数全历史：中证1000(2005~) / 科创50(2019-12~) / 创业板(2010~)
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, time
import pandas as pd
import numpy as np

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
WS = os.environ.get("OPENCLAW_WORKSPACE") or __import__("ocsecrets").workspace()
LAB = os.path.join(WS, '.cache', 'pws_lab')
os.makedirs(LAB, exist_ok=True)

IDX = {'000852.SH': '中证1000', '000688.SH': '科创50', '399006.SZ': '创业板指'}


def px(code, end='20261103', refresh=False):
    fp = os.path.join(LAB, f'px_{code.replace(".", "_")}.csv')
    if os.path.exists(fp) and not refresh:
        df = pd.read_csv(fp, dtype={'trade_date': str})
        if len(df) and df.trade_date.max() >= end:
            return df.sort_values('trade_date').reset_index(drop=True)
    import tushare_init as T
    # 长历史用直连（代理 20 年全量常 read timeout）
    p = T.get_pro(token=T.DIRECT_TOKEN, http_url='')
    d = p.index_daily(ts_code=code, start_date='20000101', end_date=end)
    if d is None or not len(d):
        return None
    d = d.sort_values('trade_date').reset_index(drop=True)
    d.to_csv(fp, index=False)
    return d


def load_all(end='20261103'):
    out = {}
    for c, n in IDX.items():
        d = px(c, end)
        if d is None or len(d) < 100:
            continue
        d['close'] = d.close.astype(float)
        d['ret'] = d.close.pct_change()
        out[c] = d
    return out


def panel(end='20261103'):
    """全市场收益宽表（复用 pws 已抓的 386 日面板）"""
    fp = os.path.join(WS, '.cache', 'pws', 'raw', 'mkt_ret.csv')
    m = pd.read_csv(fp, index_col=0)
    m.index = m.index.astype(str)
    return m[m.index <= end]


def pool(index_code):
    j = os.path.join(WS, '.cache', 'pws', 'raw', 'pool_kc_all.json')
    if index_code == '000688.SH':
        import json
        if os.path.exists(j):
            return json.load(open(j))
        return [c for c in panel().columns if c.startswith('688')]
    pj = os.path.join(LAB, f'pool_{index_code.replace(".", "_")}.json')
    import json
    if os.path.exists(pj):
        return json.load(open(pj))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pws_data as D
    codes = D.pool(index_code, '20261103')
    json.dump(codes, open(pj, 'w'))
    return codes


if __name__ == '__main__':
    t = time.time()
    for c, d in load_all().items():
        print(IDX[c], len(d), d.trade_date.iloc[0], d.trade_date.iloc[-1])
    m = panel()
    print('panel', m.shape)
    for c in IDX:
        print('pool', IDX[c], len(pool(c)))
    print('用时', round(time.time() - t, 1), 's')
