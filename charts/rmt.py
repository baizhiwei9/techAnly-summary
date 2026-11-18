#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TechAnly.charts.rmt · 随机矩阵理论（RMT）截面相关结构分析算法
=========================================================================
本模块是「RMT 谱结构预警」类脚本共用的算法库，只做数学，不碰数据抓取、不出图。

口径（2026-11-18 定稿，与大佬硬约束一致）：
  1. 相关矩阵构建      correlation_matrix()
  2. 特征值分解 eigh    eigen_decompose()
  3. MP 边界           mp_bounds()          Q = T/N = 60/N
  4. 谱隙              spectral_gap()       gap = λ1 − λ2
  5. 谱熵              spectral_entropy()   H = −Σ p_i·ln(p_i)，p_i = λi/Σλ（可归一化）
  6. 参与率            participation_ratio()  PR_k = (Σ v²)² / Σ v⁴ = 1/IPR_k
  7. 逆参与率          inverse_participation_ratio()  IPR_k = Σ v_k⁴

设计约定：
  · 输入收益矩阵形状统一为 (T, N)：T=观测数（交易日）、N=资产数。
  · 所有特征值按降序 λ1 ≥ λ2 ≥ … ≥ λN；相关矩阵是对称半正定，eigh 全实特征值。
  · 缺失值（停牌/未上市）在去均值标准化时用 nan 安全统计，标准化后再置 0，
    与库内 rmt_rc.py 的 fillna(0) 行为一致（截面上少量缺失不改变窗口主结构）。
  · 「剥离市场模式」＝ 每列收益对市场收益做 OLS，取残差再标准化构建相关矩阵。
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple, Union

import numpy as np

__all__ = [
    "correlation_matrix",
    "residualize_market",
    "eigen_decompose",
    "mp_bounds",
    "spectral_gap",
    "spectral_entropy",
    "participation_ratio",
    "inverse_participation_ratio",
    "ar5",
    "volatility_ratio",
    "rmt_window_metrics",
]


# ───────────────────────── 工具：标准化 ─────────────────────────
def _as_returns(returns) -> np.ndarray:
    """任何 (T,N) 序列 → float64 ndarray。"""
    R = np.asarray(returns, dtype=float)
    if R.ndim != 2:
        raise ValueError("收益矩阵必须是 (T, N) 二维")
    if R.shape[0] < 3:
        raise ValueError("观测数 T 至少 3，才能算样本标准差")
    return R


def _standardize(R: np.ndarray) -> np.ndarray:
    """按列去均值、除以样本标准差；NaN 安全，标准化后残 NaN 置 0。"""
    mu = np.nanmean(R, axis=0)
    sd = np.nanstd(R, axis=0, ddof=1)
    sd = np.where(sd < 1e-12, 1.0, sd)
    Z = (R - mu) / sd
    Z = np.where(np.isfinite(Z), Z, 0.0)
    return Z


# ───────────────────────── 1. 相关矩阵 ─────────────────────────
def residualize_market(returns, market_returns) -> np.ndarray:
    """剥离市场模式：每列收益对市场收益做 OLS，返回残差矩阵 (T, N)。

    Args:
        returns: (T, N) 股票收益矩阵。
        market_returns: (T,) 市场（指数）收益序列，与 returns 行对齐。

    Returns:
        (T, N) 残差矩阵（原收益 − α − β·市场收益）；某列有效样本 < 3 时该列全 NaN。
    """
    R = _as_returns(returns)
    m = np.asarray(market_returns, dtype=float).reshape(-1)
    T, N = R.shape
    if m.shape[0] != T:
        raise ValueError("market_returns 长度必须等于 returns 行数")
    X = np.column_stack([np.ones(T), m])
    out = np.full_like(R, np.nan)
    for j in range(N):
        y = R[:, j]
        valid = np.isfinite(y) & np.isfinite(m)
        if int(valid.sum()) < 3:
            continue
        beta, *_ = np.linalg.lstsq(X[valid], y[valid], rcond=None)
        out[:, j] = y - X @ beta
    return out


def correlation_matrix(
    returns,
    remove_market: bool = False,
    market_returns: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """构建 N×N 截面相关矩阵 C（去均值、标准化后）。

    Args:
        returns: (T, N) 收益矩阵。
        remove_market: True = 先用 residualize_market 剥离市场模式再用残差算。
        market_returns: (T,) 市场收益；remove_market=True 时必填。

    Returns:
        (N, N) 相关矩阵，对角线为 1，对称。
    """
    R = _as_returns(returns)
    if remove_market:
        if market_returns is None:
            raise ValueError("remove_market=True 必须传入 market_returns")
        R = residualize_market(R, market_returns)
    T, N = R.shape
    Z = _standardize(R)
    C = (Z.T @ Z) / max(T - 1, 1)
    C = 0.5 * (C + C.T)
    np.fill_diagonal(C, 1.0)
    return C


# ───────────────────────── 2. 特征值分解 ─────────────────────────
def eigen_decompose(C) -> Tuple[np.ndarray, np.ndarray]:
    """对对称相关矩阵做特征值分解，返回降序特征值与对应特征向量。

    Args:
        C: (N, N) 对称矩阵。

    Returns:
        (eigenvalues, eigenvectors)
        eigenvalues: (N,) 降序 λ1 ≥ λ2 ≥ …（对称半正定，clip 掉 -1e-12 级浮点噪声）
        eigenvectors: (N, N)，第 k 列是 λ_k 对应的单位特征向量 v_k。
    """
    C = np.asarray(C, dtype=float)
    C = 0.5 * (C + C.T)
    evals, evecs = np.linalg.eigh(C)
    order = np.argsort(evals)[::-1]
    evals = evals[order]
    evecs = evecs[:, order]
    evals = np.clip(evals, 0.0, None)
    # eigh 已单位化，这里再归一化一遍防御极端数值
    norm = np.sqrt(np.sum(evecs * evecs, axis=0))
    evecs = evecs / np.where(norm > 0, norm, 1.0)
    return evals, evecs


# ───────────────────────── 3. MP 边界 ─────────────────────────
def mp_bounds(Q: float) -> Tuple[float, float]:
    """Marchenko–Pastur 边界（假设 σ²=1 标准化）。

    Args:
        Q: 观测/资产比 Q = T/N（大佬口径，如 T=60、N=50 → Q=1.2）。

    Returns:
        (lambda_max_mp, lambda_min_mp) = ((1+√Q)², (1−√Q)²)。

    注：Q>1（N<T）时 √Q>1，下界 (1−√Q)² 仍为小正数，MP 体量集中在
    [λ_min, λ_max]；市场模式 λ1 通常远超 λ_max。
    """
    Q = float(Q)
    if Q <= 0:
        raise ValueError("Q 必须 > 0")
    r = np.sqrt(Q)
    return float((1.0 + r) ** 2), float((1.0 - r) ** 2)


# ───────────────────────── 4. 谱隙 ─────────────────────────
def spectral_gap(eigenvalues) -> float:
    """谱隙 gap = λ1 − λ2。

    衡量「市场模式」与其余模式的分离度：gap 收窄常意味着共同因子主导减弱、
    截面结构由集中转向分散（RMT 语境下是潜在regime切换前兆之一）。

    Args:
        eigenvalues: 降序特征值 (N,)。

    Returns:
        float gap；特征值不足 2 个时返回 NaN。
    """
    ev = np.asarray(eigenvalues, dtype=float)
    if ev.size < 2:
        return float("nan")
    return float(ev[0] - ev[1])


# ───────────────────────── 5. 谱熵 ─────────────────────────
def spectral_entropy(eigenvalues, normalize: bool = True) -> float:
    """特征值分布的香农熵 H = −Σ p_i·ln(p_i)，p_i = λ_i / Σλ。

    高熵 = 能量分散到许多特征模式（风险来源分散 / 噪声占优）；
    低熵 = 能量集中于少数特征值（抱团 / 单因子主导）。

    Args:
        eigenvalues: 降序特征值 (N,)。
        normalize: True = 除以 ln(N) 归一化到 (0,1]，便于跨窗口对比（N 恒定时等价）。

    Returns:
        float 谱熵；无正特征值时返回 NaN。
    """
    ev = np.asarray(eigenvalues, dtype=float)
    ev = ev[np.isfinite(ev) & (ev > 0)]
    if ev.size == 0:
        return float("nan")
    p = ev / ev.sum()
    p = p[p > 0]
    H = -float(np.sum(p * np.log(p)))
    if normalize and ev.size > 1:
        H = H / float(np.log(ev.size))
    return H


# ───────────────────────── 6. 参与率 ─────────────────────────
def participation_ratio(eigenvectors) -> np.ndarray:
    """每个特征向量的参与率 PR_k = (Σ_i v_{k,i}²)² / Σ_i v_{k,i}⁴ = 1/IPR_k。

    取值范围 [1, N]：接近 N = 该模式由几乎全部成分股共同参与（去局域化）；
    接近 1 = 只有个别成分股主导（局域化）。市场模式（k=1）的 PR 最常用。

    Args:
        eigenvectors: (N, N) 特征向量，第 k 列对应 v_k。

    Returns:
        (N,) 各特征向量的参与率 PR_k。
    """
    V = np.asarray(eigenvectors, dtype=float)
    norm = np.sqrt(np.sum(V * V, axis=0))
    V = V / np.where(norm > 1e-30, norm, 1.0)
    p2 = V * V
    num = np.sum(p2, axis=0) ** 2
    den = np.sum(p2 * p2, axis=0)
    return np.where(den > 1e-30, num / den, np.nan)


# ───────────────────────── 7. 逆参与率 ─────────────────────────
def inverse_participation_ratio(eigenvectors) -> np.ndarray:
    """每个特征向量的逆参与率 IPR_k = Σ_i v_{k,i}⁴。

    取值范围 [1/N, 1]：接近 1/N = 去局域化（广泛参与）；接近 1 = 局域化（少数成分主导）。

    Args:
        eigenvectors: (N, N) 特征向量，第 k 列对应 v_k。

    Returns:
        (N,) 各特征向量的 IPR_k。
    """
    V = np.asarray(eigenvectors, dtype=float)
    norm = np.sqrt(np.sum(V * V, axis=0))
    V = V / np.where(norm > 1e-30, norm, 1.0)
    return np.sum(V ** 4, axis=0)


# ───────────────────────── 8. AR5（原 RMT 监控图口径） ─────────────────────────
def ar5(eigenvalues, top: int = 5, denom: int = 50) -> float:
    """AR5 = (λ1+…+λ5) / Σ(λ1…λ50)：前 5 大特征值占前 50 大特征值能量比。

    与库内 charts/rmt_rc.py「定稿口径」一致（分母＝前 50 个特征值之和）。
    N≤50 时 denom 自动退化为全部特征值之和。

    Args:
        eigenvalues: 特征值（任意顺序，内部会降序）。
        top: 分子取前 top 大特征值（默认 5）。
        denom: 分母取前 denom 大特征值（默认 50）。

    Returns:
        float AR5；无有效特征值返回 NaN。
    """
    ev = np.asarray(eigenvalues, dtype=float)
    ev = ev[np.isfinite(ev) & (ev > 0)]
    if ev.size == 0:
        return float("nan")
    ev = np.sort(ev)[::-1]
    n = min(int(denom), ev.size)
    return float(ev[: min(int(top), n)].sum() / ev[:n].sum())


# ───────────────────────── 9. RC（波动率级联比） ─────────────────────────
def volatility_ratio(port_returns, short: int = 20) -> float:
    """RC = σ(近 short 日组合收益) / σ(全窗口组合收益)。

    原 RMT 监控图的「波动率级联比」：>1 表示近期组合波动放大。

    Args:
        port_returns: (T,) 等权组合（或单标的）收益序列。
        short: 短窗口长度（默认 20 日）。

    Returns:
        float RC；全窗口标准差 ~0 或样本不足返回 NaN。
    """
    r = np.asarray(port_returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 3:
        return float("nan")
    s_all = float(np.nanstd(r))
    s_short = float(np.nanstd(r[-int(short):])) if r.size >= short else s_all
    return s_short / s_all if s_all > 1e-12 else float("nan")


# ───────────────────────── 单窗口一站式 ─────────────────────────
def rmt_window_metrics(
    returns,
    market_returns: Optional[Sequence[float]] = None,
    remove_market: bool = False,
) -> dict:
    """对单个 (T,N) 收益窗口算齐 7 项 RMT 指标，返回一个 dict。

    Args:
        returns: (T, N) 窗口内收益矩阵（T=窗口长度，N=资产数）。
        market_returns: (T,) 市场收益；remove_market=True 时用于剥离市场模式。
        remove_market: False = 原收益直接算；True = 剥离市场模式后算残差结构。

    Returns:
        dict，含：
          N, T, Q                    资产数 / 观测数 / Q=T/N
          mp_max, mp_min             MP 上、下边界
          lam1, lam2, lam1_share     最大、次大特征值及 λ1 能量占比
          gap                        λ1 − λ2（谱隙）
          gap_to_mp                  λ1 − mp_max（市场模式超出随机上界的距离）
          entropy                    归一化谱熵
          pr_market, ipr_market     市场模式（λ1）参与率 / 逆参与率
          outlier_energy, outlier_count  超 MP 上界特征值能量占比 / 个数
          eigenvalues                (N,) 降序特征值（供谱图/密度诊断复用）
    """
    R = _as_returns(returns)
    T, N = R.shape
    if remove_market and market_returns is None:
        raise ValueError("remove_market=True 必须传入 market_returns")

    C = correlation_matrix(R, remove_market=remove_market, market_returns=market_returns)
    evals, evecs = eigen_decompose(C)
    pr = participation_ratio(evecs)
    ipr = inverse_participation_ratio(evecs)

    Q = T / max(N, 1)
    lam_max, lam_min = mp_bounds(Q)
    lam1 = float(evals[0]) if evals.size else float("nan")
    lam2 = float(evals[1]) if evals.size > 1 else float("nan")
    total = float(evals.sum())
    share1 = lam1 / total if total > 0 else float("nan")
    over = evals[evals > lam_max]

    return {
        "N": int(N),
        "T": int(T),
        "Q": float(Q),
        "mp_max": lam_max,
        "mp_min": lam_min,
        "lam1": lam1,
        "lam2": lam2,
        "lam1_share": share1,
        "gap": spectral_gap(evals),
        "gap_to_mp": lam1 - lam_max,
        "entropy": spectral_entropy(evals, normalize=True),
        "pr_market": float(pr[0]) if pr.size else float("nan"),
        "ipr_market": float(ipr[0]) if ipr.size else float("nan"),
        "outlier_energy": float(over.sum() / max(N, 1)),
        "outlier_count": int(over.size),
        "eigenvalues": evals,
    }
