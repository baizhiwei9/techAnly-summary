#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 固定候选方案对比（不靠网格排序，避免过拟合）
四套方案，三指数统一口径，看 7月/10月 双波 + 全样本误报：

  S1 现行（M2 2σ + M1 持续5日）           = 用户已上线版
  S2 M1严 + M2严                          = AR(1)残差持续3日>0.6 且 Δσ>3σ
  S3 S2 + 吸收比率(λ1/N,60日窗,P90)       = 三个不同维度的独立票
  S4 S2 + PSI(P95) 替代吸收比率

吸收比率与 M1 完全独立：一个是跨股票相关矩阵第一特征值占比（截面抱团），
一个是单序列时间自相关（时序记忆）。7月是"分散上涨"，10月是"抱团冲顶"——
吸收比率只对后者敏感，这是结构差异，不是参数问题。
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, itertools
import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pws_lab_data as L
import pws_lab_grid as G
import pws_lab_abs as A
import pws_lab_onset as O

z = np.load(G.ALERT_FP, allow_pickle=True)


def abs_alert(code, name, dates, win, pct):
    ARs = A.build_all(win, 300)
    if name not in ARs:
        return np.zeros(len(dates), bool)
    v = ARs[name].reindex(dates).ffill().to_numpy(float)
    al = np.zeros(len(dates), bool)
    for t in range(120, len(dates)):
        w = v[max(0, t - 120):t]
        w = w[np.isfinite(w)]
        if len(w) >= 60 and np.isfinite(v[t]):
            al[t] = v[t] > np.percentile(w, pct)
    return al


SCHEMES = {
    'S1 现行 M2-2σ + M1持续5日': lambda b: b['M1_resid_persist5_t0.6'] | b['M2_2.0'],
    'S2 M1严(持续3日) + M2严(3σ)': lambda b: b['M1_resid_persist3_t0.6'] & b['M2_3.0'],
    'S3 S2 + 吸收比率60P90': lambda b: b['M1_resid_persist3_t0.6'] & b['M2_3.0'] & b['_ABS60P90'],
    'S4 S2 + PSI(P95)': lambda b: b['M1_resid_persist3_t0.6'] & b['M2_3.0'] & b['M3_95'],
    'S5 M1严 或 (M2严+吸收)': lambda b: b['M1_resid_persist3_t0.6'] | (b['M2_3.0'] & b['_ABS60P90']),
    'S6 M1严 + 吸收60P90（2票）': lambda b: b['M1_resid_persist3_t0.6'] & b['_ABS60P90'],
}


def main():
    rows = []
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        close = L.load_all()[code].close.to_numpy(float)
        base = {k.split('|', 1)[1]: z[k].astype(bool) for k in z.files if k.startswith(f'{code}|')}
        def oct_fit(al, close, dates, code):
            import pandas as pd
            s = pd.Series(close, index=dates)
            a, b, _ = G.EVENTS[code][1]
            seg = s[(s.index >= a) & (s.index <= b)]
            pk = seg.idxmax(); pi = dates.index(pk)
            hits = np.where(O.onsets(al)[max(0, pi - O.JULY_WIN):pi + 1])[0]
            if len(hits) == 0:
                return 0, None, None
            i0 = max(0, pi - O.JULY_WIN) + hits[0]
            return 1, pi - i0, dates[i0]
        base['_ABS60P90'] = abs_alert(code, name, dates, 60, 90)
        base['_ABS60P95'] = abs_alert(code, name, dates, 60, 95)
        base['_ABS20P90'] = abs_alert(code, name, dates, 20, 90)

        for label, fn in SCHEMES.items():
            al = fn(base)
            if al.sum() < 2:
                rows.append(dict(scheme=label, name=name, on_frac=float((al & np.array([d >= O.EVAL_START for d in dates])).sum() / max(len(dates), 1)),
                                 onsets=0, clusters=0, valid=0, precision=np.nan,
                                 july=0, july_lead=None, july_onset=None, oct_hit=0, oct_lead=None))
                continue
            m = O.eval_series(al, close, dates)
            _j = O.july_fit(al, close, dates, code)
            jh, jl, jo = _j['july_hit'], _j['lead'], _j['july_onset']
            oh, ol, oo = oct_fit(al, close, dates, code)
            rows.append(dict(scheme=label, name=name, on_frac=m['on_frac'], onsets=m['onsets'],
                             clusters=m['clusters'], valid=m['valid'], precision=m['precision'],
                             july=jh, july_lead=jl, july_onset=jo, oct_hit=oh, oct_lead=ol))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(G.LAB, 'scheme_compare.csv'), index=False, encoding='utf-8-sig')

    pivot = df.pivot_table(index='scheme', columns='name', values='july_lead', aggfunc='first')
    print('=== 7月提前天数（—=未命中）===')
    print(pivot.to_string())
    print('\n=== 7月命中数 / 全样本精确率 / 常亮率 ===')
    for label in SCHEMES:
        sub = df[df.scheme == label]
        hits = int((sub.july == 1).sum())
        pr = sub.precision.mean()
        of = sub.on_frac.mean()
        octh = int((sub.oct_hit == 1).sum())
        print(f'{label:<30} 7月 {hits}/3  10月 {octh}/3  精确率 {pr*100:>5.1f}%  常亮 {of*100:>5.1f}%')
    return df


if __name__ == '__main__':
    main()
