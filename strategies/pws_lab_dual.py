#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 最终方案：双顶型双探测器（Dual Regime Detector）

发现（2026-11-03 实测）：
  · 7月顶 = 广谱顶（宽度正常、指数还在涨），AR/PSI 都不响；
            只有 Δσ 放大 + AR(1) 恢复力衰减 抓得住（提前 11~15 日）
  · 10月顶 = 抱团顶（AR λ1/N 冲到 96~98 分位），
            Δσ+AR(1) 抓得住（提前 31 日）；AR 自己反而更早进入高位（5/29 就 onset）

方案 D0（推荐）：双通道并行，各自独立报警
  通道A（时序/波动）：M1 残差持续3日>0.6  AND  M2 Δσ>3σ     ← 抓广谱顶
  通道B（截面/抱团）：AR 去共同因子 λ1/N 突破 120日 P90      ← 抓抱团顶
  任一道 onset → 预警；两道同时 → 系统级
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


def onset(a, q=10):
    o = np.zeros(len(a), bool)
    for t in range(len(a)):
        if a[t] and not a[max(0, t - q):t].any():
            o[t] = True
    return o


def crash_peaks(close, dd=0.15, gap=30):
    n = len(close)
    pk, last = [], -10 ** 9
    for t in range(5, n - 5):
        if close[t] < max(close[max(0, t - 5):t].max(initial=0), close[t + 1:t + 6].max(initial=0)):
            continue
        fw = close[t:t + 61]
        if len(fw) > 10 and fw.min() / close[t] - 1 <= -dd and t - last >= gap:
            pk.append(t); last = t
    return np.array(pk)


def ar_dm_alerts(code, dates, pct=90, ref=120):
    fp = f'.cache/pws_lab/absboth_{code.replace(".", "_")}.csv'
    df = pd.read_csv(fp)
    df['date'] = df['date'].astype(str)
    v = df.set_index('date')['ar_dm'].reindex(dates).ffill().to_numpy(float)
    al = np.zeros(len(dates), bool)
    for t in range(ref, len(dates)):
        w = v[max(0, t - ref):t]; w = w[np.isfinite(w)]
        if len(w) >= 60 and np.isfinite(v[t]):
            al[t] = v[t] > np.percentile(w, pct)
    return al


def main(seed=7, nsim=2000):
    rng = np.random.default_rng(seed)
    print('=' * 96)
    print('最终方案：通道A（M1复位+M2波动放大） vs 通道B（AR去因子抱团）  vs 合并')
    print('=' * 96)
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        close = L.load_all()[code].close.to_numpy(float)
        b = {k.split('|', 1)[1]: z[k].astype(bool) for k in z.files if k.startswith(f'{code}|')}
        A = b['M1_resid_persist3_t0.6'] & b['M2_3.0']
        B = ar_dm_alerts(code, dates, 90, 120)
        C = A | B
        peaks = crash_peaks(close)
        n = len(close)
        fd = np.full(n, np.nan)
        for t in range(n - HORIZON):
            fd[t] = close[t:t + HORIZON + 1].min() / close[t] - 1
        base = (fd <= -DROP)[np.isfinite(fd)].mean()

        print(f'\n===== {name}   大跌峰 {len(peaks)} 个   base rate {base*100:.1f}%')
        for lab, al in (('通道A M1+M2', A), ('通道B AR抱团', B), ('合并 A|B', C)):
            oi = np.where(onset(al))[0]
            hit = sum(1 for p in peaks if np.any((oi <= p) & (oi >= p - 40)))
            sims = np.array([sum(1 for p in peaks if np.any((s <= p) & (s >= p - 40)))
                             for s in (np.sort(rng.choice(n, len(oi), replace=False)) for _ in range(nsim))]) \
                if len(oi) else np.zeros(nsim)
            m = al & np.isfinite(fd)
            pr = (fd[m] <= -DROP).mean() if m.sum() else np.nan
            print(f'  {lab:<12} 亮{int(m.sum()):>4}天({m.sum()/n*100:>4.1f}%) onset{len(oi):>3}  '
                  f'峰命中 {hit:>2}/{len(peaks)}({hit/max(len(peaks),1)*100:>3.0f}%)  '
                  f'随机 {sims.mean():.1f}±{sims.std():.1f} p95={np.percentile(sims,95):.0f}  '
                  f'条件命中率 {pr*100:>4.1f}% lift {pr/base:>4.2f}x')
        # 7月/10月具体
        s = pd.Series(close, index=dates)
        for tag, (a1, a2) in (('7月', (G.EVENTS[code][0][0], G.EVENTS[code][0][1])),
                              ('10月', (G.EVENTS[code][1][0], G.EVENTS[code][1][1]))):
            seg = s[(s.index >= a1) & (s.index <= a2)]
            pi = dates.index(seg.idxmax())
            row = f'    {tag}峰 {seg.idxmax()}: '
            for lab, al in (('A', A), ('B', B), ('A|B', C)):
                on = [i for i in range(max(0, pi - 60), pi + 1) if onset(al)[i]]
                row += f'{lab}=' + (f'{dates[on[0]]}(提前{pi-on[0]}日) ' if on else '— ')
            print(row)


if __name__ == '__main__':
    main()
