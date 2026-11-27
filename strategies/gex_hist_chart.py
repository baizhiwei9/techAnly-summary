#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gex_hist_chart.py —— 把群日报抓下来的 GEX 历史画成「一图多子图」

每个标的（上证50ETF/沪深300ETF/中证500ETF/创业板ETF/科创50ETF/中证1000）一个子图，
共 6 个子图，同一张 PNG。

数据：.cache/gex_daily_from_daily.json（由 gex_hist_parse.py 生成）
输出：output/gex_hist_20260601_now.png
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import json
import argparse
import datetime as _dt

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

matplotlib.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'Noto Sans CJK SC',
                                          'WenQuanYi Micro Hei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
OUT = os.path.join(WS, 'output')

NAMES = ['上证50ETF', '沪深300ETF', '中证500ETF', '创业板ETF', '科创50ETF', '中证1000']
FNAME = {
    '上证50ETF': '上证50ETF期权 (510050)',
    '沪深300ETF': '沪深300ETF期权 (510300)',
    '中证500ETF': '中证500ETF期权 (510500)',
    '创业板ETF': '创业板ETF期权 (159915)',
    '科创50ETF': '科创50ETF期权 (588000)',
    '中证1000': '中证1000股指期权 (MO 000852)',
}


def trade_days(start, end):
    try:
        sys.path.insert(0, HERE)
        from tushare_init import get_pro
        pro = get_pro()
        cal = pro.trade_cal(exchange='SSE', start_date=start.replace('-', ''),
                            end_date=end.replace('-', ''), is_open='1')
        return set(pd.to_datetime(cal['cal_date'], format='%Y%m%d').dt.strftime('%Y-%m-%d'))
    except Exception as e:
        print('[warn] trade_cal 失败，用工作日近似:', str(e)[:60])
        rng = pd.bdate_range(start, end)
        return set(d.strftime('%Y-%m-%d') for d in rng)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-06-01')
    ap.add_argument('--end', default=_dt.date.today().strftime('%Y-%m-%d'))
    ap.add_argument('--src', default=os.path.join(WS, '.cache', 'gex_daily_from_daily.json'))
    ap.add_argument('--out', default=os.path.join(OUT, 'gex_hist_20260601_now.png'))
    a = ap.parse_args()

    d = json.load(open(a.src, encoding='utf-8'))
    bd = d['by_date']
    tds = trade_days(a.start, a.end)
    print('交易日 %d 天' % len(tds))

    # 清洗：只留交易日 + 剔除 five 解析异常（值明显不是亿量级）
    clean = {}
    for dt, row in bd.items():
        if dt < a.start or dt > a.end or dt not in tds:
            continue
        r2 = {}
        for n, v in row.items():
            if n not in NAMES:
                continue
            k = v.get('kind')
            g = v['net_gex']
            # five 块里「含波动率预警与5日预测」版列错位 → 值与同标的中位数差 2 个量级，剔掉
            if k == 'five' and n != '中证1000' and abs(g) > 60:
                continue
            if n == '中证1000' and abs(g) < 100:   # 单位异常（7.24 这类）
                continue
            r2[n] = g
        if r2:
            clean[dt] = r2
    dates = sorted(clean)
    print('有效日期 %d 天：%s .. %s' % (len(dates), dates[0], dates[-1]))

    x = pd.to_datetime(dates)
    fig, axes = plt.subplots(len(NAMES), 1, figsize=(15, 2.6 * len(NAMES) + 1.2),
                             sharex=True, constrained_layout=True)
    for ax, nm in zip(axes, NAMES):
        y = [clean[dt].get(nm) for dt in dates]
        ys = np.array([np.nan if v is None else v for v in y], dtype=float)
        ok = ~np.isnan(ys)
        ax.axhline(0, color='#999', lw=0.9, ls='--', zorder=1)
        ax.plot(x[ok], ys[ok], lw=1.5, color='#c0392b', marker='o', ms=3.2,
                mfc='w', mew=0.9, zorder=3)
        # 负值区域填色
        ax.fill_between(x[ok], 0, ys[ok], where=(ys[ok] < 0), color='#e74c3c',
                        alpha=0.22, interpolate=True, zorder=2)
        ax.fill_between(x[ok], 0, ys[ok], where=(ys[ok] >= 0), color='#2e86de',
                        alpha=0.16, interpolate=True, zorder=2)
        last_i = None
        for i in range(len(dates) - 1, -1, -1):
            if ok[i]:
                last_i = i
                break
        ttl = ('%s   最新 %s = %+.2f 亿' % (FNAME[nm], dates[last_i], ys[last_i])
               if last_i is not None else FNAME[nm] + '   无数据')
        if last_i is not None and dates[last_i] != dates[-1]:
            ttl += '  ⚠缺 %s' % dates[-1][5:]
        ax.set_title(ttl, fontsize=10.5, loc='left', pad=4)
        ax.grid(alpha=0.25, ls=':')
        ax.set_ylabel('净GEX (亿)', fontsize=9)
        # 覆盖天数统计
        ax.text(0.995, 0.06, 'N=%d' % ok.sum(), transform=ax.transAxes,
                ha='right', va='bottom', fontsize=8, color='#666')
        ax.tick_params(labelsize=8.5)
        # 极值标注
        if ok.sum() > 3:
            imax, imin = int(np.nanargmax(ys)), int(np.nanargmin(ys))
            ax.annotate('%+.1f' % ys[imax], (x[imax], ys[imax]), fontsize=7.5,
                        color='#2e86de', ha='center', va='bottom')
            ax.annotate('%+.1f' % ys[imin], (x[imin], ys[imin]), fontsize=7.5,
                        color='#c0392b', ha='center', va='top')

    axes[-1].xaxis.set_major_locator(mdates.MonthLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    axes[-1].xaxis.set_minor_locator(mdates.DayLocator(interval=7))
    fig.suptitle('A股期权 净Gamma敞口（GEX）历史 · 2026-06-01 ~ %s · 数据源：飞书群日报' % dates[-1],
                 fontsize=13.5, y=0.998)
    fig.text(0.005, 0.001,
             '注：实线=净GEX（亿，Call+/Put−），灰虚线=零轴；红底=负Gamma（做市商追涨杀跌、放大波动），蓝底=正Gamma（压波动）。'
             '中证1000 为股指期权（乘数100），量纲与 ETF 期权不可直接比大小；缺口=当日群未发/解析不到。',
             fontsize=8.2, color='#555', ha='left', va='bottom')
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(a.out, dpi=135, bbox_inches='tight')
    print('->', a.out)

    # 打印清洗后序列，便于核对
    for nm in NAMES:
        ys = [clean[dt].get(nm) for dt in dates]
        print('%-10s N=%2d  last=%s' % (nm, sum(v is not None for v in ys),
                                       next((('%s:%+.2f' % (dates[i], ys[i])) for i in range(len(dates) - 1, -1, -1) if ys[i] is not None), '-')))


if __name__ == '__main__':
    main()
