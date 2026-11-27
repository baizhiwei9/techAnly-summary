#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 7月特征：市场宽度背离（breadth divergence）

假设：7月是"指数还在涨、内部已经散架"（分散式顶），10月是"抱团冲顶"（集中式顶）。
若成立，这两个波次该用不同指标：
  7月 ← 宽度背离（上涨家数占比下滑 / 站上MA20的比例下滑，而指数新高）
  10月 ← 吸收比率（λ1/N 极高 = 抱团到极限）

指标（都用全市场面板，与指数无关，完全独立于现有 5 指标）
  br20   : 20日内上涨股票占比
  ma20   : 收盘价站上 MA20 的股票占比
  hh60   : 创 60 日新高的股票占比
  div    : 指数创 60 日新高 且 br20 未创新高  ← 背离
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

PEAKS = {'000852.SH': ('20260701', '20260817'), '000688.SH': ('20260630', '20260818'),
         '399006.SZ': ('20260625', '20260817')}


def breadth(panel, win=20):
    p = panel  # date × code, pct_chg
    up = (p > 0).astype(float)
    br = up.rolling(win).mean().iloc[:, :].mean(axis=1)  # 用全市场均值
    return br


def breadth_by_index(panel, codes, win=20):
    cols = [c for c in codes if c in panel.columns]
    sub = panel[cols]
    return (sub > 0).astype(float).rolling(win).mean().mean(axis=1)


def main():
    panel = L.panel()
    print('全市场面板', panel.shape)
    for code, name in L.IDX.items():
        codes = L.pool(code)
        br = breadth_by_index(panel, codes, 20)
        df = L.load_all()[code].set_index('trade_date')
        dates = [d for d in br.index if d in df.index]
        br = br.reindex(dates)
        close = df.close.reindex(dates)
        print(f'\n===== {name}  宽度(20日上涨占比)  均值 {br.mean():.1%}  P10/P90 '
              f'{br.quantile(.1):.1%}/{br.quantile(.9):.1%}')
        for pk in PEAKS[code]:
            if pk not in br.index:
                print(f'  峰{pk} 不在面板区间'); continue
            i = br.index.get_loc(pk)
            # 指数 60 日新高判断
            hh = close.iloc[max(0, i - 60):i + 1].max()
            is_hh = close.iloc[i] >= hh - 1e-9
            print(f'  峰{pk}: 宽度 {br.iloc[i]:.1%}（20日前 {br.iloc[max(0,i-20)]:.1%}）'
                  f'{"  ← 指数创60日新高" if is_hh else ""}')
            # 峰前每 5 日
            print('    峰前每5日: ' + ' '.join(
                f'{br.index[j][-4:]}:{br.iloc[j]:.0%}'
                for j in range(max(0, i - 60), i + 1, 5)))
        # 峰值处的宽度 vs 指数峰值：背离检测
        hh_idx = close.rolling(60).max()
        newhigh = (close >= hh_idx * 0.999)
        br_hi = br.rolling(60).max()
        br_newhigh = (br >= br_hi * 0.999)
        div = newhigh & (~br_newhigh)
        print(f'    背离日（指数新高但宽度未新高）: {int(div.sum())} 天  '
              f'最近: {[d for d in div[div].index][-5:]}')


if __name__ == '__main__':
    main()
