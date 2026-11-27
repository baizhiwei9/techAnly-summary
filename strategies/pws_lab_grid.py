#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变实验 · 指标变体库 + 7月精度网格
先把每个指标的所有可切换口径算成布尔序列（一次算完、缓存 npz），
再在组合空间里搜索「7月三指数尽量早且尽量少误报」的最优配置。

指标清单
  M1   AR(1) 自相关      ar∈{ret,resid} × 模式∈{rise,persist} × days∈{3,5,7} × thr∈{0.6,0.7}
  M1b  吸收比率 λ1/N     与本机 pool_topo 同源，win∈{20,60} × 规则∈{P80,P90,P95,斜率>0,新高}
  M2   Δσ                mult∈{1,1.5,2,2.5,3}
  M3   PSI               pct∈{80,85,90,95}
  M4   α（q累计跌幅口径）  win∈{250,500,750} × q∈{1,5,20} × thr∈{1.8,2.0,2.2,2.5}
  M5   截面λmax           wow∈{0.10,0.15,0.20,0.30}
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, time, itertools
import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pws_lab_data as L
import pws_lab_abs as A

LAB = os.path.join(L.WS, '.cache', 'pws_lab')
os.makedirs(LAB, exist_ok=True)
ALERT_FP = os.path.join(LAB, 'alerts.npz')

# 事件基准（7月/10月两波，峰=段内最高收盘，谷=峰后最低）
EVENTS = {
    '000852.SH': [('20260601', '20260731', '7月'), ('20260801', '20261031', '10月')],
    '000688.SH': [('20260601', '20260731', '7月'), ('20260801', '20261031', '10月')],
    '399006.SZ': [('20260601', '20260731', '7月'), ('20260801', '20261031', '10月')],
}


# ---------------- M1 AR(1) ----------------
def ar1_series(ret, close, mode='resid', win=20):
    n = len(ret)
    out = np.full(n, np.nan)
    if mode == 'resid':
        lp = np.log(close)
        ma = pd.Series(lp).rolling(win).mean().to_numpy()
        src = lp - ma
        lo = win + 5
    else:
        src = ret
        lo = win
    for t in range(lo, n):
        x = src[t - win:t]
        y = src[t - win + 1:t + 1]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 10:
            continue
        xx, yy = x[ok], y[ok]
        vx = xx.var()
        if vx > 0:
            out[t] = np.cov(xx, yy, bias=True)[0, 1] / vx
    return out


def ar1_alert(ar, thr=0.6, days=5, mode='persist'):
    n = len(ar)
    a = np.zeros(n, bool)
    for t in range(days - 1, n):
        if not (np.isfinite(ar[t]) and ar[t] > thr):
            continue
        seg = ar[t - days + 1:t + 1]
        if not np.all(np.isfinite(seg)):
            continue
        ok = np.all(seg > thr) if mode == 'persist' else np.all(np.diff(seg) > 0)
        if ok:
            a[t] = True
    return a


# ---------------- M1b 吸收比率 ----------------
def abs_alert(s_abs, dates, rule='P80', ref=120, slope_win=20):
    """s_abs: Series(index=date) → 对齐到 dates 的布尔"""
    v = s_abs.reindex(dates).ffill()
    x = v.to_numpy(float)
    n = len(x)
    a = np.zeros(n, bool)
    q = {'P80': 80, 'P90': 90, 'P95': 95}.get(rule)
    if q is not None:
        thr = np.full(n, np.nan)
        for t in range(ref, n):
            w = x[max(0, t - ref):t]
            w = w[np.isfinite(w)]
            if len(w) >= ref * 0.5:
                thr[t] = np.percentile(w, q)
        a = np.isfinite(x) & np.isfinite(thr) & (x > thr)
    elif rule == 'slope':
        for t in range(slope_win, n):
            w = x[t - slope_win + 1:t + 1]
            if np.all(np.isfinite(w)):
                a[t] = np.polyfit(np.arange(slope_win), w, 1)[0] > 0
    elif rule == 'newhigh':
        for t in range(ref, n):
            w = x[max(0, t - ref):t]
            w = w[np.isfinite(w)]
            if len(w) >= ref * 0.5 and np.isfinite(x[t]):
                a[t] = x[t] > np.nanmax(w)
    return a


# ---------------- M2 Δσ ----------------
def m2(ret, mult=2.0, win=20, sd_win=20):
    sig = pd.Series(ret).rolling(win).std(ddof=0).to_numpy()
    sd = pd.Series(sig).rolling(sd_win).std(ddof=0).to_numpy()
    n = len(ret)
    dsig = np.full(n, np.nan)
    for t in range(win, n):
        if np.isfinite(sig[t]) and np.isfinite(sig[t - win]):
            dsig[t] = sig[t] - sig[t - win]
    a = np.zeros(n, bool)
    for t in range(n):
        if np.isfinite(dsig[t]) and np.isfinite(sd[t]) and sd[t] > 0:
            a[t] = dsig[t] > mult * sd[t]
    return a, dsig


# ---------------- M3 PSI ----------------
def m3(ret, pct=90, win=60, kmax=5, ref=120):
    n = len(ret)
    lam = np.full(n, np.nan)
    for t in range(win, n):
        w = ret[t - win + 1:t + 1]
        w = w[np.isfinite(w)]
        if len(w) < win * 0.8:
            continue
        mu, v = w.mean(), w.var()
        if v <= 0:
            continue
        ks, logs = [], []
        for k in range(1, kmax + 1):
            aa, bb = w[:-k], w[k:]
            rho = abs(((aa - mu) * (bb - mu)).mean() / v)
            if rho > 1e-6:
                ks.append(k); logs.append(np.log(rho))
        if len(ks) < 3:
            continue
        lm = -np.polyfit(ks, logs, 1)[0]
        lam[t] = lm if lm > 1e-6 else np.nan
    sig = pd.Series(ret).rolling(20).std(ddof=0).to_numpy()
    with np.errstate(invalid='ignore', divide='ignore'):
        psi = np.where(np.isfinite(lam) & (lam > 0), sig / np.square(np.where(lam > 0, lam, np.nan)), np.nan)
    thr = np.full(n, np.nan)
    a = np.zeros(n, bool)
    for t in range(ref, n):
        w = psi[max(0, t - ref):t]
        w = w[np.isfinite(w)]
        if len(w) >= 60:
            thr[t] = np.percentile(w, pct)
            a[t] = np.isfinite(psi[t]) and psi[t] > thr[t]
    return a, psi, thr


# ---------------- M4 α ----------------
def m4(ret, win=500, q=5, pct=90, kmin=15, thr=1.8):
    n = len(ret)
    cq = pd.Series(ret).rolling(q).sum().to_numpy()
    al = np.full(n, np.nan)
    for t in range(win, n):
        s = -cq[t - win + 1:t + 1]
        s = s[np.isfinite(s) & (s > 0)]
        if len(s) < win * 0.5:
            continue
        u = np.percentile(s, pct)
        tail = s[s > u]
        if len(tail) < kmin or u <= 0:
            continue
        ml = np.mean(np.log(tail / u))
        if ml > 1e-9:
            al[t] = 1.0 / ml
    a = np.isfinite(al) & (al < thr)
    return a, al


# ---------------- M5 λmax ----------------
def m5(dates, wow_thr=0.20):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pws_xsec as X
    a = np.zeros(len(dates), bool)
    lmv = np.full(len(dates), np.nan)
    for code, tag in X.TAG.items():
        pass
    return None


def m5_one(index_code, dates, wow_thr=0.20):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pws_xsec as X
    s = X.load_lmax(index_code, '20261103')
    if s is None or len(s) == 0:
        return np.zeros(len(dates), bool), np.full(len(dates), np.nan)
    wow = s.pct_change()
    al = (wow > wow_thr).fillna(False)
    a = np.zeros(len(dates), bool)
    lmv = np.full(len(dates), np.nan)
    obs = list(s.index)
    j = -1
    for i, d in enumerate(dates):
        while j + 1 < len(obs) and obs[j + 1] <= d:
            j += 1
        if j >= 0:
            a[i] = bool(al.iloc[j])
            lmv[i] = float(s.iloc[j])
    return a, lmv


# ---------------- 长历史 α（全历史，用于看 7 月 α 到底怎么走） ----------------
def build(index_code):
    df = L.load_all()
    dfall = None
    # 优先用 load_all（带 ret）
    joined = L.load_all().get(index_code)
    df = joined if joined is not None else L.px(index_code, '20261103')
    dates = df.trade_date.tolist()
    close = df.close.to_numpy(float)
    ret = df.ret.to_numpy(float)
    d = {'dates': np.array(dates, dtype=object)}

    for mode in ('ret', 'resid'):
        ar = ar1_series(ret, close, mode)
        d[f'ar1_{mode}'] = ar
        for days in (3, 5, 7):
            for thr in (0.6, 0.7):
                for mm in ('persist', 'rise'):
                    d[f'M1_{mode}_{mm}{days}_t{thr}'] = ar1_alert(ar, thr, days, mm)

    for mult in (1.0, 1.5, 2.0, 2.5, 3.0):
        a, dsig = m2(ret, mult)
        d[f'M2_{mult}'] = a
        if mult == 2.0:
            d['dsig'] = dsig

    for pct in (80, 85, 90, 95):
        a, psi, thr = m3(ret, pct)
        d[f'M3_{pct}'] = a
        if pct == 90:
            d['psi'] = psi

    for win, q in itertools.product((250, 500, 750), (1, 5, 20)):
        a, al = m4(ret, win, q, 90, 15, 1.8)
        d[f'M4_w{win}_q{q}'] = a
        d[f'alpha_w{win}_q{q}'] = al

    return d


def build_all(force=False):
    if os.path.exists(ALERT_FP) and not force:
        return np.load(ALERT_FP, allow_pickle=True)
    store = {}
    for code in L.IDX:
        print('build', code, flush=True)
        d = build(code)
        for k, v in d.items():
            store[f'{code}|{k}'] = v
    np.savez_compressed(ALERT_FP, **store)
    return np.load(ALERT_FP, allow_pickle=True)


def events_of(dates, close, code):
    """峰谷：段内最高收盘=峰；峰后最低=谷"""
    out = []
    s = pd.Series(close, index=dates)
    for a, b, tag in EVENTS[code]:
        seg = s[(s.index >= a) & (s.index <= b)]
        if len(seg) < 10:
            continue
        pk = seg.idxmax()
        after = seg[seg.index >= pk]
        tr = after.idxmin()
        out.append((tag, pk, tr, (seg[tr] / seg[pk] - 1) * 100))
    return out


if __name__ == '__main__':
    t = time.time()
    z = build_all(force='--force' in sys.argv)
    print('指标变体键数', len(z.files), '用时', round(time.time() - t, 1), 's')
    for code, name in L.IDX.items():
        dates = [str(x) for x in z[f'{code}|dates']]
        df = L.px(code, '20261103')
        close = df.close.to_numpy(float) if len(df) == len(dates) else None
        print(f'\n== {name}  {dates[0]}~{dates[-1]}  {len(dates)}日')
        if close is not None:
            for tag, pk, tr, dd in events_of(dates, close, code):
                print(f'   {tag}: 峰 {pk}  谷 {tr}  跌幅 {dd:.1f}%')
