#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gex_multi.py —— 多标的期权 GEX / Gamma Flip 日频计算（ETF + 股指期权）

口径（dealer 视角，与 bin/gex_core.py、bin/opt_gex.py 一致）：
    单腿 Γ = e^{-qT} φ(d1) / (S σ √T),  d1 = [ln(S/K)+(r−q+σ²/2)T]/(σ√T), T=DTE/365
    GEX_i  = sign_i · Γ_i · OI_i · MULT · S² · 0.01     单位：元 / 每 1% 标的变动
    sign_i = +1(Call) / −1(Put)
    输出统一 ÷1e8 → 亿元

数据源：tushare（opt_basic / opt_daily / fund_daily / index_daily），代理 token 优先。
IV：BSM 从中价反解（close>0 用 close，否则 settle），失败或 IV∉(0.02,4.5) 丢弃。

用法：
    python3 strategies/gex_multi.py                     # 最新可交易日
    python3 strategies/gex_multi.py --date 20261026
    python3 strategies/gex_multi.py --date 20261026 --min-dte 3 --json out.json
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
import math
import json
import argparse
import datetime as _dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from tushare_init import get_pro

R = 0.02
Q = 0.0
IV_LO, IV_HI = 0.02, 4.5
MULT_ETF = 10000.0
MULT_IDX = 100.0
SQRT2 = math.sqrt(2.0)
INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

# name -> (opt_code, underlying ts_code, kind, multiplier)
TARGETS = [
    ('50ETF期权',      'OP510050.SH',  '510050.SH', 'etf', MULT_ETF),
    ('300ETF期权',     'OP510300.SH',  '510300.SH', 'etf', MULT_ETF),
    ('500ETF期权',     'OP510500.SH',  '510500.SH', 'etf', MULT_ETF),
    ('科创50ETF期权',  'OP588000.SH',  '588000.SH', 'etf', MULT_ETF),
    ('创业板ETF期权',  'OP159915.SZ',  '159915.SZ', 'etf', MULT_ETF),
    ('沪深300股指期权', 'OP000300.SH',  '000300.SH', 'idx', MULT_IDX),
    ('中证1000股指期权', 'OP000852.SH', '000852.SH', 'idx', MULT_IDX),
    ('上证50股指期权',  'OP000016.SH',  '000016.SH', 'idx', MULT_IDX),
]


def _npdf(x):
    return INV_SQRT_2PI * math.exp(-0.5 * x * x)


def bs_price(S, K, T, vol, r, q, call=True):
    if T <= 0 or vol <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + (r - q + 0.5 * vol * vol) * T) / (vol * math.sqrt(T))
    d2 = d1 - vol * math.sqrt(T)
    if call:
        return S * math.exp(-q * T) * _nd(d1) - K * math.exp(-r * T) * _nd(d2)
    return K * math.exp(-r * T) * _nd(-d2) - S * math.exp(-q * T) * _nd(-d1)


def _nd(x):
    return 0.5 * math.erfc(-x / SQRT2)


def bs_iv(price, S, K, T, r, q, call=True):
    if price is None or price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intr = max(0.0, (S - K) if call else (K - S))
    if price < intr * 0.999:
        return None
    lo, hi = 1e-4, 5.0
    if bs_price(S, K, T, lo, r, q, call) > price:
        return None
    if bs_price(S, K, T, hi, r, q, call) < price:
        return None
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, r, q, call) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-7:
            break
    return 0.5 * (lo + hi)


def gamma(S, K, T, vol, r, q):
    if T <= 0 or vol <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * vol * vol) * T) / (vol * math.sqrt(T))
    return math.exp(-q * T) * _npdf(d1) / (S * vol * math.sqrt(T))


def get_spot(pro, date, kind, und):
    if kind == 'etf':
        d = pro.fund_daily(ts_code=und, trade_date=date)
        if d is not None and len(d):
            return float(d['close'].iloc[0])
        d = pro.fund_daily(ts_code=und, start_date=(pd.to_datetime(date) - pd.Timedelta(days=15)).strftime('%Y%m%d'), end_date=date)
        if d is not None and len(d):
            d = d.sort_values('trade_date')
            return float(d['close'].iloc[-1])
    else:
        d = pro.index_daily(ts_code=und, trade_date=date)
        if d is not None and len(d):
            return float(d['close'].iloc[0])
        d = pro.index_daily(ts_code=und, start_date=(pd.to_datetime(date) - pd.Timedelta(days=15)).strftime('%Y%m%d'), end_date=date)
        if d is not None and len(d):
            d = d.sort_values('trade_date')
            return float(d['close'].iloc[-1])
    return None


def chain_for(pro, date, ex, opt_code):
    daily = pro.opt_daily(trade_date=date, exchange=ex)
    if daily is None or not len(daily):
        return None, None
    basic = pro.opt_basic(exchange=ex)
    b = basic[basic.opt_code == opt_code].copy()
    if not len(b):
        return None, None
    b['maturity_date'] = pd.to_datetime(b['maturity_date'], format='%Y%m%d')
    x = daily.merge(b[['ts_code', 'call_put', 'exercise_price', 'maturity_date']], on='ts_code', how='inner')
    return x, b


def legs_for(x, date, S, min_dte, max_dte):
    d0 = pd.to_datetime(date, format='%Y%m%d')
    rows = []
    for _, r_ in x.iterrows():
        try:
            K = float(r_['exercise_price'])
        except Exception:
            continue
        mat = r_['maturity_date']
        dte = (mat - d0).days
        if dte < min_dte or dte > max_dte:
            continue
        px = r_['close'] if (pd.notna(r_['close']) and r_['close'] > 0) else r_['settle']
        oi = r_['oi']
        if not pd.notna(px) or px <= 0:
            continue
        if not pd.notna(oi) or oi <= 0:
            continue
        cp = r_['call_put']
        if cp not in ('C', 'P'):
            continue
        rows.append(dict(K=K, dte=dte, mat=mat, px=float(px), oi=float(oi),
                         cp=cp, sgn=1.0 if cp == 'C' else -1.0))
    return rows


def gex_at(rows, S, r=R, q=Q):
    """返回 (net_gex_亿元, call_亿, put_亿, n_legs)；S 为评估价（可 ≠ 原 S）"""
    tot_c = tot_p = 0.0
    n = 0
    for it in rows:
        T = it['dte'] / 365.0
        iv = it['iv']
        if iv is None:
            continue
        G = gamma(S, it['K'], T, iv, r, q)
        if G <= 0:
            continue
        g = G * it['oi'] * it['mult'] * S * S * 0.01 / 1e8
        if it['sgn'] > 0:
            tot_c += g
        else:
            tot_p += g
        n += 1
    return tot_c - tot_p, tot_c, tot_p, n


def prep_iv(rows, S):
    out = []
    for it in rows:
        T = it['dte'] / 365.0
        iv = bs_iv(it['px'], S, it['K'], T, R, Q, it['cp'] == 'C')
        if iv is None or not (IV_LO < iv < IV_HI):
            continue
        it2 = dict(it)
        it2['iv'] = iv
        out.append(it2)
    return out


def weighted_gex(rows, S, tgt=(30, 90), w=(0.5, 0.5)):
    """按到期月分组，在 (DTE, GEX) 上插值到 30/90 天"""
    by_mat = {}
    for it in rows:
        by_mat.setdefault((it['mat'], it['dte']), []).append(it)
    pts = []
    for (mat, dte), legs in by_mat.items():
        g, _, _, _ = gex_at(legs, S)
        pts.append((dte, g))
    pts.sort()
    if len(pts) < 2:
        return None, pts
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    res = {}
    for t in tgt:
        if t <= xs[0]:
            k = 0
            x0, x1, y0, y1 = xs[0], xs[1], ys[0], ys[1]
        elif t >= xs[-1]:
            k = -1
            x0, x1, y0, y1 = xs[-2], xs[-1], ys[-2], ys[-1]
        else:
            k = None
            for i in range(len(xs) - 1):
                if xs[i] <= t <= xs[i + 1]:
                    x0, x1, y0, y1 = xs[i], xs[i + 1], ys[i], ys[i + 1]
                    break
        res[t] = y0 + (y1 - y0) * (t - x0) / (x1 - x0) if x1 != x0 else y0
    return {t: res[t] for t in tgt}, pts


def find_flips(rows, S, lo=0.90, hi=1.10, step=0.005):
    grid = np.arange(S * lo, S * hi + 1e-9, S * step)
    ys = [gex_at(rows, s)[0] for s in grid]
    flips = []
    for i in range(len(grid) - 1):
        if ys[i] == 0:
            flips.append(float(grid[i]))
        elif ys[i] * ys[i + 1] < 0:
            x0, x1, y0, y1 = grid[i], grid[i + 1], ys[i], ys[i + 1]
            flips.append(float(x0 + (x1 - x0) * (-y0) / (y1 - y0)))
    if not flips:
        return []
    flips.sort(key=lambda v: abs(v - S))
    return flips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    ap.add_argument('--min-dte', type=int, default=3)
    ap.add_argument('--max-dte', type=int, default=9999)
    ap.add_argument('--json', default='')
    a = ap.parse_args()

    pro = get_pro()
    date = a.date or _dt.date.today().strftime('%Y%m%d')

    cache = {}
    out = {'date': date, 'items': []}
    for name, opt_code, und, kind, mult in TARGETS:
        ex = 'SSE' if opt_code.endswith('.SH') else ('SZSE' if opt_code.endswith('.SZ') else 'CFFEX')
        if opt_code.startswith('OP000'):
            ex = 'CFFEX'
        S = get_spot(pro, date, kind, und)
        if S is None:
            print(f'[skip] {name}: 无现货', flush=True)
            continue
        if ex not in cache:
            cache[ex] = pro.opt_basic(exchange=ex)
        basic = cache[ex]
        b = basic[basic.opt_code == opt_code].copy()
        if not len(b):
            print(f'[skip] {name}: opt_basic 无 {opt_code}', flush=True)
            continue
        b['maturity_date'] = pd.to_datetime(b['maturity_date'], format='%Y%m%d')
        daily = pro.opt_daily(trade_date=date, exchange=ex)
        if daily is None or not len(daily):
            print(f'[skip] {name}: 无 opt_daily', flush=True)
            continue
        x = daily.merge(b[['ts_code', 'call_put', 'exercise_price', 'maturity_date']], on='ts_code', how='inner')
        if not len(x):
            print(f'[skip] {name}: 合并后为空', flush=True)
            continue
        rows = legs_for(x, date, S, a.min_dte, a.max_dte)
        for it in rows:
            it['mult'] = mult
        rows = prep_iv(rows, S)
        if not rows:
            print(f'[skip] {name}: 有效腿为 0', flush=True)
            continue
        net, gc, gp, n = gex_at(rows, S)
        flips = find_flips(rows, S)
        wg, pts = weighted_gex(rows, S)
        oi_c = sum(it['oi'] for it in rows if it['sgn'] > 0)
        oi_p = sum(it['oi'] for it in rows if it['sgn'] < 0)
        item = dict(name=name, opt_code=opt_code, und=und, S=S,
                    net_gex=net, gex_call=gc, gex_put=gp, n_legs=n,
                    oi_call=oi_c, oi_put=oi_p,
                    flip=(flips[0] if flips else None), n_flip=len(flips),
                    flip_dist=((S - flips[0]) / S * 100 if flips else None),
                    gex30=(wg[30] if wg else None), gex90=(wg[90] if wg else None),
                    gex_w=(0.5 * wg[30] + 0.5 * wg[90] if wg else None),
                    curve=[(float(p[0]), float(p[1])) for p in pts])
        out['items'].append(item)
        flip_s = f'{flips[0]:.4f}' if flips else '无翻转'
        print(f'{name:14s} S={S:10.4f}  净GEX={net:+10.2f}亿  Call={gc:+9.2f}  Put={-gp:+9.2f}  '
              f'Flip={flip_s}  腿={n}  30/90加权={item["gex_w"] if item["gex_w"] is None else round(item["gex_w"],2)}', flush=True)

    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print('json ->', a.json)


if __name__ == '__main__':
    main()
