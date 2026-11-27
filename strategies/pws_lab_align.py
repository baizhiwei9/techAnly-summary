#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · ① 吸收比率严格对齐本机口径（含去共同因子）  ② α 在个股层面是否可用

① 本机两处 AR 实现
   pool_topo.absorption(C)      原始收益相关 → λ1/N
   struct_tag._ar_disp(R)       先 eps = R - 当日截面均值（去市场共同因子）→ λ1/N + PCoA spread
   大佬 2026-11-02 定的是 struct_tag 口径。两者差别很大，必须对齐后再谈"能否预警"。

② α 的机理验证
   若指数层面 α 恒在 2~4（因中心极限定理被拉向高斯），那 α<1.8 永远不成立 ——
   这不是窗口问题。真正该测的是：个股层面 α 是否落在 1.5~2.5 可判别区。
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

LAB = os.path.join(L.WS, '.cache', 'pws_lab')
PEAKS = {'000852.SH': ('20260701', '20260817'), '000688.SH': ('20260630', '20260818'),
         '399006.SZ': ('20260625', '20260817')}


def _corr_ar(R, demean=True):
    """R: DataFrame(窗口×成员)；demean=True 走 struct_tag 口径"""
    n = R.shape[1]
    if n < 2 or len(R) < 8:
        return np.nan
    X = R.sub(R.mean(axis=1), axis=0) if demean else R
    with np.errstate(invalid='ignore', divide='ignore'):
        C = np.corrcoef(X.values.T)
    C = np.nan_to_num(np.asarray(C, float), nan=0.0)
    C = np.clip((C + C.T) / 2, -1, 1)
    np.fill_diagonal(C, 1.0)
    return float(np.linalg.eigvalsh(C)[-1] / n)


def ar_both(panel, codes, win=20, cap=300, seed=7):
    cols = [c for c in codes if c in panel.columns]
    if len(cols) > cap:
        cols = sorted(np.random.default_rng(seed).choice(cols, cap, replace=False))
    sub = panel[cols]
    idx = list(sub.index)
    rows = []
    for i in range(win - 1, len(idx)):
        w = sub.iloc[i - win + 1:i + 1].dropna(axis=1, thresh=int(win * .95)).dropna()
        if w.shape[1] < 30:
            rows.append((idx[i], np.nan, np.nan))
            continue
        rows.append((idx[i], _corr_ar(w, True), _corr_ar(w, False)))
    df = pd.DataFrame(rows, columns=['date', 'ar_dm', 'ar_raw']).set_index('date')
    return df


def part1():
    print('=' * 84)
    print('① 吸收比率对齐本机口径（去共同因子 = struct_tag / 原始 = pool_topo）')
    print('=' * 84)
    panel = L.panel()
    for code, name in L.IDX.items():
        df = ar_both(panel, L.pool(code), 20, 300)
        df.to_csv(os.path.join(LAB, f'absboth_{code.replace(".", "_")}.csv'))
        for col, lab in (('ar_dm', '去共同因子(struct_tag)'), ('ar_raw', '原始收益(pool_topo)')):
            s = df[col].dropna()
            print(f'\n  {name} · {lab}   P50={s.median():.3f} P80={s.quantile(.8):.3f} '
                  f'P90={s.quantile(.9):.3f}  区间 {s.min():.3f}~{s.max():.3f}')
            for pk in PEAKS[code]:
                if pk not in s.index:
                    continue
                i = s.index.get_loc(pk)
                pre = s.iloc[max(0, i - 20):i + 1]
                q = (s < s.iloc[i]).mean() * 100
                print(f'    峰{pk}: {s.iloc[i]:.3f}（全样本分位 {q:>3.0f}%，'
                      f'峰前20日 {pre.iloc[0]:.3f}→{s.iloc[i]:.3f}，峰前最高 {pre.max():.3f}@{pre.idxmax()[-4:]}）')


def part2():
    print('\n' + '=' * 84)
    print('② α 幂律：指数层面 vs 个股层面（证明不是窗口问题）')
    print('=' * 84)
    panel = L.panel()

    def hill(x, q=5, pct=90, kmin=15):
        cq = pd.Series(x).rolling(q).sum().to_numpy()
        v = -cq[np.isfinite(cq) & (cq < 0)]
        if len(v) < 100:
            return np.nan, 0
        u = np.percentile(v, pct)
        t = v[v > u]
        if len(t) < kmin or u <= 0:
            return np.nan, len(t)
        ml = np.mean(np.log(t / u))
        return (1 / ml if ml > 1e-9 else np.nan), len(t)

    print('\n[指数层面] 500 日窗口滚动（现有做法）')
    for code, name in L.IDX.items():
        df = L.load_all()[code]
        r = df.ret.to_numpy(float)[-500:]
        a, k = hill(r)
        print(f'  {name:<8} α={a:.2f}  k={k}  ← 用了 500 天只得到 1 个点')

    print('\n[个股层面] 同口径、同窗口，随机 300 只')
    rng = np.random.default_rng(11)
    cols = list(panel.columns)
    sample = rng.choice(cols, min(300, len(cols)), replace=False)
    res = {}
    for pct in (90, 95):
        vals = []
        for c in sample:
            x = panel[c].to_numpy(float)
            x = x[np.isfinite(x)] / 100.0
            if len(x) < 200:
                continue
            a, k = hill(x, 5, pct, 15)
            if np.isfinite(a):
                vals.append(a)
        vals = np.array(vals)
        res[pct] = vals
        if len(vals):
            print(f'  上尾{100-pct}%: n={len(vals)}  α p10/p50/p90 = '
                  f'{np.percentile(vals,10):.2f}/{np.percentile(vals,50):.2f}/{np.percentile(vals,90):.2f}  '
                  f'α<1.8 占 {100*(vals<1.8).mean():.0f}%  α<2.5 占 {100*(vals<2.5).mean():.0f}%')

    print('\n[结论] 指数 = 上千只股票的平均 → 尾部被中心极限定理抹平 → α 结构性偏大。')
    print('       α<1.8 在指数上不是参数问题，是"测错了对象"。')


if __name__ == '__main__':
    part1()
    part2()
