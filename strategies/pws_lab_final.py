#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 最终验收（严格口径）
所有提前量一律「峰前 40 交易日内最近一次 onset」，超过窗口不算命中。
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
WIN = 40
HORIZON = 20
DROP = 0.08


def onset(a, q=10):
    o = np.zeros(len(a), bool)
    for t in range(len(a)):
        if a[t] and not a[max(0, t - q):t].any():
            o[t] = True
    return o


def peaks_of(code, dates, close):
    s = pd.Series(close, index=dates)
    out = {}
    for tag, (a, b) in (('7月', (G.EVENTS[code][0][0], G.EVENTS[code][0][1])),
                        ('10月', (G.EVENTS[code][1][0], G.EVENTS[code][1][1]))):
        seg = s[(s.index >= a) & (s.index <= b)]
        pk = seg.idxmax()
        tr = seg[seg.index >= pk].idxmin()
        out[tag] = (pk, (seg[tr] / seg[pk] - 1) * 100)
    return out


def lead(al_on, dates, pi, win=WIN):
    cand = [i for i in range(max(0, pi - win), pi + 1) if al_on[i]]
    if not cand:
        return None, None
    return pi - cand[-1], dates[cand[-1]]


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
    df = pd.read_csv(fp); df['date'] = df['date'].astype(str)
    v = df.set_index('date')['ar_dm'].reindex(dates).ffill().to_numpy(float)
    al = np.zeros(len(dates), bool)
    for t in range(ref, len(dates)):
        w = v[max(0, t - ref):t]; w = w[np.isfinite(w)]
        if len(w) >= 60 and np.isfinite(v[t]):
            al[t] = v[t] > np.percentile(w, pct)
    return al, v


def main(seed=7, nsim=2000):
    rng = np.random.default_rng(seed)
    rows = []
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        close = L.load_all()[code].close.to_numpy(float)
        b = {k.split('|', 1)[1]: z[k].astype(bool) for k in z.files if k.startswith(f'{code}|')}
        A = b['M1_resid_persist3_t0.6'] & b['M2_3.0']
        B, _ = ar_dm_alerts(code, dates, 90, 120)
        pk = peaks_of(code, dates, close)
        cp = crash_peaks(close)
        n = len(close)
        fd = np.full(n, np.nan)
        for t in range(n - HORIZON):
            fd[t] = close[t:t + HORIZON + 1].min() / close[t] - 1
        base = (fd <= -DROP)[np.isfinite(fd)].mean()

        for lab, al in (('A M1+M2', A), ('B AR抱团', B), ('A|B', A | B)):
            # 并集口径：两通道各自判 onset，再取并（避免一个常亮压掉另一个的起响）
            o = (onset(A) | onset(B)) if lab == 'A|B' else onset(al)
            oi = np.where(o)[0]
            hit = sum(1 for p in cp if np.any((oi <= p) & (oi >= p - WIN)))
            sims = np.array([sum(1 for p in cp if np.any((s <= p) & (s >= p - WIN)))
                             for s in (np.sort(rng.choice(n, len(oi), replace=False)) for _ in range(nsim))]) \
                if len(oi) else np.zeros(nsim)
            m = al & np.isfinite(fd)
            pr = (fd[m] <= -DROP).mean() if m.sum() else np.nan
            for tag, (p, drop) in pk.items():
                pi = dates.index(p)
                ld, od = lead(o, dates, pi)
                rows.append(dict(指数=name, 通道=lab, 波次=tag, 峰日=p, 跌幅=round(drop, 1),
                                 首警=od, 提前=ld,
                                 历史峰命中=f'{hit}/{len(cp)}', 随机均值=round(sims.mean(), 1),
                                 随机p95=round(np.percentile(sims, 95), 0),
                                 判定=('✅' if hit > np.percentile(sims, 95) else '⚠️'),
                                 lift=round(pr / base, 2) if np.isfinite(pr) else None,
                                 亮灯率=f'{m.sum()/n*100:.1f}%'))

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(G.LAB, 'final_accept.csv'), index=False, encoding='utf-8-sig')
    print(df.to_string(index=False))


if __name__ == '__main__':
    main()
