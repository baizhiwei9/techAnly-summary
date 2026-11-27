#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 7月精度网格 + α 诊断评估
评估口径（事件级，全部诚实计入）：
  cluster  = 相邻告警日 gap<=10 归为一簇
  valid    = 簇首起 20 交易日内，指数从簇首收盘最大跌幅 >= 8%
  precision= valid / clusters
  7月提前 = 7月峰日前最后一个/第一个触发点：取峰前 60 日内**首次**触发日，提前 = 峰日索引-触发索引
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

z = np.load(G.ALERT_FP, allow_pickle=True)
WINDOW_START = '20250401'   # 评估样本（面板/截面数据只到这里）
DROP_THR = 0.08
HORIZON = 20
GAP = 10


def clusters(alert_idx):
    if len(alert_idx) == 0:
        return []
    out = [[alert_idx[0]]]
    for i in alert_idx[1:]:
        if i - out[-1][-1] <= GAP:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def evaluate(alert, close):
    """返回 (clusters数, valid数, precision)"""
    idx = np.where(alert)[0]
    cs = clusters(idx)
    valid = 0
    for c in cs:
        i = c[0]
        fw = close[i:i + HORIZON + 1]
        if len(fw) < 2:
            continue
        dd = fw.min() / close[i] - 1
        if dd <= -DROP_THR:
            valid += 1
    return len(cs), valid, (valid / len(cs) if cs else np.nan)


def july_lead(alert, dates, close, code):
    """7月峰（段内最高收盘）前首次触发"""
    s = pd.Series(close, index=dates)
    a, b, _ = G.EVENTS[code][0]
    seg = s[(s.index >= a) & (s.index <= b)]
    pk = seg.idxmax()
    pi = dates.index(pk)
    lo = max(0, pi - 60)
    seg_a = alert[lo:pi + 1]
    hits = np.where(seg_a)[0]
    first = (lo + hits[0]) if len(hits) else None
    last = (lo + hits[-1]) if len(hits) else None
    return dict(peak=pk, first=(dates[first] if first is not None else None),
                lead=(pi - first if first is not None else None),
                last=(dates[last] if last is not None else None),
                lead_last=(pi - last if last is not None else None))


def window_mask(dates):
    return np.array([d >= WINDOW_START for d in dates])


def main():
    rows = []
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        df = L.load_all()[code]
        close = df.close.to_numpy(float)
        wm = window_mask(dates)
        base = {k.split('|', 1)[1]: z[k] for k in z.files if k.startswith(f'{code}|')}

        # ---------- 单个指标：7月命中 / 全样本误报 ----------
        for key in sorted(base):
            if not (key.startswith('M1_') or key.startswith('M2_') or key.startswith('M3_') or key.startswith('M4_')):
                continue
            al = base[key].astype(bool) & wm
            nc, nv, pr = evaluate(al, close)
            jl = july_lead(base[key].astype(bool), dates, close, code)
            rows.append(dict(code=code, name=name, combo=key, n_alert_days=int(al.sum()),
                             clusters=nc, valid=nv, precision=pr,
                             july_lead=jl['lead'], july_first=jl['first'], peak=jl['peak']))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(G.LAB, 'single_indicator_eval.csv'), index=False, encoding='utf-8-sig')

    # 打印每个指标族的最佳代表
    print('=== 单指标：7月首次告警提前天数 & 全样本精确率 ===')
    for name in L.IDX.values():
        sub = df[(df.name == name) & (df.clusters > 0)]
        sub = sub.sort_values(['july_lead', 'precision'], ascending=[False, False])
        print(f'\n--- {name} ---')
        for _, r in sub.head(12).iterrows():
            print(f"  {r.combo:<28} 7月提前 {str(r.july_lead):>5}日 ({r.july_first}) "
                  f"簇{int(r.clusters):>2} 中{int(r.valid):>2} 精确率 {r.precision:.0%}")
    return df


if __name__ == '__main__':
    main()
