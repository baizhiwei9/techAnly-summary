#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TechAnly.charts.phase_signals · 相变（临界慢化）指标算法库
=================================================================
本模块只做数学，不取数、不出图。是「相变监控」family 的算法唯一来源。

⚠️ 与 RMT 的区别（大佬 2026-11-19 明确区分过，别混）：
    RMT（charts/rmt.py）     = 随机矩阵理论，谱结构 / MP 边界 / 异常特征值
    相变（本模块）            = 临界慢化 / 序参量 / 恢复力 / 结构脆弱度
    两者都用到相关矩阵特征值，但判据完全不同。这里只管相变。

指标清单（编号沿用 workspace/bin/pws_compute.py 的 2026-11-03 定稿）
------------------------------------------------------------------
    M1  AR(1) 恢复力衰减        对 log价格−MA20 残差（或原始收益）算一阶自相关
    M2  Δσ 方差放大            σ20(t) − σ20(t−20)，与 σ20 自身的 20 日标准差比较
    M3  PSI 结构脆弱度          PSI = σ / λ²，λ 由自相关函数 log(ρ_k) ~ k 的斜率给出
    M4  λmax 截面突变           (已弃用，保留接口)
    M5  吸收比率 λ1/N           Kritzman 吸收比率（去当日截面共同因子口径）

双通道判定（2026-11-03 定稿）
------------------------------
    通道A 时序/波动（抓广谱顶，7月型）：M1 恢复力衰减 AND M2 波动放大
    通道B 截面/抱团（抓抱团顶，10月型）：M5 吸收比率 λ1/N 破 120 日 P90（且收盘 > MA60）

⚠️ 2026-11-19 2026 年样本实测结论（务必保留在代码里，别后人再重复踩）
    · 通道A（AR(1)+Δσ）在 2026 年样本上 **零信息甚至反向**：
      科创50 通道A 亮灯 20 日后平均 +1.6%，20 日跌>15% 概率 0%（基准 9%）。
      M1 AR(1) 一年 212 个交易日里亮 165 天（78%），等于常量。
    · 通道B（吸收比率）非重叠检验下同样归零：科创50 亮灯后 20 日 +0.3%，
      不亮 −0.4%，差 0.7pp。它本质是同步/滞后指标
      （Spearman ρ 与未来 20/40/60 日收益 = −0.47 / −0.68 / −0.67，随 horizon 单调增强）。
    · 结论：相变族可以当「结构状态描述」，**不能当预警**。
      顶部当天指标往往不亮（科创50 20260630 顶部 AR=0.210 < 阈值 0.218）。

用法
----
    from charts.phase_signals import compute_signals
    df = compute_signals(close, ret)          # 逐日指标表
    df = compute_signals(close, ret, abs_ratio=ar_series)
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np
import pandas as pd

__all__ = [
    "ar1_resilience",
    "ar1_alert",
    "sigma_delta",
    "dsig_alert",
    "ar_lambda",
    "psi_fragility",
    "psi_alert",
    "absorption_ratio_series",
    "absorption_alert",
    "channel_a",
    "channel_b",
    "dual_channel",
    "compute_signals",
    "PARAMS",
]

# ============================ 参数（与 pws_compute 默认值一致） ============================
PARAMS = dict(
    AR_THR=0.6,            # M1 阈值
    AR_DAYS=3,             # M1 连续天数（2026-11-03 从 5 收到 3）
    AR_WIN=20,             # AR(1) 拟合窗口
    SIG_WIN=20,            # σ 窗口
    SIG_SD_WIN=20,         # σ 的 σ 窗口
    DSIG_MULT=3.0,         # M2 阈值倍数（2026-11-03 从 2σ 收到 3σ，lift 0.9→1.2x）
    LAM_WIN=60,            # M3 拟合窗口
    LAM_KMAX=5,            # M3 最大滞后阶
    PSI_REF_WIN=120,       # M3 分位参考窗口
    PSI_PCT=90,            # M3 分位阈值
    PSI_MIN_OBS=60,
    ABS_WIN=20,            # M5 相关矩阵窗口
    ABS_REF=120,           # M5 分位参考窗口
    ABS_PCT=90,            # M5 分位阈值
    MA_FILTER=60,          # M5 方向约束（收盘须在 MA60 上）
)


# ===================== M1  AR(1) 恢复力衰减 =====================
def ar1_resilience(ret, close, mode: str = "resid", win: int = 20) -> np.ndarray:
    """一阶自相关。

    mode='resid'（默认，2026-11-03 定稿）：对 log(close) − MA20 的残差算 AR(1)
        含义是「价格偏离均线后的回归速度」，比直接对收益算更贴临界慢化直觉。
    mode='ret'：对日收益直接算。

    Returns:
        (n,) 数组，前 win+5 个为 NaN。
    """
    ret = np.asarray(ret, float)
    close = np.asarray(close, float)
    n = len(ret)
    out = np.full(n, np.nan)
    if mode == "resid":
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


def ar1_alert(ar, thr: float = 0.6, days: int = 3,
              pct: Optional[float] = None, ref: int = 250) -> np.ndarray:
    """M1 亮灯：连续 days 日 AR > thr（persist 口径）。

    注：原口径是「连续 days 日单调上升」，但 AR 贴近 1 后必然震荡，
    单调上升几乎不可能触发，2026-11-03 改为持续口径。
    pct 可选：额外要求 AR 处于过去 ref 日的 pct 分位以上。
    """
    ar = np.asarray(ar, float)
    n = len(ar)
    a = np.zeros(n, bool)
    for t in range(days - 1, n):
        if not (np.isfinite(ar[t]) and ar[t] > thr):
            continue
        seg = ar[t - days + 1:t + 1]
        if not np.all(np.isfinite(seg)) or not np.all(seg > thr):
            continue
        if pct is not None and t >= ref:
            w = ar[t - ref:t]
            w = w[np.isfinite(w)]
            if len(w) < ref * 0.5 or ar[t] < np.percentile(w, pct):
                continue
        a[t] = True
    return a


# ===================== M2  Δσ 方差放大 =====================
def sigma_delta(ret, win: int = 20, sd_win: int = 20):
    """返回 (σ, σ 的σ, Δσ)，Δσ = σ(t) − σ(t−win)。"""
    sig = pd.Series(np.asarray(ret, float)).rolling(win).std(ddof=0).to_numpy()
    sd = pd.Series(sig).rolling(sd_win).std(ddof=0).to_numpy()
    n = len(sig)
    dsig = np.full(n, np.nan)
    for t in range(win, n):
        if np.isfinite(sig[t]) and np.isfinite(sig[t - win]):
            dsig[t] = sig[t] - sig[t - win]
    return sig, sd, dsig


def dsig_alert(dsig, sd, mult: float = 3.0) -> np.ndarray:
    """M2 亮灯：Δσ > mult × σ_std。"""
    dsig = np.asarray(dsig, float)
    sd = np.asarray(sd, float)
    a = np.zeros(len(dsig), bool)
    for t in range(len(dsig)):
        if np.isfinite(dsig[t]) and np.isfinite(sd[t]) and sd[t] > 0:
            if dsig[t] > mult * sd[t]:
                a[t] = True
    return a


# ===================== M3  PSI 结构脆弱度 =====================
def ar_lambda(ret, win: int = 60, kmax: int = 5, mode: str = "drop") -> np.ndarray:
    """自相关衰减指数 λ：log(ρ_k) 对 k 做线性回归，取斜率的负值。

    mode='drop' 丢弃负 ρ_k（默认）| mode='abs' 取 |ρ_k|
    """
    ret = np.asarray(ret, float)
    n = len(ret)
    out = np.full(n, np.nan)
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
            a, b = w[:-k], w[k:]
            rho = ((a - mu) * (b - mu)).mean() / v
            if mode == "abs":
                rho = abs(rho)
            if rho > 1e-6:
                ks.append(k)
                logs.append(math.log(rho))
        if len(ks) < 3:
            continue
        lam = -np.polyfit(ks, logs, 1)[0]
        out[t] = lam if lam > 1e-6 else np.nan
    return out


def psi_fragility(sig, lam) -> np.ndarray:
    """PSI = σ / λ²。λ 越小（记忆衰减越慢）PSI 越大 → 结构越脆。"""
    sig = np.asarray(sig, float)
    lam = np.asarray(lam, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(np.isfinite(lam) & (lam > 0),
                        sig / np.square(np.where(lam > 0, lam, np.nan)), np.nan)


def psi_alert(psi, ref_win: int = 120, pct: float = 90, min_obs: int = 60):
    """M3 亮灯：PSI 破过去 ref_win 日 pct 分位。返回 (阈值序列, 布尔数组)。"""
    psi = np.asarray(psi, float)
    n = len(psi)
    thr = np.full(n, np.nan)
    a = np.zeros(n, bool)
    for t in range(ref_win, n):
        w = psi[t - ref_win:t]
        w = w[np.isfinite(w)]
        if len(w) >= min_obs:
            thr[t] = np.percentile(w, pct)
            if np.isfinite(psi[t]) and psi[t] > thr[t]:
                a[t] = True
    return thr, a


# ===================== M5  吸收比率 λ1/N =====================
def absorption_ratio_series(panel: pd.DataFrame, win: int = 20,
                            dedemean: bool = True,
                            max_members: int = 300, seed: int = 7) -> pd.Series:
    """逐日吸收比率 AR = λ1 / N（Kritzman）。

    Args:
        panel: index=YYYYMMDD 字符串日期，columns=股票代码，values=日收益。
        win: 滚动相关矩阵窗口（交易日）。
        dedemean: True = 先减当日截面均值（去掉市场共同因子）。
            ⚠️ 必须为 True，这是 2026-11-02 大佬定的本机口径：
               原始收益口径下 10 月顶只到 63~86 分位，去因子后 96~98 分位。

    Returns:
        pd.Series，index 与 panel 对齐，前 win-1 个为 NaN。
    """
    if panel is None or panel.empty:
        return pd.Series(dtype=float)
    sub = panel.copy()
    if sub.shape[1] > max_members:
        cols = sorted(np.random.default_rng(seed).choice(
            sub.columns.to_numpy(), max_members, replace=False))
        sub = sub[cols]
    idx = list(sub.index)
    vals = []
    for i in range(len(idx)):
        if i < win - 1:
            vals.append(np.nan)
            continue
        w = sub.iloc[i - win + 1:i + 1].dropna(axis=1, thresh=int(win * 0.95)).dropna()
        n = w.shape[1]
        if n < 15:
            vals.append(np.nan)
            continue
        X = w.sub(w.mean(axis=1), axis=0) if dedemean else w
        with np.errstate(invalid="ignore", divide="ignore"):
            C = np.corrcoef(X.values.T)
        C = np.nan_to_num(np.asarray(C, float), nan=0.0)
        C = np.clip((C + C.T) / 2, -1, 1)
        np.fill_diagonal(C, 1.0)
        vals.append(float(np.linalg.eigvalsh(C)[-1] / n))
    return pd.Series(vals, index=idx)


def absorption_alert(ar_series: pd.Series, dates: Sequence[str], close: Optional[np.ndarray] = None,
                     ref: int = 120, pct: float = 90, ma_filter: int = 60):
    """M5 亮灯：AR_dm 破过去 ref 日 pct 分位，且（给了 close 时）收盘须在 MA{ma_filter} 上方。

    方向约束是必需的（2026-11-03 实测）：暴跌途中个股同步下跌，λ1/N 同样飙升，
    不加约束会一路误报（创业板 7/28 起 AR 从 0.347 升到 0.45 全程亮灯）。

    Returns:
        (alert_int, ar_values, thresholds) 三个与 dates 等长的数组。稀疏观测点会 forward-fill。
    """
    n = len(dates)
    a = np.zeros(n, int)
    v = np.full(n, np.nan)
    thr = np.full(n, np.nan)
    if ar_series is None or len(ar_series) == 0:
        return a, v, thr

    obs = list(ar_series.index)
    av = ar_series.to_dict()
    j = -1
    for i, d in enumerate(dates):
        while j + 1 < len(obs) and obs[j + 1] <= d:
            j += 1
        if j >= 0:
            v[i] = av.get(obs[j], np.nan)

    # 滚动分位（用已对齐的 v，只在有观测的日期上算）
    for i in range(len(v)):
        if not np.isfinite(v[i]):
            continue
        w = v[max(0, i - ref):i]
        w = w[np.isfinite(w)]
        if len(w) >= min(ref * 0.5, 30):
            thr[i] = np.percentile(w, pct)

    ma = None
    if close is not None and ma_filter > 0:
        ma = pd.Series(np.asarray(close, float)).rolling(ma_filter).mean().to_numpy()

    for i in range(len(v)):
        if np.isfinite(v[i]) and np.isfinite(thr[i]) and v[i] > thr[i]:
            if ma is None or (np.isfinite(ma[i]) and close[i] > ma[i]):
                a[i] = 1
    return a, v, thr


# ===================== 双通道判定 =====================
def channel_a(m1_alert_arr, m2_alert_arr) -> np.ndarray:
    """通道A：M1 恢复力衰减 AND M2 波动放大 → 抓广谱顶（7月型）。"""
    return (np.asarray(m1_alert_arr, bool) & np.asarray(m2_alert_arr, bool)).astype(int)


def channel_b(m5_alert_arr) -> np.ndarray:
    """通道B：M5 吸收比率破 P90（且已在 absorption_alert 里加了 MA60 方向约束）。"""
    return np.asarray(m5_alert_arr, int).astype(int)


def dual_channel(ch_a, ch_b):
    """返回 (dual, level)：level 2=A+B 共振（系统级），1=单道（预警），0=无。"""
    a = np.asarray(ch_a, bool)
    b = np.asarray(ch_b, bool)
    dual = (a | b).astype(int)
    lv = np.where(a & b, 2, np.where(a | b, 1, 0)).astype(int)
    return dual, lv


# ===================== 一站式 =====================
def compute_signals(close, ret, dates=None,
                    abs_ratio: Optional[pd.Series] = None,
                    ar_mode: str = "resid",
                    lam_mode: str = "drop",
                    dedemean_panel: Optional[pd.DataFrame] = None,
                    **overrides) -> pd.DataFrame:
    """把 M1/M2/M3/M5 一次算齐，返回逐日指标表。

    Args:
        close: (n,) 收盘价
        ret: (n,) 日收益
        dates: YYYYMMDD 字符串列表；不给则用 0..n-1
        abs_ratio: 已算好的吸收比率 Series（可与 dates index 对齐）；
                   若给 dedemean_panel 而没给 abs_ratio，会自动算。
        overrides: 覆盖 PARAMS 里的任意参数。

    Returns:
        DataFrame，列：
            date, close, ret,
            m1_ar, m1_alert,
            m2_sig, m2_sd, m2_dsig, m2_alert,
            m3_lam, m3_psi, m3_psi_thr, m3_alert,
            m5_ar, m5_thr, m5_alert,
            chA, chB, dual, dual_lv,
            votes（M1+M2+M3+M5 票数）
    """
    P = dict(PARAMS)
    P.update(overrides)

    close = np.asarray(close, float)
    ret = np.asarray(ret, float)
    n = len(close)
    dates = list(dates) if dates is not None else [str(i) for i in range(n)]

    ar = ar1_resilience(ret, close, ar_mode, P["AR_WIN"])
    ar_a = ar1_alert(ar, P["AR_THR"], P["AR_DAYS"])

    sig, sd, dsig = sigma_delta(ret, P["SIG_WIN"], P["SIG_SD_WIN"])
    ds_a = dsig_alert(dsig, sd, P["DSIG_MULT"])

    lam = ar_lambda(ret, P["LAM_WIN"], P["LAM_KMAX"], lam_mode)
    psi = psi_fragility(sig, lam)
    psi_thr, psi_a = psi_alert(psi, P["PSI_REF_WIN"], P["PSI_PCT"], P["PSI_MIN_OBS"])

    if abs_ratio is None and dedemean_panel is not None:
        abs_ratio = absorption_ratio_series(dedemean_panel, P["ABS_WIN"])
    m5_a, m5_v, m5_thr = absorption_alert(
        abs_ratio, dates, close, P["ABS_REF"], P["ABS_PCT"], P["MA_FILTER"])

    ch_a = channel_a(ar_a, ds_a)
    ch_b = channel_b(m5_a)
    dual, lv = dual_channel(ch_a, ch_b)

    votes = (ar_a.astype(int) + ds_a.astype(int) + psi_a.astype(int) + np.asarray(m5_a, int))

    return pd.DataFrame({
        "date": dates, "close": close, "ret": ret,
        "m1_ar": ar, "m1_alert": ar_a.astype(int),
        "m2_sig": sig, "m2_sd": sd, "m2_dsig": dsig, "m2_alert": ds_a.astype(int),
        "m3_lam": lam, "m3_psi": psi, "m3_psi_thr": psi_thr, "m3_alert": psi_a.astype(int),
        "m5_ar": m5_v, "m5_thr": m5_thr, "m5_alert": m5_a,
        "chA": ch_a, "chB": ch_b, "dual": dual, "dual_lv": lv,
        "votes": votes,
    })
