#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 全历史稳健性检验（防过拟合）
不做网格搜索，只用固定方案，跑完 20 年：
  S1 现行（M2 2σ + M1持续5日）
  S2 M1严（残差持续3日>0.6）AND M2严（Δσ>3σ）
统计每个方案在所有历史大跌前的表现：
  · 大跌事件：指数从 60 日最高回撤 ≥15% 的峰（局部最高收盘）
  · 命中：峰前 40 交易日内有 onset
  · 误报：onset 后 20 日未跌 8%
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json
import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pws_lab_data as L
import pws_lab_grid as G

z = np.load(G.ALERT_FP, allow_pickle=True)
QUIET = 10
HORIZON = 20
DROP_THR = 0.08
LEAD_WIN = 40


def onsets(a, quiet=QUIET):
    n = len(a)
    out = np.zeros(n, bool)
    for t in range(n):
        if a[t] and not a[max(0, t - quiet):t].any():
            out[t] = True
    return out


def clusters(idx, gap=10):
    if len(idx) == 0:
        return []
    o = [[idx[0]]]
    for i in idx[1:]:
        if i - o[-1][-1] <= gap:
            o[-1].append(i)
        else:
            o.append([i])
    return o


def crash_peaks(close, min_dd=0.15, min_gap=30):
    """峰值：之后 60 日内跌幅 >= min_dd 的局部最高点（去重：间隔 >= min_gap）"""
    n = len(close)
    peaks = []
    last = -10 ** 9
    for t in range(5, n - 5):
        if close[t] < max(close[max(0, t - 5):t].max(initial=0), close[t + 1:t + 6].max(initial=0)):
            continue
        fw = close[t:t + 61]
        if len(fw) < 10:
            continue
        dd = fw.min() / close[t] - 1
        if dd <= -min_dd and t - last >= min_gap:
            peaks.append((t, dd))
            last = t
    return peaks


def main():
    schemes = {
        'S1 现行 M2-2σ+M1持续5日': lambda b: b['M1_resid_persist5_t0.6'] | b['M2_2.0'],
        'S2 M1严(持续3日)+M2严(3σ)': lambda b: b['M1_resid_persist3_t0.6'] & b['M2_3.0'],
        'S2b M1严(持续5日)+M2严(3σ)': lambda b: b['M1_resid_persist5_t0.6'] & b['M2_3.0'],
        'S2c M1(连涨3日)+M2严(3σ)': lambda b: b['M1_resid_rise3_t0.6'] & b['M2_3.0'],
        'S7 M2严(3σ) 单独': lambda b: b['M2_3.0'],
    }
    print(f"{'方案':<28}{'指数':<9}{'大跌峰':>6}{'命中':>6}{'命中率':>7}{'平均提前':>9}{'onset簇':>8}{'有效':>5}{'精确率':>7}")
    agg = {}
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        close = L.load_all()[code].close.to_numpy(float)
        base = {k.split('|', 1)[1]: z[k].astype(bool) for k in z.files if k.startswith(f'{code}|')}
        peaks = crash_peaks(close)
        for label, fn in schemes.items():
            al = fn(base)
            ons = onsets(al)
            hit, leads = 0, []
            for (pi, dd) in peaks:
                lo = max(0, pi - LEAD_WIN)
                h = np.where(ons[lo:pi + 1])[0]
                if len(h):
                    hit += 1
                    leads.append(pi - (lo + h[0]))
            idx = np.where(al)[0]
            cs = clusters(idx)
            valid = sum(1 for c in cs if len(close[c[0]:c[0] + HORIZON + 1]) > 1
                        and close[c[0]:c[0] + HORIZON + 1].min() / close[c[0]] - 1 <= -DROP_THR)
            pr = valid / len(cs) if cs else float('nan')
            print(f'{label:<28}{name:<9}{len(peaks):>6}{hit:>6}{hit/max(len(peaks),1)*100:>6.0f}%'
                  f'{(np.mean(leads) if leads else 0):>8.1f}日{len(cs):>8}{valid:>5}{pr*100:>6.0f}%')
            agg.setdefault(label, []).append((hit, len(peaks), pr, len(cs)))
    print()
    for label, v in agg.items():
        h = sum(a for a, _, _, _ in v); t = sum(b for _, b, _, _ in v)
        prs = [p for _, _, p, _ in v if np.isfinite(p)]
        print(f'{label:<28} 总命中 {h}/{t} ({h/t*100:.0f}%)  平均精确率 {np.mean(prs)*100:.0f}%')


if __name__ == '__main__':
    main()
