#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · ① α 到底是不是窗口问题 ② 投票组合搜索（7月精度优先）

① α 诊断
   - 三种窗口 250/500/750 × q∈{1,5,20}，算 α 序列
   - 用 α 的滚动分位（0~1）分组，看前瞻 20 日最大跌幅的分布（低 α 组是否更差）
   - 结论只看数：α 越低下一次跌得越狠吗？

② 投票组合
   - 候选池 = 每个指标族里"常亮率合理且 7 月有信号"的代表参数
   - 枚举 2 票 / 3 票组合，评估：7月提前量、全样本 onset 精确率、常亮率
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
    a = alert.astype(bool)
    n = len(a)
    out = np.zeros(n, bool)
    for t in range(n):
        if a[t] and not a[max(0, t - quiet):t].any():
            out[t] = True
    return out


def eval_series(alert, close, dates):
    ev = np.array([d >= EVAL_START for d in dates])
    idx = np.where(alert & ev)[0]
    cs = clusters(idx)
    valid = 0
    for c in cs:
        i = c[0]
        fw = close[i:i + HORIZON + 1]
        if len(fw) > 1 and fw.min() / close[i] - 1 <= -DROP_THR:
            valid += 1
    return dict(on_frac=float((alert & ev).sum() / max(ev.sum(), 1)),
                onsets=int((onsets(alert) & ev).sum()),
                clusters=len(cs), valid=valid,
                precision=(valid / len(cs) if cs else float('nan')))


def july_fit(alert, close, dates, code):
    s = pd.Series(close, index=dates)
    a, b, _ = G.EVENTS[code][0]
    seg = s[(s.index >= a) & (s.index <= b)]
    pk = seg.idxmax()
    pi = dates.index(pk)
    hits = np.where(onsets(alert)[max(0, pi - JULY_WIN):pi + 1])[0]
    if len(hits) == 0:
        return 0, None, None
    i0 = max(0, pi - JULY_WIN) + hits[0]
    return 1, pi - i0, dates[i0]


def oct_fit(alert, close, dates, code):
    s = pd.Series(close, index=dates)
    a, b, _ = G.EVENTS[code][1]
    seg = s[(s.index >= a) & (s.index <= b)]
    pk = seg.idxmax()
    pi = dates.index(pk)
    hits = np.where(onsets(alert)[max(0, pi - JULY_WIN):pi + 1])[0]
    if len(hits) == 0:
        return 0, None, None
    i0 = max(0, pi - JULY_WIN) + hits[0]
    return 1, pi - i0, dates[i0]


# ============ ① α 诊断 ============
def alpha_diag():
    print('=' * 78)
    print('① α 幂律指数诊断：不是窗口问题（数据说话）')
    print('=' * 78)
    print('\n[A] 各口径的 α 水平（全历史，含 2008/2015 股灾）')
    print(f"{'指数':<8}{'口径':<18}{'有效日':>7}{'p10':>7}{'p50':>7}{'p90':>7}{'最小':>7}{'α<1.8天':>8}")
    out = {}
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        for win, q in itertools.product((250, 500, 750), (1, 5, 20)):
            al = z[f'{code}|alpha_w{win}_q{q}']
            v = al[np.isfinite(al)]
            if len(v) == 0:
                continue
            out[(name, win, q)] = (dates, al)
            print(f"{name:<8}{f'w{win}/q{q}':<18}{len(v):>7}{np.percentile(v,10):>7.2f}"
                  f"{np.percentile(v,50):>7.2f}{np.percentile(v,90):>7.2f}{v.min():>7.2f}"
                  f"{int((v<1.8).sum()):>8}")

    print('\n[B] α 有没有预测力？按 α 滚动分位分组，看前瞻 20 日最大跌幅（越负越差）')
    print(f"{'指数':<8}{'口径':<12}{'低分位组':>10}{'中分位组':>10}{'高分位组':>10}{'差(低-高)':>10}")
    for (name, win, q), (dates, al) in out.items():
        if q != 5:
            continue
        close = L.load_all()[[c for c, n in L.IDX.items() if n == name][0]].close.to_numpy(float)
        # 滚动分位
        rk = np.full(len(al), np.nan)
        for t in range(250, len(al)):
            w = al[max(0, t - 250):t]
            w = w[np.isfinite(w)]
            if len(w) >= 125 and np.isfinite(al[t]):
                rk[t] = (w < al[t]).mean()
        fwd = np.full(len(al), np.nan)
        for t in range(len(al) - HORIZON):
            fw = close[t:t + HORIZON + 1]
            if len(fw) > 1:
                fwd[t] = fw.min() / close[t] - 1
        m = np.isfinite(rk) & np.isfinite(fwd) & np.array([d >= EVAL_START for d in dates])
        if m.sum() < 50:
            continue
        lo = fwd[m & (rk < 0.33)].mean() * 100
        md = fwd[m & (rk >= 0.33) & (rk < 0.67)].mean() * 100
        hi = fwd[m & (rk >= 0.67)].mean() * 100
        print(f"{name:<8}{f'w{win}/q{q}':<12}{lo:>9.2f}%{md:>9.2f}%{hi:>9.2f}%{lo-hi:>9.2f}pp")


# ============ ② 投票组合 ============
CANDS = {
    'M1a': 'M1_resid_persist3_t0.6',
    'M1b': 'M1_resid_persist5_t0.7',
    'M1c': 'M1_resid_rise3_t0.6',
    'M2a': 'M2_2.0',
    'M2b': 'M2_3.0',
    'M3a': 'M3_80',
    'M3b': 'M3_95',
    'M4a': 'M4_w500_q1',      # 唯一在创业板有触发
    'M4b': 'M4_w500_q20',     # 中证1000/创业板有触发
}


def build_abs_alerts(code, name, dates):
    AR = A.build_all(20, 300)
    AR60 = A.build_all(60, 300)
    out = {}
    for win, ARs in ((20, AR), (60, AR60)):
        if name not in ARs:
            continue
        v = ARs[name].reindex(dates).ffill().to_numpy(float)
        for thr in (90, 95):
            al = np.zeros(len(dates), bool)
            for t in range(120, len(dates)):
                w = v[max(0, t - 120):t]
                w = w[np.isfinite(w)]
                if len(w) >= 60 and np.isfinite(v[t]):
                    al[t] = v[t] > np.percentile(w, thr)
            out[f'ABS{win}_P{thr}'] = al
    return out


def vote_search():
    print('\n' + '=' * 78)
    print('② 投票组合搜索（onset 口径；7月/10月双波）')
    print('=' * 78)
    results = []
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        close = L.load_all()[code].close.to_numpy(float)
        base = {k.split('|', 1)[1]: z[k] for k in z.files if k.startswith(f'{code}|')}
        pool = {k: base[v].astype(bool) for k, v in CANDS.items() if v in base}
        pool.update(build_abs_alerts(code, name, dates))

        keys = list(pool)
        for r in (2, 3):
            for combo in itertools.combinations(keys, r):
                al = np.logical_and.reduce([pool[k] for k in combo])
                if al.sum() < 3:
                    continue
                m = eval_series(al, close, dates)
                jh, jl, jo = july_fit(al, close, dates, code)
                oh, ol, oo = oct_fit(al, close, dates, code)
                results.append(dict(code=code, name=name, r=r, combo='+'.join(combo),
                                    on_frac=m['on_frac'], onsets=m['onsets'],
                                    clusters=m['clusters'], valid=m['valid'],
                                    precision=m['precision'],
                                    july_hit=jh, july_lead=jl, july_onset=jo,
                                    oct_hit=oh, oct_lead=ol))
    df = pd.DataFrame(results)
    df.to_csv(os.path.join(LAB, 'vote_search.csv'), index=False, encoding='utf-8-sig')

    for name in L.IDX.values():
        sub = df[(df.name == name) & (df.july_hit == 1) & (df.precision >= 0.25)]
        sub = sub.sort_values(['july_lead', 'precision'], ascending=[False, False])
        print(f'\n--- {name}：7月命中且精确率≥25%，按提前量排序 ---')
        if sub.empty:
            print('  （无）')
            continue
        for _, r in sub.head(10).iterrows():
            print(f"  {r.r}票 {r.combo:<34} 7月提前 {int(r.july_lead):>3}日 @{r.july_onset} "
                  f"10月{'✓' if r.oct_hit else '✗'} 常亮{r.on_frac*100:>5.1f}% "
                  f"簇{int(r.clusters):>2}/中{int(r.valid):>2} 精确{r.precision*100:>5.1f}%")
    return df


if __name__ == '__main__':
    alpha_diag()
    vote_search()
