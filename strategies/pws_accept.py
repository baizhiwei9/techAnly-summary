#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变系统 · 诚实的实盘口径评估（最终验收，同步线上代码）

明确区分两件事，避免自欺：
  ① 覆盖（recall）：108 年-次大跌峰里，喊到了多少 —— 只看"喊没喊"
  ② 领先（lead）：峰前 40 日内最近一次 onset —— 但必须扣掉「随机也能覆盖」的部分

判定标准（硬指标）：
  真实覆盖数 > 蒙特卡洛（同样 onset 个数随机撒）p95  → 才算有信息
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
import pws_data as D
import pws_xsec as X
import pws_compute as C
import pws_absratio as AB
import pws_lab_data as L
import pws_lab_grid as G

OUT = os.path.join(os.path.dirname(_BIN), '.cache', 'pws')


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


def main(seed=11, nsim=2000):
    rng = np.random.default_rng(seed)
    z = np.load(G.ALERT_FP, allow_pickle=True)
    print('=' * 100)
    print('双通道实盘口径：覆盖 & 领先 & 随机对照（样本 2025-04 ~ 2026-11）')
    print('=' * 100)
    allrows = []
    for code, name in D.IDX_NAME.items():
        px = D.index_px(code, '20250401', '20261103')
        r = C.compute(code, name, px, xsec_lmax=X.load_lmax(code),
                      ar_mode='resid', lam_mode='abs', abs_ratio=AB.load_ar(code, '20261103'))
        df = r.df
        close = df.close.to_numpy(float)
        dates = df.date.tolist()
        chA = df.chA.to_numpy(bool)
        chB = df.chB.to_numpy(bool)
        # 只在「有数据」的窗口内找峰（否则 2005~2025 那些峰根本没数据可喊，评估会失真）
        peaks = crash_peaks(close)          # close 已经是 20250401 起的窗口
        n = len(close)
        fo = onset(chA) | onset(chB)
        oi = np.where(fo)[0]
        hit = sum(1 for p in peaks if np.any((oi <= p) & (oi >= p - 40)))
        sims = np.array([sum(1 for p in peaks if np.any((s <= p) & (s >= p - 40)))
                         for s in (np.sort(rng.choice(n, len(oi), replace=False)) for _ in range(nsim))]) \
            if len(oi) else np.zeros(nsim)
        p95 = np.percentile(sims, 95)
        print(f'\n{name}: 窗口内大跌峰 {len(peaks)} 个；通道 onset {len(oi)} 次')
        print(f'  覆盖 {hit}/{len(peaks)}（{hit/max(len(peaks),1)*100:.0f}%）  '
              f'随机均值 {sims.mean():.1f}  随机p95 {p95:.0f}  '
              f'{"✅ 显著优于随机" if hit > p95 else "⚠️ 与随机持平"}')
        allrows.append(dict(指数=name, 通道onset=len(oi), 大跌峰=len(peaks), 覆盖=hit,
                            随机均值=round(sims.mean(), 1), 随机p95=int(p95),
                            判定='✅' if hit > p95 else '⚠️'))
    d = pd.DataFrame(allrows)
    d.to_csv(os.path.join(OUT, 'dual_accept.csv'), index=False, encoding='utf-8-sig')
    print('\n' + d.to_string(index=False))


if __name__ == '__main__':
    main()
