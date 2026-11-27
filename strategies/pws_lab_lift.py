#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 有没有真本事？—— 基础概率对照（base rate）+ 增量分解

base rate = 随机挑一天，未来 20 日最大跌幅 >=8% 的概率。
精确率若 ≈ base rate，等于没有信息量（只是天天喊跌）。
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
HORIZON = 20
DROP = 0.08


def fwd_dd(close, h=HORIZON):
    n = len(close)
    out = np.full(n, np.nan)
    for t in range(n - h):
        fw = close[t:t + h + 1]
        out[t] = fw.min() / close[t] - 1
    return out


def onsets(a, quiet=10):
    out = np.zeros(len(a), bool)
    for t in range(len(a)):
        if a[t] and not a[max(0, t - quiet):t].any():
            out[t] = True
    return out


def main():
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        close = L.load_all()[code].close.to_numpy(float)
        fd = fwd_dd(close)
        base = {k.split('|', 1)[1]: z[k].astype(bool) for k in z.files if k.startswith(f'{code}|')}
        ev = (fd <= -DROP)
        br = ev[np.isfinite(fd)].mean()

        print(f'\n===== {name}  全历史 {len(dates)} 日  base rate = {br*100:.1f}% '
              f'(随机日命中率，任何精确率要显著高于它才算有信息)')
        tests = {
            'M2 2σ（现行）': base['M2_2.0'],
            'M2 3σ': base['M2_3.0'],
            'M1 持续5日>0.6（现行）': base['M1_resid_persist5_t0.6'],
            'M1 持续3日>0.6': base['M1_resid_persist3_t0.6'],
            'M1连涨3日>0.6': base['M1_resid_rise3_t0.6'],
            'M2 3σ AND M1持续3日': base['M2_3.0'] & base['M1_resid_persist3_t0.6'],
            'M2 3σ AND 收盘<MA20': None,
            'M2 3σ AND M1持续3日 AND 收盘<MA20': None,
            'M2 3σ AND M1持续3日 AND 距60日高点<3%': None,
        }
        ma20 = pd.Series(close).rolling(20).mean().to_numpy()
        hh60 = pd.Series(close).rolling(60).max().to_numpy()
        below = close < ma20
        nearhigh = close / hh60 - 1 > -0.03
        tests['M2 3σ AND 收盘<MA20'] = base['M2_3.0'] & below
        tests['M2 3σ AND M1持续3日 AND 收盘<MA20'] = base['M2_3.0'] & base['M1_resid_persist3_t0.6'] & below
        tests['M2 3σ AND M1持续3日 AND 距60日高点<3%'] = base['M2_3.0'] & base['M1_resid_persist3_t0.6'] & nearhigh

        print(f"{'条件':<38}{'触发日':>6}{'命中率':>8}{'lift':>7}{'onset后命中':>11}")
        for label, al in tests.items():
            if al is None:
                continue
            m = al & np.isfinite(fd)
            hit = ev[m].mean() if m.sum() else np.nan
            lift = hit / br if np.isfinite(hit) else np.nan
            ons = onsets(al) & np.isfinite(fd)
            oh = ev[ons].mean() if ons.sum() else np.nan
            print(f'{label:<38}{int(m.sum()):>6}{hit*100:>7.1f}%{lift:>7.2f}x'
                  f'{(f"{oh*100:.0f}% ({int(ons.sum())}次)" if ons.sum() else "—"):>11}')


if __name__ == '__main__':
    main()
