#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gex_core.py —— 科创50ETF(588000) 期权 GEX / Gamma Flip 纯计算库
================================================================================

设计原则
--------
本模块**只做计算，不做 IO**（不读文件、不写文件、不发邮件、不读系统时间）。
调用方（`bin/gex_flip_alert.py`）负责喂 DataFrame 进来、决定输出到哪。
这样做的好处：可在 notebook / 单测里直接 import 复用，也方便回归比对。

口径（与 bin/opt_gex.py、bin/opt_gex_flip.py 保持一致）
--------------------------------------------------------
    单腿 Gamma(per share)  Γ = e^{-qT} φ(d1) / (S σ √T)
        d1 = [ln(S/K) + (r − q + σ²/2)·T] / (σ√T),  T = DTE/365
    单腿 GEX               GEX_i = sign_i · Γ_i · OI_i · MULT · S² · 0.01
        sign_i = +1(Call) / −1(Put)
        单位：元 / 每 1% 标的变动；对外展示统一 ÷1e8 → 亿元
    净 GEX                  Σ GEX_i （每个到期月先各自汇总，再合计）

期限口径（老板 2026-10 指定的「新口径」）
------------------------------------------
    * 剔除近月：DTE < dte_lo(默认 30) —— 近月 Γ ∝ 1/√T 会爆炸，且日历效应抖动
    * 剔除远月：DTE > dte_hi(默认 180) —— LEAPS 流动性差、IV 噪声大
    * 30/90 恒定期限加权（VIX 式插值）：
        gex30 = 在 (DTE, 净GEX) 平面上线性插值得 DTE=30 处
        gex90 = 同上，DTE=90 处
        gex_weighted = 0.5·gex30 + 0.5·gex90
      目标两侧点不足时用最近两点外推，并标记 extrap_lo / extrap_hi

Gamma Flip
----------
    在 S* ∈ [S·lo, S·hi]（默认 0.90~1.10，步长 0.5%）网格上，**每个点重算全链 Γ**
    （σ、K、T、OI 全部固定，只把 S 换成 S*），得到 net GEX(S*) 曲线；
    在曲线变号处线性插值求零点。允许存在多个零点，`flip_1` 取**离现价最近**的那个。

公开 API
--------
    prepare_chain(hist_df)                     # 规范化期权链缓存
    build_legs(chain, date, S, ...)             # 单日有效腿 + 每腿 gex
    net_gex_at(legs, S, ...)                    # 任意 S 处重算全链净 GEX
    gex_curve(legs, S, ...)                     # 曲线扫描 -> (s_arr, y)
    find_flips(s_arr, y, S)                     # 全部零点，最近者优先
    weighted_gex(legs, S, ...)                  # 30/90 插值加权
    daily_snapshot(chain, date, S, ...)         # 单日全量指标（含 flip / dist）
    percentile_rank(hist_values, value)         # 分位排名 0~100

单位约定：本模块所有 gex_* 返回值均为 **亿元**（已 ÷1e8）。
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import math
import datetime as _dt
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ 常量
SQRT2 = math.sqrt(2.0)
INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

MULT = 10000.0          # 科创50ETF 期权合约乘数：10000 股/张
IV_LO, IV_HI = 0.02, 4.5  # 可接受 IV 区间，超出视为反解失败
SCALE = 1e8             # 元 -> 亿元

# 默认参数（全部可被调用方覆盖，模块内不出现任何"写死到某一天"的东西）
DEF_R = 0.02            # 无风险利率（10Y 国债近似；与 opt_gex_flip.py 一致）
DEF_Q = 0.0             # 股息率
DEF_DTE_LO = 30         # 剔除近月
DEF_DTE_HI = 180        # 剔除超远月
DEF_TARGETS = (30, 90)  # 恒定期限目标
DEF_WEIGHTS = (0.5, 0.5)
DEF_CURVE_LO = 0.90     # Flip 扫描下界（相对 S 的倍数）
DEF_CURVE_HI = 1.10     # Flip 扫描上界
DEF_CURVE_STEP = 0.005  # 0.5%


# ================================================================== BSM 基础
def npdf(x: float) -> float:
    """标准正态密度 φ(x)。"""
    return INV_SQRT_2PI * math.exp(-0.5 * x * x)


def ncdf(x: float) -> float:
    """标准正态分布函数 Φ(x)，用 erfc 实现避免精度损失。"""
    return 0.5 * math.erfc(-x / SQRT2)


def bs_price(S: float, K: float, T: float, vol: float,
             r: float, q: float, call: bool = True) -> float:
    """Black-Scholes-Merton 欧式期权理论价（连续股息率 q）。"""
    if T <= 0 or vol <= 0:
        return max(0.0, (S - K) if call else (K - S))
    sq = vol * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * vol * vol) * T) / sq
    d2 = d1 - sq
    if call:
        return S * math.exp(-q * T) * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2)
    return K * math.exp(-r * T) * ncdf(-d2) - S * math.exp(-q * T) * ncdf(-d1)


def implied_vol(price: float, S: float, K: float, T: float,
                r: float, q: float, call: bool = True,
                lo: float = 1e-4, hi: float = IV_HI,
                iters: int = 100) -> float:
    """由中价二分反解隐含波动率；失败返回 nan（不抛异常，便于批量过滤）。

    价格低于内在价值 98% 视为脏数据；区间外无解也返回 nan。
    """
    if (not np.isfinite(price)) or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return np.nan
    intr = max(0.0, (S - K) if call else (K - S))
    if price < intr * 0.98:
        return np.nan
    if bs_price(S, K, T, hi, r, q, call) < price:
        return np.nan
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, r, q, call) < price:
            lo = mid
        else:
            hi = mid
    v = 0.5 * (lo + hi)
    if not (IV_LO < v < IV_HI):
        return np.nan
    return v


def gamma_at(S: float, K: float, T: float, vol: float,
             r: float = DEF_R, q: float = DEF_Q) -> float:
    """单腿 Gamma（per share）Γ = e^{−qT} φ(d1) / (S σ √T)。

    Gamma 对 r 不敏感（r 只通过 d1 里的小量进入），但为口径完整仍保留。
    """
    if T <= 0 or vol <= 0 or S <= 0 or K <= 0:
        return 0.0
    sq = vol * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * vol * vol) * T) / sq
    return math.exp(-q * T) * npdf(d1) / (S * sq)


# ================================================================== 数据整形
def prepare_chain(hist_df: pd.DataFrame) -> pd.DataFrame:
    """规范化期权链缓存 DataFrame（幂等，可重复调用）。

    期望列：trade_date, K, cp, maturity, close, settle, vol, oi
    输出附加列：mid（close>0 用 close，否则 settle）、call(bool)
    同时剔除 mid<=0 / K<=0 / oi<=0 的脏腿。

    入参 `hist_df` 不会被就地改写。
    """
    d = hist_df.copy()
    d['trade_date'] = pd.to_numeric(d['trade_date'], errors='coerce').astype('Int64')
    # maturity 允许 20261023 整数 / 字符串 / datetime
    if not np.issubdtype(d['maturity'].dtype, np.datetime64):
        d['maturity'] = pd.to_datetime(
            d['maturity'].astype(str).str.replace(r'\.0$', '', regex=True).str[:8],
            format='%Y%m%d', errors='coerce')
    for c in ('close', 'settle', 'vol', 'oi', 'K'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d['mid'] = d['close'].where(d['close'] > 0, d['settle'])
    d['call'] = d['cp'].astype(str).str.upper().str.startswith('C')
    d = d[(d['mid'] > 0) & (d['K'] > 0) & (d['oi'] > 0)
          & d['trade_date'].notna() & d['maturity'].notna()]
    return d.reset_index(drop=True)


def _to_date(x) -> _dt.date:
    """20261023 / '20261023' / datetime -> datetime.date"""
    if isinstance(x, _dt.date) and not isinstance(x, _dt.datetime):
        return x
    if isinstance(x, _dt.datetime):
        return x.date()
    s = str(int(x))
    return _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ================================================================== 单日腿表
def build_legs(chain: pd.DataFrame, date: int, S: float,
               r: float = DEF_R, q: float = DEF_Q,
               dte_lo: int = DEF_DTE_LO, dte_hi: int = DEF_DTE_HI) -> Tuple[pd.DataFrame, Dict]:
    """构造某个交易日的「有效腿表」。

    Parameters
    ----------
    chain : prepare_chain() 的输出（已规范化的全量缓存）
    date  : 交易日 YYYYMMDD（int）
    S     : 基准标的价（当日 ETF 收盘），用于反解 IV & 计算 Γ
    r, q  : 无风险利率 / 股息率
    dte_lo, dte_hi : DTE 过滤区间（闭区间）

    Returns
    -------
    legs : DataFrame，列
        K, call, maturity, dte, T, iv, oi, sign, gamma, gex, gex_abs
        gex 单位亿元，已含 Call+/Put− 符号
    meta : dict 各过滤阶段剩余条数（n_raw/n_dte/n_mid/n_iv），用于质量诊断
    """
    d = chain[chain['trade_date'] == int(date)]
    if d.empty:
        raise ValueError(f'期权链快照为空: {date}')
    d0 = _to_date(date)
    d = d.assign(dte=[(_to_date(m) - d0).days for m in d['maturity']])
    n_raw = len(d)
    d = d[(d['dte'] >= dte_lo) & (d['dte'] <= dte_hi)]
    n_dte = len(d)
    if d.empty:
        return pd.DataFrame(columns=['K', 'call', 'maturity', 'dte', 'T', 'iv',
                                     'oi', 'sign', 'gamma', 'gex', 'gex_abs']), \
            dict(n_raw=n_raw, n_dte=0, n_mid=0, n_iv=0)

    d = d.assign(T=d['dte'] / 365.0)
    d = d.assign(iv=[implied_vol(row.mid, S, row.K, row.T, r, q, row.call)
                     for row in d.itertuples()])
    n_mid = len(d)
    d = d[np.isfinite(d['iv'])]
    n_iv = len(d)
    if d.empty:
        return d.assign(sign=[], gamma=[], gex=[], gex_abs=[]), \
            dict(n_raw=n_raw, n_dte=n_dte, n_mid=n_mid, n_iv=0)

    d = d.assign(sign=np.where(d['call'], 1.0, -1.0))
    d = d.assign(gamma=[gamma_at(S, row.K, row.T, row.iv, r, q)
                        for row in d.itertuples()])
    # GEX_i = sign · Γ · OI · MULT · S² · 0.01   [元/1%] → 亿元
    unit = MULT * S * S * 0.01 / SCALE
    d = d.assign(gex=d['sign'] * d['gamma'] * d['oi'] * unit)
    d = d.assign(gex_abs=d['gamma'] * d['oi'] * unit)
    return d.reset_index(drop=True), dict(n_raw=n_raw, n_dte=n_dte,
                                          n_mid=n_mid, n_iv=n_iv)


# ================================================================== 曲线 / Flip
def net_gex_at(legs: pd.DataFrame, S_star: float,
               r: float = DEF_R, q: float = DEF_Q,
               mult: float = MULT) -> float:
    """在任意 S* 处重算全链 Γ 后的净 GEX（亿元/1%）。

    σ、K、T、OI 均取自 legs（即当日快照），只有 S 被替换成 S_star。
    这是 Gamma Flip 扫描的核心：把"标的价格"当作自变量。
    """
    if legs.empty:
        return 0.0
    unit = mult * S_star * S_star * 0.01 / SCALE
    tot = 0.0
    for row in legs.itertuples():
        tot += row.sign * gamma_at(S_star, row.K, row.T, row.iv, r, q) * row.oi
    return tot * unit


def gex_curve(legs: pd.DataFrame, S: float,
              r: float = DEF_R, q: float = DEF_Q,
              lo: float = DEF_CURVE_LO, hi: float = DEF_CURVE_HI,
              step: float = DEF_CURVE_STEP) -> Tuple[np.ndarray, np.ndarray]:
    """扫描 S* 网格上的净 GEX 曲线。

    Returns (s_arr, y)：s_arr 为绝对价格网格（41 点 @ 0.90~1.10 步长 0.5%），
    y 为对应净 GEX（亿元）。调用方可用 np.interp 反查任意 S 处数值。

    实现上对 (S*, leg) 做 numpy 广播（等价于逐点调用 net_gex_at，但快 ~100 倍：
    单日 41×N 的 Γ 重算从 ~30ms 降到 <1ms，全历史 243 天可在 1s 内跑完）。
    """
    n = int(round((hi - lo) / step)) + 1
    s_arr = np.round(np.linspace(S * lo, S * hi, n), 6)
    if legs.empty:
        return s_arr, np.zeros(len(s_arr))
    K = legs['K'].to_numpy(float)
    T = legs['T'].to_numpy(float)
    iv = legs['iv'].to_numpy(float)
    oi = legs['oi'].to_numpy(float)
    sign = legs['sign'].to_numpy(float)
    sq = iv * np.sqrt(T)                                   # (L,)
    s2 = s_arr[:, None]                                    # (G,1)
    d1 = (np.log(s2 / K[None, :]) + (r - q + 0.5 * iv * iv)[None, :] * T[None, :]) / sq[None, :]
    gam = np.exp(-q * T)[None, :] * np.exp(-0.5 * d1 * d1) * INV_SQRT_2PI / (s2 * sq[None, :])
    contrib = (sign * oi)[None, :] * gam                   # (G,L) 单位：股/元
    y = contrib.sum(axis=1) * (MULT * s_arr * s_arr * 0.01 / SCALE)
    return s_arr, y


def _zeros(s_arr: np.ndarray, y: np.ndarray) -> List[Tuple[float, float]]:
    """线性插值找所有零点，返回 [(S*, 局部斜率), ...]（按 S* 升序）。"""
    zs: List[Tuple[float, float]] = []
    for i in range(len(y) - 1):
        a, b = float(y[i]), float(y[i + 1])
        if a == 0.0:
            zs.append((float(s_arr[i]), b - a))
        elif a * b < 0:
            t = a / (a - b)                       # 线性插值权重
            zs.append((float(s_arr[i] + t * (s_arr[i + 1] - s_arr[i])), b - a))
    if len(y) and float(y[-1]) == 0.0:
        zs.append((float(s_arr[-1]), 0.0))
    out: List[Tuple[float, float]] = []
    for z, sl in zs:                              # 去掉数值重复的零点
        if not out or abs(z - out[-1][0]) > 1e-9:
            out.append((z, sl))
    return out


def find_flips(s_arr: np.ndarray, y: np.ndarray,
               S: float) -> Tuple[List[Dict], Optional[float]]:
    """找 Gamma Flip（净 GEX 曲线过零点）。

    Returns
    -------
    flips : [{'S': float, 'slope': float, 'dist_pct': float}, ...]
            按「离现价 |S*−S| 由近到远」排序
    flip_1: 离现价最近的零点价格；无零点返回 None
    """
    zs = _zeros(s_arr, y)
    zs = sorted(zs, key=lambda t: abs(t[0] - S))
    flips = [dict(S=float(z), slope=float(sl), dist_pct=float((S - z) / S * 100.0))
             for z, sl in zs]
    return flips, (flips[0]['S'] if flips else None)


# ================================================================== 30/90 加权
def _interp_at(pts: Sequence[Tuple[int, float]], target: float) -> Tuple[float, str]:
    """在 (DTE, 净GEX) 平面上取 target 处的值。

    mode ∈ {'exact','interp','extrap_lo','extrap_hi','single','none'}
    单点情形返回该点值并标 'single'（此时 30/90 相同，加权无意义）。
    """
    if not pts:
        return float('nan'), 'none'
    ds = [p[0] for p in pts]
    if target in ds:
        return float(pts[ds.index(target)][1]), 'exact'
    if len(pts) == 1:
        return float(pts[0][1]), 'single'
    if target < ds[0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
        mode = 'extrap_lo'
    elif target > ds[-1]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        mode = 'extrap_hi'
    else:
        mode = 'interp'
        x0 = y0 = x1 = y1 = None
        for i in range(len(pts) - 1):
            if ds[i] < target < ds[i + 1]:
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                break
        if x0 is None:                            # 理论上不会发生
            return float('nan'), 'none'
    if x1 == x0:
        return float(y0), mode
    return float(y0 + (y1 - y0) * (target - x0) / (x1 - x0)), mode


def weighted_gex(legs: pd.DataFrame, S: Optional[float] = None,
                 targets: Tuple[int, int] = DEF_TARGETS,
                 weights: Tuple[float, float] = DEF_WEIGHTS) -> Dict[str, object]:
    """30/90 恒定期限加权 GEX（VIX 式插值法）。

    步骤：
      1) 按 maturity 分组，各自 Σgex → 得到 (DTE, netGEX) 点集
      2) 在 DTE=30 / 90 处线性插值（不足两点则外推并打标记）
      3) gex_weighted = w0·gex30 + w1·gex90
      4) gex_mix_simple = 全部纳入月份的净 GEX 直接加总（对照口径）

    Returns dict(gex30, gex90, gex_weighted, gex_mix_simple,
                 n_legs, n_maturities, dte_list, interp_mode)
    """
    if legs.empty:
        return dict(gex30=float('nan'), gex90=float('nan'),
                    gex_weighted=float('nan'), gex_mix_simple=float('nan'),
                    n_legs=0, n_maturities=0, dte_list='', interp_mode='none/none')
    grp = legs.groupby('maturity').agg(dte=('dte', 'first'),
                                       net=('gex', 'sum'),
                                       n=('gex', 'size'))
    grp = grp.sort_values('dte')
    pts = [(int(r.dte), float(r.net)) for r in grp.itertuples()]
    g30, m30 = _interp_at(pts, float(targets[0]))
    g90, m90 = _interp_at(pts, float(targets[1]))
    gw = weights[0] * g30 + weights[1] * g90
    return dict(
        gex30=float(g30), gex90=float(g90), gex_weighted=float(gw),
        gex_mix_simple=float(sum(p[1] for p in pts)),
        n_legs=int(len(legs)), n_maturities=int(len(pts)),
        dte_list=','.join(str(p[0]) for p in pts),
        interp_mode=f'{m30}/{m90}',
    )


# ================================================================== 分位数
def percentile_rank(hist_values: Iterable[float], value: float) -> Tuple[float, int]:
    """当前值在历史序列中的百分位排名（0~100，越低=离 Flip 越近）。

    定义：pct = 100 × (#严格小于 value 的样本) / N
    （含当日样本自身；这样"历史最低"=0%，"历史最高"≈100%）

    Returns (pct, n_used)；序列为空返回 (nan, 0)。
    """
    arr = np.asarray([v for v in hist_values if v is not None and np.isfinite(v)],
                     dtype=float)
    if arr.size == 0:
        return float('nan'), 0
    less = int((arr < value).sum())
    return float(less / arr.size * 100.0), int(arr.size)


# ================================================================== 单日快照
def daily_snapshot(chain: pd.DataFrame, date: int, S: float,
                   r: float = DEF_R, q: float = DEF_Q,
                   dte_lo: int = DEF_DTE_LO, dte_hi: int = DEF_DTE_HI,
                   targets: Tuple[int, int] = DEF_TARGETS,
                   weights: Tuple[float, float] = DEF_WEIGHTS,
                   curve_lo: float = DEF_CURVE_LO, curve_hi: float = DEF_CURVE_HI,
                   curve_step: float = DEF_CURVE_STEP,
                   with_flip: bool = True) -> Dict[str, object]:
    """单日全量指标（序列 + Flip + 质量标记），供 CLI 与回测共用。

    with_flip=False 时跳过曲线扫描（只算序列时可省 ~90% 时间）。

    Returns dict:
        trade_date, S, gex30, gex90, gex_weighted, gex_mix_simple,
        n_legs, n_maturities, dte_list, interp_mode, quality,
        flip_1, flip_2, flip_count, dist, curve_min, curve_max, legs(DataFrame)
    """
    legs, meta = build_legs(chain, date, S, r=r, q=q, dte_lo=dte_lo, dte_hi=dte_hi)
    if legs.empty:
        return dict(trade_date=int(date), S=float(S), gex30=float('nan'),
                    gex90=float('nan'), gex_weighted=float('nan'),
                    gex_mix_simple=float('nan'), n_legs=0, n_maturities=0,
                    dte_list='', interp_mode='none/none', quality='empty',
                    flip_1=None, flip_2=None, flip_count=0, dist=float('nan'),
                    curve_min=float('nan'), curve_max=float('nan'),
                    legs=legs, meta=meta)
    w = weighted_gex(legs, S, targets=targets, weights=weights)
    out: Dict[str, object] = dict(trade_date=int(date), S=float(S), **w)
    # 质量：至少 2 个不同到期月，"插值"才有意义
    out['quality'] = 'ok' if w['n_maturities'] >= 2 else 'low'
    out['flip_1'] = None
    out['flip_2'] = None
    out['flip_count'] = 0
    out['dist'] = float('nan')
    out['curve_min'] = float('nan')
    out['curve_max'] = float('nan')
    if with_flip:
        s_arr, y = gex_curve(legs, S, r=r, q=q, lo=curve_lo, hi=curve_hi, step=curve_step)
        flips, f1 = find_flips(s_arr, y, S)
        out['flip_1'] = f1
        out['flip_2'] = flips[1]['S'] if len(flips) > 1 else None
        out['flip_count'] = len(flips)
        out['dist'] = float((S - f1) / S * 100.0) if f1 is not None else float('nan')
        out['curve_min'] = float(np.min(y))
        out['curve_max'] = float(np.max(y))
        out['curve'] = (s_arr, y)
    out['legs'] = legs
    out['meta'] = meta
    return out


# ================================================================== 自检
def _self_check() -> None:
    """ΣGEX_i == 曲线在 S*=S 处的值（浮点误差应 < 1e-9）。"""
    rng = np.random.default_rng(0)
    rows = []
    for k in (1.3, 1.4, 1.5):
        for cp in ('C', 'P'):
            rows.append(dict(trade_date=20261023, K=k, cp=cp, maturity=20261223,
                             close=0.05, settle=0.05, vol=1.0, oi=1000.0))
    chain = prepare_chain(pd.DataFrame(rows))
    legs, _ = build_legs(chain, 20261023, 1.42)
    a = float(legs['gex'].sum())
    b = net_gex_at(legs, 1.42)
    assert abs(a - b) < 1e-9, (a, b)
    print(f'self-check ok: ΣGEX_i={a:.12f}  curve(S)={b:.12f}  diff={a - b:.3e}')


if __name__ == '__main__':
    _self_check()
