#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变系统 · 双通道回测（跑真实 pws_compute，不是实验脚本副本）
验证「改完代码后线上到底会不会报警」。
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

OUT = os.path.join(os.path.dirname(_BIN), '.cache', 'pws')
EVENTS = {
    '000852.SH': [('20260601', '20260731', '7月'), ('20260801', '20261031', '10月')],
    '000688.SH': [('20260601', '20260731', '7月'), ('20260801', '20261031', '10月')],
    '399006.SZ': [('20260601', '20260731', '7月'), ('20260801', '20261031', '10月')],
}


def onset(a, q=10):
    o = np.zeros(len(a), bool)
    for t in range(len(a)):
        if a[t] and not a[max(0, t - q):t].any():
            o[t] = True
    return o


def main():
    rows = []
    for code, name in D.IDX_NAME.items():
        px = D.index_px(code, '20250401', '20261103')
        r = C.compute(code, name, px, xsec_lmax=X.load_lmax(code),
                      ar_mode='resid', lam_mode='abs',
                      abs_ratio=AB.load_ar(code, '20261103'))
        df = r.df
        dates = df.date.tolist()
        close = df.close.to_numpy(float)
        chA = df.chA.to_numpy(bool)
        chB = df.chB.to_numpy(bool)
        onA, onB = onset(chA), onset(chB)
        for a, b, tag in EVENTS[code]:
            ii = [i for i, d in enumerate(dates) if a <= d <= b]
            if not ii:
                continue
            seg = close[ii[0]:ii[-1] + 1]
            pk = ii[0] + int(np.argmax(seg))
            tr = pk + int(np.argmin(close[pk:ii[-1] + 1]))
            drop = (close[tr] / close[pk] - 1) * 100

            def first_on(on):
                c = [i for i in range(max(0, pk - 40), pk + 1) if on[i]]
                return (f'{dates[c[0]]}(提前{pk-c[0]}日)' if c else '—')

            rows.append(dict(指数=name, 波次=tag, 峰日=dates[pk], 谷日=dates[tr],
                             跌幅=f'{drop:.1f}%',
                             通道A=first_on(onA), 通道B=first_on(onB),
                             A亮日=int(chA.sum()), B亮日=int(chB.sum()),
                             共振日=int(((chA & chB)).sum())))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'dual_backtest.csv'), index=False, encoding='utf-8-sig')
    print(df.to_string(index=False))


if __name__ == '__main__':
    main()
