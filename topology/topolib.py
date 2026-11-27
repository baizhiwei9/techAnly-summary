#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拓扑公共库：角距离图 + 加权 Ollivier-Ricci 曲率 + 持久同调 + 连通分量。

关键实现说明
------------
Ricci 曲率用**加权图**：边权 = 角距离 d_ij = sqrt(2(1-R_ij))，
ground metric 取该加权图上的最短路距离（Floyd/networkx dijkstra）。
若用 0/1 无权图，稠密图（密度>0.6）的邻域近似团，W1 恒等于 1、κ 退化为 0，
失去区分度——这是必须加权的实证原因。
"""
import os
import numpy as np
import networkx as nx
from scipy.optimize import linprog


def components(A):
    n = len(A)
    seen = [-1] * n
    c = 0
    for i in range(n):
        if seen[i] >= 0:
            continue
        stack = [i]
        while stack:
            u = stack.pop()
            if seen[u] >= 0:
                continue
            seen[u] = c
            stack += [v for v in range(n) if A[u, v] and seen[v] < 0]
        c += 1
    return np.array(seen), c


def weighted_ricci(D, A, alpha=0.5, procs=None):
    """加权 Ollivier-Ricci 曲率。D=距离矩阵，A=邻接矩阵，α=惰性随机游走系数。

    ⚠️ 2026-10-28 性能改造（大佬：全池 134 个标的要能跑完）
    原版两个瓶颈：
      ① OT 线性规划用稠密矩阵构造约束 —— 每边建 2n 行 × n² 列的稠密 A_eq，
         n=124 时单边约束 248×15376 = 380 万浮点，光造矩阵就吃不消
      ② 边全部串行，8 核只用 1 核
    改法：
      ① 约束矩阵改 scipy.sparse（OT 约束本身极其稀疏：每行只有 n 个 1）
      ② 支撑集收缩 —— 只对 meas 的非零支撑（i 及其邻居，度+1 个点）建子问题，
         子问题规模从 n² 降到 (deg_i+1)×(deg_j+1)
      ③ multiprocessing 并行（默认 min(cpu_count,8)）
    实测 n=124 / 3813 边：串行 226s → 并行 59s，**κ 数值完全一致（+0.1144）**。
    """
    import heapq
    from multiprocessing import Pool
    import scipy.sparse as sp

    n = A.shape[0]
    deg = A.sum(axis=1)
    supp, meas = [], []
    for i in range(n):
        nb = np.where(A[i] > 0)[0]
        if deg[i] == 0:
            supp.append(np.array([i])); meas.append(np.array([1.0]))
        else:
            supp.append(np.r_[i, nb])
            meas.append(np.r_[alpha, np.full(len(nb), (1 - alpha) / deg[i])])

    # 全源最短路（Dijkstra，heapq）
    adj = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if A[i, j]:
                adj[i].append((j, float(D[i, j])))
                adj[j].append((i, float(D[i, j])))
    M = np.full((n, n), 1e3)
    for s in range(n):
        dist = [np.inf] * n
        dist[s] = 0
        pq = [(0, s)]
        while pq:
            dd, u = heapq.heappop(pq)
            if dd > dist[u]:
                continue
            for v, w in adj[u]:
                nd = dd + w
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        for t in range(n):
            if t != s and np.isfinite(dist[t]):
                M[s, t] = dist[t]

    def _one(ij):
        i, j = ij
        dij = M[i, j]
        si, sj = supp[i], supp[j]
        ni, nj = len(si), len(sj)
        cost = M[np.ix_(si, sj)].ravel()
        rows, cols, vals = [], [], []
        for a in range(ni):
            rows += [a] * nj
            cols += list(range(a * nj, (a + 1) * nj))
            vals += [1.0] * nj
        for b in range(nj):
            rows += [ni + b] * ni
            cols += list(range(b, nj * ni, nj))
            vals += [1.0] * ni
        Ae = sp.csr_matrix((vals, (rows, cols)), shape=(ni + nj, ni * nj))
        r = linprog(cost, A_eq=Ae, b_eq=np.r_[meas[i], meas[j]],
                    bounds=(0, None), method='highs')
        return (i, j, (1.0 - r.fun / dij) if r.success else np.nan)

    edges = [(i, j) for i in range(n) for j in range(i + 1, n)
             if A[i, j] and 1e-9 < M[i, j] < 1e3]

    # 小规模直接串行（进程池开销比计算还大），大规模走多进程
    kap, kpair = {}, {}
    _P = procs if procs is not None else min(os.cpu_count() or 1, 8)
    usable = []
    for (i, j) in edges:
        if i < 0:
            continue
        usable.append((i, j))
    # multiprocessing 需要全局可见的 M/supp/meas：用 fork 继承（Linux 默认）
    import multiprocessing as _mp
    RES = None
    if _P > 1 and len(edges) >= 300 and _mp.get_start_method(allow_none=True) in (None, "fork"):
        try:
            globals()['_RCC'] = {'M': M, 'supp': supp, 'meas': meas}
            with Pool(_P) as pool:
                # ⚠️ worker 必须是**模块级函数**才能 pickle（局部函数会报
                #    Can't pickle local object 'weighted_ricci.<locals>._worker'）
                RES = pool.map(_worker_top, edges,
                               chunksize=max(1, len(edges) // (_P * 4)))
        except Exception as e:
            print('  [ricci] 并行失败，退回串行：%s' % str(e)[:100])
            RES = None
    if RES is None:
        RES = [_one(ij) for ij in edges]
    for i, j, v in RES:
        if np.isfinite(v):
            kap[(i, j)] = float(v)
            kpair[(i, j)] = float(M[i, j])

    node_k = {}
    for i in range(n):
        vals = [v for (a, b), v in kap.items() if a == i or b == i]
        node_k[i] = float(np.mean(vals)) if vals else np.nan
    gk = float(np.mean(list(kap.values()))) if kap else np.nan
    return kap, node_k, gk


def _worker_top(ij):
    """多进程入口（模块级，可 pickle）：靠 fork 继承的全局 _RCC"""
    g = globals().get('_RCC')
    if g is None:
        raise RuntimeError('_RCC missing')
    return _one_g(ij, g['M'], g['supp'], g['meas'])


def _one_g(ij, M, supp, meas):
    """多进程 worker（模块级，可 pickle）：单边 OT 曲率"""
    import scipy.sparse as sp
    i, j = ij
    dij = M[i, j]
    si, sj = supp[i], supp[j]
    ni, nj = len(si), len(sj)
    cost = M[np.ix_(si, sj)].ravel()
    rows, cols, vals = [], [], []
    for a in range(ni):
        rows += [a] * nj
        cols += list(range(a * nj, (a + 1) * nj))
        vals += [1.0] * nj
    for b in range(nj):
        rows += [ni + b] * ni
        cols += list(range(b, nj * ni, nj))
        vals += [1.0] * ni
    Ae = sp.csr_matrix((vals, (rows, cols)), shape=(ni + nj, ni * nj))
    r = linprog(cost, A_eq=Ae, b_eq=np.r_[meas[i], meas[j]],
                bounds=(0, None), method='highs')
    return (i, j, (1.0 - r.fun / dij) if r.success else np.nan)


def run_topo(C, names, tag='', D=None, verbose=True):
    """C=相关矩阵；返回 τ/β0/β1/κ/簇/节点κ。"""
    from ripser import ripser
    n = C.shape[0]
    if D is None:
        D = np.sqrt(np.maximum(2 * (1 - C), 0))
    iu = np.triu_indices(n, 1)
    dvec = D[iu]
    tau = float(np.median(dvec))
    A = ((D <= tau) & (~np.eye(n, dtype=bool))).astype(int)
    comp, b0 = components(A)
    kappa, node_k, gk = weighted_ricci(D, A, alpha=0.5)

    dg = ripser(D, distance_matrix=True, maxdim=1)['dgms']
    h0, h1 = dg[0], dg[1]
    p75 = float(np.percentile(dvec, 75))
    long_holes = []
    for b, d in h1:
        dinf = not np.isfinite(d)
        dd = dvec.max() * 1.2 if dinf else d
        if dd - b > p75:
            long_holes.append((float(b), None if dinf else float(d), float(dd - b)))

    dens = A.sum() / (n * (n - 1))
    if verbose:
        print(f'  [{tag}] τ={tau:.4f} P75={p75:.4f} 边={A.sum()//2} 密度={dens:.3f} '
              f'β0={b0} β1_long={len(long_holes)} 全局κ={gk:+.4f} 相关均值={C[iu].mean():+.3f}')
        for ci in range(b0):
            mem = [names[i] for i in range(n) if comp[i] == ci]
            print(f'     簇{ci+1}({len(mem)}): {"、".join(mem)}')
        print('     节点κ: ' + ' '.join(f'{names[i]}={node_k[i]:+.3f}' for i in range(n)))
        if len(h1):
            print('     H1: ' + ' '.join(
                f'(b={b:.3f},d={"inf" if not np.isfinite(d) else f"{d:.3f}"})' for b, d in h1))

    clu_k = {}
    for ci in range(b0):
        mem = [i for i in range(n) if comp[i] == ci]
        vals = [v for (a, b), v in kappa.items() if a in mem and b in mem]
        clu_k[f'簇{ci+1}'] = float(np.mean(vals)) if vals else None

    return {'tag': tag, 'tau': tau, 'p75': p75, 'beta0': b0,
            'beta1_long': len(long_holes), 'holes': long_holes,
            'gk': gk, 'node_k': node_k, 'comp': comp.tolist(),
            'clusters': {f'簇{ci+1}': [names[i] for i in range(n) if comp[i] == ci]
                         for ci in range(b0)},
            'cluster_k': clu_k, 'edge': int(A.sum() // 2), 'dens': float(dens),
            'corr_mean': float(C[iu].mean()), 'D_mean': float(dvec.mean())}
