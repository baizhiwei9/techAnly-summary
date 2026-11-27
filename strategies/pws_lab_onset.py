#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 以「起响日(onset)」为核心的精度评估 + 投票组合搜索

为什么换成 onset：
  单指标常年一半时间亮着 → "提前60日"其实是"从4月一直亮到7月"，没有信息量。
  onset = 由静到响的第一天（前面至少 QUIET 日不响），才是可交易的信号。

指标归一化：每个指标用它自己过去 250 日的分位（rank）表达，
  这样不同量纲（AR 0.6 / Δσ / PSI / α 3.0 / λmax 300）可以放进同一张票。

评估
  · 7月：峰前 40 交易日内是否出现 onset；lead = 峰 - onset
  · 全样本：onset 个数、其中 20 日内跌>=8% 的比例（precision）
  · 常亮率：告警天数占比（越低越好，说明有区分度）
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, itertools
import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pws_lab_data as L
import pws_lab_grid as G
import pws_lab_abs as A

LAB = G.LAB
EVAL_START = '20250401'
QUIET = 10
DROP_THR = 0.08
HORIZON = 20
JULY_WIN = 40
GAP = 10

z = np.load(G.ALERT_FP, allow_pickle=True)


def rank_norm(x, ref=250):
    """滚动分位（0~1），值与 ref 天前的历史比"""
    n = len(x)
    out = np.full(n, np.nan)
    for t in range(ref, n):
        w = x[max(0, t - ref):t]
        w = w[np.isfinite(w)]
        if len(w) < ref * 0.5:
            continue
        if not np.isfinite(x[t]):
            continue
        out[t] = (w < x[t]).mean()
    return out


def clusters(idx, gap=GAP):
    if len(idx) == 0:
        return []
    out = [[idx[0]]]
    for i in idx[1:]:
        if i - out[-1][-1] <= gap:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def onsets(alert, quiet=QUIET):
    """起响日：今天是 on，且过去 quiet 日全 off"""
    a = alert.astype(bool)
    n = len(a)
    out = np.zeros(n, bool)
    for t in range(n):
        if not a[t]:
            continue
        lo = max(0, t - quiet)
        if not a[lo:t].any():
            out[t] = True
    return out


def eval_series(alert, close, dates):
    ons = onsets(alert)
    ev = np.array([d >= EVAL_START for d in dates])
    ons_ev = ons & ev
    idx = np.where(alert & ev)[0]
    cs = clusters(idx)
    valid = 0
    for c in cs:
        i = c[0]
        fw = close[i:i + HORIZON + 1]
        if len(fw) > 1 and fw.min() / close[i] - 1 <= -DROP_THR:
            valid += 1
    return dict(on_frac=float((alert & ev).sum() / max(ev.sum(), 1)),
                onsets=int(ons_ev.sum()),
                clusters=len(cs), valid=valid,
                precision=(valid / len(cs) if cs else np.nan))


def july_fit(alert, close, dates, code):
    s = pd.Series(close, index=dates)
    a, b, _ = G.EVENTS[code][0]
    seg = s[(s.index >= a) & (s.index <= b)]
    pk = seg.idxmax()
    pi = dates.index(pk)
    ons = onsets(alert)
    lo = max(0, pi - JULY_WIN)
    hits = np.where(ons[lo:pi + 1])[0]
    if len(hits) == 0:
        return dict(july_hit=0, lead=None, july_onset=None)
    i0 = lo + hits[0]
    return dict(july_hit=1, lead=pi - i0, july_onset=dates[i0])


def main():
    AR = A.build_all(20, 300)
    AR60 = A.build_all(60, 300)
    rows = []

    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        close = L.load_all()[code].close.to_numpy(float)
        base = {k.split('|', 1)[1]: z[k] for k in z.files if k.startswith(f'{code}|')}

        # 吸收比率三条规则（本机同源口径）
        for win, ARs in ((20, AR), (60, AR60)):
            if name not in ARs:
                continue
            s = ARs[name]
            v = s.reindex(dates).ffill().to_numpy(float)
            for rule, thr in (('P80', 80), ('P90', 90), ('P95', 95)):
                al = np.zeros(len(dates), bool)
                for t in range(120, len(dates)):
                    w = v[max(0, t - 120):t]
                    w = w[np.isfinite(w)]
                    if len(w) >= 60 and np.isfinite(v[t]):
                        al[t] = v[t] > np.percentile(w, thr)
                base[f'ABS_w{win}_{rule}'] = al
            # 上升斜率
            al = np.zeros(len(dates), bool)
            k = 20
            for t in range(k, len(dates)):
                w = v[t - k + 1:t + 1]
                if np.all(np.isfinite(w)):
                    al[t] = np.polyfit(np.arange(k), w, 1)[0] > 0
            base[f'ABS_w{win}_slope20'] = al

        for key, al in base.items():
            al = al.astype(bool)
            if al.sum() < 3:
                continue
            m = eval_series(al, close, dates)
            j = july_fit(al, close, dates, code)
            rows.append(dict(code=code, name=name, combo=key, **m, **j))

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(LAB, 'onset_eval.csv'), index=False, encoding='utf-8-sig')

    print('=== 单指标 onset 评估（按 7月命中 + precision 排序）===')
    for name in L.IDX.values():
        sub = df[(df.name == name)]
        sub = sub.sort_values(['july_hit', 'precision', 'lead'], ascending=[False, False, False])
        print(f'\n--- {name} ---')
        for _, r in sub.head(14).iterrows():
            ld = f"{int(r.lead):>3}日" if r.july_hit else '   —'
            print(f"  {r.combo:<24} 常亮{r.on_frac*100:>5.1f}% onset{r.onsets:>3} "
                  f"簇{int(r.clusters):>2}/中{int(r.valid):>2} 精确{r.precision*100:>5.1f}% "
                  f"7月{ld} @{r.july_onset if r.july_hit else '—'}")
    return df


if __name__ == '__main__':
    main()
