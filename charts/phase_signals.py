#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TechAnly.charts.phase_signals · 相变（临界慢化）指标算法库
=================================================================
本模块只做数学，不取数、不出图。是「相变监控」family 的算法**唯一来源**。

⚠️ 与 RMT 的区别（大佬 2026-11-19 明确区分过，别混）：
    RMT（charts/rmt.py）     = 随机矩阵理论，谱结构 / MP 边界 / 异常特征值
    相变（本模块）            = 临界慢化 / 序参量 / 恢复力 / 结构脆弱度
    两者都用到相关矩阵特征值，但判据完全不同。这里只管相变。

指标清单（编号/列名严格沿用 workspace/bin/pws_compute.py 的 2026-11-03 定稿）
------------------------------------------------------------------
  时序口径
    M1  AR(1) 恢复力衰减        对日收益（或 log价格−MA20 残差）算一阶自相关
    M2  Δσ 方差放大            σ20(t) − σ20(t−20)，与 σ20 自身的 20 日标准差比较
    M3  PSI 结构脆弱度          PSI = σ / λ²，λ 由自相关函数 log(ρ_k) ~ k 的斜率给出
  截面口径
    λmax（df 列 m4_lmax / m4_alert）   相关矩阵最大特征值，周环比 > 20% 亮
    AR_dm（df 列 m5_ar / m5_alert）    吸收比率 λ1/N，先减当日截面共同因子
  α Hill 指数（df 无列）：2026-11-03 大佬下线，二十年零触发，m4_alpha 仅留接口

双通道判定（2026-11-03 定稿）
------------------------------
    通道A 时序/波动（抓广谱顶，7月型）：M1 恢复力衰减 AND M2 波动放大
    通道B 截面/抱团（抓抱团顶，10月型）：吸收比率破 120 日 P90（且收盘 > MA60）
    系统级：5 个指标（M1/M2/M3/λmax/AR_dm）里 ≥3 个同时亮

⚠️ 2026-11-19 2026 年样本实测结论（务必保留在代码里，别后人再重复踩）
    · 通道A（AR(1)+Δσ）在 2026 年样本上 **零信息甚至反向**：
      科创50 通道A 亮灯 20 日后平均 +1.6%，20 日跌>15% 概率 0%（基准 9%）。
      M1 AR(1) 一年 212 个交易日里亮 165 天（78%），等于常量。
    · 通道B（吸收比率）非重叠检验下同样归零：科创50 亮灯后 20 日 +0.3%，
      不亮 −0.4%，差 0.7pp。它本质是同步/滞后指标
      （Spearman ρ 与未来 20/40/60 日收益 = −0.47 / −0.68 / −0.67，随 horizon 单调增强）。
    · 结论：相变族可以当「结构状态描述」，**不能当预警**。
      顶部当天指标往往不亮（科创50 20260630 顶部 AR=0.210 < 阈值 0.218）。

⚠️ 口径统一（2026-11-20 大佬要求：本地指标并进来，别再各算各的）
------------------------------------------------------------------
    本模块 = workspace/bin/pws_compute.py 的**逐字迁移 + ComputeReport/compute 包装**。
    2026-11-19 那版 phase_signals 有两处与本机口径不一致，已按本机定稿修正：
      ① M1 默认口径改为 mode='ret'（本机 2026-11-03 定稿 / pws_main --ar ret），
         原版写死 'resid'；'resid' 仍可显式传。
      ② M1 默认 persist 持续口径（pws_compute AR_PERSIST=True），
         原版硬编码成「单调上升」——AR 贴近 1 后必然震荡，单调上升几乎不可能触发。
      ③ M5 原版直接拿掉 M5=λmax 那一层、只留吸收比率，编号与 pws_compute 对不上。
         现在补回真实 M5=λ1/N（相关矩阵**不**去因子），M6=去因子吸收比率，
         与 pws_compute 的 m4_lmax / m5_ar 列语义严格一致。
      ④ 新增 xsec_lmax_calc / xsec_alert / xsec_align 三个截面函数（原在 pws_compute），
         以及 compute() / snapshot()（原 pws_compute 主函数 + 快照）。
         绘图侧只 import，不再自己复制一遍算法。

用法
----
    from charts.phase_signals import compute_signals
    df = compute_signals(close, ret)          # 逐日指标表（不取数、不出图）

    # 与原 workspace/bin/pws_compute.compute 等价（含 Result 包装 / 快照）
    from charts.phase_signals import compute, snapshot
    r = compute('000852.SH', '中证1000', px, xsec_lmax=lm, abs_ratio=ar6)
    print(snapshot(r))
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd

__all__ = [
    # 参数
    "PARAMS",
    # M1
    "ar1_resilience", "ar1_alert", "m1_ar1", "m1_alert",
    # M2
    "sigma_delta", "dsig_alert", "m2_dsig", "m2_alert",
    # M3
    "ar_lambda", "psi_fragility", "psi_alert", "m3_lambda", "m3_psi", "m3_alert",
    # M4（已下线，保留接口）
    "m4_alpha", "m4_alert",
    # M5 截面 λmax
    "xsec_lmax_calc", "xsec_alert", "xsec_align",
    # M6 吸收比率
    "absorption_ratio_series", "absorption_alert",
    # 双通道
    "channel_a", "channel_b", "dual_channel",
    # 一站式
    "compute_signals", "compute", "snapshot", "Result",
]

# ============================ 参数（与 pws_compute 默认值一致） ============================
PARAMS = dict(
    AR_THR=0.6,            # M1 阈值
    AR_DAYS=3,             # M1 连续天数（2026-11-03 从 5 收到 3）
    AR_WIN=20,             # AR(1) 拟合窗口
    AR_PERSIST=True,       # M1 持续口径（AR 贴近 1 后必然震荡，单调上升不可能）
    SIG_WIN=20,            # σ 窗口
    SIG_SD_WIN=20,         # σ 的 σ 窗口
    DSIG_MULT=3.0,         # M2 阈值倍数（2026-11-03 从 2σ 收到 3σ，lift 0.9→1.2x）
    LAM_WIN=60,            # M3 拟合窗口
    LAM_KMAX=5,            # M3 最大滞后阶
    PSI_REF_WIN=120,       # M3 分位参考窗口
    PSI_PCT=90,            # M3 分位阈值
    PSI_MIN_OBS=60,
    XSEC_WIN=20,           # M5 相关矩阵窗口
    XSEC_STEP=5,           # M5 观测步长
    XSEC_WOW=0.20,         # M5 亮灯：周环比 > 20%
    ABS_WIN=20,            # M6 相关矩阵窗口
    ABS_REF=120,           # M6 分位参考窗口
    ABS_PCT=90,            # M6 分位阈值
    MA_FILTER=60,          # M6 方向约束（收盘须在 MA60 上）
    ALPHA_THR=1.8,         # M4（下线，仅记录）
    ALPHA_KMIN=50,
    ALPHA_SIG_MULT=2.5,
    ALPHA_SIG_HORIZON=5,
    ENABLE_M4=False,       # α 永久失效（二十年零触发），不计票
    ALERT_VOTES=3,         # 可用指标里 ≥3 个同时亮 → 系统级
)

# 计入票数的指标（M4 不在内）
VOTE_KEYS = ("m1_alert", "m2_alert", "m3_alert", "m4_alert", "m5_alert")


# ===================== M1  AR(1) 恢复力衰减 =====================
def ar1_resilience(ret, close, mode: str = "ret", win: int = 20) -> np.ndarray:
    """一阶自相关。

    mode='ret'（默认，本机 2026-11-03 定稿，pws_main --ar ret）：对日收益直接算。
    mode='resid'：对 log(close) − MA20 的残差算 AR(1)
        含义是「价格偏离均线后的回归速度」，更贴临界慢化直觉，但本机默认不用它。

    Returns:
        (n,) 数组，前 win(+5) 个为 NaN。
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


def ar1_alert(ar, thr: float = 0.6, days: int = 3, persist: bool = True,
              pct: Optional[float] = None, ref: int = 250) -> np.ndarray:
    """M1 亮灯。

    persist=True（默认，本机口径）：连续 days 日 AR > thr
    persist=False：原口径「连续 days 日单调上升」——AR 贴近 1 后必然震荡，
                   单调上升几乎不可能触发，只作历史对照保留。
    pct 可选：额外要求 AR 处于过去 ref 日的 pct 分位以上。
    """
    ar = np.asarray(ar, float)
    n = len(ar)
    a = np.zeros(n, bool)
    for t in range(days - 1, n):
        if not (np.isfinite(ar[t]) and ar[t] > thr):
            continue
        seg = ar[t - days + 1:t + 1]
        if not np.all(np.isfinite(seg)):
            continue
        ok = np.all(seg > thr) if persist else np.all(np.diff(seg) > 0)
        if not ok:
            continue
        if pct is not None and t >= ref:
            w = ar[t - ref:t]
            w = w[np.isfinite(w)]
            if len(w) < ref * 0.5 or ar[t] < np.percentile(w, pct):
                continue
        a[t] = True
    return a


# 本机 pws_compute 旧名（逐字兼容）
m1_ar1 = ar1_resilience
m1_alert = ar1_alert


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


m2_dsig = sigma_delta
m2_alert = dsig_alert


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


m3_lambda = ar_lambda
m3_psi = psi_fragility
m3_alert = psi_alert


# ============ M4 α Hill 指数（2026-11-03 大佬要求彻底下线，仅留接口可复算） ============
def _drawdowns(close):
    """局部峰谷配对，返回每段回撤幅度（正）。"""
    n = len(close)
    if n < 5:
        return []
    ext = []
    for i in range(1, n - 1):
        if close[i] >= close[i - 1] and close[i] > close[i + 1]:
            ext.append((i, "P"))
        elif close[i] <= close[i - 1] and close[i] < close[i + 1]:
            ext.append((i, "T"))
    res, last_p = [], None
    for i, typ in ext:
        if typ == "P":
            last_p = i
        elif typ == "T" and last_p is not None:
            pk, tr = close[last_p], close[i]
            if pk > 0 and tr < pk:
                s = (pk - tr) / pk
                if s > 0:
                    res.append(s)
            last_p = None
    return res


def m4_alpha(close, ret, k_mode: str = "expand", k_min: int = 50, rwin: int = 500,
             sig_mult: float = 2.5, sig_h: int = 5):
    """M4 Hill 幂律指数 α。**已下线**（二十年零触发），仅保留以便复算/对照。"""
    n = len(close)
    out = np.full(n, np.nan)
    kcnt = np.zeros(n, int)
    uu = np.full(n, np.nan)
    for t in range(60, n):
        lo = 0 if k_mode == "expand" else max(0, t + 1 - rwin)
        c = close[lo:t + 1]
        dd = _drawdowns(c)
        if not dd:
            continue
        s = np.array(dd, float)
        r = ret[lo + 1:t + 1]
        r = r[np.isfinite(r)]
        if len(r) < 20:
            continue
        sig = r.std(ddof=0)
        u = max(np.percentile(s, 95), sig_mult * sig * math.sqrt(sig_h))
        tail = s[s > u]
        k = len(tail)
        kcnt[t] = k
        uu[t] = u
        if k < k_min or u <= 0:
            continue
        ml = np.mean(np.log(tail / u))
        if ml > 1e-9:
            out[t] = 1.0 / ml
    return out, kcnt, uu


def m4_alert(alpha, thr: float = 1.8):
    return np.isfinite(alpha) & (alpha < thr)


# ===================== M5  截面最大特征值 λ1/N =====================
def xsec_lmax_calc(panel, win: int = 20, step: int = 5) -> pd.Series:
    """截面 λ1/N 时间序列（相关矩阵**不**去共同因子，本机 pws_xsec 口径）。

    Args:
        panel: index=YYYYMMDD 字符串日期，columns=股票代码，values=日收益(pct_chg)。
        win: 滚动窗口
        step: 观测步长（稀疏采样，M5 只用周环比）

    Returns:
        pd.Series（稀疏观测点）
    """
    out = {}
    idx = list(panel.index)
    for i in range(win, len(idx), step):
        sub = panel.iloc[i - win + 1:i + 1]
        sub = sub.dropna(axis=1, thresh=int(win * 0.95)).dropna()
        if sub.shape[1] < 10:
            continue
        C = np.corrcoef(sub.to_numpy(float).T)
        C = np.nan_to_num(C, nan=0.0)
        out[idx[i]] = float(np.linalg.eigvalsh(C)[-1])
    return pd.Series(out).sort_index()


def xsec_alert(lmax, wow_thr: float = 0.20):
    """M5 亮灯：λmax 周环比（相邻观测点变化）> wow_thr。返回 (wow, 布尔数组)。"""
    wow = lmax.pct_change()
    a = (wow > wow_thr).fillna(False)
    return wow, a.astype(int)


def xsec_align(df_dates, lmax, alert):
    """把稀疏的 λmax 观测点对齐到每个交易日（取最近一个已观测点）。"""
    if lmax is None or len(lmax) == 0:
        return np.zeros(len(df_dates), int), np.full(len(df_dates), np.nan)
    obs = list(lmax.index)
    av = alert.to_dict()
    lv = lmax.to_dict()
    A, L = [], []
    j = -1
    for d in df_dates:
        while j + 1 < len(obs) and obs[j + 1] <= d:
            j += 1
        if j >= 0:
            A.append(int(av.get(obs[j], 0)))
            L.append(lv.get(obs[j]))
        else:
            A.append(0)
            L.append(np.nan)
    return np.array(A), np.array(L, float)


# ===================== M6  吸收比率 AR_dm = λ1/N（去共同因子） =====================
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
        max_members: 池子抽样上限（速度）。

    Returns:
        pd.Series，index 与 panel 对齐，前 win-1 个为 NaN。
    """
    if panel is None or panel.empty:
        return pd.Series(dtype=float)
    sub = panel.copy()
    if max_members and sub.shape[1] > max_members:
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
                     ref: int = 120, pct: float = 90, ma_filter: int = 60,
                     min_obs: int = 60):
    """M6 亮灯：AR_dm 破过去 ref 日 pct 分位，且（给了 close 时）收盘须在 MA{ma_filter} 上方。

    方向约束是必需的（2026-11-03 实测）：暴跌途中个股同步下跌，λ1/N 同样飙升，
    不加约束会一路误报（创业板 7/28 起 AR 从 0.347 升到 0.45 全程亮灯）。

    ⚠️ 口径与 workspace/bin/pws_absratio.alert 严格一致（2026-11-20 对齐）：
        滚动分位从 t >= ref 才开始（之前一律 NaN/不亮），窗口内有效观测须 >= min_obs(60)。

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

    # 滚动分位（用已对齐的 v；起点与 pws_absratio 一致：t >= ref）
    for i in range(n):
        if not np.isfinite(v[i]):
            continue
        if i < ref:
            continue
        w = v[max(0, i - ref):i]
        w = w[np.isfinite(w)]
        if len(w) >= min_obs:
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
    """通道B：M6 吸收比率破 P90（且已在 absorption_alert 里加了 MA60 方向约束）。"""
    return np.asarray(m5_alert_arr, int).astype(int)


def dual_channel(ch_a, ch_b):
    """返回 (dual, level)：level 2=A+B 共振（系统级），1=单道（预警），0=无。"""
    a = np.asarray(ch_a, bool)
    b = np.asarray(ch_b, bool)
    dual = (a | b).astype(int)
    lv = np.where(a & b, 2, np.where(a | b, 1, 0)).astype(int)
    return dual, lv


# ===================== 一站式（轻量：只给价序列） =====================
def compute_signals(close, ret, dates=None,
                    abs_ratio: Optional[pd.Series] = None,
                    ar_mode: str = "ret",
                    lam_mode: str = "drop",
                    dedemean_panel: Optional[pd.DataFrame] = None,
                    **overrides) -> pd.DataFrame:
    """把 M1/M2/M3/M6 一次算齐，返回逐日指标表（不取数、不出图）。

    Args:
        close: (n,) 收盘价
        ret: (n,) 日收益
        dates: YYYYMMDD 字符串列表；不给则用 0..n-1
        abs_ratio: 已算好的吸收比率 Series（稀疏观测点，index 为 YYYYMMDD）；
                   若给 dedemean_panel 而没给 abs_ratio，会自动算。
        ar_mode: 'ret'（默认，本机口径）| 'resid'
        overrides: 覆盖 PARAMS 里的任意参数。

    Returns:
        DataFrame，列：
            date, close, ret,
            m1_ar, m1_alert,
            m2_sig, m2_sd, m2_dsig, m2_alert,
            m3_lam, m3_psi, m3_psi_thr, m3_alert,
            m5_ar, m5_thr, m5_alert,
            chA, chB, dual, dual_lv,
            votes（M1+M2+M3+M6 票数）
    """
    P = dict(PARAMS)
    P.update(overrides)

    close = np.asarray(close, float)
    ret = np.asarray(ret, float)
    n = len(close)
    dates = list(dates) if dates is not None else [str(i) for i in range(n)]

    ar = ar1_resilience(ret, close, ar_mode, P["AR_WIN"])
    ar_a = ar1_alert(ar, P["AR_THR"], P["AR_DAYS"], P["AR_PERSIST"])

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


# ===================== 完整版（等价原 pws_compute.compute） =====================
@dataclass
class Result:
    """相变计算结果：逐日指标表 + 参数快照。"""
    code: str
    name: str
    df: pd.DataFrame
    params: dict


def compute(code, name, px, panel=None, xsec_lmax=None,
            ar_mode="ret", lam_mode="drop", k_mode="expand",
            k_min=50, params_override=None,
            ar_days=3, ar_persist=True, ar_pct=None, dsig_mult=None,
            abs_ratio=None):
    """算指标 → 返回 Result（与 workspace/bin/pws_compute.compute 等价）。

    Args:
        px: DataFrame，需含 close / ret / trade_date 三列（按日期升序）
        panel: 全市场收益面板（index=YYYYMMDD, columns=code），算 M5 用；
               也可直接给 xsec_lmax 省掉这一步
        xsec_lmax: 已算好的稀疏 λmax Series（pws_xsec.load_lmax 同口径）
        abs_ratio: 已算好的吸收比率 Series（pws_absratio.load_ar 同口径）
        ar_mode: 'ret'（本机定稿）| 'resid'
        lam_mode: 'drop' | 'abs'
        k_mode / k_min: α 口径（M4 已下线，仅记录参数）

    五指标编号（2026-11-03 定稿）：
        M1 = AR(1) 恢复力衰减   M2 = Δσ 波动放大   M3 = PSI 结构脆弱度
        M4 = α Hill（下线不计票） M5 = 截面 λmax（周环比 > 20%）
        M6 = 吸收比率 λ1/N（本机 struct_tag 口径，df 列名沿用 m5_ar）
    """
    if px is None or len(px) < 100:
        return None
    close = px.close.to_numpy(float)
    ret = px.ret.to_numpy(float)
    dates = px.trade_date.tolist()
    n = len(close)
    P = dict(PARAMS)
    P.update(dict(ar_mode=ar_mode, lam_mode=lam_mode, k_mode=k_mode, k_min=k_min))
    if params_override:
        P.update(params_override)

    ar = ar1_resilience(ret, close, ar_mode, P["AR_WIN"])
    ar_a = ar1_alert(ar, P["AR_THR"], ar_days, ar_persist, ar_pct)
    sig, sd, dsig = sigma_delta(ret, P["SIG_WIN"], P["SIG_SD_WIN"])
    var_a = dsig_alert(dsig, sd, dsig_mult if dsig_mult is not None else P["DSIG_MULT"])
    lam = ar_lambda(ret, P["LAM_WIN"], P["LAM_KMAX"], lam_mode)
    psi = psi_fragility(sig, lam)
    psi_thr, psi_a = psi_alert(psi, P["PSI_REF_WIN"], P["PSI_PCT"], P["PSI_MIN_OBS"])
    # 注：α 已下线不再计算（M4 α 二十年零触发，大佬 2026-11-03 要求彻底去掉）
    alpha = ak = au = None
    a_a = np.zeros(n, int)

    # 指标5：截面 λmax
    if xsec_lmax is not None:
        wow, xa = xsec_alert(xsec_lmax, P["XSEC_WOW"])
        x4_a, lmax_arr = xsec_align(dates, xsec_lmax, xa)
    elif panel is not None:
        xsec_lmax = xsec_lmax_calc(panel, P["XSEC_WIN"], P["XSEC_STEP"])
        if len(xsec_lmax):
            wow, xa = xsec_alert(xsec_lmax, P["XSEC_WOW"])
            x4_a, lmax_arr = xsec_align(dates, xsec_lmax, xa)
        else:
            x4_a, lmax_arr, wow = np.zeros(n, int), np.full(n, np.nan), None
    else:
        x4_a, lmax_arr, wow = np.zeros(n, int), np.full(n, np.nan), None

    # 指标6：吸收比率 λ1/N（去共同因子，本机 struct_tag 口径）
    if abs_ratio is None and panel is not None:
        abs_ratio = absorption_ratio_series(panel, P["ABS_WIN"], max_members=300)
    if abs_ratio is not None and len(abs_ratio):
        # close 传入 → 启用方向约束（暴跌途中不误报，2026-11-03 实测必需）
        m5_a, m5_v, m5_thr = absorption_alert(
            abs_ratio, dates, close=close, ref=P["ABS_REF"], pct=P["ABS_PCT"],
            ma_filter=P["MA_FILTER"])
        ar5 = m5_a.astype(int)
    else:
        ar5, m5_v, m5_thr = np.zeros(n, int), np.full(n, np.nan), np.full(n, np.nan)

    votes = ar_a.astype(int) + var_a.astype(int) + psi_a.astype(int) + x4_a + ar5
    sys_a = votes >= P["ALERT_VOTES"]
    streak = np.zeros(n, int)
    for t in range(n):
        streak[t] = (streak[t - 1] + 1) if (t > 0 and sys_a[t]) else (1 if sys_a[t] else 0)

    # ---- 双通道（2026-11-03 定稿）----
    # 通道A 时序/波动（抓 7月型广谱顶）：M1 恢复力衰减 AND M2 波动放大
    # 通道B 截面/抱团（抓 10月型抱团顶）：M6 吸收比率破 120 日 P90
    chA = (ar_a & var_a).astype(int)
    chB = ar5
    dual = chA | chB
    dual_lv = np.where(chA & chB, 2, np.where(dual, 1, 0)).astype(int)   # 2=双道共振 1=单道

    df = pd.DataFrame({
        'date': dates, 'close': close, 'ret': ret,
        'm1_ar': ar, 'm1_alert': ar_a.astype(int),
        'm2_sig': sig, 'm2_sd': sd, 'm2_dsig': dsig, 'm2_alert': var_a.astype(int),
        'm3_lam': lam, 'm3_psi': psi, 'm3_psi_thr': psi_thr, 'm3_alert': psi_a.astype(int),
        'm4_lmax': lmax_arr, 'm4_alert': x4_a,
        'm5_ar': m5_v, 'm5_thr': m5_thr, 'm5_alert': ar5,
        'votes': votes, 'sys_alert': sys_a.astype(int), 'streak': streak,
        'chA': chA, 'chB': chB, 'dual': dual, 'dual_lv': dual_lv,
    })
    p = dict(ar_mode=ar_mode, lam_mode=lam_mode, k_mode=k_mode, k_min=k_min,
             vote_sys=P["ALERT_VOTES"], ar_thr=P["AR_THR"],
             alpha_thr=P["ALPHA_THR"], enable_m4=P["ENABLE_M4"],
             vote_keys=list(VOTE_KEYS), xsec_wow=P["XSEC_WOW"], psi_pct=P["PSI_PCT"],
             ar_days=ar_days, ar_persist=ar_persist, ar_pct=ar_pct,
             dsig_mult=(dsig_mult if dsig_mult is not None else P["DSIG_MULT"]),
             abs_ratio=abs_ratio is not None)
    if params_override:
        p.update(params_override)
    return Result(code=code, name=name, df=df, params=p)


# ===================== 报告辅助 =====================
def snapshot(r: Result, idx=None):
    """某日的五指标快照（等价 workspace/bin/pws_compute.snapshot）。"""
    row = r.df.iloc[idx] if idx is not None else r.df.iloc[-1]

    def f(x, d=3):
        return None if (x is None or (isinstance(x, float) and not np.isfinite(x))) else round(float(x), d)
    return {
        'date': row['date'], 'close': f(row['close'], 2),
        'm1_ar': f(row['m1_ar']), 'm1': int(row['m1_alert']),
        'm2_dsig': f(row['m2_dsig'], 5), 'm2': int(row['m2_alert']),
        'm3_psi': f(row['m3_psi']), 'm3_thr': f(row['m3_psi_thr']), 'm3': int(row['m3_alert']),
        'm4_lmax': f(row['m4_lmax']), 'm4': int(row['m4_alert']),
        'm5_ar': f(row.get('m5_ar'), 4), 'm5_thr': f(row.get('m5_thr'), 4),
        'm5': int(row.get('m5_alert', 0)),
        'chA': int(row.get('chA', 0)), 'chB': int(row.get('chB', 0)),
        'dual': int(row.get('dual', 0)), 'dual_lv': int(row.get('dual_lv', 0)),
        'votes': int(row['votes']), 'sys': int(row['sys_alert']),
    }
