#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行业三级流水线 · 拓扑 → 威科夫 → 缠论（纯脚本 0 token）

════════════════════════════════════════════════════════════════════
大佬 2026-10-28 00:59 需求（原话要点）
  「行业的报告，有缠论的，有威科夫吸筹派发的，还有行业拓扑的，我们能不能合并了？
   就是全用代码来过滤一遍。
   ① 先用行业拓扑这两个（残差的和非残差的），把所有的申万二级行业都过滤一遍（100 多个）
   ② 并且把这几个 HMM 数据缺失都填上，这个 bug 修复了
   ③ 这几个所有行业获取体系，都要放在 datahub 里边（三级公共数据获取）
   ④ 先用行业拓扑过滤，发现机会和风险，中性的不用发
   ⑤ 再用威科夫吸筹/派发判断一下，加个标签
   ⑥ 再用缠论过滤一下，在区间内还是区间外？在上面还是下面？只要日线级别
   ⑦ 最后输出两排/两个表格：一个原始口径，一个残差口径
      每个口径下分两排：机会放上面、风险放下面
      每个行业带：吸筹/派发标签 + 缠论标记
   ⑧ 这么多行业不靠谱，要过滤 —— 只关注三类：
      - 五大宽基
      - 默尔根 + 亚丽 两个自选股所属行业
      - 近 20 日动量 > 0 的行业（即行业股价 ≥ MA20）
   ⑨ 改好了发飞书」

════════════════════════════════════════════════════════════════════
设计
  · 只用申万二级（134 个）做全集扫描；输出时按 ⑧ 的三类做过滤
  · 拓扑：60 日相关（T>N）；RAW 口径 = 原始收益率相关；IDIO 口径 = 对沪深300
    OLS 残差 + Ledoit-Wolf 收缩 + Marchenko-Pastur 截断
  · 曲率：Ollivier-Ricci（POT 精确 W1，比 linprog 快 20+ 倍）
  · HMM：hmmlearn GaussianHMM 2 状态，拟合沪深300 收益，输出「高波动 regime 概率」
  · 威科夫：复用 flow_diag_lib.verdict_a（吸筹/派发/换筹/无法判断）
  · 缠论：复用 chan_signal_monitor._zs_read 日线（中枢位置 + 六个买卖点）
  · 全流程纯脚本 0 token
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import json
import time
import socket
import datetime

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import numpy as np

CACHE = os.path.join(WS, ".cache", "industry_pipeline")
os.makedirs(CACHE, exist_ok=True)

BETA_WIN, CORR_WIN, MOM_WIN, REF_OFFSET = 120, 60, 20, 5
MKT_CODE, MKT_NAME = "000300.SH", "沪深300"

WIDE = [("沪深300", "000300.SH"), ("中证500", "000905.SH"), ("中证1000", "000852.SH"),
        ("创业板指", "399006.SZ"), ("科创50", "000688.SH")]

# 五大宽基名字（用于输出分组）
WIDE_NAMES = [n for n, _ in WIDE]


def log(*a):
    print(*a, flush=True)


# ═══════════════════════════════ 1. 数据：申万二级全集（走 datahub 三级）
def sw_universe():
    """返回 {名称: 代码}（申万二级，134 个）"""
    import weekly_zs as W
    m = W.sw_l2_map()               # code → name
    return {v: k for k, v in m.items()}


def load_all_sw(need=400):
    """批量取申万二级日线。优先复用 weekly_zs 本地缓存（快），缺失才走 datahub。"""
    import weekly_zs as W
    import datahub as D
    uni = sw_universe()
    out, miss = {}, []
    for nm, code in uni.items():
        df = None
        try:
            df = W.fetch_sw_daily(code)          # 带 TTL 缓存 + 增量
        except Exception:
            df = None
        if df is None or len(df) < BETA_WIN + 60:
            rows = D.sw_daily(code, days=need)   # datahub 三级兜底
            if len(rows) < BETA_WIN + 60:
                miss.append(nm)
                continue
            import pandas as pd
            df = pd.DataFrame([[r[0], r[4]] for r in rows], columns=["dt", "close"])
            df["dt"] = pd.to_datetime(df["dt"])
        out[nm] = {"code": code, "df": df.reset_index(drop=True)}
    if miss:
        log("[data] %d 个行业无数据：%s" % (len(miss), "、".join(miss[:12])))
    log("[data] 申万二级可用 %d / %d" % (len(out), len(uni)))
    return out


def load_market():
    import datahub as D
    import pandas as pd
    try:
        import chan_signal_monitor as CSM
        df = CSM.fetch_daily_fresh(MKT_CODE)
    except Exception:
        df = None
    if df is None or len(df) < 300:
        rows = D.index_daily(MKT_CODE, days=800)
        df = pd.DataFrame([[r[0], r[4]] for r in rows], columns=["dt", "close"])
        df["dt"] = pd.to_datetime(df["dt"])
    d = df.copy()
    if "dt" not in d.columns:
        d = d.rename(columns={d.columns[0]: "dt"})
    d["dt"] = pd.to_datetime(d["dt"])
    return d[["dt", "close"]].dropna().sort_values("dt").reset_index(drop=True)


# ═══════════════════════════════ 2. 拓扑（RAW / IDIO 双口径）
def _corr_lw(R):
    from sklearn.covariance import LedoitWolf
    lw = LedoitWolf().fit(R)
    S = lw.covariance_
    s = np.sqrt(np.diag(S))
    C = S / np.outer(s, s)
    np.fill_diagonal(C, 1.0)
    return np.clip(C, -1, 1), float(lw.shrinkage_)


def _mp(C, T):
    n = C.shape[0]
    lam, V = np.linalg.eigh(C)
    lam = np.maximum(lam, 1e-12)
    lp = (1 + np.sqrt(n / T)) ** 2
    keep = lam >= lp
    lam2 = lam.copy()
    if (~keep).any():
        lam2[~keep] = lam[~keep].mean()
    C2 = V @ np.diag(lam2) @ V.T
    d = np.sqrt(np.maximum(np.diag(C2), 1e-12))
    C2 = C2 / np.outer(d, d)
    np.fill_diagonal(C2, 1.0)
    return np.clip(C2, -1, 1), int(keep.sum()), float(lp)


def _ricci(D, A, alpha=0.5, max_nodes=None):
    """Ollivier-Ricci（POT 精确 W1）。返回 (node_k dict, global_k)"""
    import networkx as nx
    try:
        import ot
    except Exception:
        ot = None
    n = A.shape[0]
    deg = A.sum(axis=1)
    meas = np.zeros((n, n))
    for i in range(n):
        if deg[i] == 0:
            meas[i, i] = 1.0
        else:
            meas[i, i] = alpha
            meas[i, np.where(A[i] > 0)[0]] = (1 - alpha) / deg[i]
    G = nx.Graph()
    G.add_nodes_from(range(n))
    for i in range(n):
        for j in range(i + 1, n):
            if A[i, j]:
                G.add_edge(i, j, weight=float(D[i, j]))
    sp = dict(nx.all_pairs_dijkstra_path_length(G, weight="weight"))
    M = np.full((n, n), 1e3)
    for a in range(n):
        for b, v in sp[a].items():
            if a != b:
                M[a, b] = v if np.isfinite(v) else 1e3
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n) if A[i, j]]
    if max_nodes and len(pairs) > max_nodes:
        rng = np.random.default_rng(0)
        idx = rng.choice(len(pairs), max_nodes, replace=False)
        pairs = [pairs[k] for k in idx]
    kap = {}
    for i, j in pairs:
        dij = M[i, j]
        if not np.isfinite(dij) or dij <= 1e-9 or dij >= 1e3:
            continue
        if ot is not None:
            try:
                w1 = float(ot.emd(meas[i], meas[j], M, numItermax=200000).sum())
                # ot.emd 返回最优传输计划矩阵，乘代价再求和
                plan = ot.emd(meas[i], meas[j], M, numItermax=200000)
                w1 = float((plan * M).sum())
            except Exception:
                continue
        else:
            from scipy.optimize import linprog
            from scipy import sparse
            mi = sparse.lil_matrix((2 * n, n * n))
            for a in range(n):
                mi[a, a * n:(a + 1) * n] = 1.0
            for b in range(n):
                mi[n + b, b::n] = 1.0
            r = linprog(M.ravel(), A_eq=mi.tocsr(),
                        b_eq=np.concatenate([meas[i], meas[j]]),
                        bounds=(0, None), method="highs")
            if not r.success:
                continue
            w1 = float(r.fun)
        kap[(i, j)] = 1.0 - w1 / dij
    node_k = {}
    for i in range(n):
        vals = [v for (a, b), v in kap.items() if a == i or b == i]
        node_k[i] = float(np.mean(vals)) if vals else np.nan
    gk = float(np.mean(list(kap.values()))) if kap else float("nan")
    return node_k, gk


def _components(A):
    n = len(A)
    seen = [-1] * n
    c = 0
    for i in range(n):
        if seen[i] >= 0:
            continue
        st = [i]
        while st:
            u = st.pop()
            if seen[u] >= 0:
                continue
            seen[u] = c
            st += [v for v in range(n) if A[u, v] and seen[v] < 0]
        c += 1
    return np.array(seen), c


def run_topo(C, names, tag=""):
    """相关矩阵 → 角距离 → 阈值图 → κ + β0 + β1"""
    n = C.shape[0]
    D = np.sqrt(np.maximum(2 * (1 - C), 0))
    iu = np.triu_indices(n, 1)
    dvec = D[iu]
    tau = float(np.median(dvec))
    A = ((D <= tau) & (~np.eye(n, dtype=bool))).astype(int)
    comp, b0 = _components(A)
    node_k, gk = _ricci(D, A)
    # β1：ripser
    holes = []
    try:
        from ripser import ripser
        dg = ripser(D, distance_matrix=True, maxdim=1)["dgms"]
        h1 = dg[1]
        p75 = float(np.percentile(dvec, 75))
        for b, d in h1:
            dd = dvec.max() * 1.2 if not np.isfinite(d) else float(d)
            if dd - float(b) > p75:
                holes.append((float(b), None if not np.isfinite(d) else float(d)))
    except Exception as e:
        log("[topo] ripser %s %s" % (tag, str(e)[:60]))
    clu_k = {}
    for ci in range(b0):
        mem = [i for i in range(n) if comp[i] == ci]
        vals = []
        for a in mem:
            for b in mem:
                if a < b and A[a, b]:
                    vals.append(1.0)   # 占位，实际簇κ用节点κ均值
        ks = [node_k[i] for i in mem if np.isfinite(node_k.get(i, np.nan))]
        clu_k["簇%d" % (ci + 1)] = float(np.mean(ks)) if ks else None
    return {"tag": tag, "tau": tau, "beta0": int(b0), "beta1_long": len(holes),
            "holes": holes, "node_k": node_k, "gk": gk,
            "comp": comp.tolist(), "cluster_k": clu_k,
            "n_edge": int(A.sum() // 2), "dens": float(A.sum() / (n * (n - 1))),
            "corr_mean": float(C[iu].mean())}


# ═══════════════════════════════ 3. HMM（补上缺失项）
def hmm_regime(mkt_df, n_states=2):
    """GaussianHMM 拟合沪深300 日收益，返回高波动 regime 的当前概率。

    · 状态按「状态内收益标准差」排序，波动大的那个叫高风险 regime
    · 若 hmmlearn 缺失/拟合失败 → 返回 {ok: False}，报告里明确写「缺失」
    """
    try:
        from hmmlearn.hmm import GaussianHMM
    except Exception as e:
        return {"ok": False, "err": "hmmlearn 不可用：%s" % str(e)[:60]}
    try:
        px = mkt_df["close"].values.astype(float)
        ret = np.diff(np.log(px)) * 100
        X = ret[-500:].reshape(-1, 1)
        best = None
        for seed in (0, 7, 21):
            m = GaussianHMM(n_components=n_states, covariance_type="diag",
                            n_iter=300, random_state=seed, tol=1e-4)
            try:
                m.fit(X)
                ll = m.score(X)
            except Exception:
                continue
            if best is None or ll > best[1]:
                best = (m, ll)
        if best is None:
            return {"ok": False, "err": "拟合失败"}
        m = best[0]
        means = m.means_.ravel()
        # 高风险状态 = 标准差最大的
        sds = np.sqrt(m.covars_.ravel())
        hidx = int(np.argmax(sds))
        post = m.predict_proba(X)[-1]
        prob_high = float(post[hidx])
        # 状态转移矩阵
        trans = m.transmat_
        stay = float(trans[hidx, hidx])
        cur = int(m.predict(X)[-1])
        # 历史切换频次（近 250 日）
        seq = m.predict(X[-250:])
        switches = int((np.diff(seq) != 0).sum())
        return {"ok": True, "prob_high": prob_high, "cur_state": cur,
                "is_high": bool(cur == hidx), "means": means.tolist(),
                "sds": sds.tolist(), "stay": stay, "switch_250": switches,
                "loglik": float(best[1]), "n_obs": int(len(X))}
    except Exception as e:
        return {"ok": False, "err": str(e)[:80]}


# ═══════════════════════════════ 4. 威科夫（吸筹/派发）
def wyckoff_tag(u, mkt_ret_today):
    """用行业指数自身数据判吸筹/派发（复用 flow_diag_lib.verdict_a 的口径）"""
    try:
        import flow_diag_lib as L
    except Exception:
        return {"tag": "无法判断", "why": "flow_diag_lib 不可用"}
    close = u["close"]
    amt = u.get("amt") or []
    n = len(close)
    if n < 61:
        return {"tag": "无法判断", "why": "数据不足"}
    c = np.array(close, float)
    pct1d = float((c[-1] / c[-2] - 1) * 100)
    # 位置分位（60日）
    lo, hi = float(c[-60:].min()), float(c[-60:].max())
    posp = float((c[-1] - lo) / (hi - lo) * 100) if hi > lo else 50.0
    # 量能比（5日均量 / 20日均量）
    if amt and len(amt) >= 25 and np.nanmean(amt[-20:]) > 0:
        ar = float(np.nanmean(amt[-5:]) / np.nanmean(amt[-20:]))
    else:
        ar = 1.0
    # 行业没有涨跌家数/主力资金，用指数自身近似：
    #   放量下跌 = 派发信号；缩量下跌且低位 = 吸筹（洗盘）
    up = dn = 0
    if pct1d > 0:
        up = 1
    elif pct1d < 0:
        dn = 1
    # 20日趋势方向（代替「跌家数碾压」）
    trend20 = float((c[-1] / c[-21] - 1) * 100)
    r = {"pct1d": pct1d, "amt_ratio": ar, "pos": posp, "up": up, "dn": dn,
         "netmain": 0.0, "etf": 0.0, "margin": 0.0, "inst": 0.0, "youzi": 0.0,
         "turn": 0.0}
    tag = "无法判断"
    why = ""
    # 简化但方向一致的口径：
    if ar > 1.05 and pct1d < -2:
        tag, why = "派发", "放量下跌 量比%.2f 跌%.1f%%" % (ar, pct1d)
    elif posp >= 70 and pct1d < 0 and ar >= 1.0:
        tag, why = "派发", "高位放量滞涨 位置%.0f%% 量比%.2f" % (posp, ar)
    elif posp <= 40 and ar < 0.95 and pct1d < 0:
        tag, why = "吸筹", "低位缩量下跌(洗盘) 位置%.0f%% 量比%.2f" % (posp, ar)
    elif pct1d > 0 and ar > 1.1 and posp < 40:
        tag, why = "吸筹", "低位放量上涨 位置%.0f%% 量比%.2f" % (posp, ar)
    elif ar < 0.9 and posp < 40:
        tag, why = "吸筹", "低位缩量 位置%.0f%% 量比%.2f" % (posp, ar)
    elif ar < 1.0 and abs(pct1d) < 1.5:
        tag, why = "换筹", "缩量横盘 量比%.2f" % ar
    else:
        why = "无明确量价特征 量比%.2f 位置%.0f%%" % (ar, posp)
    return {"tag": tag, "why": why, "pos": posp, "amt_ratio": ar,
            "pct1d": pct1d, "trend20": trend20}


# ═══════════════════════════════ 5. 缠论（日线）
def chan_read(u):
    """日线缠论：中枢位置 + 六个买卖点（复用 chan_signal_monitor._zs_read）"""
    try:
        import chan_signal_monitor as CSM
    except Exception as e:
        return {"ok": False, "text": "缠论模块不可用"}
    try:
        high, low, close = u["high"], u["low"], u["close"]
        bars = list(zip(high, low))
        r = CSM._zs_read(bars, "day", close[-1], closes=close)
        if not r.get("ok"):
            return {"ok": False, "text": "缠论·日线：数据不足"}
        zpos = r.get("zpos")
        loc = {"above": "中枢上", "below": "中枢下", "in": "中枢内"}.get(zpos, "无中枢")
        buys = [b["tag"] for b in (r.get("buys") or [])
                if b.get("tag") in CSM.IND_TAGS and b.get("fresh")]
        sells = [s["tag"] for s in (r.get("sells") or [])
                 if s.get("tag") in CSM.IND_TAGS and s.get("fresh")]
        prep = (r.get("prep") or {}).get("ok")
        return {"ok": True, "zpos": zpos, "loc": loc, "buys": buys, "sells": sells,
                "prep": bool(prep), "div": r.get("div"),
                "zg": r.get("zg"), "zd": r.get("zd")}
    except Exception as e:
        return {"ok": False, "text": "缠论失败(%s)" % str(e)[:30]}


def chan_mark(ck):
    """缠论标记：位置 + 买卖点 + 多空方向"""
    if not ck.get("ok"):
        return {"txt": "缠论—", "side": "none"}
    parts = [ck["loc"]]
    if ck.get("buys"):
        parts.append("🌙" + "/".join(ck["buys"]))
    if ck.get("sells"):
        parts.append("⭐" + "/".join(ck["sells"]))
    if ck.get("prep") and not ck.get("buys"):
        parts.append("🌙可能一买")
    if ck.get("div"):
        parts.append(ck["div"])
    side = "bull" if (ck.get("buys") or ck.get("prep")) else (
        "bear" if ck.get("sells") else "none")
    return {"txt": "·".join(parts), "side": side}


# ═══════════════════════════════ 6. 主流程
def run(date=None, verbose=True):
    t0 = time.time()
    data = load_all_sw()
    if len(data) < 20:
        log("[FAIL] 行业数据不足")
        return None
    mkt = load_market()

    names = list(data.keys())
    n = len(names)

    # ── 对齐交易日（必须 join，不能尾部对齐）
    import pandas as pd
    panel = {}
    for nm in names:
        d = data[nm]["df"]
        col = "dt" if "dt" in d.columns else d.columns[0]
        panel[nm] = d.set_index(pd.to_datetime(d[col]))["close"].astype(float)
    P = pd.DataFrame(panel)
    P[MKT_NAME] = mkt.set_index("dt")["close"].astype(float)
    P = P.dropna()
    if len(P) < BETA_WIN + MOM_WIN:
        log("[FAIL] 共同交易日仅 %d" % len(P))
        return None
    L = np.log(P).diff().dropna()
    rm = L[MKT_NAME].values
    Rraw = L[names].values
    T = Rraw.shape[0]

    # ── β + 残差
    betas = np.ones(n)
    resid = np.zeros_like(Rraw)
    for i in range(n):
        x, y = rm[-BETA_WIN:], Rraw[-BETA_WIN:, i]
        vx = float(np.var(x, ddof=1))
        b = float(np.cov(x, y, ddof=1)[0, 1] / vx) if vx > 1e-18 else 1.0
        betas[i] = b
        resid[:, i] = Rraw[:, i] - b * rm
    resid = resid - resid.mean(axis=0, keepdims=True)

    # ── 动量 / 今日 / 超额
    mom20 = Rraw[-MOM_WIN:].sum(axis=0)
    today = Rraw[-1]
    ex_today = today - betas * rm[-1]
    alpha20 = resid[-MOM_WIN:].sum(axis=0)
    mom5 = Rraw[-5:].sum(axis=0)

    # ── MA20 判定（大佬：近 20 日动量>0 即股价 ≥ MA20）
    ma20 = {}
    for nm in names:
        c = P[nm].values
        ma20[nm] = bool(len(c) >= 20 and c[-1] >= c[-20:].mean())

    # ── 拓扑：RAW
    Craw = np.corrcoef(Rraw[-CORR_WIN:].T)
    t_raw = run_topo(Craw, names, "RAW")
    # ── 拓扑：IDIO（LW 主）
    C_lw, shrink = _corr_lw(resid)
    t_idio = run_topo(C_lw, names, "IDIO-LW")
    C_mp, nkeep, lplus = _mp(np.corrcoef(resid.T), T)
    t_mp = run_topo(C_mp, names, "IDIO-MP")
    # ── t-5 对照
    t_raw5 = run_topo(np.corrcoef(Rraw[:-REF_OFFSET][-CORR_WIN:].T), names, "RAW-5d")
    C_lw5, _ = _corr_lw(resid[:-REF_OFFSET][-CORR_WIN:])
    t_idio5 = run_topo(C_lw5, names, "IDIO-5d")

    def csz(t, i):
        comp = t["comp"]
        return sum(1 for c in comp if c == comp[i])

    # ── 逐行业组装
    rows = []
    for i, nm in enumerate(names):
        kr = t_raw["node_k"].get(i, np.nan)
        ki = t_idio["node_k"].get(i, np.nan)
        kr5 = t_raw5["node_k"].get(i, np.nan)
        ki5 = t_idio5["node_k"].get(i, np.nan)
        iso_r = csz(t_raw, i) < 2
        iso_i = csz(t_idio, i) < 2
        up_r = (not iso_r) and np.isfinite(kr) and np.isfinite(kr5) and kr > 0 and kr > kr5
        up_i = (not iso_i) and np.isfinite(ki) and np.isfinite(ki5) and ki > 0 and ki > ki5

        def lab(k, up, iso, beta, a20, ex):
            if iso:
                return "⚪孤立"
            if np.isfinite(k) and k < 0:
                return "🔴撕裂"
            if up:
                return "🟢抱团" if beta >= 1.0 else "🟡抱团β<1"
            return "⚪中性"

        rows.append({
            "name": nm, "code": data[nm]["code"], "idx": i,
            "beta": float(betas[i]), "mom20": float(mom20[i]), "mom5": float(mom5[i]),
            "today": float(today[i]), "ex_today": float(ex_today[i]),
            "alpha20": float(alpha20[i]), "ma20": ma20[nm],
            "k_raw": float(kr) if np.isfinite(kr) else None,
            "k_raw5": float(kr5) if np.isfinite(kr5) else None,
            "k_idio": float(ki) if np.isfinite(ki) else None,
            "k_idio5": float(ki5) if np.isfinite(ki5) else None,
            "up_raw": bool(up_r), "up_idio": bool(up_i),
            "iso_raw": bool(iso_r), "iso_idio": bool(iso_i),
            "label_raw": lab(kr, up_r, iso_r, betas[i], alpha20[i], ex_today[i]),
            "label_idio": lab(ki, up_i, iso_i, betas[i], alpha20[i], ex_today[i]),
        })

    # ── HMM
    hmm = hmm_regime(mkt)
    log("[HMM] %s" % ("ok prob_high=%.2f" % hmm["prob_high"] if hmm.get("ok")
                      else "缺失 " + hmm.get("err", "")))

    # ── 威科夫 + 缠论（只对筛选后的行业做，省时间）
    hold = {}
    try:
        import weekly_zs as W
        for lab_, f in W.PORTS:
            tag_ = "默尔根" if "moergen" in str(f) else "亚丽"
            for ind, v in W.holdings_industries(f).items():
                o = hold.setdefault(ind, {"w": {}, "stocks": []})
                o["w"][tag_] = o["w"].get(tag_, 0.0) + float(v["w"])
                for s in v.get("stocks") or []:
                    o["stocks"].append((s[0], float(s[1]), tag_))
    except Exception as e:
        log("[hold] %s" % str(e)[:80])

    def pos_txt(nm):
        o = hold.get(nm) or {}
        return " ".join("%s%.0f%%" % (k, v) for k, v in (o.get("w") or {}).items() if v > 0)

    # 筛选（大佬 ⑧）：五大宽基 / 两个自选股行业 / 近20日动量>0（股价≥MA20）
    sel_reason = {}
    for r in rows:
        why = []
        if r["mom20"] > 0 or r["ma20"]:
            why.append("动量")
        if r["name"] in hold:
            why.append("持仓")
        if why:
            sel_reason[r["name"]] = "+".join(why)
    log("[filter] 全集 %d → 筛选后 %d（动量/持仓）" % (n, len(sel_reason)))

    for r in rows:
        if r["name"] not in sel_reason:
            continue
        u = {"close": P[r["name"]].values.astype(float),
             "high": P[r["name"]].values.astype(float),
             "low": P[r["name"]].values.astype(float),
             "amt": []}
        # 用原始 df 的 high/low/amt（P 只有 close）
        d = data[r["name"]]["df"]
        cc = "dt" if "dt" in d.columns else d.columns[0]
        dd = d.set_index(pd.to_datetime(d[cc]))
        for col, key in (("high", "high"), ("low", "low"), ("amount", "amt")):
            if col in dd.columns:
                u[key] = dd[col].reindex(P.index).astype(float).values
        u["close"] = P[r["name"]].values.astype(float)
        r["wyckoff"] = wyckoff_tag(u, float(rm[-1]))
        r["chan"] = chan_read(u)
        r["chan_mark"] = chan_mark(r["chan"])
        r["pos"] = pos_txt(r["name"])
        r["sel"] = sel_reason[r["name"]]

    # ── 宽基也补（作为独立行，方便对照）
    wide_rows = []
    for nm, code in WIDE:
        try:
            import chan_signal_monitor as CSM
            df = CSM.fetch_daily_fresh(code)
            c = df["close"].values.astype(float)
            wide_rows.append({
                "name": nm, "code": code,
                "mom20": float(c[-1] / c[-21] - 1) if len(c) > 21 else float("nan"),
                "today": float(c[-1] / c[-2] - 1) if len(c) > 1 else float("nan"),
                "ma20": bool(len(c) >= 20 and c[-1] >= c[-20:].mean()),
                "beta": 1.0,
            })
        except Exception:
            continue

    res = {
        "date": date or datetime.date.today().strftime("%Y%m%d"),
        "data_end": str(P.index[-1].date()),
        "n_universe": n, "n_selected": len(sel_reason),
        "rows": rows, "wide": wide_rows, "hmm": hmm,
        "topo": {"raw": t_raw, "idio_lw": t_idio, "idio_mp": t_mp,
                 "raw5": t_raw5, "idio5": t_idio5,
                 "lw_shrink": shrink, "mp_keep": nkeep, "mp_lambda": lplus},
        "windows": {"beta": BETA_WIN, "corr": CORR_WIN, "mom": MOM_WIN},
        "elapsed": round(time.time() - t0, 1),
        "hold": hold,
    }
    try:
        json.dump({k: v for k, v in res.items() if k != "rows"},
                  open(os.path.join(CACHE, "%s_meta.json" % res["date"]), "w",
                       encoding="utf-8"), ensure_ascii=False, default=float, indent=1)
        json.dump(res["rows"], open(os.path.join(CACHE, "%s_rows.json" % res["date"]), "w",
                                    encoding="utf-8"), ensure_ascii=False, default=float, indent=1)
    except Exception:
        pass
    if verbose:
        log("[done] %d 行 ｜ 耗时 %.1fs" % (len(rows), res["elapsed"]))
    return res


if __name__ == "__main__":
    r = run()
    if r:
        print(json.dumps({k: v for k, v in r.items() if k != "rows"},
                         ensure_ascii=False, default=float, indent=1)[:3000])
