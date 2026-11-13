#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spd.py · SPD 流形 + TDA + 里奇曲率 指标计算（纯数值，零 LLM）
================================================================
输入：成分股收盘面板（n_days+1 个交易日 × 成分股），转成 n_days 行日度收益。
输出：一个 dict，含以下信号（每个都是真实计算，绝不编造）：

  横轴维度
    corr     成分股平均两两相关（同步度，0~1）→ 越高越「锁死」
  纵轴维度
    h1       Vietoris-Rips H1 环寿命（拓扑再生度）→ 越高越「分化/机会」
  佐证指标
    g        Log-Euclidean 测地距离（SPD 流形）
    ent      黎曼谱熵（相关矩阵特征值熵，归一化）
    ricci    里奇曲率近似（测地距离二阶导数的相反数）
    hurst    Hurst 指数（R/S，指数 log 收盘）
    dev      成分偏离度（个股与协方差重心的黎曼对数距离，归一化）

  各信号均为「滚动子窗口」序列（默认 60 天窗口 → 31 个子窗口），
  当前状态取序列末值。
"""
from __future__ import annotations

import numpy as np


def shrink_cov(R, delta=0.10):
    """样本协方差 + 向单位阵收缩（保证 SPD 正定）。"""
    T = R.shape[0]
    S = (R - R.mean(0)).T @ (R - R.mean(0)) / max(T - 1, 1)
    n = S.shape[0]
    target = (np.trace(S) / max(n, 1)) * np.eye(n)
    return (1 - delta) * S + delta * target


def le_spectral_dist(lw, lref, k=30):
    """Log-Euclidean 谱距离：||log λ_w - log λ_ref||（取前 k 个主特征值）。"""
    k = min(len(lw), len(lref), k)
    a = np.clip(lw[:k], 1e-12, None)
    b = np.clip(lref[:k], 1e-12, None)
    return float(np.sqrt(np.sum((np.log(a) - np.log(b)) ** 2)))


def corr_metrics(R):
    """相关矩阵的谱熵 + 平均 |相关性|（SVD 实现，避免大矩阵特征分解）。"""
    T, n = R.shape
    Z = (R - R.mean(0)) / (R.std(0, ddof=1) + 1e-12)
    s = np.linalg.svd(Z, compute_uv=False)
    eig = s ** 2 / max(T - 1, 1)
    eig = eig[eig > 1e-12]
    lam = eig / eig.sum()
    entropy = float(-np.sum(lam * np.log(lam)) / np.log(max(len(lam), 2)))
    C = (Z.T @ Z) / max(T - 1, 1)
    if n > 1:
        off = (np.abs(C).sum() - np.trace(np.abs(C))) / (n * (n - 1))
    else:
        off = 0.0
    return entropy, float(off), eig


def stock_2d_embedding(R):
    """成分股 2D 嵌入：Σ 的前两个右奇异向量（前两主成分载荷）。"""
    Rc = R - R.mean(0)
    _, _, Vt = np.linalg.svd(Rc, full_matrices=False)
    if Vt.shape[0] < 2:
        pts = np.column_stack([Vt[0], np.zeros_like(Vt[0])])
    else:
        pts = np.column_stack([Vt[0], Vt[1]])
    pts = (pts - pts.mean(0)) / (pts.std(0) + 1e-12)
    return pts


def subsample(pts, k=150):
    """确定性降采样点云（ripser 300 点 2.2s → 150 点 ~0.01s）。"""
    n = len(pts)
    if n <= k:
        return pts
    idx = np.linspace(0, n - 1, k).astype(int)
    return pts[idx]


def h1_lifetime(pts, rips):
    """Vietoris-Rips H1 最大寿命（环生死）。无 H1 类 → 0（环塌缩）。"""
    dgms = rips.fit_transform(pts)
    if len(dgms) < 2 or len(dgms[1]) == 0:
        return 0.0
    return float((dgms[1][:, 1] - dgms[1][:, 0]).max())


def hurst_rs(x):
    """R/S 方法估计 Hurst 指数（1D 序列）。"""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 16:
        return float("nan")
    lags = np.unique(np.floor(np.logspace(np.log10(4), np.log10(len(x) // 2), 15)).astype(int))
    lags = lags[lags >= 4]
    rs = []
    for lag in lags:
        seg = len(x) // lag
        if seg < 1:
            continue
        r_vals = []
        for i in range(seg):
            y = x[i * lag:(i + 1) * lag]
            y = y - y.mean()
            Z = np.cumsum(y)
            R = Z.max() - Z.min()
            S = np.std(y, ddof=1)
            if S > 1e-12:
                r_vals.append(R / S)
        if r_vals:
            rs.append((np.log(lag), np.log(np.mean(r_vals))))
    if len(rs) < 4:
        return float("nan")
    xs = np.array([a for a, _ in rs])
    ys = np.array([b for _, b in rs])
    return float(np.polyfit(xs, ys, 1)[0])


def ricci_proxy(g):
    """里奇曲率近似 = -测地距离二阶导数（中心差分）。凹(加速收缩) → 正。"""
    gs = np.convolve(g, np.ones(3) / 3, mode="same")
    return -np.gradient(np.gradient(gs))


def compute(close, idx_close=None, n_days=60, rips=None, verbose=False):
    """核心计算：close 面板（n_days+1 行）→ 信号 dict。

    Returns:
        dict(ret, dates, comps, g, ent, corr, h1, ricci, hurst_full, dev,
             states, idx_close, W, m, h1n, corr_c)
    失败返回 None。
    """
    from ripser import Rips
    ret = close.pct_change().iloc[1:]
    ret = ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    dates = [str(x) for x in close.index[1:]]
    comps = [str(c) for c in close.columns]

    T, n = ret.shape
    if T < 16 or n < 5:
        return None
    W = max(12, T // 2)
    m = T - W + 1

    R = ret.values
    Sigma_ref = shrink_cov(R)
    eig_ref, V_ref = np.linalg.eigh(Sigma_ref)
    order = np.argsort(eig_ref)[::-1]
    eig_ref, V_ref = eig_ref[order], V_ref[:, order]
    eig_ref = np.clip(eig_ref, 1e-12, None)
    lref = eig_ref[:30]

    if rips is None:
        rips = Rips(maxdim=1, verbose=False)

    g_series, ent_series, corr_series, h1_series = [], [], [], []
    for w in range(m):
        Rw = R[w:w + W]
        sw = np.linalg.svd(Rw - Rw.mean(0), compute_uv=False)
        lw = (sw ** 2) / (W - 1)
        g_series.append(le_spectral_dist(lw, lref))
        ent, corr, _ = corr_metrics(Rw)
        ent_series.append(ent)
        corr_series.append(corr)
        h1_series.append(h1_lifetime(subsample(stock_2d_embedding(Rw)), rips))
        if verbose and (w + 1) % 10 == 0:
            print("  [spd] 窗口 %d/%d  g=%.3f  h1=%.3f" % (w + 1, m, g_series[-1], h1_series[-1]), flush=True)

    g = np.array(g_series)
    ent = np.array(ent_series)
    corr = np.array(corr_series)
    h1 = np.array(h1_series)
    ricci = ricci_proxy(g)

    hurst_full = float("nan")
    if idx_close is not None and len(idx_close) >= 17:
        log_p = np.log(idx_close.values + 1e-9)
        hurst_full = hurst_rs(np.diff(log_p))

    # 成分偏离度：Log-Euclidean 切空间 L = V log(λ) V'，个股列与重心距离
    L = (V_ref * np.log(eig_ref)) @ V_ref.T
    centroid = L.mean(axis=1)
    dev = np.array([np.linalg.norm(L[:, i] - centroid) for i in range(n)])
    dev = dev / (dev.max() + 1e-12)

    h1n = h1 / (h1.max() + 1e-12)

    # 状态分区：红=锁死(风险) 蓝=分化(机会) 黄=卡滞
    states = np.full(m, 1, dtype=int)
    states[(corr > 0.55) & (h1n < 0.30)] = 0
    blue = (h1n > 0.50) & ((corr < 0.50) | (ent > np.median(ent)))
    states[blue] = 2
    for _ in range(2):
        states = np.array([np.median(states[max(0, i - 1):i + 2]) for i in range(m)]).astype(int)

    return dict(
        ret=ret, dates=dates, comps=comps,
        g=g, ent=ent, corr=corr, h1=h1, ricci=ricci,
        hurst_full=hurst_full, dev=dev, states=states,
        idx_close=idx_close, W=W, m=m,
        h1n=h1n, corr_c=np.clip(corr, 0, 1),
        Sigma_ref=Sigma_ref, V_ref=V_ref, eig_ref=eig_ref,
    )


def classify(corr, h1n, corr_th=0.50, h1_th=0.50):
    """按 corr / 归一化 H1 划分当前象限。返回 zone 标签 key。"""
    if corr < corr_th and h1n >= h1_th:
        return "chance"
    if corr >= corr_th and h1n < h1_th:
        return "risk"
    if corr < corr_th and h1n < h1_th:
        return "bottom"
    return "alert"
