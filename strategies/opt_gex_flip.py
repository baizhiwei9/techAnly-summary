#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科创50ETF (588000 / OP588000.SH) 期权 —— 净 GEX + Gamma Flip 曲线

严格口径（老板指定）：
    基准日 S = ETF 收盘（默认最新交易日，20261023 = 1.422）
    r = 0.02（10Y 国债近似），q = 0

  1) 筛选
     - DTE = maturity - trade_date（自然日），只留 DTE >= 7 天
     - 中价：close>0 用 close，否则用 settle；close<=0 或 oi<=0 剔除
     - IV：BSM 从中价反解（call/put 各自），失败或 IV ∉ (0.02, 4.5) 剔除
     - 乘数 10000 股/张
  2) Γ_i = φ(d1) / (S·σ·√T)
     d1 = [ln(S/K) + (r−q+σ²/2)T] / (σ√T),  T = DTE/365
  3) GEX_i = Γ_i · OI_i · 10000 · S² · 0.01   【元 / 每 1% 标的变动】
     Call 取 +，Put 取 −（净 GEX = ΣCall − Σ|Put|）
     输出单位：亿元（÷1e8）
  4) 净 GEX = Σ GEX_i（每个到期月单独算，再合计）
  5) Gamma Flip：S* ∈ [0.90S, 1.10S] 步长 0.5%（交叉验证 0.85~1.15 步长 0.25%）
     每个 S* 重算全部 Γ（σ、K、T、OI 固定，只改 S），找 net GEX(S*)=0 的零点
     符号变化处线性插值，多零点全列

用法：
    python3 strategies/opt_gex_flip.py                     # 最新交易日，只打印
    python3 strategies/opt_gex_flip.py --date 20261023 --plot
    python3 strategies/opt_gex_flip.py --r 0.02 --min-dte 7

输出：
    output/kc50_gex_flip_<date>.csv   S*, net_gex_total, 各到期月列
    output/kc50_gex_flip_<date>.png   GEX(S*) 曲线
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import math
import argparse
import datetime as _dt

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

matplotlib.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'Noto Sans CJK SC',
                                          'WenQuanYi Micro Hei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WS, 'output')
HIST = os.path.join(OUT, 'opt588000_hist_cache.csv')
ETF_CSV = os.path.join(OUT, 'kc50_etf_close.csv')

MULT = 10000.0
IV_LO, IV_HI = 0.02, 4.5
MIN_DTE = 7
SQRT2 = math.sqrt(2.0)
INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


# ---------- BSM ----------
def _nd(x):
    return 0.5 * math.erfc(-x / SQRT2)


def _npdf(x):
    return INV_SQRT_2PI * math.exp(-0.5 * x * x)


def bs_price(S, K, T, vol, r, q, call=True):
    if T <= 0 or vol <= 0:
        return max(0.0, (S - K) if call else (K - S))
    sq = vol * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * vol * vol) * T) / sq
    d2 = d1 - sq
    if call:
        return S * math.exp(-q * T) * _nd(d1) - K * math.exp(-r * T) * _nd(d2)
    return K * math.exp(-r * T) * _nd(-d2) - S * math.exp(-q * T) * _nd(-d1)


def implied_vol(price, S, K, T, r, q, call=True):
    """中价反解 IV；失败返回 nan"""
    if (not np.isfinite(price)) or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return np.nan
    intr = max(0.0, (S - K) if call else (K - S))
    if price < intr * 0.98:
        return np.nan
    lo, hi = 1e-4, IV_HI
    if bs_price(S, K, T, hi, r, q, call) < price:
        return np.nan
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, r, q, call) < price:
            lo = mid
        else:
            hi = mid
    v = 0.5 * (lo + hi)
    if not (IV_LO < v < IV_HI):
        return np.nan
    return v


def gamma(S, K, T, vol, r, q):
    if T <= 0 or vol <= 0 or S <= 0:
        return 0.0
    sq = vol * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * vol * vol) * T) / sq
    return math.exp(-q * T) * _npdf(d1) / (S * sq)


# ---------- 数据 ----------
def load_etf_close(date):
    e = pd.read_csv(ETF_CSV, dtype={'trade_date': int})
    e = e[e.trade_date <= date].sort_values('trade_date')
    if e.empty:
        raise SystemExit(f'ETF 收盘无 {date} 之前的数据')
    return float(e.iloc[-1].close), int(e.iloc[-1].trade_date)


def build_legs(date, S, r, q, min_dte, hist_df=None):
    d = hist_df if hist_df is not None else pd.read_csv(
        HIST, dtype={'trade_date': int, 'maturity': int})
    snap = d[d.trade_date == date].copy()
    if snap.empty:
        raise SystemExit(f'期权链快照无 {date}')
    def _to_dt(x):
        x = int(x)
        return _dt.date(x // 10000, (x // 100) % 100, x % 100)
    d0 = _to_dt(date)
    snap['dte'] = [(_to_dt(m) - d0).days for m in snap.maturity]
    n_raw = len(snap)
    snap = snap[snap.dte >= min_dte].copy()
    n_dte = len(snap)

    snap['mid'] = np.where(snap.close > 0, snap.close, snap.settle)
    snap = snap[(snap.mid > 0) & (snap.oi > 0)].copy()
    n_mid = len(snap)

    snap['T'] = snap.dte / 365.0
    snap['call'] = snap.cp.str.upper().str.startswith('C')
    snap['iv'] = [
        implied_vol(row.mid, S, row.K, row.T, r, q, row.call)
        for row in snap.itertuples()
    ]
    n_before_iv = len(snap)
    snap = snap[np.isfinite(snap.iv)].copy()
    n_iv = len(snap)

    snap['sign'] = np.where(snap.call, 1.0, -1.0)
    snap['gamma0'] = [
        gamma(S, row.K, row.T, row.iv, r, q) for row in snap.itertuples()
    ]
    # GEX_i = Γ·OI·10000·S²·0.01  (元/1%) → 亿元
    snap['gex'] = (snap.sign * snap.gamma0 * snap.oi * MULT * S * S * 0.01) / 1e8
    snap['gex_abs'] = (snap.gamma0 * snap.oi * MULT * S * S * 0.01) / 1e8
    meta = dict(n_raw=n_raw, n_dte=n_dte, n_mid=n_mid,
                n_before_iv=n_before_iv, n_iv=n_iv)
    return snap, meta


# ---------- 曲线 / 零点 ----------
def curve(legs, S, r, q, lo_mult, hi_mult, step):
    """返回 (S数组, total数组, {maturity: 数组})；每个 S* 重算 Γ，OI/σ/K/T 固定"""
    n = int(round((hi_mult - lo_mult) / step)) + 1
    s_arr = np.round(np.linspace(S * lo_mult, S * hi_mult, n), 6)
    mats = sorted(legs.maturity.unique())
    tot = np.zeros(len(s_arr))
    per = {m: np.zeros(len(s_arr)) for m in mats}
    for i, s in enumerate(s_arr):
        for m in mats:
            sub = legs[legs.maturity == m]
            g = 0.0
            for row in sub.itertuples():
                gm = gamma(s, row.K, row.T, row.iv, r, q)
                g += row.sign * gm * row.oi * MULT * s * s * 0.01 / 1e8
            per[m][i] = g
            tot[i] += g
    return s_arr, tot, per


def find_zeros(s_arr, y):
    """线性插值找所有零点；返回 [(S*, 斜率方向)]"""
    zs = []
    for i in range(len(y) - 1):
        a, b = y[i], y[i + 1]
        if a == 0.0:
            zs.append((float(s_arr[i]), 0.0))
        elif a * b < 0:
            t = a / (a - b)
            zs.append((float(s_arr[i] + t * (s_arr[i + 1] - s_arr[i])),
                       float(b - a)))
    if y[-1] == 0.0:
        zs.append((float(s_arr[-1]), 0.0))
    # 去重
    out = []
    for z, sl in zs:
        if not out or abs(z - out[-1][0]) > 1e-9:
            out.append((z, sl))
    return out


def value_at(s_arr, y, s0):
    return float(np.interp(s0, s_arr, y))


# ---------- 时间序列 ----------
def net_gex_at(legs, s, r, q):
    """在任意 S* 处重算全部 Γ 后的净 GEX（亿元/1%），Call +，Put −"""
    tot = 0.0
    for row in legs.itertuples():
        tot += row.sign * gamma(s, row.K, row.T, row.iv, r, q) \
            * row.oi * MULT * s * s * 0.01 / 1e8
    return tot


def series_curve(legs, S, r, q, lo_mult=0.90, hi_mult=1.10, step=0.005):
    """S* 扫描网格上的净 GEX 曲线（逐点重算 Γ）"""
    n = int(round((hi_mult - lo_mult) / step)) + 1
    s_arr = np.round(np.linspace(S * lo_mult, S * hi_mult, n), 6)
    y = np.array([net_gex_at(legs, float(s), r, q) for s in s_arr])
    return s_arr, y


def pick_flips(s_arr, y, S):
    """返回 (flip_1, flip_2, flip_count, flip_1斜率)；flip 按距现价远近排序"""
    zs = find_zeros(s_arr, y)
    zs_sorted = sorted(zs, key=lambda t: abs(t[0] - S))
    f1 = zs_sorted[0][0] if len(zs_sorted) >= 1 else np.nan
    f2 = zs_sorted[1][0] if len(zs_sorted) >= 2 else np.nan
    d1 = zs_sorted[0][1] if len(zs_sorted) >= 1 else np.nan
    return f1, f2, len(zs), d1


def run_series(args):
    hist = pd.read_csv(HIST, dtype={'trade_date': int, 'maturity': int})
    etf = pd.read_csv(ETF_CSV, dtype={'trade_date': int})
    etf_map = dict(zip(etf.trade_date, etf.close))

    dates = sorted(d for d in hist.trade_date.unique()
                   if args.start <= d <= args.end and d in etf_map)
    print('=' * 96)
    print(f'科创50ETF 期权 净GEX / Gamma Flip 时间序列   {dates[0]} ~ {dates[-1]}  '
          f'共 {len(dates)} 个交易日')
    print(f'口径：S=当日ETF收盘  r={args.r}  q={args.q}  乘数10000  T=DTE/365  '
          f'仅 DTE>={args.min_dte}  Call正/Put负（gex_put 列即负值）  OI=T日')
    print(f'Flip：S*∈[0.90S,1.10S] 步长 0.5%（{int(round(0.2 / 0.005)) + 1} 点），'
          f'每个 S* 重算全部 Γ，线性插值求零点，全部列出')
    print('=' * 96)

    recs = []
    for i, date in enumerate(dates, 1):
        date = int(date)
        S = float(etf_map[date])
        legs, meta = build_legs(date, S, args.r, args.q, args.min_dte, hist_df=hist)
        if legs.empty:
            print(f'  [{i:>3}/{len(dates)}] {date} 无有效腿，跳过')
            continue
        net = float(legs.gex.sum())
        gc = float(legs[legs.call].gex.sum())
        gp = float(legs[~legs.call].gex.sum())
        s_arr, y = series_curve(legs, S, args.r, args.q)
        f1, f2, fcnt, fdir = pick_flips(s_arr, y, S)
        if fcnt == 0:
            regime = 'ALL_POS' if y.min() > 0 else ('ALL_NEG' if y.max() < 0 else 'NONE')
        else:
            regime = 'ZERO'
        recs.append(dict(
            trade_date=date, S=round(S, 6),
            net_gex=round(net, 6), gex_call=round(gc, 6), gex_put=round(gp, 6),
            n_legs=len(legs), n_maturities=int(legs.maturity.nunique()),
            flip_1=(round(f1, 6) if np.isfinite(f1) else np.nan),
            flip_2=(round(f2, 6) if np.isfinite(f2) else np.nan),
            flip_count=fcnt,
            flip_pct=(round((f1 / S - 1) * 100, 4) if np.isfinite(f1) else np.nan),
            flip_1_slope=(round(float(fdir), 6) if np.isfinite(fdir) else np.nan),
            gex_lo=round(float(y[0]), 6), gex_hi=round(float(y[-1]), 6),
            gex_min=round(float(y.min()), 6), gex_max=round(float(y.max()), 6),
            regime=regime,
        ))
        if i % 10 == 0 or i == len(dates):
            print(f'  [{i:>3}/{len(dates)}] {date}  S={S:.3f}  net={net:+8.3f}亿  '
                  f'flip={("—" if not np.isfinite(f1) else f"{f1:.4f}")}  legs={len(legs)}')

    df = pd.DataFrame(recs)
    csv_path = os.path.join(OUT, 'kc50_gex_series.csv')
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\nCSV → {csv_path}  ({len(df)} 行)')
    return df


def report_series(df):
    n = len(df)
    pos = int((df.net_gex > 0).sum())
    neg = int((df.net_gex < 0).sum())
    print('\n' + '=' * 96)
    print('【A】全区间净 GEX 正负统计')
    print('-' * 96)
    print(f'  交易日 {n} 天：正值 {pos} 天 ({pos / n * 100:.1f}%)   '
          f'负值 {neg} 天 ({neg / n * 100:.1f}%)   零值 {n - pos - neg} 天')

    st = np.sign(df.net_gex.values)
    changes = [i for i in range(1, n)
               if st[i] != st[i - 1] and st[i] != 0 and st[i - 1] != 0]
    print('\n【B】正负切换（state change）')
    print('-' * 96)
    first_neg = df.index[df.net_gex < 0]
    if len(first_neg):
        i0 = int(first_neg[0])
        if i0 > 0:
            print(f'  首次转负：{int(df.trade_date[i0])}  S={df.S[i0]:.3f}  '
                  f'net={df.net_gex[i0]:+.3f} 亿  '
                  f'（前一日 {int(df.trade_date[i0 - 1])} net={df.net_gex[i0 - 1]:+.3f} 亿）')
        else:
            print(f'  首次转负：{int(df.trade_date[i0])}  首日即负')
    else:
        print('  ⚠️ 全区间从未出现负净 GEX')
    print(f'  切换次数：{len(changes)}')
    for i in changes:
        arrow = '正 → 负' if st[i] < 0 else '负 → 正'
        print(f'    {int(df.trade_date[i - 1])} → {int(df.trade_date[i])}   {arrow}   '
              f'net {df.net_gex[i - 1]:+.3f} → {df.net_gex[i]:+.3f} 亿   S={df.S[i]:.3f}')

    print('\n【C】净 GEX 极值')
    print('-' * 96)
    imin = int(np.argmin(df.net_gex.values))
    imax = int(np.argmax(df.net_gex.values))
    print(f'  最小 {df.net_gex[imin]:+.4f} 亿  @ {int(df.trade_date[imin])}  '
          f'(S={df.S[imin]:.3f})')
    print(f'  最大 {df.net_gex[imax]:+.4f} 亿  @ {int(df.trade_date[imax])}  '
          f'(S={df.S[imax]:.3f})')
    print(f'  均值 {df.net_gex.mean():+.4f} 亿   中位数 {df.net_gex.median():+.4f} 亿   '
          f'为负占比 {neg / n * 100:.1f}%')

    print('\n【D】净 GEX 负值区间段（连续）')
    print('-' * 96)
    segs, cur = [], None
    for i in range(n):
        if df.net_gex[i] < 0:
            cur = [i, i] if cur is None else [cur[0], i]
            cur[1] = i
        elif cur is not None:
            segs.append(cur); cur = None
    if cur is not None:
        segs.append(cur)
    if not segs:
        print('  无负值区间（全区间净 GEX ≥ 0）')
    else:
        print(f'  共 {len(segs)} 段：')
        print(f'  {"#":<3}{"起始日":<11}{"结束日":<11}{"天数":>5}{"段内均值":>12}'
              f'{"段内最负":>12}{"S区间":>18}')
        for k, (a, b) in enumerate(segs, 1):
            sub = df.iloc[a:b + 1]
            print(f'  {k:<3}{int(df.trade_date[a]):<11}{int(df.trade_date[b]):<11}'
                  f'{b - a + 1:>5}{sub.net_gex.mean():>12.3f}{sub.net_gex.min():>12.3f}'
                  f'{f"{sub.S.min():.3f}~{sub.S.max():.3f}":>18}')
        print(f'  合计负值天数 {sum(b - a + 1 for a, b in segs)}')

    print('\n【E】Gamma Flip 序列')
    print('-' * 96)
    fv = df.flip_1.dropna()
    noz = int((df.flip_count == 0).sum())
    print(f'  有零点 {len(fv)} 天 / 无零点 {noz} 天 '
          f'（区间全正 {int((df.regime == "ALL_POS").sum())} 天，'
          f'区间全负 {int((df.regime == "ALL_NEG").sum())} 天）')
    if len(fv):
        print(f'  Flip min {fv.min():.4f} @ {int(df.trade_date[int(np.argmin(df.flip_1.fillna(9e9).values))])}'
              f'   max {fv.max():.4f} @ {int(df.trade_date[int(np.argmax(df.flip_1.fillna(-9e9).values))])}'
              f'   mean {fv.mean():.4f}')
        print(f'  Flip 距现价%：min {df.flip_pct.min():+.2f}%  max {df.flip_pct.max():+.2f}%  '
              f'mean {df.flip_pct.mean():+.2f}%')
        print(f'  多零点天数 {int((df.flip_count > 1).sum())} 天')

    last = df.iloc[-1]
    print(f'\n【F】最新一日 {int(last.trade_date)}')
    print('-' * 96)
    print(f'  S={last.S:.4f}  净GEX={last.net_gex:+.4f} 亿  '
          f'(Call GEX {last.gex_call:+.4f} / Put GEX（负）{last.gex_put:+.4f})  '
          f'腿数 {int(last.n_legs)}  到期月 {int(last.n_maturities)}')
    if np.isfinite(last.flip_1):
        print(f'  Flip_1={last.flip_1:.4f} ({last.flip_pct:+.2f}% 距现价)  '
              f'Flip_2={("—" if not np.isfinite(last.flip_2) else f"{last.flip_2:.4f}")}  '
              f'零点数 {int(last.flip_count)}')
        print(f'  → 现价{"高于" if last.S > last.flip_1 else "低于"} Flip，'
              f'处于{"正" if last.S > last.flip_1 else "负"} Gamma 区')
    else:
        print(f'  区间内无零点（{last.regime}）→ '
              f'{"全区间正 Gamma" if last.regime == "ALL_POS" else "全区间负 Gamma"}')
    print('=' * 96)


def plot_series(df, date_tag):
    """时间序列三图：① 净GEX ② ETF 现价 ③ 独立 Gamma Flip 线"""
    import matplotlib.dates as mdates

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(16, 11), dpi=140, sharex=True,
        gridspec_kw=dict(height_ratios=[1.15, 1, 1], hspace=0.13))
    x = pd.to_datetime(df.trade_date.astype(str), format='%Y%m%d')
    last = df.iloc[-1]

    # ---------- ① 净 GEX ----------
    colors = ['#d32f2f' if v > 0 else '#1565c0' for v in df.net_gex]
    ax1.bar(x, df.net_gex, color=colors, width=0.78, zorder=3)
    ax1.axhline(0, color='k', lw=1.6, zorder=4)
    neg_n = int((df.net_gex < 0).sum())
    ax1.set_ylabel('净 GEX（亿元 / 每 1% 标的变动）', fontsize=11)
    ax1.set_title(f'① 净 GEX 时间序列　最新 {int(last.trade_date)} = {last.net_gex:+.2f} 亿　'
                  f'负值 {neg_n}/{len(df)} 天（蓝柱）', fontsize=13, loc='left')
    ax1.grid(alpha=0.28, ls=':', zorder=0)
    # 负值日标注
    negd = df[df.net_gex < 0]
    if len(negd):
        xn = pd.to_datetime(negd.trade_date.astype(str), format='%Y%m%d')
        ax1.plot(xn, negd.net_gex, 'v', color='#0d47a1', ms=6, zorder=6,
                 label=f'净GEX为负日（{len(negd)} 天）')
    ax1.legend(handles=[
        plt.Rectangle((0, 0), 1, 1, color='#d32f2f', alpha=0.85),
        plt.Rectangle((0, 0), 1, 1, color='#1565c0', alpha=0.85),
        plt.Line2D([], [], marker='v', ls='', color='#0d47a1', ms=6)],
        labels=['净 GEX > 0（Call+ / Put− 合计为正）', '净 GEX < 0',
                f'净GEX为负日（{len(negd)} 天）'],
        fontsize=9, loc='upper right', ncol=3, framealpha=0.9)

    # ---------- ② ETF 收盘价 ----------
    ax2.plot(x, df.S, '-', color='k', lw=1.9, label='ETF 收盘价 S', zorder=5)
    ax2.set_ylabel('价格（元）', fontsize=11)
    ax2.set_title(f'② 科创50ETF(588000) 收盘价　最新 {last.S:.3f} 元　'
                  f'区间 {df.S.min():.3f}~{df.S.max():.3f}', fontsize=12.5, loc='left')
    ax2.grid(alpha=0.28, ls=':', zorder=0)
    ax2.legend(fontsize=9.5, loc='lower left', framealpha=0.92)
    negmask = (df.net_gex < 0).values
    if negmask.any():
        ax2.fill_between(x, df.S.min() * 0.98, df.S.max() * 1.02, where=negmask,
                         color='#ef9a9a', alpha=0.22, interpolate=True, zorder=1,
                         label='净GEX为负区间')

    # ---------- ③ 独立 Gamma Flip ----------
    has_flip = df.flip_1.notna().values
    ax3.plot(x, df.S, '-', color='#9e9e9e', lw=1.1, alpha=0.85, zorder=2,
             label='ETF 收盘价（参照）')
    ax3.plot(x, df.flip_1, '-', color='#7b1fa2', lw=1.9, marker='o', ms=3,
             label='Gamma Flip（主，最近零点）', zorder=5)
    if df.flip_2.notna().any():
        ax3.plot(x, df.flip_2, '--', color='#b39ddb', lw=1.4,
                 label='Gamma Flip（次）', zorder=4)

    # 无零点日：在 ETF 价格位置画灰色 x
    nod = df[df.flip_1.isna()]
    if len(nod):
        ax3.plot(pd.to_datetime(nod.trade_date.astype(str), format='%Y%m%d'), nod.S,
                 'x', color='#757575', ms=7, mew=1.6, zorder=6,
                 label=f'当日区间内无零点（{len(nod)} 天）')

    # 负 Gamma 区（现价 < Flip）用浅红 axvspan
    below = (df.flip_1.notna() & (df.S < df.flip_1)).values
    seg, cur = [], None
    for i in range(len(below)):
        if below[i]:
            cur = [i, i] if cur is None else [cur[0], i]
            cur[1] = i
        elif cur is not None:
            seg.append(cur); cur = None
    if cur is not None:
        seg.append(cur)
    pad = pd.Timedelta(days=0.9)
    for k, (a, b) in enumerate(seg):
        ax3.axvspan(x.iloc[a] - pad, x.iloc[b] + pad, color='#ef9a9a', alpha=0.30, zorder=1,
                    label=('负 Gamma 区（S < Flip）' if k == 0 else None))

    vals = pd.concat([df.S, df.flip_1.dropna()])
    if len(vals):
        ylo, yhi = float(vals.min()), float(vals.max())
        rng = max(yhi - ylo, 1e-3)
        ax3.set_ylim(ylo - 0.10 * rng, yhi + 0.10 * rng)
    ax3.set_ylabel('价格（元）', fontsize=11)
    if np.isfinite(last.flip_1):
        ttl = (f'③ Gamma Flip 线　最新 {last.flip_1:.4f}（距现价 {last.flip_pct:+.2f}%）　'
               f'Flip 上方=正Gamma / 下方=负Gamma')
    else:
        ttl = (f'③ Gamma Flip 线　最新一日区间内无零点（{last.regime}）　'
               f'Flip 上方=正Gamma / 下方=负Gamma')
    ax3.set_title(ttl, fontsize=12.5, loc='left')
    ax3.grid(alpha=0.28, ls=':', zorder=0)
    ax3.legend(fontsize=9, loc='lower left', framealpha=0.92, ncol=2)

    for a in (ax1, ax2, ax3):
        a.xaxis.set_major_locator(mdates.MonthLocator())
        a.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        a.margins(x=0.01)
    for lb in ax3.get_xticklabels():
        lb.set_rotation(0)
        lb.set_ha('center')

    fig.suptitle(f'科创50ETF(588000) 期权 净GEX 与 Gamma Flip 时间序列   '
                 f'{int(df.trade_date.iloc[0])} ~ {int(df.trade_date.iloc[-1])}\n'
                 f'口径：DTE≥7、Call正 / Put负（sign统一）GEX=Γ·OI·10000·S²·0.01、'
                 f'S*扫描 0.90S~1.10S 步长0.5%、OI=T日、r=0.02、q=0、乘数10000',
                 fontsize=12.5, y=0.985)
    fig.subplots_adjust(left=0.055, right=0.985, top=0.905, bottom=0.06, hspace=0.13)
    png = os.path.join(OUT, f'kc50_gex_series_{date_tag}.png')
    fig.savefig(png)
    plt.close(fig)
    print(f'PNG → {png}')
    return png


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='交易日 YYYYMMDD，默认最新')
    ap.add_argument('--r', type=float, default=0.02, help='无风险利率，默认 0.02')
    ap.add_argument('--q', type=float, default=0.0)
    ap.add_argument('--min-dte', type=int, default=MIN_DTE)
    ap.add_argument('--plot', action='store_true')
    ap.add_argument('--series', action='store_true', help='时间序列模式：逐日净GEX + Gamma Flip')
    ap.add_argument('--start', type=int, default=20260601)
    ap.add_argument('--end', type=int, default=20991231)
    args = ap.parse_args()

    if args.series:
        df = run_series(args)
        report_series(df)
        png = plot_series(df, int(df.trade_date.iloc[-1]))
        import shutil
        dst_dir = '/root/.openclaw/media/qqbot'
        if os.path.isdir(dst_dir):
            dst = os.path.join(dst_dir, 'gex_series_flip.png')
            shutil.copy(png, dst)
            print(f'PNG 副本 → {dst}')
        return

    d = pd.read_csv(HIST, usecols=['trade_date'], dtype={'trade_date': int})
    date = args.date or int(d.trade_date.max())
    date = int(date)
    r, q = args.r, args.q

    S, s_date = load_etf_close(date)
    if s_date != date:
        print(f'⚠️ ETF 收盘最新为 {s_date}，与快照 {date} 不一致，按 {s_date} 收盘 {S}')
    legs, meta = build_legs(date, S, r, q, args.min_dte)

    print('=' * 78)
    print(f'科创50ETF 期权 净GEX / Gamma Flip    基准日 {date}  S={S:.4f}  r={r:.3f}  q={q:.2f}')
    print(f'公式 GEX_i = Γ_i·OI_i·10000·S²·0.01  (元/1%)，Γ=φ(d1)/(Sσ√T)，单位亿元')
    print(f'筛选：原始 {meta["n_raw"]} 腿 → DTE>={args.min_dte} {meta["n_dte"]} 腿 '
          f'→ 中价/OI>0 {meta["n_mid"]} 腿 → IV可解 {meta["n_iv"]} 腿')
    print('=' * 78)

    # ---- 1. 分月明细 ----
    rows = []
    for m, sub in legs.groupby('maturity'):
        c = sub[sub.call]
        p = sub[~sub.call]
        rows.append(dict(maturity=int(m), dte=int(sub.dte.iloc[0]), n=len(sub),
                         n_c=len(c), n_p=len(p),
                         call_gex=float(c.gex.sum()), put_gex=float(p.gex.sum()),
                         net=float(sub.gex.sum())))
    tab = pd.DataFrame(rows).sort_values('maturity')
    net_total = float(tab.net.sum())

    print('\n【1】基准净 GEX（S=%.4f，单位：亿元 / 每 1%% 标的变动）' % S)
    print('-' * 78)
    print(f'{"到期月":<10}{"DTE":>5}{"腿数":>6}{"Call GEX(+)":>12}{"Put GEX(−)":>12}{"Net":>12}')
    for t in tab.itertuples():
        print(f'{t.maturity:<10}{t.dte:>5}{t.n:>6}{t.call_gex:>12.4f}{t.put_gex:>12.4f}{t.net:>12.4f}')
    print('-' * 78)
    print(f'{"合计":<10}{"":>5}{len(legs):>6}{tab.call_gex.sum():>12.4f}'
          f'{tab.put_gex.sum():>12.4f}{net_total:>12.4f}')
    print(f'\n>>> 基准净 GEX = {net_total:+.4f} 亿元 / 1%   '
          f'({"正 GEX：做市商压波动、钉价" if net_total > 0 else "负 GEX：做市商追涨杀跌、放大波动"})')
    print(f'    其中 Call GEX 总 +{tab.call_gex.sum():.4f} 亿，'
          f'Put GEX（负）总 {tab.put_gex.sum():.4f} 亿 → Net = Call + Put')
    print(f'    口径：Put GEX 取负值（gex_put 列本身就是负号数），Net = gex_call + gex_put')

    # ---- 2. Gamma Flip ----
    s1, y1, per1 = curve(legs, S, r, q, 0.90, 1.10, 0.005)
    s2, y2, per2 = curve(legs, S, r, q, 0.85, 1.15, 0.0025)
    z1 = find_zeros(s1, y1)
    z2 = find_zeros(s2, y2)

    def fmt_zeros(zs, sarr, y):
        if not zs:
            return f'无零点（区间 {sarr[0]:.4f}~{sarr[-1]:.4f}，两端 {y[0]:+.4f} / {y[-1]:+.4f} 亿）'
        parts = []
        for z, sl in zs:
            kind = '上穿(+斜率)' if sl > 0 else ('下穿(-斜率)' if sl < 0 else '端点')
            parts.append(f'{z:.4f}（{(z / S - 1) * 100:+.2f}% 距现价，{kind}）')
        return '  |  '.join(parts)

    print('\n【2】Gamma Flip（净 GEX(S*)=0 的零点，σ/K/T/OI 固定，只改 S）')
    print('-' * 78)
    print(f'0.5% 步长 [0.90S,1.10S]  ({s1[0]:.4f}~{s1[-1]:.4f}, {len(s1)} 点)：')
    print('   ' + fmt_zeros(z1, s1, y1))
    print(f'0.25% 步长 [0.85S,1.15S] ({s2[0]:.4f}~{s2[-1]:.4f}, {len(s2)} 点)：')
    print('   ' + fmt_zeros(z2, s2, y2))

    # ---- 3. 关键点数值 ----
    print('\n【3】曲线关键点 net GEX（亿元 / 1%）')
    print('-' * 78)
    print(f'{"S*":>8}{"占现价":>10}{"net GEX":>12}{"0.25%版核对":>14}')
    key = [0.90, 0.95, 1.00, 1.05, 1.10]
    for k in key:
        s0 = S * k
        print(f'{s0:>8.4f}{k * 100:>9.1f}%{value_at(s1, y1, s0):>12.4f}'
              f'{value_at(s2, y2, s0):>14.4f}')

    # ---- 4. 各到期月自己的 flip ----
    print('\n【4】各到期月单独的 Gamma Flip（0.5% 步长）')
    print('-' * 78)
    month_flip = {}
    for m in sorted(per1.keys()):
        ym = per1[m]
        zm = find_zeros(s1, ym)
        month_flip[int(m)] = [z for z, _ in zm]
        dte = int(legs[legs.maturity == m].dte.iloc[0])
        print(f'{m} (DTE {dte:>3})：net@S={ym[np.argmin(abs(s1 - S))]:+.4f} 亿   flip → '
              + (', '.join(f'{z:.4f}' for z, _ in zm) if zm
                 else f'区间内无零点（{ym[0]:+.4f} ~ {ym[-1]:+.4f}）'))

    # ---- 5. OI 口径 ----
    print('\n【5】OI 日期口径')
    print('-' * 78)
    print('  快照 trade_date=%d 的 oi 判定为【T 日收盘持仓量】(T 日盘后交易所公布口径)，非 T-1。' % date)
    print('  证据：新挂月份在首个出现日 oi 即非零（如 20260722 于 20260528 首见，oi=27758 张）；')
    print('        若为 T-1 口径，新合约首日 oi 必为 0。另 vol 与 ΔOI(T) 同向正相关(≈0.31)，')
    print('        而与 ΔOI(T+1) 相关更低(≈0.27)，符合同日口径。')
    print('  含义：GEX 与当日收盘 S 同一时点，无需再滞后一期；但收盘后无盘中持仓变动信息。')

    # ---- 6. 一致性自检 ----
    print('\n【6】一致性自检')
    print('-' * 78)
    iS = int(np.argmin(abs(s1 - S)))
    print(f'  网格上 S*={s1[iS]:.4f}  net={y1[iS]:+.6f} 亿 ;  ΣGEX_i={net_total:+.6f} 亿 ; '
          f'差={y1[iS] - net_total:+.2e}  {"(网格点即 1.00S) ✓" if abs(s1[iS] - S) < 1e-9 else ""}')
    s_exact = S
    tot_exact = 0.0
    for row in legs.itertuples():
        tot_exact += row.sign * gamma(s_exact, row.K, row.T, row.iv, r, q) * row.oi * MULT * s_exact ** 2 * 0.01 / 1e8
    print(f'  在 S*={S} 精确重算单点：{tot_exact:+.6f} 亿 ; ΣGEX_i={net_total:+.6f} 亿 ; '
          f'差={tot_exact - net_total:+.2e}  → {"一致 ✓" if abs(tot_exact - net_total) < 1e-9 else "不一致 ✗"}')
    print(f'  Call 腿符号：全为正？{bool((legs[legs.call].gex > 0).all())} '
          f'（{len(legs[legs.call])} 腿）；Put 腿符号：全为负？{bool((legs[~legs.call].gex < 0).all())} '
          f'（{len(legs[~legs.call])} 腿）')
    print('  样本（K, cp, IV, OI, Γ, GEX亿）：')
    for tag, sub in [('CALL', legs[legs.call].head(3)), ('PUT', legs[~legs.call].head(3))]:
        for row in sub.itertuples():
            print(f'    {tag} K={row.K:.3f} {row.cp} IV={row.iv * 100:5.2f}% OI={int(row.oi):>7} '
                  f'Γ={row.gamma0:.5f} GEX={row.gex:+.5f}亿')

    # ---- 7. 输出 CSV ----
    csv_path = os.path.join(OUT, f'kc50_gex_flip_{date}.csv')
    out = pd.DataFrame({'S_star': s1, 'net_gex_total': y1})
    for m in sorted(per1.keys()):
        out[f'gex_{m}'] = per1[m]
    out['pct_of_spot'] = (out.S_star / S - 1) * 100
    out.round(8).to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\nCSV → {csv_path}')

    # ---- 8. 画图 ----
    if args.plot:
        png = os.path.join(OUT, f'kc50_gex_flip_{date}.png')
        fig, ax = plt.subplots(figsize=(11, 6.5), dpi=150)
        ax.axhspan(0, max(y1.max(), 0) * 1.35 + 0.5, color='#2e7d32', alpha=0.07, zorder=0)
        ax.axhspan(min(y1.min(), 0) * 1.35 - 0.5, 0, color='#c62828', alpha=0.07, zorder=0)
        ax.axhline(0, color='k', lw=1.0, ls='-', alpha=0.75, zorder=2, label='零线 net GEX=0')
        ax.plot(s1, y1, 'o-', color='#1565c0', lw=2.0, ms=3, zorder=3,
                label='净 GEX(S*)  0.5% 步长')
        ax.plot(s2, y2, '--', color='#6a1b9a', lw=1.2, alpha=0.75, zorder=2,
                label='净 GEX(S*)  0.25% 步长（交叉验证）')
        ax.axvline(S, color='#ef6c00', ls='--', lw=1.8, zorder=4,
                   label=f'当前 S = {S:.3f}（净 GEX {net_total:+.2f} 亿）')
        for i, (z, sl) in enumerate(z1):
            ax.axvline(z, color='#00838f', ls=':', lw=1.8, zorder=4,
                       label=('Gamma Flip' if i == 0 else None) +
                             f' = {z:.4f} ({(z / S - 1) * 100:+.2f}%)')
            ax.plot([z], [0], 'o', mfc='#00838f', mec='w', ms=9, zorder=6)
            ax.annotate(f'flip {z:.4f}\n({(z / S - 1) * 100:+.2f}%)',
                        xy=(z, 0), xytext=(0, 28 if y1[len(y1) // 2] > 0 else -42),
                        textcoords='offset points', ha='center', fontsize=9,
                        color='#006064',
                        bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#00838f', alpha=0.9))
        ax.set_xlabel('标的价 S*（元）', fontsize=11)
        ax.set_ylabel('净 GEX（亿元 / 每 1% 标的变动）', fontsize=11)
        ax.set_title(f'科创50ETF 期权 净 GEX 曲线 与 Gamma Flip    基准日 {date}  S={S:.3f}  '
                     f'净GEX={net_total:+.2f}亿', fontsize=12.5)
        ymax = max(y1.max(), y2.max(), 0.0)
        ymin = min(y1.min(), y2.min(), 0.0)
        pad = (ymax - ymin) * 0.14 + 1e-6
        ax.set_ylim(ymin - pad, ymax + pad)
        ax.grid(alpha=0.28, ls=':')
        ax.legend(fontsize=8.5, loc='best', framealpha=0.92)
        fig.tight_layout()
        fig.savefig(png)
        plt.close(fig)
        print(f'PNG → {png}')
        import shutil
        dst_dir = '/root/.openclaw/media/qqbot'
        if os.path.isdir(dst_dir):
            dst = os.path.join(dst_dir, 'gex_flip_1013.png')
            shutil.copy(png, dst)
            print(f'PNG 副本 → {dst}')


if __name__ == '__main__':
    main()
