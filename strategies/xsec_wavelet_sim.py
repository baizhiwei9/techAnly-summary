# -*- coding: utf-8 -*-
"""
xsec_wavelet_sim.py —— 「小波多空模拟」三栏模拟交易引擎（纯脚本，0 token）
================================================================================
用户需求（2026-10-14 晚 改版，2026-10-14 21:2x 追加小波确认）：
  · 两栏模拟（国债栏用户已要求删除）
      1) 商品栏  —— 商品 60 日反转横截面多空（全池，已剔有色）
      2) 股指栏  —— 股指期货基差横截面多空 **(× 小波 OI/量/价 方向确认)**
  · **等名义额**建仓：多头总名义额 ≈ 空头总名义额（不再出现"多2手空1手对不上钱"）
  · **不留裸多裸空**：多空腿数对称（K 多 / K 空），两侧金额对齐
  · 有色板块整体剔除

用法：
  python3 strategies/xsec_wavelet_sim.py                 # 更新到最新交易日并出报告
  python3 strategies/xsec_wavelet_sim.py --dry           # 只打印，不落库
  python3 strategies/xsec_wavelet_sim.py --date 2026-10-13
  python3 strategies/xsec_wavelet_sim.py --no-fetch      # 只用缓存

状态文件：memory/xsec_wavelet_sim/state.json
日志：    memory/xsec_wavelet_sim/sim.log
"""
from __future__ import annotations

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, math, time, argparse, warnings
from datetime import datetime, date as _date
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ---------- 路径 ----------
HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
FW = os.path.join(WS, 'futures_wavelet_bt')
FW_CACHE = os.path.join(FW, '.cache')
SIM_DIR = os.path.join(WS, 'memory', 'xsec_wavelet_sim')
CACHE = os.path.join(SIM_DIR, 'cache')
os.makedirs(SIM_DIR, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)
STATE_FP = os.path.join(SIM_DIR, 'state.json')
LOG_FP = os.path.join(SIM_DIR, 'sim.log')

CAPITAL = 500000.0
# 各栏本金（用户 2026-10-14 定稿：商品 50 万 + 股指 300 万）
CAPITAL_OF = {'commodity': 500000.0, 'idx': 3000000.0}
LEV = {'commodity': 1.5, 'idx': 1.0}
COST = {'commodity': 2.0, 'idx': 1.0}     # bp，单边
BOOKS = ['commodity', 'idx']              # 计资金的实盘栏（两条线：商品 + 股指）
OBS_BOOKS = []                           # 信号观察栏（不计资金）
ALL_BOOKS = ['commodity', 'idx']

# 横截面多空每边腿数
TOPK = {'commodity': 6, 'idx': 2}

# ---- 股指栏「小波(OI/量/价)方向确认层」（用户 2026-10-14 追加，默认开启）----
# 融合：基差 z 权重 × [(1-w) + w·1(小波方向≥0)，腿级符号对齐]
# 多层滞后对齐报告 §二 原文（信号 T-1 收盘生成，T+1 执行）→ Wt 再做一次 shift(1)
WAVE_CONFIRM = True
WAVE_W = 0.7
# 单腿最大名义额上限（相对于每腿目标，防止大体量合约吃满预算）
MAXLEG = 4.0

# 期货栏（小波）品种池板块：只做化工+农产品，忽略股指
WAVE_SECTS = ('化工', '农产品')

# ---------- 股指 ----------
IDX = ['IF', 'IH', 'IC', 'IM']
IDX_SPOT = {'IF': '000300.SH', 'IH': '000016.SH', 'IC': '000905.SH', 'IM': '000852.SH'}
IDX_MULT = {'IF': 300, 'IH': 300, 'IC': 200, 'IM': 200}
IDX_NAME = {'IF': '沪深300(IF)', 'IH': '上证50(IH)', 'IC': '中证500(IC)', 'IM': '中证1000(IM)'}

# ---------- 国债（数据保留，本栏目已停用） ----------
BOND = ['T', 'TF', 'TS', 'TL']
BOND_MULT = {'T': 10000, 'TF': 10000, 'TS': 20000, 'TL': 10000}
BOND_NAME = {'T': '10年期(T)', 'TF': '5年期(TF)', 'TS': '2年期(TS)', 'TL': '30年期(TL)'}

# ---------- 商品池（48 品种，剔除有色板块）----------
CMDTY = {
    'CU': ('沪铜', 5), 'AL': ('沪铝', 5), 'ZN': ('沪锌', 5), 'PB': ('沪铅', 5),
    'NI': ('沪镍', 1), 'SN': ('沪锡', 1), 'AO': ('氧化铝', 20), 'SS': ('不锈钢', 5),
    'RB': ('螺纹钢', 10), 'I': ('铁矿石', 100), 'J': ('焦炭', 100), 'JM': ('焦煤', 60),
    'HC': ('热卷', 10), 'SF': ('硅铁', 5), 'SM': ('锰硅', 5), 'ZC': ('动力煤', 100),
    'TA': ('PTA', 5), 'MA': ('甲醇', 10), 'PP': ('聚丙烯', 5), 'L': ('塑料', 5),
    'EG': ('乙二醇', 10), 'V': ('PVC', 5), 'SC': ('原油', 1000), 'RU': ('橡胶', 10),
    'BU': ('沥青', 10), 'FG': ('玻璃', 20), 'SA': ('纯碱', 20), 'UR': ('尿素', 20),
    'PX': ('对二甲苯', 5), 'SH': ('烧碱', 20),
    'AU': ('黄金', 1000), 'AG': ('白银', 15),
    'A': ('豆一', 10), 'JD': ('鸡蛋', 10), 'LH': ('生猪', 16), 'SP': ('纸浆', 10),
    'M': ('豆粕', 10), 'Y': ('豆油', 10), 'P': ('棕榈油', 10), 'RM': ('菜粕', 10),
    'OI': ('菜油', 10), 'C': ('玉米', 10), 'CS': ('玉米淀粉', 10), 'SR': ('白糖', 10),
    'CF': ('棉花', 5), 'AP': ('苹果', 10), 'CJ': ('红枣', 5), 'PK': ('花生', 5),
}
# 剔除板块（用户 2026-10-14 要求去掉有色）
EXCLUDE_SECTS = {'有色'}

# 品种 → 板块
SECT = {
    'IF': '股指', 'IH': '股指', 'IC': '股指', 'IM': '股指',
    'AU': '贵金属', 'AG': '贵金属',
    'CU': '有色', 'AL': '有色', 'ZN': '有色', 'PB': '有色', 'NI': '有色',
    'SN': '有色', 'AO': '有色', 'SS': '有色',
    'RB': '黑色', 'I': '黑色', 'J': '黑色', 'JM': '黑色', 'HC': '黑色',
    'SF': '黑色', 'SM': '黑色', 'ZC': '黑色',
    'TA': '化工', 'MA': '化工', 'PP': '化工', 'L': '化工', 'EG': '化工', 'V': '化工',
    'SC': '化工', 'RU': '化工', 'BU': '化工', 'FG': '化工', 'SA': '化工',
    'UR': '化工', 'PX': '化工', 'SH': '化工',
    'A': '农产品', 'JD': '农产品', 'LH': '农产品', 'SP': '农产品', 'M': '农产品',
    'Y': '农产品', 'P': '农产品', 'RM': '农产品', 'OI': '农产品', 'C': '农产品',
    'CS': '农产品', 'SR': '农产品', 'CF': '农产品', 'AP': '农产品',
    'CJ': '农产品', 'PK': '农产品',
}

# ---------- 小波参数 ----------
WIN, LEVEL, KSLOPE, SMOOTH, ZLOOK, ZTHR = 320, 6, 40, 5, 60, 0.25


def log(msg):
    line = f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}'
    print(line, flush=True)
    with open(LOG_FP, 'a') as f:
        f.write(line + '\n')


# =====================================================================
# 数据层
# =====================================================================
def _ipv4():
    import socket
    _o = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]


def fetch_commodity(sym, end=None, no_fetch=False):
    end = end or datetime.now().strftime('%Y%m%d')
    fw_fp = os.path.join(FW_CACHE, f'{sym}0_long.csv')
    fp = os.path.join(CACHE, f'{sym}.csv')
    if os.path.exists(fp):
        try:
            d = pd.read_csv(fp, parse_dates=['date'])
            if no_fetch or (len(d) and d['date'].max().strftime('%Y%m%d') >= end):
                return d
        except Exception:
            pass
    if os.path.exists(fw_fp):
        try:
            d = pd.read_csv(fw_fp, parse_dates=['date'])
            if no_fetch or (len(d) and d['date'].max().strftime('%Y%m%d') >= end):
                d.to_csv(fp, index=False)
                return d
        except Exception:
            pass
    if no_fetch:
        for p in (fp, fw_fp):
            if os.path.exists(p):
                return pd.read_csv(p, parse_dates=['date'])
        return None
    try:
        _ipv4()
        from data_hub import ak as ak
        raw = ak.futures_main_sina(symbol=f'{sym}0', start_date='20170101', end_date=end)
        raw = raw.rename(columns={'日期': 'date', '开盘价': 'open', '最高价': 'high',
                                  '最低价': 'low', '收盘价': 'close', '成交量': 'vol',
                                  '持仓量': 'oi'})
        raw['date'] = pd.to_datetime(raw['date'])
        d = raw[['date', 'open', 'high', 'low', 'close', 'vol', 'oi']].dropna()
        d = d[(d['vol'] > 0) & (d['oi'] > 0)].reset_index(drop=True)
        d.to_csv(fp, index=False)
        return d
    except Exception as e:
        log(f'  fetch {sym} FAIL {repr(e)[:70]}')
        for p in (fp, fw_fp):
            if os.path.exists(p):
                return pd.read_csv(p, parse_dates=['date'])
        return None


def load_commodity_panel(end=None, no_fetch=False):
    C, O, V, meta = {}, {}, {}, {}
    for sym, (nm, mult) in CMDTY.items():
        if SECT.get(sym) in EXCLUDE_SECTS:
            continue
        d = fetch_commodity(sym, end=end, no_fetch=no_fetch)
        if d is None or len(d) < 400:
            continue
        d = d.drop_duplicates('date', keep='last').set_index('date').sort_index()
        C[sym] = d['close']; O[sym] = d['oi']; V[sym] = d['vol']
        meta[sym] = (nm, mult)
    if not C:
        return None, None, None, None
    idx = pd.DataFrame(C).sort_index().index
    return (pd.DataFrame(C).reindex(idx), pd.DataFrame(O).reindex(idx),
            pd.DataFrame(V).reindex(idx), meta)


def load_idx_panel(no_fetch=False):
    """股指主力连续 + 现货 + 近月 → 年化基差率（含 oi/vol，供小波用）"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from tushare_init import get_pro
        pro = get_pro()
    except Exception as e:
        log(f'  tushare init FAIL {repr(e)[:80]}')
        pro = None
    today = datetime.now().strftime('%Y%m%d')
    basic = {}
    fpb = os.path.join(FW_CACHE, 'cffex_basic.csv')
    if os.path.exists(fpb):
        b = pd.read_csv(fpb, dtype=str)
        basic = {r['ts_code']: str(r['delist_date']) for _, r in b.iterrows()}
    outs = {}
    for k in IDX:
        fmain = os.path.join(FW_CACHE, f'fut_{k}_main.csv')
        fspot = os.path.join(FW_CACHE, f'spot_{IDX_SPOT[k]}.csv')
        fmap = os.path.join(FW_CACHE, f'map_{k}.csv')
        if pro is not None and not no_fetch:
            fut = _cached(fmain, lambda k=k: pro.fut_daily(ts_code=f'{k}.CFX',
                                                           start_date='20190101', end_date=today))
            spot = _cached(fspot, lambda k=k: pro.index_daily(ts_code=IDX_SPOT[k],
                                                              start_date='20190101', end_date=today))
            mp = _cached(fmap, lambda k=k: pro.fut_mapping(ts_code=f'{k}.CFX',
                                                           start_date='20190101', end_date=today))
        else:
            fut = pd.read_csv(fmain, dtype={'trade_date': str}) if os.path.exists(fmain) else None
            spot = pd.read_csv(fspot, dtype={'trade_date': str}) if os.path.exists(fspot) else None
            mp = pd.read_csv(fmap, dtype={'trade_date': str}) if os.path.exists(fmap) else None
        if fut is None or spot is None:
            log(f'  idx {k} data missing'); continue
        _cols = [c for c in ['trade_date', 'close', 'oi', 'vol'] if c in fut.columns]
        fut = fut[_cols].rename(columns={'close': 'fut'})
        spot = spot[['trade_date', 'close']].rename(columns={'close': 'spot'})
        fut['trade_date'] = fut['trade_date'].astype(str)
        spot['trade_date'] = spot['trade_date'].astype(str)
        d = fut.merge(spot, on='trade_date', how='inner').sort_values('trade_date')
        has_map = False
        if mp is not None and 'mapping_ts_code' in mp.columns:
            mp = mp[['trade_date', 'mapping_ts_code']].rename(columns={'mapping_ts_code': 'contract'})
            mp['trade_date'] = mp['trade_date'].astype(str)
            d = d.merge(mp, on='trade_date', how='left')
            has_map = True
        if not has_map:
            d['contract'] = ''
        d['delist'] = d['contract'].map(lambda c: basic.get(c, '')) if basic else ''
        d['days'] = (pd.to_datetime(d['delist'], errors='coerce') -
                     pd.to_datetime(d['trade_date'], format='%Y%m%d', errors='coerce')).dt.days
        d['basis'] = d['fut'] - d['spot']
        d['basis_rate'] = d['basis'] / d['spot']
        d['ann_basis'] = d['basis_rate'] * 365 / d['days'].clip(lower=1)
        if 'vol' not in d.columns:
            d['vol'] = np.nan
        outs[k] = d.set_index(pd.to_datetime(d['trade_date'], format='%Y%m%d'))
    return outs


def load_bond_panel(no_fetch=False):
    outs = {}
    for P in BOND:
        fp = os.path.join(FW_CACHE, f'bondx_{P}.csv')
        if os.path.exists(fp):
            d = pd.read_csv(fp, parse_dates=['trade_date']).set_index('trade_date').sort_index()
            outs[P] = d
    return outs


def _ts_retry(fn, n=6, wait=6):
    for i in range(n):
        try:
            r = fn()
            if r is not None and len(r):
                return r
        except Exception as e:
            log(f'    ts retry{i+1}: {repr(e)[:60]}')
            time.sleep(wait)
    return None


def _cached(path, fn, refresh_days=0.5):
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < refresh_days * 86400:
        return pd.read_csv(path, dtype={'trade_date': str})
    d = _ts_retry(fn)
    if d is not None:
        d.to_csv(path, index=False)
        return d
    if os.path.exists(path):
        return pd.read_csv(path, dtype={'trade_date': str})
    return None


# =====================================================================
# 信号层
# =====================================================================
import pywt


def _slope_swt(arr, k=KSLOPE):
    """静态小波(SWT)近似系数斜率，向量化（不设 level，自动最大）"""
    a = np.asarray(arr, dtype=float)
    if len(a) < 64 or np.isnan(a).any():
        a = pd.Series(a).ffill().bfill().values
    co = pywt.swt(a, 'db4', trim_approx=True)
    a = np.asarray(co[-1], dtype=float)[-len(a):]
    sl = (a - np.roll(a, k)) / k
    sl[:k] = np.nan
    return sl


def wedge(x, win=WIN, k=KSLOPE):
    """滑动窗口 SWT 斜率（精度高但慢）；供回测/最终信号用"""
    x = np.asarray(x, float)
    n = len(x)
    out = np.full(n, np.nan)
    for t in range(n):
        if t < win + 5:
            continue
        lo = max(0, t - win + 1)
        seg = x[lo:t + 1]
        if len(seg) < 128:
            continue
        pad = (-len(seg)) % 2
        if pad:
            seg = np.concatenate([np.full(pad, seg[0]), seg])
        try:
            co = pywt.swt(seg, 'db4', trim_approx=True)
        except Exception:
            continue
        a = np.asarray(co[-1], float)[-len(seg):]
        if len(a) > k:
            out[t] = (a[-1] - a[-1 - k]) / k
    return pd.Series(out, index=getattr(x, 'index', None))


def zsc(s, Z=ZLOOK):
    s = pd.Series(s)
    m = s.rolling(Z, min_periods=10).mean()
    sd = s.rolling(Z, min_periods=10).std()
    return ((s - m) / (sd + 1e-12)).values


def wavelet_dir(close, oi, vol):
    """→ Series[-1,1]：小波(价/持仓量/成交量)长周期方向（滑动窗口版，供单次/live）"""
    n = len(close)
    lp = np.log(np.asarray(close, float))
    lo = np.log(np.asarray(oi, float) + 1.0)
    lv = np.log(np.asarray(vol, float) + 1.0)
    sp = np.full(n, np.nan); sv = np.full(n, np.nan); so = np.full(n, np.nan)
    for t in range(n):
        if t < WIN + 5:
            continue
        sp[t] = _slope(lp, t); sv[t] = _slope(lv, t); so[t] = _slope(lo, t)
    sp = pd.Series(sp).rolling(SMOOTH, min_periods=1).mean().values
    sv = pd.Series(sv).rolling(SMOOTH, min_periods=1).mean().values
    so = pd.Series(so).rolling(SMOOTH, min_periods=1).mean().values
    zp, zv, zo = zsc(sp), zsc(sv), zsc(so)
    st = np.zeros(n)
    for t in range(n):
        if np.isnan(zp[t]) or np.isnan(zo[t]) or np.isnan(zv[t]):
            continue
        P, V, O = zp[t] > ZTHR, zv[t] > ZTHR, zo[t] > ZTHR
        Pn, Vn, On = zp[t] < -ZTHR, zv[t] < -ZTHR, zo[t] < -ZTHR
        if P and V and O: st[t] = 1.0
        elif P and O: st[t] = 0.66
        elif Pn and Vn and On: st[t] = -1.0
        elif Pn and On: st[t] = -0.66
        elif P: st[t] = 0.33
        elif Pn: st[t] = -0.33
    return pd.Series(st, index=close.index)


def _slope(a, t, window=WIN, k=KSLOPE):
    lo = max(0, t - window + 1)
    seg = np.asarray(a[lo:t + 1], float)
    if len(seg) < 128:
        return np.nan
    pad = (-len(seg)) % 2
    if pad:
        seg = np.concatenate([np.full(pad, seg[0]), seg])
    try:
        co = pywt.swt(seg, 'db4', trim_approx=True)
    except Exception:
        return np.nan
    aa = np.asarray(co[-1], float)[-len(seg):]
    return (aa[-1] - aa[-1 - k]) / k if len(aa) > k else np.nan


# =====================================================================
# 权重层（横截面多空，等名义额：K 多 / K 空，各腿权重 ±1/(2K)）
# =====================================================================
def _topk_W(sig, K):
    """向量化：每行取信号最大的 K 个做多、最小 K 个做空，等权 ±1/(2K)。"""
    s = sig.copy()
    R = s.rank(axis=1, ascending=False)
    n = s.notna().sum(axis=1)
    Wl = (R <= K).astype(float)
    Ws = (R > (n.values[:, None] - K)).astype(float)
    W = (Wl - Ws)
    tot = (W.abs().sum(axis=1).replace(0, np.nan))
    W = W.div(tot, axis=0).fillna(0.0)
    return W


# ---- 各栏信号 ----
def book_wave(C, O, V, k=60):
    """① 期货栏（用户 2026-10-14 定稿）：只做 **化工+农产品**，忽略股指。
    信号 = **价格 60 日反转** r60 = ln(C_t/C_{t-60}) 取负号 → 横截面 z → 多跌/空涨。
    （原「价/持仓量/成交量 小波低频方向」经扩样本复核无正 α，已弃用）"""
    pool = [c for c in C.columns if SECT.get(c) in WAVE_SECTS]
    R = np.log(C[pool]).diff()
    S = -np.log(C[pool]).diff(k)                     # 价格反转信号（多跌多/空涨多）
    z = S.sub(S.mean(axis=1), axis=0).div(S.std(axis=1) + 1e-12, axis=0)
    W = _topk_W(z, TOPK['wave'])
    return W, R, S, z


def book_commodity(C, O, V, k=60):
    R = np.log(C).diff()
    mom = np.log(C).diff(k)
    sig = -mom                                  # 反转：多跌多/空涨多
    W = _topk_W(sig, TOPK['commodity'])
    return W, R, sig


def load_idx_panel_aligned(no_fetch=False):
    """股指主力连续（fut）+ 年化基差率（ann_basis）+ oi/vol（供小波确认），对齐成面板。"""
    outs = load_idx_panel(no_fetch=no_fetch)
    if not outs:
        return None, None, None, None, None, None
    fut = pd.DataFrame({k: outs[k]['fut'] for k in outs if 'fut' in outs[k]})
    basis = pd.DataFrame({k: outs[k]['ann_basis'] for k in outs if 'ann_basis' in outs[k]})
    oi = pd.DataFrame({k: outs[k]['oi'] for k in outs if 'oi' in outs[k]})
    vol = pd.DataFrame({k: outs[k]['vol'] for k in outs if 'vol' in outs[k]})
    common = [k for k in IDX if k in fut.columns]
    if not common:
        return None, None, None, None, None, None
    idx = fut[common].sort_index().index
    return (fut[common].reindex(idx), basis[common].reindex(idx),
            oi[common].reindex(idx), vol[common].reindex(idx), IDX_NAME, IDX_MULT)


def book_idx(fut, basis, oi=None, vol=None):
    """② 股指栏：股指期货「基差（贴水）」横截面多空 × 小波(OI/量/价)方向确认。
    信号 = 年化基差率 ann_basis 的横截面 z；规则 = **多贴水最深、空贴水最浅**（K=2）。
    确认层（用户 2026-10-14 追加）：小波(价/持仓量/成交量)长周期方向与持仓腿同向才满仓，
      权重 ×[(1-w) + w·1(方向≥0 - 看该腿方向与持仓方向是否一致)]，w=WAVE_W。
    返回 (W, R, sig, z, wdict)；wdict 供报告展示小波每腿确认。"""
    z = basis.sub(basis.mean(axis=1), axis=0).div(basis.std(axis=1) + 1e-12, axis=0)
    sig = -z                                     # 取负 → 多最负基差（贴水最深）
    W = _topk_W(sig, TOPK['idx'])
    wdict = {}
    if WAVE_CONFIRM and oi is not None and vol is not None:
        dirs = {}
        for k in W.columns:
            try:
                dirs[k] = wavelet_dir(fut[k], oi[k].ffill(), vol[k].ffill())
            except Exception:
                dirs[k] = pd.Series(0.0, index=fut.index)
        # wday 当天快照（未滞后）供展示
        for k in W.columns:
            wdict[k] = float(dirs[k].iloc[-1]) if len(dirs[k]) else 0.0
        # 腿级确认：小波方向与持仓腿**同向**→1，**反向**→0（中性/未预热的不惩罚，归一化后自动抵消）
        # 方向按报告 §二 口径额外滞后 1 日对齐（信号 T-1 生成，T+1 执行）
        for k in W.columns:
            leg = np.sign(W[k])
            dw = np.sign(dirs[k].shift(1).fillna(0.0))
            ok = 1.0 - (((leg > 0) & (dw < 0)) | ((leg < 0) & (dw > 0))).astype(float)
            W[k] = W[k] * ((1 - WAVE_W) + WAVE_W * ok)
        W = W.div(W.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    R = np.log(fut).diff()
    return W, R, sig, z, wdict


# =====================================================================
# 模拟引擎
# =====================================================================
def md(v):
    return round(float(v), 2)


def build_targets(date_str, no_fetch=False):
    tg = {}
    C, O, V, meta = load_commodity_panel(end=date_str.replace('-', ''), no_fetch=no_fetch)
    if C is not None:
        Wm, Rm, sigm = book_commodity(C, O, V)
        tg['commodity'] = dict(W=Wm, R=Rm, sig=sigm, px=C, meta=meta)
    # ② 股指栏（基差 × 小波确认）
    fut, basis, oi, vol, nmap, mmap = load_idx_panel_aligned(no_fetch=no_fetch)
    if fut is not None:
        Wi, Ri, sigi, zi, wd = book_idx(fut, basis, oi=oi, vol=vol)
        tg['idx'] = dict(W=Wi, R=Ri, sig=sigi, z=zi, px=fut,
                         mult=mmap, name=nmap, wave=wd)
    return tg


def positions_from_weights(W, prev_w, book, px, meta=None, mult=None, name=None, minw=0.0):
    """等名义额建仓：两侧金额对齐。
    1) 先剔除 1 手名义额过大的合约（在 50 万本金下无法配平）
    2) 每侧每腿目标名义额 = 本金×杠杆/(2K)，手数取整
    3) 迭代缩放两侧到 min(总多,总空)，尽量消除裸敞口
    ⚠️ 股指栏：单张面值巨大（40~170 万/手），50 万本金下**每腿固定 1 手**展示（qty_one）
    """
    if W is None or W.empty:
        return [], [], {}, 0.0, 0.0
    last = W.dropna(how='all').index[-1]
    w = W.loc[last].dropna()
    w = w[w.abs() > 0]
    K = TOPK[book]
    gross = CAPITAL_OF.get(book, CAPITAL) * LEV[book]
    # --- 候选腿（含 1 手名义额）---
    cand = {'多': [], '空': []}
    for key, ww in w.items():
        p = float(px.loc[last, key]) if key in px.columns else np.nan
        if not np.isfinite(p) or p <= 0:
            continue
        m = (mult.get(key) if mult else (meta.get(key, (None, 1))[1] if meta else 1)) or 1
        unit = p * m                              # 1 手名义额
        nm = (name.get(key) if name else (meta.get(key, (key, 0))[0] if meta else key))
        side = '多' if ww > 0 else '空'
        cand[side].append(dict(key=key, name=nm, side=side, weight=float(ww),
                               px=float(p), mult=float(m), unit=float(unit)))
    # --- 剔除过大合约（1 手 > MAXLEG×每腿目标）---
    tgt_leg = gross / (2 * K)
    for s in ('多', '空'):
        cand[s] = [c for c in cand[s] if c['unit'] <= MAXLEG * tgt_leg] or cand[s]
    # --- 初配手数 ---
    for s in ('多', '空'):
        per = (gross / 2) / max(1, len(cand[s]))
        for c in cand[s]:
            c['qty'] = max(1, int(round(per / c['unit'])))
    # --- 迭代配平 ---
    for _ in range(8):
        L = sum(c['qty'] * c['unit'] for c in cand['多'])
        S = sum(c['qty'] * c['unit'] for c in cand['空'])
        if L <= 0 or S <= 0:
            break
        tgt = min(L, S)
        changed = False
        for s, tot in (('多', L), ('空', S)):
            if abs(tot - tgt) < 1e-9:
                continue
            f = tgt / tot
            for c in cand[s]:
                q = max(1, int(round(c['qty'] * f)))
                if q != c['qty']:
                    c['qty'] = q; changed = True
        if not changed:
            break
    # --- 双向微调：每次选一个动作（重的一边 -1手 / 轻的一边 +1手）使 |diff| 最小 ---
    for _ in range(600):
        L = sum(c['qty'] * c['unit'] for c in cand['多'])
        S = sum(c['qty'] * c['unit'] for c in cand['空'])
        diff = L - S
        if abs(diff) <= 1e-9:
            break
        heavy = '多' if diff > 0 else '空'
        light = '空' if diff > 0 else '多'
        best = None
        for c in cand[heavy]:                      # 重的一边 -1 手（降至 0 删除）
            nd = diff - c['unit'] if heavy == '多' else diff + c['unit']
            if best is None or abs(nd) < abs(best[0]):
                best = (nd, c, 'rm')
        for c in cand[light]:                      # 轻的一边 +1 手
            nd = diff + c['unit'] if light == '多' else diff - c['unit']
            if best is None or abs(nd) < abs(best[0]):
                best = (nd, c, 'add')
        if best is None or abs(best[0]) >= abs(diff):
            break
        nd, c, act = best
        if act == 'rm':
            if c['qty'] <= 1:
                cand[heavy].remove(c)
            else:
                c['qty'] -= 1
        else:
            c['qty'] += 1
        if not cand['多'] or not cand['空']:
            break
    # --- 股指栏：单张面值过大时自动降为 1 手/腿（300 万本金一般无需）---
    if book == 'idx':
        L0 = sum(c['qty'] * c['unit'] for c in cand['多'])
        S0 = sum(c['qty'] * c['unit'] for c in cand['空'])
        tgt = CAPITAL_OF.get(book, CAPITAL) * LEV[book] / 2
        if min(L0, S0) < 0.4 * tgt:            # 手数凑不出足够名义额 → 固定 1 手
            for s in ('多', '空'):
                for c in cand[s]:
                    c['qty'] = 1
    # --- 输出 ---
    pos, events = [], []
    long_not = short_not = 0.0
    for s in ('多', '空'):
        for c in sorted(cand[s], key=lambda x: -abs(x['weight'])):
            qty = int(c['qty'])
            notional = qty * c['unit']
            if s == '多':
                long_not += notional
            else:
                short_not += notional
            pos.append(dict(key=c['key'], name=c['name'], side=s, weight=c['weight'],
                            px=c['px'], qty=qty, notional=float(notional), mult=c['mult']))
            pw = float(prev_w.get(c['key'], 0.0)) if prev_w else 0.0
            if abs(pw) < 1e-9:
                events.append(dict(act='开仓', key=c['key'], name=c['name'], side=s, qty=qty,
                                   px=c['px'], text=f"🟢 开{s} {c['name']} {qty}手 @ {md(c['px'])}（名义 {notional/10000:.1f}万）"))
            elif np.sign(pw) != np.sign(c['weight']):
                events.append(dict(act='反手', key=c['key'], name=c['name'], side=s, qty=qty,
                                   px=c['px'], text=f"🔄 反手 {c['name']} → {s} {qty}手 @ {md(c['px'])}（名义 {notional/10000:.1f}万）"))
    new_w = {p['key']: p['weight'] for p in pos}
    for key, pw in (prev_w or {}).items():
        if abs(pw) >= 1e-9 and key not in new_w:
            nm = (name.get(key) if name else (meta.get(key, (key, 0))[0] if meta else key))
            events.append(dict(act='平仓', key=key, name=nm, side=('多' if pw > 0 else '空'),
                               qty=0, px=float('nan'), text=f"🔴 平仓 {nm}（{'多' if pw>0 else '空'}）"))
    return pos, events, new_w, long_not, short_not


def run(date_str=None, dry=False, no_fetch=False):
    date_str = date_str or datetime.now().strftime('%Y-%m-%d')
    log(f'===== 运行 {date_str} =====')
    st = {}
    if os.path.exists(STATE_FP):
        try:
            st = json.load(open(STATE_FP))
        except Exception:
            st = {}
    books = st.get('books', {})
    prev_w = st.get('weights', {})
    done = st.setdefault('done_dates', [])
    already = date_str in done
    if already:
        log(f'  {date_str} 已处理过，本次只重出报告（不重复计权益）')

    tg = build_targets(date_str, no_fetch=no_fetch)
    if not tg:
        log('无数据，退出'); return None

    out = dict(date=date_str, books={}, trades={}, pnl={}, balance={}, obs={})
    for book, d in tg.items():
        pos, events, neww, ln, sn = positions_from_weights(
            d['W'], prev_w.get(book, {}), book, d['px'],
            meta=d.get('meta'), mult=d.get('mult'), name=d.get('name'))
        W, R = d['W'], d['R']
        last = W.dropna(how='all').index[-1]
        w0 = W.shift(1).loc[last].fillna(0)
        r0 = R.loc[last].fillna(0)
        gross = float((w0 * r0).sum())
        turn = float((W.shift(1) - W.shift(2)).loc[last].abs().sum()) if len(W) > 2 else 0.0
        cost = turn * COST[book] / 10000.0
        net = (gross - cost) * LEV[book]
        is_obs = book in OBS_BOOKS
        out['books'][book] = dict(positions=pos, n=len(pos), observe=is_obs,
                                  wsum=sum(abs(x['weight']) for x in pos),
                                  long_not=ln, short_not=sn,
                                  wave=d.get('wave'),
                                  equity=float(books[book]['equity']) if book in books else CAPITAL_OF.get(book, CAPITAL),
                                  ret=float(books[book]['ret']) if book in books else 0.0,
                                  capital=CAPITAL_OF.get(book, CAPITAL))
        out['trades'][book] = events
        out['pnl'][book] = net
        out['balance'][book] = dict(long=ln, short=sn)
        prev_w[book] = neww
        if is_obs:
            out['obs'][book] = dict(daily=float(net))

    if not already:
        for book in BOOKS:
            cap = CAPITAL_OF.get(book, CAPITAL)
            b = books.setdefault(book, dict(capital=cap, equity=cap, ret=0.0,
                                            history=[], pos_value=0.0))
            r = out['pnl'].get(book, 0.0)
            b['equity'] = float(b['equity'] * (1 + r))
            b['ret'] = float(b['equity'] / cap - 1)
            b['last_ret'] = float(r)
            b['history'] = (b['history'] + [dict(date=out['date'], ret=round(float(r), 6),
                                                 equity=round(b['equity'], 2))])[-1500:]
        done.append(date_str)
    for book in ALL_BOOKS:
        if book not in out['books']:
            bb = books.get(book, {})
            cap = CAPITAL_OF.get(book, CAPITAL)
            out['books'][book] = dict(positions=[], n=0, equity=bb.get('equity', cap),
                                      ret=bb.get('ret', 0.0), capital=cap,
                                      long_not=0, short_not=0, observe=book in OBS_BOOKS)
            out['trades'][book] = []
            out['pnl'][book] = 0.0
    total_cap = sum(CAPITAL_OF.get(b, CAPITAL) for b in BOOKS)
    total_eq = sum(books.get(b, {}).get('equity', CAPITAL_OF.get(b, CAPITAL)) for b in BOOKS)
    for b_ in ALL_BOOKS:
        if b_ in out['books'] and b_ in books:
            out['books'][b_]['equity'] = float(books[b_]['equity'])
            out['books'][b_]['ret'] = float(books[b_]['ret'])
            out['books'][b_]['capital'] = CAPITAL_OF.get(b_, CAPITAL)
    out['total'] = dict(capital=total_cap, equity=float(total_eq),
                        ret=float(total_eq / total_cap - 1))

    st['books'] = books
    st['weights'] = prev_w
    st['date'] = date_str
    st['last_run'] = datetime.now().isoformat(timespec='seconds')
    if not dry:
        json.dump(st, open(STATE_FP, 'w'), ensure_ascii=False, indent=1)
        rp = os.path.join(SIM_DIR, f'report_{date_str}.json')
        json.dump(out, open(rp, 'w'), ensure_ascii=False, indent=1, default=str)
    log(f"完成：总权益 {out['total']['equity']:.0f} ({out['total']['ret']:+.2%})")
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--no-fetch', action='store_true')
    a = ap.parse_args()
    run(a.date, a.dry, a.no_fetch)
