#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""α 实验：为什么 M4 恒空 + 到底是不是窗口问题
对比 4 种估计口径（同一批数据、同一指数）：
  A. 现有口径：局部峰谷回撤 + 扩张窗口 + k>=50          （pws_compute.m4_alpha expand）
  B. 现有口径 roll(500) + k>=50
  C. q 日累计跌幅 + 500 日滚动 + 上尾 10% + kmin=15      （phase_ews_v2.hill_alpha）
  D. q 日累计跌幅 + 250/750/1000 日滚动 + kmin=15/30     （窗口敏感性）
输出：每个口径的有效样本数 k 分布、α 分布、α<1.8 触发天数
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys
import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pws_lab_data as L


def dd_series(close):
    n = len(close)
    ext = []
    for i in range(1, n - 1):
        if close[i] >= close[i - 1] and close[i] > close[i + 1]:
            ext.append((i, 'P'))
        elif close[i] <= close[i - 1] and close[i] < close[i + 1]:
            ext.append((i, 'T'))
    res, last_p = [], None
    for i, typ in ext:
        if typ == 'P':
            last_p = i
        elif typ == 'T' and last_p is not None:
            pk, tr = close[last_p], close[i]
            if pk > 0 and tr < pk:
                res.append((i, (pk - tr) / pk))
            last_p = None
    return res


def alpha_A(close, ret, k_min=50):
    """A/B 同源：回撤样本 Hill"""
    out = np.full(len(close), np.nan)
    ks = np.zeros(len(close), int)
    for t in range(60, len(close)):
        c = close[:t + 1]
        dd = dd_series(c)
        if not dd:
            continue
        s = np.array([x[1] for x in dd], float)
        r = ret[1:t + 1]
        r = r[np.isfinite(r)]
        if len(r) < 20:
            continue
        sig = r.std(ddof=0)
        u = max(np.percentile(s, 95), 2.5 * sig * np.sqrt(5))
        tail = s[s > u]
        ks[t] = len(tail)
        if len(tail) < k_min or u <= 0:
            continue
        ml = np.mean(np.log(tail / u))
        if ml > 1e-9:
            out[t] = 1.0 / ml
    return out, ks


def alpha_roll(close, ret, win=500, k_min=50):
    out = np.full(len(close), np.nan)
    ks = np.zeros(len(close), int)
    for t in range(win, len(close)):
        c = close[t - win + 1:t + 1]
        s = np.array([x[1] for x in dd_series(c)], float)
        if len(s) < 20:
            continue
        r = ret[t - win + 2:t + 1]
        r = r[np.isfinite(r)]
        sig = r.std(ddof=0)
        u = max(np.percentile(s, 95), 2.5 * sig * np.sqrt(5))
        tail = s[s > u]
        ks[t] = len(tail)
        if len(tail) < k_min or u <= 0:
            continue
        ml = np.mean(np.log(tail / u))
        if ml > 1e-9:
            out[t] = 1.0 / ml
    return out, ks


def alpha_cum(ret, win=500, q=5, kmin=15, pct=90):
    """C/D：q 日累计跌幅，滚动窗 win，上尾 (100-pct)%，Hill"""
    n = len(ret)
    cq = pd.Series(ret).rolling(q).sum().to_numpy()
    out = np.full(n, np.nan)
    ks = np.zeros(n, int)
    for t in range(win, n):
        s = -cq[t - win + 1:t + 1]
        s = s[np.isfinite(s) & (s > 0)]
        if len(s) < win * 0.5:
            continue
        u = np.percentile(s, pct)
        tail = s[s > u]
        ks[t] = len(tail)
        if len(tail) < kmin or u <= 0:
            continue
        ml = np.mean(np.log(tail / u))
        if ml > 1e-9:
            out[t] = 1.0 / ml
    return out, ks


def desc(x, ks):
    v = x[np.isfinite(x)]
    return (f'n_valid={len(v):<5} k_med={int(np.median(ks[ks>0])) if (ks>0).any() else 0:<4} '
            f'k_max={int(ks.max()):<4} '
            + (f'alpha p10/p50/p90={np.percentile(v,10):.2f}/{np.percentile(v,50):.2f}/{np.percentile(v,90):.2f} '
               f'<1.8={int((v<1.8).sum())}天' if len(v) else 'alpha=全空'))


if __name__ == '__main__':
    for code, df in L.load_all().items():
        name = L.IDX[code]
        close = df.close.to_numpy(float)
        ret = df.ret.to_numpy(float)
        print(f'\n===== {name}  {len(close)} 日  {df.trade_date.iloc[0]}~{df.trade_date.iloc[-1]}')
        a, k = alpha_A(close, ret, 50)
        print('  A expand  k>=50      ', desc(a, k))
        a, k = alpha_roll(close, ret, 500, 50)
        print('  B roll500 k>=50      ', desc(a, k))
        for win in (250, 500, 750, 1000):
            a, k = alpha_cum(ret, win, 5, 15, 90)
            print(f'  C q5 roll{win:<4} kmin15  ', desc(a, k))
        for kmin in (15, 30, 50):
            a, k = alpha_cum(ret, 500, 5, kmin, 90)
            print(f'  C q5 roll500 kmin{kmin:<3}   ', desc(a, k))
        for q in (1, 5, 10, 20):
            a, k = alpha_cum(ret, 500, q, 15, 90)
            print(f'  C q{q:<2} roll500 kmin15  ', desc(a, k))
        for pct in (80, 90, 95):
            a, k = alpha_cum(ret, 500, 5, 15, pct)
            print(f'  C q5 roll500 pct{pct:<3}   ', desc(a, k))
