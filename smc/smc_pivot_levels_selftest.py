#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pivot_levels 自测（不联网，合成数据）
================================================================
守住一个**静默出错**的坑：`smc_layer()['hi'|'lo']` 返回的是 K 线**索引**，
不是价格。历史上调用方直接把索引当价位用（`support = r['lo'][-1]`），
得到「支撑 187」这种连现价千分之一都不到的数字，而且不报错、不告警，
只有人工看图才发现（2026-11-25 options_utility `index_signal_board` 踩过）。

本自测验证 pivot_levels 的八条契约：
  1) 上沿 = 最近 n 个摆动高的最高值，下沿 = 最近 n 个摆动低的最低值
  2) 区间必须包含现价（正常行情下），且与「索引当价位」的结果差一个量级
  3) 空 hi/lo → None（不编造）
  4) 索引越界 / all-invalid → 过滤后为空 → None
  5) n=1 的区间比 n=2 更窄（被 n=2 区间包含）
  6) 现价突破上/下沿时 above_range / below_range 正确置位，pos_pct 截到 100/0
  7) n<=0 等非法参数返回 None（不拿全量摆动点凑数）
  8) 现价 NaN 返回 None（不出 nan 指标）

用法：
  python3 smc/smc_pivot_levels_selftest.py        # 退出码 0=通过
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np                        # noqa: E402
import pandas as pd                       # noqa: E402
import smc_core as C                      # noqa: E402


def _frame(seed=0, n=200):
    """合成一段随机游走日线（DT/OHLC 齐全，可被 pivots 正常识别）。"""
    rng = np.random.default_rng(seed)
    px = 100 + np.cumsum(rng.standard_normal(n) * 0.8)
    return pd.DataFrame({
        'dt': pd.date_range('2026-01-01', periods=n),
        'open': px,
        'high': px + 1.2,
        'low': px - 1.2,
        'close': px,
    })


def run():
    fails = []

    def chk(cond, msg):
        if not cond:
            fails.append(msg)

    df = _frame()
    r = C.smc_layer(df, 3)
    hi, lo = r['hi'], r['lo']
    chk(len(hi) >= 2 and len(lo) >= 2, '合成数据摆动点不足，无法自测')

    lv = C.pivot_levels(df, hi, lo)
    chk(lv is not None, '正常输入返回 None')
    if lv is None:
        return fails

    # 1) 上沿/下沿口径
    exp_high = max(float(df['high'].values[x]) for x in hi[-2:])
    exp_low = min(float(df['low'].values[x]) for x in lo[-2:])
    chk(abs(lv['high'] - exp_high) < 1e-3, '上沿 != 最近2个摆动高的最大值')
    chk(abs(lv['low'] - exp_low) < 1e-3, '下沿 != 最近2个摆动低的最小值')

    # 2) 含现价 + 与「索引当价位」不同量级
    close = float(df['close'].iloc[-1])
    chk(lv['low'] < close < lv['high'], '区间不包含现价')
    # 旧 bug：直接把索引当价位 → 量级远小于真实价格
    chk(abs(lo[-1] - lv['low']) > 1.0,
        '索引值与真实价位几乎一样，本自测失去意义（请检查数据规模）')

    # 3) 空输入
    chk(C.pivot_levels(df, [], []) is None, '空 hi/lo 未返回 None')

    # 4) 越界索引
    chk(C.pivot_levels(df, [-1, -2], [10 ** 6, 10 ** 7]) is None,
        '全越界索引未返回 None')

    # 5) n=1 比 n=2 更窄
    l1 = C.pivot_levels(df, hi, lo, n=1)
    chk(l1 is not None and l1['high'] <= lv['high'] and l1['low'] >= lv['low'],
        'n=1 区间未被 n=2 区间包含')

    # 6) 破位标记 + pos 截断
    up = df.copy()
    up.loc[up.index[-1], 'close'] = lv['high'] + 100
    up_lv = C.pivot_levels(up, hi, lo)
    chk(up_lv['above_range'] is True, 'above_range 未置位')
    chk(up_lv['pos_pct'] == 100.0, '突破上沿时 pos_pct 未截到 100')
    dn = df.copy()
    dn.loc[dn.index[-1], 'close'] = max(lv['low'] - 100, 0.01)
    dn_lv = C.pivot_levels(dn, hi, lo)
    chk(dn_lv['below_range'] is True, 'below_range 未置位')
    chk(dn_lv['pos_pct'] == 0.0, '跌破下沿时 pos_pct 未截到 0')

    # 7) 非法 n
    chk(C.pivot_levels(df, hi, lo, n=0) is None, 'n=0 未返回 None')
    chk(C.pivot_levels(df, hi, lo, n=-1) is None, 'n=-1 未返回 None')

    # 8) 现价 NaN
    bad = df.copy()
    bad.loc[bad.index[-1], 'close'] = np.nan
    chk(C.pivot_levels(bad, hi, lo) is None, 'NaN 现价未返回 None')

    return fails


def main():
    fails = run()
    print('=' * 70)
    if fails:
        print('❌ pivot_levels 自测失败 %d 项：' % len(fails))
        for f in fails:
            print('   -', f)
        return 1
    print('✅ pivot_levels 自测全部通过（8 条契约）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
