#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科创50ETF (588000) 期权 Gamma Exposure (GEX) 日频序列

口径（dealer 视角，标注清楚）：
    单腿 Gamma(per share) = e^{-qT} φ(d1) / (S·σ·√T)
    GEX = Σ_call Γ·OI·10000·S²·0.01  −  Σ_put Γ·OI·10000·S²·0.01
    单位：元 / 每 1% 标的变动；输出换算为【亿元】(÷1e8)

含义：假设做市商/交易商持有全部 Call 多头、Put 空头（客户买 Put 卖出 Call 的
常见持仓结构），则正 GEX = 做市商在上涨时卖、下跌时买（压波动、钉住价格）；
负 GEX = 做市商追涨杀跌（放大波动）。符号约定为「dealer 视角」：
看涨期权 OI 贡献为正、看跌期权 OI 贡献为负。

数据源（全离线，读现成缓存）：
    output/opt588000_hist_cache.csv   期权链日频快照
    output/kc50_etf_close.csv         ETF 收盘价

约定：
    - 中价 = close (close>0) 否则 settle
    - DTE = maturity - trade_date（自然日），仅取 DTE >= --min-dte（默认 3，
      避免临近到期 gamma 爆炸导致的数值失真；与 skew 脚本口径一致）
    - IV 反解失败 或 IV ∉ (0.02, 4.5) 的腿丢弃
    - oi 为 NaN 或 <=0 的腿丢弃
    - 合约乘数 10000 股/张
    - T = DTE / 365

用法：
    python3 strategies/opt_gex.py                # 有缓存则复用
    python3 strategies/opt_gex.py --force        # 强制重算
    python3 strategies/opt_gex.py --start 20260601 --min-dte 3

输出：output/kc50_gex.csv
    trade_date, S, gex_call, gex_put, gex_put_signed, gex_net, oi_call, oi_put, n_legs
    （gex_* 单位：亿元；gex_put 为原始正数幅值，gex_put_signed = −gex_put；
      gex_net = gex_call + gex_put_signed（与原 gex_call − gex_put 完全等价））
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import math
import argparse
import datetime

import numpy as np
import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output')
HIST = os.path.join(OUT, 'opt588000_hist_cache.csv')
ETF_CSV = os.path.join(OUT, 'kc50_etf_close.csv')
GEX_CSV = os.path.join(OUT, 'kc50_gex.csv')
GEX_W_CSV = os.path.join(OUT, 'kc50_gex_w.csv')

# 新口径（30/90 双期限加权）参数
W_DTE_LO = 30           # 剔除近月：DTE < 30 全部不要
W_DTE_HI = 180          # 剔除超远月
W_TARGETS = (30, 90)    # 恒定期限目标点（VIX 插值法）
W_WEIGHTS = (0.5, 0.5)  # 等权加权

R = 0.015
Q = 0.0
MULT = 10000.0          # 科创50ETF 期权合约乘数：10000 股/张
IV_LO, IV_HI = 0.02, 4.5


def _nd(x):
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S, K, T, vol, call=True):
    if T <= 0 or vol <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + (R - Q + 0.5 * vol * vol) * T) / (vol * math.sqrt(T))
    d2 = d1 - vol * math.sqrt(T)
    if call:
        return S * math.exp(-Q * T) * _nd(d1) - K * math.exp(-R * T) * _nd(d2)
    return K * math.exp(-R * T) * _nd(-d2) - S * math.exp(-Q * T) * _nd(-d1)


def implied_vol(price, S, K, T, call=True):
    """二分法反解 IV；失败返回 nan"""
    if not np.isfinite(price) or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return np.nan
    intr = max(0.0, (S - K) if call else (K - S))
    if price < intr * 0.98:
        return np.nan
    lo, hi = 1e-4, IV_HI
    if bs_price(S, K, T, hi, call) < price:
        return np.nan
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, call) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def bs_gamma(S, K, T, vol):
    """per-share gamma，call/put 同式"""
    if T <= 0 or vol <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (R - Q + 0.5 * vol * vol) * T) / (vol * math.sqrt(T))
    return math.exp(-Q * T) * _npdf(d1) / (S * vol * math.sqrt(T))


def build(start='20260601', min_dte=3, force=False):
    """计算 GEX 日序列，返回 DataFrame"""
    if os.path.exists(GEX_CSV) and not force:
        df = pd.read_csv(GEX_CSV)
        df['trade_date'] = df['trade_date'].astype(str)
        return df

    if not os.path.exists(HIST):
        raise SystemExit(f'缺期权链缓存: {HIST}')
    if not os.path.exists(ETF_CSV):
        raise SystemExit(f'缺 ETF 收盘: {ETF_CSV}')

    etf = pd.read_csv(ETF_CSV)
    closes = {str(k): float(v) for k, v in zip(etf['trade_date'].astype(str), etf['close'])}

    d = pd.read_csv(HIST)
    d['trade_date'] = d['trade_date'].astype(str)
    d['maturity'] = pd.to_datetime(d['maturity'].astype(str).str[:8], format='%Y%m%d')
    d = d[d['trade_date'] >= str(start)].copy()
    for c in ('close', 'settle', 'vol', 'oi', 'K'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d['mid'] = d['close'].where(d['close'] > 0, d['settle'])
    d = d[(d['mid'] > 0) & (d['K'] > 0) & (d['oi'] > 0) & d['oi'].notna()]

    recs = []
    for dt, grp in d.groupby('trade_date'):
        S = closes.get(str(dt))
        if not S or S <= 0:
            continue
        today = datetime.datetime.strptime(str(dt), '%Y%m%d')
        grp = grp.copy()
        grp['dte'] = (grp['maturity'] - today).dt.days
        grp = grp[grp['dte'] >= min_dte]
        if grp.empty:
            continue

        gex_c = gex_p = oi_c = oi_p = 0.0
        n = 0
        for _, r in grp.iterrows():
            is_call = (r['cp'] == 'C')
            T = float(r['dte']) / 365.0
            iv = implied_vol(float(r['mid']), S, float(r['K']), T, is_call)
            if not np.isfinite(iv) or not (IV_LO < iv < IV_HI):
                continue
            gam = bs_gamma(S, float(r['K']), T, iv)
            if not np.isfinite(gam) or gam <= 0:
                continue
            # 元 / 每 1% 变动
            contrib = gam * float(r['oi']) * MULT * S * S * 0.01
            if is_call:
                gex_c += contrib
                oi_c += float(r['oi'])
            else:
                gex_p += contrib
                oi_p += float(r['oi'])
            n += 1

        if n == 0:
            continue
        recs.append(dict(trade_date=str(dt), S=S,
                         gex_call=gex_c / 1e8, gex_put=gex_p / 1e8,
                         gex_put_signed=-gex_p / 1e8,
                         gex_net=(gex_c - gex_p) / 1e8,
                         oi_call=oi_c, oi_put=oi_p, n_legs=n))

    df = pd.DataFrame(recs).sort_values('trade_date').reset_index(drop=True)
    df.to_csv(GEX_CSV, index=False)
    return df


def _leg_gex(grp, S, today, dte_lo, dte_hi):
    """单日：按到期月聚合净 GEX（Call 正 / Put 负），返回 [(dte, net, n_legs, oi_c, oi_p), ...]"""
    g = grp.copy()
    g['dte'] = (g['maturity'] - today).dt.days
    g = g[(g['dte'] >= dte_lo) & (g['dte'] <= dte_hi)]
    if g.empty:
        return []
    out = []
    for mat, sub in g.groupby('maturity'):
        gex_c = gex_p = 0.0
        n = 0
        for _, r in sub.iterrows():
            is_call = (r['cp'] == 'C')
            T = float(r['dte']) / 365.0
            iv = implied_vol(float(r['mid']), S, float(r['K']), T, is_call)
            if not np.isfinite(iv) or not (IV_LO < iv < IV_HI):
                continue
            gam = bs_gamma(S, float(r['K']), T, iv)
            if not np.isfinite(gam) or gam <= 0:
                continue
            contrib = gam * float(r['oi']) * MULT * S * S * 0.01
            if is_call:
                gex_c += contrib
            else:
                gex_p += contrib
            n += 1
        if n == 0:
            continue
        out.append((int(sub['dte'].iloc[0]), (gex_c - gex_p) / 1e8, n))
    return sorted(out, key=lambda z: z[0])


def _interp_at(pts, target):
    """在 (dte, net) 平面上线性插值取 target 处的 net。

    pts: [(dte, net), ...] 已按 dte 升序，长度 >= 1
    返回 (value, mode)，mode ∈ {'exact', 'interp', 'extrap_lo', 'extrap_hi', 'single'}
    """
    if not pts:
        return np.nan, 'none'
    ds = [p[0] for p in pts]
    if target in ds:
        return pts[ds.index(target)][1], 'exact'
    if len(pts) == 1:
        return pts[0][1], 'single'
    if target < ds[0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
        mode = 'extrap_lo'
    elif target > ds[-1]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        mode = 'extrap_hi'
    else:
        mode = 'interp'
        for i in range(len(pts) - 1):
            if ds[i] < target < ds[i + 1]:
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                break
    if x1 == x0:
        return y0, mode
    return y0 + (y1 - y0) * (target - x0) / (x1 - x0), mode


def build_weighted(force=False):
    """新口径：剔除近月(DTE<30)，只保留 30≤DTE≤180，做 30/90 双期限加权（VIX 恒定期限插值法）

    gex30 / gex90 : 在「DTE ↔ 净GEX」平面上线性插值到目标期限的净 GEX（亿元）
    gex_weighted  : 0.5·gex30 + 0.5·gex90
    gex_mix_simple: 30≤DTE≤180 所有到期月净 GEX 直接等权加总（对照口径）
    interp_mode   : gex30 与 gex90 的取得方式，如 "interp/extrap_hi"
    dte_list      : 当日纳入的到期月 DTE，逗号分隔
    """
    if os.path.exists(GEX_W_CSV) and not force:
        df = pd.read_csv(GEX_W_CSV)
        df['trade_date'] = df['trade_date'].astype(str)
        return df

    if not os.path.exists(HIST):
        raise SystemExit(f'缺期权链缓存: {HIST}')
    if not os.path.exists(ETF_CSV):
        raise SystemExit(f'缺 ETF 收盘: {ETF_CSV}')

    etf = pd.read_csv(ETF_CSV)
    closes = {str(k): float(v) for k, v in zip(etf['trade_date'].astype(str), etf['close'])}

    d = pd.read_csv(HIST)
    d['trade_date'] = d['trade_date'].astype(str)
    d['maturity'] = pd.to_datetime(d['maturity'].astype(str).str[:8], format='%Y%m%d')
    for c in ('close', 'settle', 'vol', 'oi', 'K'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d['mid'] = d['close'].where(d['close'] > 0, d['settle'])
    d = d[(d['mid'] > 0) & (d['K'] > 0) & (d['oi'] > 0) & d['oi'].notna()]

    recs = []
    for dt in sorted(d['trade_date'].unique()):
        S = closes.get(str(dt))
        if not S or S <= 0:
            continue
        today = datetime.datetime.strptime(str(dt), '%Y%m%d')
        legs = _leg_gex(d[d['trade_date'] == dt], S, today, W_DTE_LO, W_DTE_HI)
        if not legs:
            continue
        pts = [(x[0], x[1]) for x in legs]
        g30, m30 = _interp_at(pts, W_TARGETS[0])
        g90, m90 = _interp_at(pts, W_TARGETS[1])
        gw = W_WEIGHTS[0] * g30 + W_WEIGHTS[1] * g90
        simple = float(sum(p[1] for p in pts))
        recs.append(dict(trade_date=str(dt), S=S,
                         gex30=g30, gex90=g90, gex_weighted=gw,
                         gex_mix_simple=simple,
                         n_legs=int(sum(x[2] for x in legs)),
                         n_maturities=len(legs),
                         dte_list=','.join(str(p[0]) for p in pts),
                         interp_mode=f'{m30}/{m90}'))

    df = pd.DataFrame(recs).sort_values('trade_date').reset_index(drop=True)
    df.to_csv(GEX_W_CSV, index=False)
    return df


def report(df):
    if df.empty:
        print('无数据')
        return
    last = df.iloc[-1]
    print(f'最新 {last["trade_date"]}  S={last["S"]:.3f}  '
          f'GEX_net={last["gex_net"]:+.2f} 亿元/1%  '
          f'(Call {last["gex_call"]:+.2f} / Put {last["gex_put_signed"]:+.2f} signed)  '
          f'legs={int(last["n_legs"])}')
    print('\n近 5 日：')
    for _, r in df.tail(5).iterrows():
        print(f'  {r["trade_date"]}  GEX_net {r["gex_net"]:+8.2f} 亿元  '
              f'(C {r["gex_call"]:+7.2f} / P {r["gex_put_signed"]:+7.2f} signed)  S={r["S"]:.3f}')

    pos = int((df['gex_net'] > 0).sum())
    neg = int((df['gex_net'] < 0).sum())
    print(f'\n{df["trade_date"].iloc[0]} 起：正 {pos} 天 / 负 {neg} 天')

    sign = np.sign(df['gex_net'].fillna(0))
    prev = sign.shift(1)
    flips = df[(sign != prev) & prev.notna()]
    print('符号切换日期：')
    if flips.empty:
        print('  （无）')
    for _, r in flips.iterrows():
        print(f'  {r["trade_date"]}  转{"正" if r["gex_net"] > 0 else "负"}  {r["gex_net"]:+.2f} 亿元')
    first_neg = df[df['gex_net'] < 0]
    if not first_neg.empty:
        print(f'首次转负：{first_neg["trade_date"].iloc[0]}  ({first_neg["gex_net"].iloc[0]:+.2f} 亿元)')


def build_old_full():
    """对照用：旧口径（DTE≥3，所有到期月净 GEX 直接加总）覆盖全历史，仅内存返回不落盘"""
    etf = pd.read_csv(ETF_CSV)
    closes = {str(k): float(v) for k, v in zip(etf['trade_date'].astype(str), etf['close'])}
    d = pd.read_csv(HIST)
    d['trade_date'] = d['trade_date'].astype(str)
    d['maturity'] = pd.to_datetime(d['maturity'].astype(str).str[:8], format='%Y%m%d')
    for c in ('close', 'settle', 'vol', 'oi', 'K'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d['mid'] = d['close'].where(d['close'] > 0, d['settle'])
    d = d[(d['mid'] > 0) & (d['K'] > 0) & (d['oi'] > 0) & d['oi'].notna()]
    recs = []
    for dt in sorted(d['trade_date'].unique()):
        S = closes.get(str(dt))
        if not S or S <= 0:
            continue
        today = datetime.datetime.strptime(str(dt), '%Y%m%d')
        legs = _leg_gex(d[d['trade_date'] == dt], S, today, 3, 10 ** 6)
        if not legs:
            continue
        recs.append(dict(trade_date=str(dt), gex_net=float(sum(x[1] for x in legs))))
    return pd.DataFrame(recs).sort_values('trade_date').reset_index(drop=True)


def _stats(s):
    s = pd.Series(s).astype(float).dropna()
    if s.empty:
        return dict(n=0, std=0.0, rng=0.0, mad=0.0, mean=0.0)
    return dict(n=len(s), std=float(s.std(ddof=1)) if len(s) > 1 else 0.0,
                rng=float(s.max() - s.min()), mad=float(s.diff().abs().mean()),
                mean=float(s.mean()))


def report_weighted(w):
    """新口径报告"""
    if w.empty:
        print('无数据（没有任何日期存在 30≤DTE≤180 的到期月）')
        return
    last = w.iloc[-1]
    print(f'最新 {last["trade_date"]}  S={last["S"]:.3f}  '
          f'gex30={last["gex30"]:+.4f}  gex90={last["gex90"]:+.4f}  '
          f'加权={last["gex_weighted"]:+.4f} 亿  (简单加总 {last["gex_mix_simple"]:+.4f})  '
          f'到期月 {last["n_maturities"]} 个 DTE=[{last["dte_list"]}]  '
          f'interp={last["interp_mode"]}')
    print('\n近 10 日：')
    for _, r in w.tail(10).iterrows():
        print(f'  {r["trade_date"]}  gex30 {r["gex30"]:+7.3f}  gex90 {r["gex90"]:+7.3f}  '
              f'加权 {r["gex_weighted"]:+7.3f}  简单 {r["gex_mix_simple"]:+7.3f}  '
              f'DTE[{r["dte_list"]}]  {r["interp_mode"]}')

    g = w['gex_weighted'].dropna()
    pos = int((g > 0).sum())
    neg = int((g < 0).sum())
    print(f'\n{w["trade_date"].iloc[0]}~{w["trade_date"].iloc[-1]}  共 {len(w)} 天  '
          f'正 {pos} 天 / 负 {neg} 天 / 零 {len(w) - pos - neg} 天')
    if not g.empty:
        imax, imin = g.idxmax(), g.idxmin()
        print(f'最大值 {g.max():+.4f} 亿 ({w.loc[imax, "trade_date"]})　'
              f'最小值 {g.min():+.4f} 亿 ({w.loc[imin, "trade_date"]})')
        print(f'均值 {g.mean():+.4f} 亿　中位数 {g.median():+.4f} 亿')
    early = w[w['n_maturities'] < 2]
    if not early.empty:
        print(f'⚠ 腿数不足（<2 个到期月，插值退化为单点）的日期 {len(early)} 天：'
              f'{early["trade_date"].iloc[0]} ~ {early["trade_date"].iloc[-1]}')
    ext = w[w['interp_mode'].str.contains('extrap')]
    if not ext.empty:
        print(f'⚠ 含外推的日期 {len(ext)} 天：{ext["trade_date"].iloc[0]} ~ {ext["trade_date"].iloc[-1]}')


def compare(old, w):
    """新老口径波动对比"""
    print('\n' + '=' * 78)
    print('波动对比（单位：亿元 / 每 1% 标的变动）')
    print('=' * 78)
    # 对齐到新口径覆盖的日期区间，保证可比
    lo, hi = w['trade_date'].iloc[0], w['trade_date'].iloc[-1]
    o = old[(old['trade_date'] >= lo) & (old['trade_date'] <= hi)]['gex_net']
    rows = [('旧口径 DTE≥3（kc50_gex.csv）', _stats(o)),
            ('新口径 30/90 加权（gex_weighted）', _stats(w['gex_weighted'])),
            ('新 口径 简单加总（gex_mix_simple）', _stats(w['gex_mix_simple'])),
            ('新口径 gex30（单点）', _stats(w['gex30'])),
            ('新口径 gex90（单点）', _stats(w['gex90']))]
    base = rows[0][1]
    print(f'{"口径":<34}{"天数":>5}{"std":>10}{"极差":>10}{"日均|Δ|":>10}{"均值":>10}')
    print('-' * 78)
    for name, s in rows:
        print(f'{name:<34}{s["n"]:>5}{s["std"]:>10.4f}{s["rng"]:>10.4f}'
              f'{s["mad"]:>10.4f}{s["mean"]:>+10.4f}')
    print('-' * 78)
    for name, s in rows[1:]:
        if base['std'] > 0:
            print(f'  {name} std 相对旧口径降幅 {100 * (1 - s["std"] / base["std"]):.1f}%')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20260601')
    ap.add_argument('--min-dte', type=int, default=3)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--weighted', action='store_true',
                    help='额外计算新口径（剔除近月 + 30/90 期限加权）→ kc50_gex_w.csv')
    args = ap.parse_args()

    df = build(args.start, args.min_dte, force=args.force)
    print(f'GEX {args.start}~{df["trade_date"].iloc[-1] if not df.empty else "-"}  '
          f'{len(df)} 个交易日  → {GEX_CSV}')
    report(df)

    if args.weighted:
        print('\n' + '=' * 78)
        print('新口径：剔除近月 DTE<30，仅 30≤DTE≤180，30/90 双期限等权加权')
        print('=' * 78)
        w = build_weighted(force=True)
        report_weighted(w)
        print('（旧口径对照用全历史重算，保证与新口径同区间可比）')
        compare(build_old_full(), w)


if __name__ == '__main__':
    main()
