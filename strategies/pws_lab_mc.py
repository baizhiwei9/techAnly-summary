#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 蒙特卡洛随机对照（最关键的一步）
问题：S2 命中 75% 的大跌峰，是真的有本事，还是因为 onset 太多、撒得够密？

做法：保持 onset 个数不变，随机撒在同样区间 N=2000 次，看随机能覆盖多少峰。
  若真实命中的峰数落在随机分布的 95% 区间内 → 没有信息量。
  只有显著高于随机 95% 分位，才算"这套指标提前定位到了崩盘"。
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
import pws_lab_grid as G

z = np.load(G.ALERT_FP, allow_pickle=True)
LEAD_WIN = 40
N_SIM = 2000


def onsets(a, quiet=10):
    out = np.zeros(len(a), bool)
    for t in range(len(a)):
        if a[t] and not a[max(0, t - quiet):t].any():
            out[t] = True
    return out


def crash_peaks(close, min_dd=0.15, min_gap=30):
    n = len(close)
    peaks, last = [], -10 ** 9
    for t in range(5, n - 5):
        if close[t] < max(close[max(0, t - 5):t].max(initial=0), close[t + 1:t + 6].max(initial=0)):
            continue
        fw = close[t:t + 61]
        if len(fw) < 10:
            continue
        if fw.min() / close[t] - 1 <= -min_dd and t - last >= min_gap:
            peaks.append(t)
            last = t
    return np.array(peaks)


def cover(onset_idx, peaks, win=LEAD_WIN):
    """有多少峰在 onset 后 win 交易日内"""
    hit = 0
    for p in peaks:
        if np.any((onset_idx <= p) & (onset_idx >= p - win)):
            hit += 1
    return hit


def main(seed=42):
    rng = np.random.default_rng(seed)
    schemes = {
        'S1 现行 M2-2σ+M1持续5日': lambda b: b['M1_resid_persist5_t0.6'] | b['M2_2.0'],
        'S2 M1严(持续3日)+M2严(3σ)': lambda b: b['M1_resid_persist3_t0.6'] & b['M2_3.0'],
        'S7 M2严(3σ) 单独': lambda b: b['M2_3.0'],
    }
    print(f"{'方案':<26}{'指数':<9}{'峰数':>5}{'真实命中':>7}{'随机均值':>8}{'随机95%':>8}{'比值':>7}{'判定':>6}")
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        close = L.load_all()[code].close.to_numpy(float)
        base = {k.split('|', 1)[1]: z[k].astype(bool) for k in z.files if k.startswith(f'{code}|')}
        peaks = crash_peaks(close)
        n = len(close)
        for label, fn in schemes.items():
            al = fn(base)
            oi = np.where(onsets(al))[0]
            real = cover(oi, peaks, LEAD_WIN)
            sims = np.array([cover(np.sort(rng.choice(n, len(oi), replace=False)), peaks, LEAD_WIN)
                             for _ in range(N_SIM)]) if len(oi) else np.zeros(N_SIM)
            p95 = np.percentile(sims, 95)
            verdict = '✅有信息' if real > p95 else ('⚠️持平' if real >= np.percentile(sims, 50) else '❌无信息')
            print(f'{label:<26}{name:<9}{len(peaks):>5}{real:>7}{sims.mean():>8.1f}{p95:>8.1f}'
                  f'{real/max(sims.mean(),1e-9):>7.2f}{verdict:>6}')


if __name__ == '__main__':
    main()
