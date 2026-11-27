#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
相变异常检测系统 · 指标5 截面模块 (pws_xsec)
==========================================
把「全市场收益面板 + 成分池」算成 λmax 时间序列，落盘缓存。
独立于 pws_compute，供其 import，也可单跑。

用法
----
    import pws_xsec as X
    X.build('000852.SH', '20250401', '20261103')   # 算并缓存
    s = X.load_lmax('000852.SH')                    # 读缓存
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import json

import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WS = os.environ.get("OPENCLAW_WORKSPACE") or __import__("ocsecrets").workspace()
XS = os.path.join(WS, '.cache', 'pws', 'xsec')
os.makedirs(XS, exist_ok=True)

TAG = {'000300.SH': 'hs300', '000905.SH': 'zz500', '000852.SH': 'zz1000',
       '000688.SH': 'kc_all', '399006.SZ': 'cyb'}


def build(index_code, start, end, win=20, step=5, min_stock=10):
    import pws_data as D
    panel = D.market_ret(start, end)
    if panel is None or panel.empty:
        return None
    codes = D.pool(index_code, end)
    cols = [c for c in codes if c in panel.columns]
    if len(cols) < min_stock:
        return None
    sub = panel[cols]
    idx = list(sub.index)
    out = {}
    for i in range(win, len(idx), step):
        w = sub.iloc[i - win + 1:i + 1]
        w = w.dropna(axis=1, thresh=int(win * 0.95)).dropna()
        if w.shape[1] < min_stock:
            continue
        C = np.corrcoef(w.to_numpy(float).T)
        C = np.nan_to_num(C, nan=0.0)
        out[idx[i]] = float(np.linalg.eigvalsh(C)[-1])
    s = pd.Series(out).sort_index()
    if s.empty:
        return None
    fp = os.path.join(XS, f'lmax_{TAG[index_code]}.json')
    s.to_json(fp)
    meta = os.path.join(XS, f'meta_{TAG[index_code]}.json')
    json.dump({'index': index_code, 'n_stock': len(cols), 'points': len(s),
               'start': start, 'end': end, 'first': str(s.index[0]), 'last': str(s.index[-1]),
               'win': win, 'step': step}, open(meta, 'w'), ensure_ascii=False, indent=1)
    return s


def load_lmax(index_code, end=None):
    fp = os.path.join(XS, f'lmax_{TAG[index_code]}.json')
    if not os.path.exists(fp):
        return None
    s = pd.read_json(fp, typ='series')
    s.index = s.index.astype(str)
    s = s.sort_index()
    if end:
        s = s[s.index <= end]
    return s if len(s) else None


def build_all(start, end):
    res = {}
    for code, tag in TAG.items():
        print(f'  ▶ {code} ({tag}) ...', end=' ', flush=True)
        s = build(code, start, end)
        if s is None:
            print('skip')
            continue
        print(f'{len(s)} 观测点  {s.index[0]}~{s.index[-1]}')
        res[code] = s
    return res


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20250401')
    ap.add_argument('--end', default=pd.Timestamp.now().strftime('%Y%m%d'))
    a = ap.parse_args()
    print(f'[截面层] {a.start} ~ {a.end}')
    build_all(a.start, a.end)
    print('[截面层] 完成')
