#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manifold_topo.py · 拓扑流形图（UMAP 2D × 里奇曲率网络）
=========================================================
一张图两半，都是「真实数值算出来的」，零 LLM、不造数据：

  左图：成分股收益距离 → UMAP 2D 嵌入散点
        · 气泡大小 = 近 5 日动量（涨越多越大，跌越多越小）
        · 颜色     = 机会 / 风险 / 中性 三分类（动量 × 结构位置合成）
        · 背景     = 机会区 / 风险区 软标注
        · 页脚     = 流形形态 + Betti_0 / Betti_1（ripser 实测）

  右图：成分股关联网络（相关矩阵 top-k 边）× 离散曲率
        · 节点颜色 = 节点平均 Ollivier-Ricci 曲率（越负越蓝）
        · 咽喉     = 曲率最负的枢纽股（与同篮多只强相关、卡在传导要道）
        · 页脚     = 咽喉说明（位置敏感 ≠ 现在就在跌）

用法：
    python3 topology/manifold_topo.py --target 科创50
    python3 topology/manifold_topo.py --index 000688.SH --name 科创50 --days 60
    python3 topology/manifold_topo.py --target 科创50 --csv /tmp/close.csv
    python3 topology/manifold_topo.py --target 科创50 --score-mode multi   # 旧的多维加权口径
    python3 topology/manifold_topo.py --audit                              # hermer2 口径偏误审计

取数优先级：--csv > data_loader(dh_bridge/data_hub) > tushare 直连(index_weight + daily)。

打分口径（--score-mode）：
    hermer2（默认）  照 hermer2 公布的分项结论反推的结构打分（见 hermer2_score 注释）
    multi            原多维加权打分 market_score()（趋势/回撤/广度/动能/相关性/波动/短期）
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ─────────────── 路径（2026-11-14 去本机绝对路径）：部署目录可用 TECH_WS 覆盖 ───────────────
_WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE = os.path.join(_WS, "misc", "core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import matplotlib.patheffects as pe

# ─────────────── 中文字体 ───────────────
plt.rcParams["font.sans-serif"] = [
    "Noto Sans CJK SC", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

# ─────────────── 配色（白底、红标题、蓝曲率） ───────────────
TITLE_RED = "#d92b2b"
INK = "#2b2b2b"
MUTED = "#7a7a7a"
GRID = "#e6e6e6"
GREEN = "#2eb872"   # 机会核心
RED = "#e05252"     # 风险边缘
BLUE = "#3b6fd4"    # 中性过渡
NAVY = "#0d2b5e"    # 咽喉

CURV_CMAP = LinearSegmentedColormap.from_list(
    "curv_multi",
    ["#b3202e", "#e0523a", "#f08a35", "#f5c542", "#a8cf5a", "#2eb872", "#2e6fbf"],
)

INDEX_ALIAS = {
    "科创50": ("000688.SH", "科创50"),
    "科创50指数": ("000688.SH", "科创50"),
    "上证50": ("000016.SH", "上证50"),
    "沪深300": ("000300.SH", "沪深300"),
    "中证500": ("000905.SH", "中证500"),
    "中证1000": ("000852.SH", "中证1000"),
    "创业板指": ("399006.SZ", "创业板指"),
}


# ─────────────────────── 取数 ───────────────────────
def stock_names(codes):
    """股票代码 → 简称（tushare stock_basic）。失败返回 {}。"""
    if not codes:
        return {}
    try:
        sys.path.insert(0, _CORE)
        from tushare_init import get_pro  # noqa: E402
        sb = get_pro().stock_basic(fields="ts_code,name")
        m = dict(zip(sb["ts_code"], sb["name"]))
        return {c: m.get(c, c) for c in codes}
    except Exception:
        return {}


def fetch_by_tushare(index_code: str, days: int = 60):
    """tushare 直连：index_weight 取成分 + daily 逐只取收盘。返回 (dates, close_df, name_map)。"""
    sys.path.insert(0, _CORE)
    from tushare_init import get_pro  # noqa: E402

    pro = get_pro()
    end = datetime.now()
    start = end - timedelta(days=days * 2 + 40)
    iw = pro.index_weight(
        index_code=index_code,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if iw is None or len(iw) == 0:
        raise RuntimeError("index_weight 为空：%s" % index_code)
    # 取最新一期成分
    latest = iw["trade_date"].max()
    codes = sorted(set(iw.loc[iw["trade_date"] == latest, "con_code"].tolist()))

    d0 = (end - timedelta(days=days * 2 + 30)).strftime("%Y%m%d")
    d1 = end.strftime("%Y%m%d")
    cols = {}
    for c in codes:
        try:
            df = pro.daily(ts_code=c, start_date=d0, end_date=d1)
            if df is not None and len(df):
                cols[c] = df.set_index("trade_date")["close"]
        except Exception:
            continue
    close = pd.DataFrame(cols).sort_index()
    close = close.tail(days + 1)

    names = {}
    try:
        sb = pro.stock_basic(fields="ts_code,name")
        names = dict(zip(sb["ts_code"], sb["name"]))
    except Exception:
        pass
    return list(close.index), close, names


def fetch_by_datahub(target: str, days: int = 60):
    """走 techAnly 取数层（dh_bridge / data_hub）。"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from topology import data_loader as dl  # noqa: E402
    from topology import targets as T  # noqa: E402

    t = T.resolve(target)
    codes = dl.components(t)
    if not codes:
        raise RuntimeError("data_hub 取不到成分股：%s" % target)
    dates, close = dl.close_panel(codes, n_days=days)
    if close is None or len(close) < 20:
        raise RuntimeError("data_hub 取不到收盘面板：%s" % target)
    return list(close.index), close, {}


# ─────────────────────── 指标 ───────────────────────
def ollivier_ricci(corr: np.ndarray, k: int = 5, alpha: float = 0.5):
    """节点平均 Ollivier-Ricci 曲率。

    图：每只股票连到相关性最高的 k 个邻居，边长为 1-corr（相关越高越近）。
    测度：m_x = alpha·δ_x + (1-alpha)·Σ w_xy δ_y（w 按相关性归一）。
    曲率：κ(x,y) = 1 - W1(m_x, m_y)/d(x,y)，节点曲率取入射边均值。
    """
    import networkx as nx
    from scipy.sparse.csgraph import shortest_path

    n = corr.shape[0]
    sim = np.clip(corr, 0, None).copy()
    np.fill_diagonal(sim, 0.0)
    G = nx.Graph()
    G.add_nodes_from(range(n))
    for i in range(n):
        nb = np.argsort(sim[i])[::-1][:k]
        for j in nb:
            if i < j:
                G.add_edge(i, int(j), w=float(sim[i, j]))

    # 边长 = 1 - 相关
    for u, v, d in G.edges(data=True):
        d["length"] = max(1e-6, 1.0 - d["w"])

    idx = {nid: i for i, nid in enumerate(G.nodes())}
    A = np.full((n, n), np.inf)
    for u, v, d in G.edges(data=True):
        A[idx[u], idx[v]] = A[idx[v], idx[u]] = d["length"]
    np.fill_diagonal(A, 0.0)
    D = shortest_path(A)

    meas = []
    for i in range(n):
        v = np.zeros(n)
        v[i] = alpha
        nbs = list(G.neighbors(i))
        if nbs:
            ws = np.array([G[i][j]["w"] for j in nbs], dtype=float)
            ws = ws / ws.sum()
            for j, w in zip(nbs, ws):
                v[j] += (1 - alpha) * w
        meas.append(v)

    edge_curv = {}
    for u, v in G.edges():
        duv = D[u, v]
        if not np.isfinite(duv) or duv <= 0:
            edge_curv[(u, v)] = 0.0
            continue
        mu, mv = meas[u], meas[v]
        # 只在支撑集上求 EMD，省算力
        supp = np.where((mu > 1e-9) | (mv > 1e-9))[0]
        cost = D[np.ix_(supp, supp)]
        try:
            import ot
            w1 = ot.emd2(mu[supp], mv[supp], cost, numItermax=100000)
        except Exception:
            w1 = float(np.abs(mu[supp] - mv[supp]).sum())
        edge_curv[(u, v)] = 1.0 - w1 / duv

    node_curv = np.zeros(n)
    for i in range(n):
        vals = [edge_curv[(min(i, j), max(i, j))] for j in G.neighbors(i)]
        node_curv[i] = float(np.mean(vals)) if vals else 0.0
    deg = np.array([G.degree(i) for i in range(n)])
    return node_curv, deg, G, edge_curv


def market_score(close: pd.DataFrame, index_close: pd.Series = None, corr: np.ndarray = None):
    """多维真实打分（−2 风险 … +2 机会）。

    每项回归原始口径定分，再做加权平均；**不用 z 分数取均值**
    （z 分数均值恒为 0，会把任何行情都判成中性 —— 之前就是这个 bug）。

    输入：close = 成分股收盘面板（行=交易日）；index_close = 指数收盘序列（无则用成分等权合成）。
    返回 dict：score / verdict / tone / items。
    """
    ret = np.log(close / close.shift(1)).dropna()
    if corr is None:
        corr = ret.corr().values
    n = corr.shape[0]
    iu = np.triu_indices(n, 1)
    c_full = float(corr[iu].mean())
    c20 = float(ret.tail(20).corr().values[iu].mean()) if len(ret) >= 20 else c_full

    if index_close is None or len(index_close) < 25:
        idx = (close / close.iloc[0] * 1000).mean(axis=1)  # 等权合成
    else:
        # 索引对齐：统一转成 str（tushare 日历可能是 int64）
        _ci = close.copy()
        _ci.index = _ci.index.astype(str)
        _ic = index_close.copy()
        _ic.index = _ic.index.astype(str)
        idx = _ic.reindex(_ci.index).ffill().bfill()
    idx = idx.dropna()
    if len(idx) < 25:
        _ci = close.copy()
        _ci.index = _ci.index.astype(str)
        idx = (_ci / _ci.iloc[0] * 1000).mean(axis=1)
    last = float(idx.iloc[-1])
    ma20 = float(idx.rolling(20).mean().iloc[-1])
    ma60 = float(idx.rolling(min(60, len(idx))).mean().iloc[-1])
    hi60 = float(idx.tail(60).max())

    d_ma60 = (last / ma60 - 1) * 100
    d_high = (last / hi60 - 1) * 100
    r5 = (last / idx.iloc[-6] - 1) * 100 if len(idx) > 6 else 0.0
    d_ma20 = (last / ma20 - 1) * 100

    above20 = float((close.iloc[-1] > close.rolling(20).mean().iloc[-1]).mean() * 100)
    mom5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100
    med5 = float(mom5.median())
    vol = float((ret.std() * np.sqrt(252) * 100).median())

    def band(v, cuts):
        """cuts = [(阈值, 分)...] 升序；取第一个 v < 阈值 的分，否则末尾分。"""
        for th, s in cuts:
            if v < th:
                return s
        return cuts[-1][1]

    def corr_score(delta):
        """相关性 20d 相对 60d 抬升 = 同步化 = 系统性风险。"""
        if delta > 0.08:
            return -1.5
        if delta > 0.03:
            return -0.8
        if delta > -0.03:
            return 0.0
        if delta > -0.08:
            return 0.6
        return 1.2

    items = [
        ("趋势位置（距MA60）", d_ma60, band(d_ma60, [(-15, -2), (-8, -1.5), (-2, -0.5), (2, 0.5)]), 1.5),
        ("回撤（距60日高）", d_high, band(d_high, [(-25, -2), (-15, -1.5), (-5, -0.5), (0, 1)]), 1.2),
        ("广度（站上MA20）", above20, band(above20, [(35, -1.5), (45, -0.5), (60, 0.5), (200, 1.5)]), 1.3),
        ("个股动能（5日中位）", med5, band(med5, [(-3, -1.5), (-1, -0.5), (1, 0.5), (99, 1.5)]), 1.2),
        ("相关性（20d−60d）", (c20 - c_full) * 100, corr_score(c20 - c_full), 0.9),
        ("波动率（年化%）", vol, band(vol, [(25, 1.5), (38, 0.5), (50, -0.5), (999, -1.5)]), 1.0),
        ("短期（5日）", r5, 1.0 if (r5 > 0 and d_ma20 > 0) else (-0.5 if r5 < 0 else 0.0), 0.8),
    ]
    tot = sum(s * w for _, _, s, w in items) / sum(w for _, _, _, w in items)
    score = float(np.clip(tot, -2, 2))

    if score <= -1.0:
        verdict, tone = "偏风险", "结构偏弱，注意分化与咽喉节点风险"
    elif score <= -0.35:
        verdict, tone = "偏风险（轻）", "结构转弱，反弹需确认量能与广度"
    elif score < 0.35:
        verdict, tone = "中性", "结构中性，等待方向选择"
    elif score < 1.0:
        verdict, tone = "偏机会（轻）", "结构回暖，关注广度扩散能否延续"
    else:
        verdict, tone = "偏机会", "结构偏强，分化扩散中"
    return dict(score=score, verdict=verdict, tone=tone, items=items,
                d_ma60=d_ma60, d_high=d_high, above20=above20, med5=med5,
                c_full=c_full, c20=c20, vol=vol, r5=r5)


# ─────────────── hermer2 口径打分（规则型结构打分） ───────────────
SCORE_MODE_LABEL = {
    # hermer2 = 纯结构口径（相关矩阵几何），不含方向；方向另由「趋势层（含方向）」给出
    "hermer2": "结构口径：连通簇分化 · 闭环 · 平均曲率 · 簇数趋势（不含价格与动量）",
    "multi": "研判口径：趋势 · 回撤 · 广度 · 动能 · 相关性 · 波动 · 短期",
}


# hermer2 结构分档（**只描述结构状态，不含方向**）：顺序 = 分化偏高 → 聚合
VERDICT_ORDER = ["结构分化偏高", "结构略分化", "结构中性", "结构略聚合", "结构聚合"]
RISK_SIDE = ("结构分化偏高", "结构略分化")      # 分化侧（旧文档里叫「偏风险」，已改名）
OPPO_SIDE = ("结构略聚合", "结构聚合")          # 聚合侧（旧文档里叫「偏机会」，已改名）
NEUTRAL = "结构中性"


def corr_manifold_inputs(close: pd.DataFrame, k: int = 5):
    """从收盘面板算拓扑口径三件套：corr / b0 / b1 / 曲率均值。

    Betti 的阈值口径：距离矩阵 d=sqrt(2(1-corr))，取 d>0 的 35 分位（与画图一致）。
    返回 dict（betti 算不出时 b0=b1=-1）。
    """
    ret = np.log(close / close.shift(1)).dropna()
    corr = ret.corr().values
    corr = np.nan_to_num(corr, nan=0.0)
    dmat = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
    np.fill_diagonal(dmat, 0.0)
    pos = dmat[dmat > 0]
    thresh = float(np.percentile(pos, 35)) if pos.size else 0.0
    b0, b1 = betti_numbers(dmat, thresh)
    return dict(corr=corr, dmat=dmat, thresh=thresh, b0=b0, b1=b1)


def avg_curvature(corr: np.ndarray, k: int = 5):
    """节点平均 Ollivier-Ricci 曲率（取节点曲率均值 = 「平均曲率」）。"""
    try:
        curv, deg, G, ec = ollivier_ricci(corr, k=k)
        return float(np.mean(curv)), curv, deg, G, ec
    except Exception:
        return None, None, None, None, None


def hermer2_score(b0, b1, curv_mean, b0_prev, b0_now):
    """hermer2 口径打分（−2 风险 … +2 机会）。

    ⚠️ 重要声明：hermer2 的原始源码拿不到（它云端会话上下文 1.18M tokens
    超 1M 上限，所有 `coze agent at` 请求都 400 失败）。本函数**不是**它的源码复现，
    而是**按它自己公布过的分项结论反推**的规则口径 —— 阈值是反推值，不是原始实现值。

    反推依据（hermer2 公布原文）：
      科创50  形态=散点(高度分化) / Betti_0=12(>5 高度分化=风险信号) / Betti_1=0(无闭环)
              / 平均曲率 -0.49(结构脆弱) / Betti_0 趋势 前29天=4→后30天=6(分裂) → 打分 -2 偏风险
      沪深300 形态=丝带(发散) / Betti_0=1(单一簇) / 平均曲率 -0.40
              / Betti_0 趋势 2→1(收敛，机会信号) → 打分 -1 中性偏弱

    规则（全部为惩罚/奖励项，权重等权，总分 clip 到 [−2, +2]）：
      1. Betti_0（连通簇数，分化度）
           >=5 → −1（高度分化，风险信号）；==1 → +0.5（单一簇，聚合）；其余 0
      2. Betti_1（闭环数，独立方向）
           ==0 → −0.5（无闭环 / 无独立方向）；>=2 → +0.5；==1 → 0
      3. 平均曲率（节点曲率均值）
           < −0.35 → −1；−0.35~−0.2 → −0.5；−0.2~0 → −0.25；>=0 → +0.5
      4. Betti_0 趋势（后 30 日窗口 vs 前 30 日窗口）
           后段 > 前段 → −0.5（分裂）；后段 < 前段 → +0.5（收敛）；相等/不可算 → 0
      5. 总分 clip [−2, 2]，分档（**结构描述，不含方向**）：
           <=−1 结构分化偏高 / <=−0.35 结构略分化 / <0.35 结构中性
           / <1 结构略聚合 / >=1 结构聚合

    ⚠️ 口径声明（老板 2026-11-14 定调）：本分只有「连通簇数 / 闭环数 / 平均曲率 /
    簇数趋势」四项，**全部是相关系数矩阵的几何性质，不含任何价格、动量、涨跌幅信息**。
    所以它只能说明「结构现在长什么样」（分裂成很多阵营，还是拧成一股），
    **不能用来判方向**（涨还是跌）。旧版叫「偏风险 / 偏机会」是错的 ——
    那是把结构状态当成了方向结论。图上已改名「结构分化偏高 / 结构略分化 /
    结构中性 / 结构略聚合 / 结构聚合」，方向另由「趋势层（含方向）」单独一行给出。

    ⚠️ 另一件事（老板原话「它一直说是风险」）：上面 4 项里只有 2 项能给正分，
    且需 Betti_0==1 或曲率>=0 这类较严苛条件才给；结构上天然偏「分化」。
    这正是 --audit 要量化验证的东西（不是靠感觉）。
    """
    items = []

    # 1) Betti_0 分化度
    if b0 is None or b0 < 0:
        s_b0, v_b0 = 0.0, float("nan")
    else:
        v_b0 = float(b0)
        s_b0 = -1.0 if b0 >= 5 else (0.5 if b0 == 1 else 0.0)
    items.append(("连通簇数（分化度）", v_b0, s_b0))

    # 2) Betti_1 闭环
    if b1 is None or b1 < 0:
        s_b1, v_b1 = 0.0, float("nan")
    else:
        v_b1 = float(b1)
        s_b1 = -0.5 if b1 == 0 else (0.5 if b1 >= 2 else 0.0)
    items.append(("闭环个数（独立方向）", v_b1, s_b1))

    # 3) 平均曲率
    if curv_mean is None or not np.isfinite(curv_mean):
        s_c, v_c = 0.0, float("nan")
    else:
        v_c = float(curv_mean)
        if v_c < -0.35:
            s_c = -1.0
        elif v_c < -0.2:
            s_c = -0.5
        elif v_c < 0.0:
            s_c = -0.25
        else:
            s_c = 0.5
    items.append(("平均曲率", v_c, s_c))

    # 4) Betti_0 趋势（前段 → 后段）
    if (b0_prev is None or b0_now is None
            or not np.isfinite(b0_prev) or not np.isfinite(b0_now)
            or b0_prev < 0 or b0_now < 0):
        s_t, v_t = 0.0, float("nan")
        trend_txt = "不可算"
    else:
        v_t = float(b0_now - b0_prev)
        if b0_now > b0_prev:
            s_t = -0.5
            trend_txt = "分裂"
        elif b0_now < b0_prev:
            s_t = 0.5
            trend_txt = "收敛"
        else:
            s_t = 0.0
            trend_txt = "持平"
    items.append(("簇数趋势（%s）" % trend_txt, v_t, s_t))

    tot = sum(s for _, _, s in items)
    score = float(np.clip(tot, -2.0, 2.0))

    # ⚠️ 标签只描述「结构状态」，不含方向（见函数头口径声明）
    if score <= -1.0:
        verdict = "结构分化偏高"
        tone = "分成了多个阵营、互不联动 —— 只说明结构状态，不判涨跌"
    elif score <= -0.35:
        verdict = "结构略分化"
        tone = "阵营略有增加、联动变弱 —— 只说明结构状态，不判涨跌"
    elif score < 0.35:
        verdict = "结构中性"
        tone = "聚合与分化并存、无明确偏向 —— 只说明结构状态，不判涨跌"
    elif score < 1.0:
        verdict = "结构略聚合"
        tone = "开始出现局部聚合 / 闭环 —— 只说明结构状态，不判涨跌"
    else:
        verdict = "结构聚合"
        tone = "掰成单一簇、存在闭环、曲率为正 —— 只说明结构状态，不判涨跌"

    return dict(score=score, verdict=verdict, tone=tone, items=items,
                b0=v_b0, b1=v_b1, curv=v_c, trend=v_t, trend_txt=trend_txt,
                b0_prev=(float(b0_prev) if b0_prev is not None else float("nan")),
                b0_now=(float(b0_now) if b0_now is not None else float("nan")),
                mode="hermer2")


def trend_layer(close: pd.DataFrame, index_code=None):
    """趋势层（**真正含方向**的数据）—— 与结构分物理隔离，单独一行输出。

    为什么单独拉一层：hermer2 结构分只看相关矩阵的几何（连通簇/闭环/曲率/簇数趋势），
    里面**一个字的价格都没有**，判不了涨跌。要看方向必须另算，所以这里给一条
    纯价格口径的趋势读数，从不参与结构打分。

    口径说明：
      · 基准序列 = 指数自己的收盘（传了 index_code 且取到）；取不到则用
        **成分股等权合成**（每只归一到 1000 再取均值，等价「把成分股等权买一遍」）。
      · r60 / r10 / r5   = 基准近 60 / 10 / 5 个交易日的涨跌幅（%）
      · dd              = 基准现价距「近 60 日最高点」的回撤（%，负数=回撤）
      · vs_ma60         = 基准现价相对 60 日均线（%）
      · above20         = 站上自己 MA20 的成分股占比（%，广度；成分等权，与基准无关）

    返回 dict：r60 / dd / vs_ma60 / r10 / r5 / above20 / basis（口径字符串）
    """
    # 基准序列：指数收盘优先，取不到就用成分等权合成
    idx = None
    basis = "成分等权合成"
    if index_code:
        try:
            idx = _index_close_series(index_code, days=120)
        except Exception:
            idx = None
    if idx is not None and len(idx) >= 25:
        basis = "指数收盘 %s" % index_code
        _s = idx.copy()
        _s.index = _s.index.astype(str)
        idx = _s.tail(len(close)).astype(float)
    else:
        _c = close.copy()
        _c.index = _c.index.astype(str)
        idx = (_c / _c.iloc[0] * 1000).mean(axis=1)

    idx = pd.Series(idx).dropna()
    last = float(idx.iloc[-1])
    n = len(idx)

    def _ret(k):
        if n > k:
            return (last / float(idx.iloc[-k - 1]) - 1) * 100
        return (last / float(idx.iloc[0]) - 1) * 100

    r60 = _ret(60)
    r10 = _ret(10)
    r5 = _ret(5)
    hi60 = float(idx.tail(60).max())
    dd = (last / hi60 - 1) * 100
    ma60 = float(idx.rolling(min(60, n)).mean().iloc[-1])
    vs_ma60 = (last / ma60 - 1) * 100

    try:
        ma20 = close.rolling(20).mean()
        above20 = float((close.iloc[-1] > ma20.iloc[-1]).mean() * 100)
    except Exception:
        above20 = float("nan")

    return dict(r60=float(r60), dd=float(dd), vs_ma60=float(vs_ma60),
                r10=float(r10), r5=float(r5), above20=float(above20), basis=basis,
                days=int(n), last=last)


def trend_layer_text(tl: dict) -> str:
    """趋势层单行文案（图上一行 + stdout 同一份）。前缀写死，不许改。"""
    if not tl:
        return "趋势层（含方向）：数据不足，未计算"
    return ("趋势层（含方向）：近60日 %+.1f%% · 距60日高 %+.1f%% · vs MA60 %+.1f%%"
            " · 近10日 %+.1f%% · 近5日 %+.1f%% · 站上MA20 %.0f%%"
            % (tl["r60"], tl["dd"], tl["vs_ma60"], tl["r10"], tl["r5"], tl["above20"]))


def pc1_profile(close: pd.DataFrame, corr: np.ndarray, codes, names, top_n: int = 10):
    """第一主成分画像：它到底是个什么东西。

    回答老板的问题：第一主成分既不是一只股票，也不是一个选股因子，
    而是**从这一篮子股票里"算"出来的一条人造指数线**（线性组合 Σ w_i · r_i）。
      · 解释占比 = 这条线能吃下多少"共同波动"。占比越高 → 板块越像"一个东西"。
      · 权重符号 = 成员是否同向。几乎全同号 → 齐涨齐跌；有正有负 → 内部对冲。
      · 权重最大那几只 = 这个板块的"主心骨"（它们动，指数才动）。
    """
    ret = np.log(close / close.shift(1)).dropna()
    ev, evec = np.linalg.eigh(corr)
    order = np.argsort(ev)[::-1]
    ev, evec = ev[order], evec[:, order]
    share = ev / ev.sum()
    w = evec[:, 0].copy()
    if w.sum() < 0:
        w = -w

    idx = np.argsort(np.abs(w))[::-1][:top_n]
    top = [(names.get(codes[i], codes[i]), float(w[i])) for i in idx]

    # 第一主成分合成线 vs 等权指数（都归一到 100）
    r = ret.values
    pc = r @ w
    eq = r.mean(axis=1)
    pc_s = np.exp(np.cumsum(pc)) * 100
    eq_s = np.exp(np.cumsum(eq)) * 100

    return dict(share1=float(share[0]), share2=float(share[1]), share3=float(share[2]),
                share5=float(share[:5].sum()), eig1=float(ev[0]),
                n_pos=int((w > 0).sum()), n_neg=int((w < 0).sum()),
                w_min=float(w.min()), w_max=float(w.max()),
                top=top, pc=pc_s, eq=eq_s, w=w,
                corr_pc_eq=float(np.corrcoef(pc, eq)[0, 1]))


def structure_diag(close: pd.DataFrame, corr: np.ndarray, k_cluster: int = 5):
    """结构诊断：当前是「抱团」还是「撕裂」，两者各占多少。

    老板的判断（对）：抱团 = 既是风险也是机会 ——
      · 抱团（同步度高）→ 一起涨也一起跌，是**系统性风险**的来源；
      · 撕裂（阵营分化）→ 阵营间轮动，是**机会**的来源。
    所以不能只给一个「风险/机会」标签，要把这两个维度分开量化。

    返回 dict：
      sync20 / sync60 / sync_pct  —— 20日均同步度、60日均、当前在历史的分位
      pc1                          —— 第一主成分解释占比（抱团度的另一视角）
      n_bloc                       —— 阵营数（连通簇）
      w_in / w_out / gap           —— 簇内平均相关 / 簇间平均相关 / 差值（撕裂度）
      tear, hug                    —— 撕裂度、抱团度（0~1，可直接读）
      label / note                 —— 一句话诊断
    """
    ret = np.log(close / close.shift(1)).dropna()
    n = corr.shape[0]
    iu = np.triu_indices(n, 1)

    sync60 = float(corr[iu].mean())
    sync20 = float(ret.tail(20).corr().values[iu].mean()) if len(ret) >= 20 else sync60

    # 同步度历史分位（20日滚动）
    roll = []
    for k in range(20, len(ret) + 1):
        w = ret.iloc[k - 20:k].corr().values
        roll.append(float(w[iu].mean()))
    roll = np.asarray(roll)
    sync_pct = float((roll < roll[-1]).mean() * 100) if len(roll) > 5 else 50.0

    ev = np.linalg.eigvalsh(corr)[::-1]
    pc1 = float(ev[0] / ev.sum())

    # 阵营划分（谱聚类，不可用时退化为按 1-相关做层次聚类）
    try:
        from sklearn.cluster import SpectralClustering
        d = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
        lab = SpectralClustering(n_clusters=k_cluster, affinity="precomputed",
                                 random_state=7).fit_predict(1.0 / (1.0 + d))
    except Exception:
        d = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
        np.fill_diagonal(d, 0.0)
        order = np.argsort(d.sum(axis=1))
        lab = np.zeros(n, dtype=int)
        for r, i in enumerate(order):
            lab[i] = r % k_cluster

    win, wout = [], []
    for i in range(n):
        for j in range(i + 1, n):
            (win if lab[i] == lab[j] else wout).append(float(corr[i, j]))
    w_in = float(np.mean(win)) if win else float("nan")
    w_out = float(np.mean(wout)) if wout else float("nan")
    gap = w_in - w_out if np.isfinite(w_in) and np.isfinite(w_out) else 0.0
    n_bloc = int(len(set(lab.tolist())))

    # 归一化到 0~1
    hug = float(np.clip((sync_pct / 100.0), 0, 1))                       # 抱团度 = 同步度历史分位
    tear = float(np.clip((gap + 0.05) / 0.40, 0, 1))                     # 撕裂度 = 簇内簇间差
    hug_lv = "高" if hug >= 0.75 else ("中" if hug >= 0.45 else "低")
    tear_lv = "强" if tear >= 0.6 else ("中" if tear >= 0.3 else "弱")

    if hug >= 0.7 and tear >= 0.5:
        label = "抱团 + 撕裂并存"
        note = ("板块内部分成了 %d 个阵营（撕裂 → 有轮动、有独立方向，是机会来源），"
                "但每个阵营内部高度抱团、整体同步度处在历史 %d%% 分位（抱团 → 一起跌，是风险来源）。"
                "这种结构要分开看：阵营内部一起倒的时候杀伤大，阵营之间会轮动。" % (n_bloc, round(sync_pct)))
    elif hug >= 0.7:
        label = "整体抱团"
        note = ("同步度处在历史 %d%% 分位 —— 板块几乎一起动，个股差异被抹平。"
                "抱团后期是风险（一起跌，无避风港），抱团初期是机会（资金集中推涨）。" % round(sync_pct))
    elif tear >= 0.5:
        label = "结构撕裂"
        note = ("簇内相关 %.2f、簇间相关 %.2f，分成 %d 个阵营、互不联动。"
                "撕裂 = 分化行情，选股机会多，但指数级机会少。" % (w_in, w_out, n_bloc))
    else:
        label = "结构松散"
        note = "既不明显抱团、也没有清晰阵营，资金没有主线。"

    return dict(sync20=sync20, sync60=sync60, sync_pct=sync_pct, pc1=pc1,
                n_bloc=n_bloc, w_in=w_in, w_out=w_out, gap=gap,
                tear=tear, hug=hug, hug_lv=hug_lv, tear_lv=tear_lv,
                label=label, note=note)


def _wrap(txt: str, width: int = 24):
    """中文按字数硬折行（不依赖第三方库）。"""
    out, cur, ln = [], "", 0
    for ch in txt:
        cur += ch
        ln += 1
        if ch in "，。；、：）" or ln >= width:
            out.append(cur)
            cur, ln = "", 0
    if cur:
        out.append(cur)
    return out


def betti_numbers(dmat: np.ndarray, thresh: float):
    """ripser 实测在给定半径下的 Betti_0 / Betti_1（有限距离矩阵）。"""
    try:
        from ripser import ripser
        res = ripser(dmat, distance_matrix=True, maxdim=1, thresh=thresh)
        dgms = res["dgms"]
        b0 = int(np.sum((dgms[0][:, 1] > thresh) | np.isinf(dgms[0][:, 1])))
        b1 = int(np.sum(dgms[1][:, 1] > thresh)) if len(dgms) > 1 and len(dgms[1]) else 0
        return b0, b1
    except Exception:
        return -1, -1


def umap_embed(dmat: np.ndarray, n_neighbors: int = 10, min_dist: float = 0.3, seed: int = 7):
    import umap
    reducer = umap.UMAP(
        metric="precomputed",
        n_neighbors=min(n_neighbors, max(2, dmat.shape[0] - 1)),
        min_dist=min_dist,
        random_state=seed,
        n_jobs=1,
    )
    return reducer.fit_transform(dmat)


def zs(x: np.ndarray) -> np.ndarray:
    s = np.std(x)
    return (x - np.mean(x)) / s if s > 1e-12 else np.zeros_like(x)


# ─────────────────────── 画图 ───────────────────────
def plot(emb, mom5, curv, deg, corr, close, codes, names, index_code, index_name, out_path,
         k=5, mkt=None, diag=None, pcp=None, trend=None):
    n = len(codes)
    # 颜色分类：动量 × 结构位置
    combo = 0.6 * zs(mom5) + 0.4 * zs(curv)  # 曲率越负越脆 → 负向贡献
    cls = np.where(combo >= 0.45, 0, np.where(combo <= -0.45, 1, 2))  # 0绿 1红 2蓝
    colmap = {0: GREEN, 1: RED, 2: BLUE}
    colors = [colmap[c] for c in cls]

    # 气泡大小：动量分位线性映射（涨越多越大）
    mr = pd.Series(mom5).rank(pct=True).values
    size = 60 + 620 * mr

    # 曲率：咽喉 = 最负 3 只
    order = np.argsort(curv)
    throat = list(order[:3])

    mkt = mkt or {}
    score = mkt.get("score", 0.0)
    verdict = mkt.get("verdict", "中性")
    tone = mkt.get("tone", "结构中性，等待方向选择")
    mode = mkt.get("mode", "multi")

    dmat = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
    # Betti 口径与打分器保持同一套：33 分位阈值（hermer2 模式下直接复用打分器的值）
    if mkt.get("mode") == "hermer2" and np.isfinite(mkt.get("b0", np.nan)):
        b0, b1 = int(mkt["b0"]), int(mkt.get("b1", 0) if np.isfinite(mkt.get("b1", np.nan)) else 0)
    else:
        b0, b1 = betti_numbers(dmat, float(np.percentile(dmat[dmat > 0], 35)))
    shape_txt = "散点（高度分化）" if b1 == 0 else "含环（存在独立阵营）"
    if b0 < 0:
        shape_txt, b0, b1 = "散点", 0, 0

    fig = plt.figure(figsize=(22.6, 17.2), dpi=100, facecolor="white")
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.05], wspace=0.09,
                          left=0.045, right=0.935, top=0.768, bottom=0.492)

    # ═══════════ 左：UMAP 2D ═══════════
    ax = fig.add_subplot(gs[0, 0])
    ax.set_facecolor("white")

    # 机会 / 风险 软背景
    for c, lab, tint in ((0, "机会区", GREEN), (1, "风险区", RED)):
        m = cls == c
        if m.sum() >= 2:
            cx, cy = emb[m, 0].mean(), emb[m, 1].mean()
            r = max(np.ptp(emb[m, 0]), np.ptp(emb[m, 1])) * 0.62 + 0.35
            ax.add_patch(plt.Circle((cx, cy), r, color=tint, alpha=0.055, zorder=0))
            ax.text(cx, cy, lab, color=tint, alpha=0.55, fontsize=17,
                    ha="center", va="center", zorder=1)

    sc = ax.scatter(emb[:, 0], emb[:, 1], s=size, c=colors, alpha=0.72,
                    edgecolors="white", linewidths=1.1, zorder=3)

    # 标注：动量最强 / 最弱 各 2 只，咽喉股加方框
    def tag(i, color, weight="normal"):
        ax.annotate(names.get(codes[i], codes[i]), (emb[i, 0], emb[i, 1]),
                    textcoords="offset points", xytext=(0, 13),
                    fontsize=11.5, color=color, ha="center", weight=weight,
                    path_effects=[pe.withStroke(linewidth=3.2, foreground="white")],
                    zorder=6)

    for i in np.argsort(mom5)[-2:]:
        tag(i, "#1f7a4d")
    for i in np.argsort(mom5)[:2]:
        tag(i, "#a83232")
    for i in throat:
        ax.scatter([emb[i, 0]], [emb[i, 1]], s=size[i] + 210, facecolors="none",
                   edgecolors=NAVY, linewidths=1.8, zorder=5)
        ax.annotate(names.get(codes[i], codes[i]), (emb[i, 0], emb[i, 1]),
                    textcoords="offset points", xytext=(0, -22),
                    fontsize=11.5, color=NAVY, ha="center", weight="bold",
                    path_effects=[pe.withStroke(linewidth=3.2, foreground="white")],
                    zorder=6)

    ax.set_xlabel("结构位置 ①", fontsize=13, color=INK)
    ax.set_ylabel("结构位置 ②", fontsize=13, color=INK)
    ax.set_title("个股结构位置 · 颜色＝动能强弱，气泡大小＝近5日涨幅",
                 fontsize=17.5, color=INK, pad=12)
    ax.grid(color=GRID, lw=0.7, alpha=0.9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c8c8c8")
    ax.tick_params(colors=MUTED, labelsize=11)

    handles = [
        Line2D([], [], marker="o", ls="", ms=11, mfc=GREEN, mec="white", label="机会核心"),
        Line2D([], [], marker="o", ls="", ms=11, mfc=RED, mec="white", label="风险边缘"),
        Line2D([], [], marker="o", ls="", ms=11, mfc=BLUE, mec="white", label="中性过渡"),
        Line2D([], [], marker="o", ls="", ms=13, mfc="none", mec=NAVY, mew=1.8,
               label="咽喉股票（最易带崩板块）"),
    ]
    ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=12,
              handletextpad=0.35, borderaxespad=0.6)

    ax.text(0.5, -0.088,
            "整体形态：%s ｜ 连通簇 %d 个 ｜ 闭环 %d 个" % (shape_txt, b0, b1),
            transform=ax.transAxes, ha="center", fontsize=12.5, color=MUTED)

    # ═══════════ 右：关联网络 × 里奇曲率 ═══════════
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor("white")

    import networkx as nx
    sim = np.clip(corr, 0, None).copy()
    np.fill_diagonal(sim, 0.0)
    G = nx.Graph()
    G.add_nodes_from(range(n))
    for i in range(n):
        for j in np.argsort(sim[i])[::-1][:k]:
            if i < j:
                G.add_edge(i, int(j), w=float(sim[i, j]))
    pos = nx.spring_layout(G, weight="w", seed=11, k=1.9 / np.sqrt(n), iterations=260)

    # 边：相关性越强越深
    for u, v, d in G.edges(data=True):
        a = float(np.clip((d["w"] - 0.2) / 0.7, 0.08, 0.75))
        ax2.plot([pos[u][0], pos[v][0]], [pos[u][1], pos[v][1]],
                 color="#9aa7bb", lw=0.7 + 1.6 * max(0.0, d["w"] - 0.3), alpha=a, zorder=1)

    # 颜色用「排名归一化」：让 50 只股票把整条色带（红→橙→黄→绿→蓝）均匀铺开，
    # 避免绝大多数挤在某一端导致看起来「都是同一个颜色」。
    from matplotlib.colors import Normalize as _Norm
    _rk = pd.Series(curv).rank(method="average").values
    _rkn = (_rk - 1) / max(1.0, (len(_rk) - 1))          # 0~1 均匀
    vmin, vmax = float(np.min(curv)), float(np.max(curv))
    norm = _Norm(vmin=0.0, vmax=1.0)
    _cval = _rkn
    sizes2 = 170 + 8 * deg
    for i in range(n):
        c = CURV_CMAP(norm(_cval[i]))
        ax2.scatter([pos[i][0]], [pos[i][1]], s=sizes2[i], c=[c],
                    edgecolors="white", linewidths=1.1, zorder=3)
    # 只标咽喉股名字（曲率数值放图例，避免文字打架）
    placed = []
    for i in throat:
        ax2.scatter([pos[i][0]], [pos[i][1]], s=sizes2[i] + 420, facecolors="none",
                    edgecolors=NAVY, linewidths=2.0, zorder=4)
        x, y = pos[i]
        dx, dy = (0, 22) if y >= 0 else (0, -30)
        # 避免两个标注重叠
        for px, py in placed:
            if abs(px - x) < 0.12 and abs(py - y) < 0.14:
                dx, dy = (dx + 55, dy)
        placed.append((x, y))
        ax2.annotate("%s  脆弱度 %.2f" % (names.get(codes[i], codes[i]), curv[i]),
                     (x, y), textcoords="offset points",
                     xytext=(dx, dy), fontsize=11.5, color=NAVY, ha="center",
                     weight="bold",
                     path_effects=[pe.withStroke(linewidth=3.4, foreground="white")],
                     zorder=6)

    ax2.set_xlim(ax2.get_xlim()[0] - 0.14, ax2.get_xlim()[1] + 0.14)
    ax2.set_ylim(ax2.get_ylim()[0] - 0.10, ax2.get_ylim()[1] + 0.10)
    ax2.set_title("个股联动关系 · 颜色＝结构脆弱度（与涨跌无关）", fontsize=17.5, color=INK, pad=12)
    ax2.set_xticks([]); ax2.set_yticks([])
    for s in ax2.spines.values():
        s.set_visible(False)

    ax2.legend(handles=[Line2D([], [], marker="o", ls="", ms=13, mfc="none", mec=NAVY,
                               mew=2.0, label="咽喉 = 板块联动的枢纽股票（见下说明）")],
               loc="upper left", frameon=False, fontsize=12, handletextpad=0.35,
               borderaxespad=0.6)

    cax = fig.add_axes([0.952, 0.40, 0.012, 0.26])
    cb = matplotlib.colorbar.ColorbarBase(cax, cmap=CURV_CMAP, norm=norm,
                                          orientation="vertical")
    cb.set_label("结构脆弱度（越红越脆弱 · 越蓝越稳固）", fontsize=11.5, color=INK)
    cb.set_ticks([0.0, 0.5, 1.0])
    cb.set_ticklabels(["最脆弱", "中位", "最稳固"])
    cb.ax.tick_params(labelsize=10.5, colors=MUTED)
    cb.outline.set_edgecolor("#cccccc")

    note = ("咽喉 = 板块联动网络里的枢纽股票。\n"
            "它们跟同板块很多只股票强相关、卡在传导要道上（图上越红越脆弱）。\n"
            "含义：它们若先跌，容易把同板块一起带跌（级联风险），\n"
            "所以叫「最脆弱」。注意：这说的是位置敏感，≠ 它们现在就在跌。")
    ax2.text(0.005, 0.004, note, transform=ax2.transAxes, fontsize=11.5,
             color="#4a4a4a", va="bottom", ha="left", linespacing=1.65,
             bbox=dict(boxstyle="round,pad=0.55", fc="white", ec="#e0e0e0",
                       lw=0.9, alpha=0.94), zorder=7)

    # ═══════════ 标题区 ═══════════
    fig.text(0.045, 0.982, "%s 成分股结构图（左：位置与动能 / 右：联动与脆弱点）"
             % index_name,
             fontsize=26, color=TITLE_RED, weight="bold")
    fig.text(0.045, 0.955, SCORE_MODE_LABEL.get(mode, "口径：多维加权"),
             fontsize=12.5, color="#b06a00", weight="bold")
    if mode == "hermer2":
        # 结构分的边界声明（老板 2026-11-14：结构分不含方向，必须写在图上）
        fig.text(0.045, 0.934,
                 "结构分只看连通簇 / 闭环 / 曲率 / 簇数趋势，不含任何价格与动量信息，不能用来判方向",
                 fontsize=11.8, color="#8a5a00")
        _vprefix = "结构研判（不含方向）"
    else:
        _vprefix = "综合研判"
    fig.text(0.045, 0.909, "%s：%s（%+.2f）   %s" % (_vprefix, verdict, score, tone),
             fontsize=16.5, color=INK)
    # 分项一览（小字，放在标题下方右侧）
    if mkt.get("items"):
        bits = "  ·  ".join("%s %+.1f" % (nm.split("（")[0], s)
                            for nm, _v, s, *_rest in mkt["items"])
        fig.text(0.045, 0.884, bits, fontsize=11.2, color=MUTED)
    _detail = ""
    if mode == "hermer2":
        _detail = "连通簇 %s 个 · 闭环 %s 个 · 平均脆弱度 %.2f · 簇数趋势 %s→%s（%s）" % (
            ("—" if not np.isfinite(mkt.get("b0", np.nan)) else int(mkt["b0"])),
            ("—" if not np.isfinite(mkt.get("b1", np.nan)) else int(mkt["b1"])),
            (mkt.get("curv") if np.isfinite(mkt.get("curv", np.nan)) else float("nan")),
            ("—" if not np.isfinite(mkt.get("b0_prev", np.nan)) else int(mkt["b0_prev"])),
            ("—" if not np.isfinite(mkt.get("b0_now", np.nan)) else int(mkt["b0_now"])),
            mkt.get("trend_txt", "—"),
        )
    _sample = "样本：%d 只成分股 · 近 %d 个交易日 · 联动按最近 %d 只强相关股票连线" % (
        n, len(close) - 1, k)
    _y = 0.860
    fig.text(0.045, _y, _detail or _sample, fontsize=12.0, color=MUTED)
    if _detail:
        _y -= 0.021
        fig.text(0.045, _y, _sample, fontsize=11.8, color=MUTED)

    # ═══════════ 趋势层（含方向）—— 与结构分物理隔离，单独一行 ═══════════
    # 老板 2026-11-14：结构分不含方向，方向必须另给一行；前缀「趋势层（含方向）：」写死。
    _tt = trend_layer_text(trend)
    fig.text(0.045, 0.816, _tt, fontsize=15.5, color="#0d2b5e", weight="bold",
             bbox=dict(boxstyle="round,pad=0.42", fc="#f2f6fd", ec="#c8d8f0",
                       lw=0.9, alpha=0.96))
    fig.text(0.045, 0.797, "↑ 这一行才是含方向的（纯价格口径：%s）；上面结构分不参与此计算"
             % ((trend or {}).get("basis", "成分等权合成")),
             fontsize=11.0, color="#5a6b86")
    fig.text(0.5, 0.016,
             "气泡大小 = 近5日动量（五日动量涨得越多气泡越大，跌得越多气泡越小）",
             fontsize=13.5, color=MUTED, ha="center")

    # ═══════════ 底部注释带：抱团 还是 撕裂 ═══════════
    if diag:
        bax = fig.add_axes([0.045, 0.288, 0.890, 0.178])
        bax.set_facecolor("#fbfbfc")
        bax.set_xticks([]); bax.set_yticks([])
        for sp in bax.spines.values():
            sp.set_color("#dcdcdc"); sp.set_linewidth(1.0)
        bax.set_xlim(0, 1); bax.set_ylim(0, 1)

        bax.text(0.012, 0.985, "结构诊断：这是「抱团」还是「撕裂」？（两者可以同时成立）",
                 fontsize=14.5, color=TITLE_RED, weight="bold", va="top")

        # ---- 左：抱团度 / 撕裂度 两条读数条 ----
        bars = [
            ("抱团度", diag["hug"], diag["hug_lv"], "#e05252",
             "同步度处历史 %d%% 分位 · 板块一起动（风险来源）" % round(diag["sync_pct"])),
            ("撕裂度", diag["tear"], diag["tear_lv"], "#2e6fbf",
             "簇内 %.2f ｜ 簇间 %.2f · %d 个阵营不联动（机会来源）"
             % (diag["w_in"], diag["w_out"], diag["n_bloc"])),
        ]
        y0 = 0.60
        for nm, val, lv, col, sub in bars:
            bax.text(0.014, y0 + 0.075, nm, fontsize=13.0, color=INK, weight="bold", va="center")
            bax.text(0.082, y0 + 0.075, "%s %.0f%%" % (lv, val * 100),
                     fontsize=12.5, color=col, weight="bold", va="center")
            bax.plot([0.014, 0.352], [y0, y0], color="#e8e8e8", lw=10,
                     solid_capstyle="round", zorder=1)
            bax.plot([0.014, 0.014 + 0.338 * float(np.clip(val, 0, 1))], [y0, y0],
                     color=col, lw=10, solid_capstyle="round", zorder=2)
            bax.text(0.014, y0 - 0.105, sub, fontsize=10.5, color=MUTED, va="center")
            y0 -= 0.30

        bax.text(0.014, 0.075,
                 "抱团 = 既看风险也看机会：抱团初期资金集中推涨（机会），抱团末期一起崩（风险）。",
                 fontsize=10.5, color="#3a3a3a", va="center")

        # ---- 右：抱团度 × 撕裂度 定位图 ----
        qx0, qx1, qy0, qy1 = 0.400, 0.590, 0.215, 0.845
        qw, qh = qx1 - qx0, qy1 - qy0
        bax.plot([qx0, qx1, qx1, qx0, qx0], [qy0, qy0, qy1, qy1, qy0],
                 color="#cfcfcf", lw=1.0)
        bax.plot([qx0 + qw / 2, qx0 + qw / 2], [qy0, qy1], color="#ececec", lw=0.9, ls="--")
        bax.plot([qx0, qx1], [qy0 + qh / 2, qy0 + qh / 2], color="#ececec", lw=0.9, ls="--")
        bax.text((qx0 + qx1) / 2, qy0 - 0.085, "抱团度 →（越右越同步）",
                 fontsize=9.8, color=MUTED, ha="center")
        bax.text(qx0 - 0.010, (qy0 + qy1) / 2, "撕裂度", fontsize=9.8, color=MUTED,
                 ha="right", va="center", rotation=90)

        px = qx0 + qw * float(np.clip(diag["hug"], 0.02, 0.98))
        py = qy0 + qh * float(np.clip(diag["tear"], 0.02, 0.98))
        bax.scatter([px], [py], s=200, c=[TITLE_RED], edgecolors="white",
                    linewidths=1.6, zorder=5)
        _dx, _dy = (-96, 4) if px > qx0 + qw * 0.6 else (96, 4)
        bax.annotate("现在：%s" % diag["label"], (px, py), textcoords="offset points",
                     xytext=(_dx, _dy), fontsize=11.0, color=TITLE_RED, weight="bold",
                     ha="center", va="center",
                     path_effects=[pe.withStroke(linewidth=3.2, foreground="white")], zorder=6)

        # ---- 最右：一句话结论 ----
        _lines = _wrap(diag["note"], 24)[:6]
        bax.text(0.628, 0.52, "\n".join(_lines), fontsize=10.6, color="#3a3a3a",
                 va="center", ha="left", linespacing=1.75)

    # ═══════════ 第三条带：第一主成分是什么 ═══════════
    if pcp:
        pax = fig.add_axes([0.045, 0.045, 0.890, 0.212])
        pax.set_facecolor("#fbfbfc")
        pax.set_xticks([]); pax.set_yticks([])
        for sp in pax.spines.values():
            sp.set_color("#dcdcdc"); sp.set_linewidth(1.0)
        pax.set_xlim(0, 1); pax.set_ylim(0, 1)

        pax.text(0.012, 0.955,
                 "第一主成分是什么？—— 它不是某只股票，也不是选股因子，是「这一篮子股票合起来的一条人造指数线」",
                 fontsize=14.2, color=TITLE_RED, weight="bold", va="top")
        pax.text(0.012, 0.855,
                 "算法：把每只股票的权重 × 它的涨跌幅，全部加起来（数学叫「主成分分析」）。"
                 "这一条线能解释板块 %.1f%% 的共同涨跌 —— 百分比越高，板块越像「一个东西」。"
                 % (pcp["share1"] * 100),
                 fontsize=11.2, color="#3a3a3a", va="top")

        # ---- 左：解释占比条（PC1..PC3） ----
        pax.text(0.018, 0.655, "共同波动被谁吃掉了", fontsize=11.5, color=INK, weight="bold")
        _bars = [("第一条线", pcp["share1"], "#b3202e"),
                 ("第二条线", pcp["share2"], "#e0a23a"),
                 ("第三条线", pcp["share3"], "#7fa8c9")]
        _yy = 0.545
        for nm, v, col in _bars:
            pax.text(0.018, _yy + 0.052, nm, fontsize=10.8, color=INK, va="center")
            pax.text(0.128, _yy + 0.052, "%.1f%%" % (v * 100), fontsize=10.8,
                     color=col, weight="bold", va="center")
            pax.plot([0.205, 0.335], [_yy, _yy], color="#e8e8e8", lw=9,
                     solid_capstyle="round", zorder=1)
            pax.plot([0.205, 0.205 + 0.130 * min(1.0, v / 0.5)], [_yy, _yy],
                     color=col, lw=9, solid_capstyle="round", zorder=2)
            _yy -= 0.135
        pax.text(0.018, 0.13,
                 "前五条线合计 %.1f%%（剩下 %d 只股票只贡献 %.1f%%）"
                 % (pcp["share5"] * 100, len(pcp["top"]), 100 - pcp["share5"] * 100),
                 fontsize=10.4, color=MUTED, va="center")
        pax.text(0.018, 0.045,
                 "成员同向：正权重 %d 只 / 负权重 %d 只 —— 近乎全同向 = 齐涨齐跌"
                 % (pcp["n_pos"], pcp["n_neg"]),
                 fontsize=10.4, color=MUTED, va="center")

        # ---- 中：主心骨名单（权重最大的股票） ----
        pax.text(0.375, 0.655, "这条线主要由谁决定（权重最大）",
                 fontsize=11.5, color=INK, weight="bold")
        _ys = 0.545
        for k, (nm2, wv) in enumerate(pcp["top"][:10]):
            col = "#b3202e" if wv > 0 else "#2e6fbf"
            pax.plot([0.378, 0.378 + 0.115 * min(1.0, abs(wv) / 0.22)], [_ys, _ys],
                     color=col, lw=6.5, solid_capstyle="round", alpha=0.85)
            pax.text(0.500, _ys, "%s  %+.2f" % (nm2, wv), fontsize=10.4,
                     color="#333333", va="center")
            _ys -= 0.058

        # ---- 右：合成线 vs 等权指数 ----
        from matplotlib import axes as _maxes
        iax = fig.add_axes([0.640, 0.070, 0.272, 0.160])
        iax.plot(pcp["eq"], color="#8a8a8a", lw=1.5, label="等权指数")
        iax.plot(pcp["pc"], color="#b3202e", lw=1.9, label="第一主成分合成线")
        iax.set_xticks([]); iax.tick_params(labelsize=8.5, colors=MUTED)
        iax.legend(fontsize=9.2, frameon=False, loc="upper left")
        iax.set_title("两条线几乎重合（相关 %.3f）" % pcp["corr_pc_eq"], fontsize=10.2,
                      color=INK, pad=4)
        for s2 in ("top", "right"):
            iax.spines[s2].set_visible(False)
        for s2 in ("left", "bottom"):
            iax.spines[s2].set_color("#cccccc")
        iax.grid(color="#eeeeee", lw=0.6, alpha=0.9)

        pax.text(0.640, 0.245,
                 "它跟「把 50 只股票等权买一遍」几乎一样 —— 所以它衡量的是"
                 "整个板块的共同情绪，不是某只票的强弱。",
                 fontsize=10.4, color="#3a3a3a", va="top")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return dict(score=score, verdict=verdict, b0=b0, b1=b1, items=mkt.get("items"),
                throat=[(names.get(codes[i], codes[i]), round(float(curv[i]), 2)) for i in throat])


# ─────────────── 偏误审计（hermer2 口径） ───────────────
def _index_close_series(index_code: str, days: int = 700):
    """指数收盘序列（严格 IPv4，不走新接口）。失败返回 None。"""
    try:
        sys.path.insert(0, _CORE)
        from tushare_init import get_pro  # noqa: E402
        _d0 = (datetime.now() - timedelta(days=days * 2 + 60)).strftime("%Y%m%d")
        _d1 = datetime.now().strftime("%Y%m%d")
        _ix = get_pro().index_daily(ts_code=index_code, start_date=_d0, end_date=_d1)
        if _ix is not None and len(_ix) > 30:
            s = _ix.sort_values("trade_date").set_index("trade_date")["close"]
            s.index = s.index.astype(str)
            return s
    except Exception as e:
        print("[audit] 指数收盘取数失败（改用成分等权）：%s" % str(e)[:80])
    return None


def _betti0_of_window(ret_w: pd.DataFrame, pct: float = 35.0):
    """单个窗口的 Betti_0 / Betti_1（同 Betti 阈值口径：d>0 的 pct 分位）。"""
    if ret_w.shape[0] < 10 or ret_w.shape[1] < 5:
        return -1, -1, None
    corr = np.nan_to_num(ret_w.corr().values, nan=0.0)
    dmat = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
    np.fill_diagonal(dmat, 0.0)
    pos = dmat[dmat > 0]
    if not pos.size:
        return -1, -1, corr
    th = float(np.percentile(pos, pct))
    b0, b1 = betti_numbers(dmat, th)
    return b0, b1, corr


def audit_hermer2(target="科创50", index_code="000688.SH", days=250,
                  win=60, step=5, fwd=10, k=5, outdir=None):
    """hermer2 口径偏误审计：滚动 60 日窗口 → hermer2_score → 看未来 10 日涨跌。

    目的：量化验证老板那句「它一直说是风险」。若「结构分化偏高/略分化」窗口占绝大比例，
    且这些窗口里未来 10 日上涨的也不少，则证明该**结构口径**无方向区分度
    （2026-11-14 起标签已改名为结构描述，见 hermer2_score 口径声明）。

    返回 (summary: dict, rows: list[dict])；取不到数据返回 (None, [])。
    """
    outdir = outdir or os.path.join(_WS, "reports", "topology")
    os.makedirs(outdir, exist_ok=True)

    # 1) 取数：优先 data_hub，失败退化 tushare 直连（天数不够再降）
    close = None
    used_days = days
    for try_days in ([days] if days >= 250 else [days]) + ([180] if days >= 250 else []):
        try:
            _d, _c, _n = fetch_by_datahub(target, try_days)
            if _c is not None and len(_c) >= win + fwd + 20:
                close = _c
                used_days = len(_c) - 1
                break
        except Exception as e:
            print("[audit] data_hub 不可用（%s），走 tushare 直连" % str(e)[:60])
            break
    if close is None:
        for try_days in sorted({days, 180, 120}, reverse=True):
            try:
                _d, _c, _n = fetch_by_tushare(index_code, try_days)
            except Exception as e:
                print("[audit] tushare 取数失败（%d 日）：%s" % (try_days, str(e)[:80]))
                continue
            if _c is not None and len(_c) >= win + fwd + 20:
                close = _c
                used_days = len(_c) - 1
                print("[audit] tushare 取数成功：申请 %d 日 → 实际 %d 日 × %d 只"
                      % (try_days, used_days, _c.shape[1]))
                break
    if close is None:
        print("[audit] 取不到足够面板，审计未执行（不编造数字）")
        return None, []

    close = close.dropna(axis=1, how="any")
    if close.shape[1] < 5 or len(close) < win + fwd + 10:
        print("[audit] 面板不足（%s），审计未执行" % (close.shape,))
        return None, []

    ret = np.log(close / close.shift(1)).dropna()
    idx_dates = list(ret.index.astype(str))

    # 2) 指数基准（未来 10 日涨跌幅）；取不到用成分等权合成
    idx_close = _index_close_series(index_code)
    if idx_close is None or len(idx_close) < 50:
        eq = (close / close.iloc[0] * 1000).mean(axis=1)
        eq.index = eq.index.astype(str)
        idx_close = eq
        bench = "成分等权合成"
    else:
        bench = "指数收盘 %s" % index_code

    total_days = len(close)
    print("[audit] 面板：%d 只 × %d 交易日（申请 %d 日 → 实际 %d 日）· 基准=%s"
          % (close.shape[1], total_days, days, used_days, bench))
    print("[audit] 窗口=%d 步长=%d 未来=%d 日 · 曲率 k=%d" % (win, step, fwd, k))

    # 3) 滚动窗口
    rows = []
    curv_cache = []
    corr_cache = []
    corr_h1 = []
    corr_h2 = []
    starts = list(range(0, len(ret) - win - fwd + 1, step))
    print("[audit] 共 %d 个窗口，开始滚动…" % len(starts))
    t0 = time.time()
    for wi, s in enumerate(starts):
        w = ret.iloc[s:s + win]
        b0, b1, corr = _betti0_of_window(w)
        if corr is None:
            continue
        cm, _cv, _dg, _G, _ec = avg_curvature(corr, k=k)

        # Betti_0 前后段趋势：窗口内前 30 日 vs 后 30 日
        half = win // 2
        b0p, _b1p, _c1 = _betti0_of_window(w.iloc[:half])
        b0n, _b1n, _c2 = _betti0_of_window(w.iloc[half:])
        if b0p < 0 or b0n < 0:
            b0p = b0n = None          # 算不出 → 退化，打分记 0 并在输出标注

        sc = hermer2_score(b0, b1, cm, b0p, b0n)

        # 未来 10 日指数涨跌幅（窗口结束日的下一日起）
        end_i = s + win - 1           # ret 行索引位置
        d_end = idx_dates[end_i] if end_i < len(idx_dates) else None
        fwd_ret = np.nan
        d_fwd = None
        if d_end is not None:
            j = idx_dates.index(d_end)
            if j + fwd < len(idx_dates):
                d_fwd = idx_dates[j + fwd]
                try:
                    a = float(idx_close.loc[d_end])
                    b = float(idx_close.loc[d_fwd])
                    if a > 0:
                        fwd_ret = (b / a - 1) * 100
                except Exception:
                    fwd_ret = np.nan

        rows.append(dict(
            win_start=idx_dates[s], win_end=d_end, fwd_end=d_fwd,
            b0=b0, b1=b1, curv=(None if cm is None else round(cm, 4)),
            b0_prev=(None if b0p is None else b0p),
            b0_now=(None if b0n is None else b0n),
            score=round(sc["score"], 3), verdict=sc["verdict"],
            fwd10=(None if not np.isfinite(fwd_ret) else round(fwd_ret, 3)),
        ))
        curv_cache.append(cm)
        corr_cache.append(corr)
        corr_h1.append(_c1)
        corr_h2.append(_c2)
        if (wi + 1) % 5 == 0 or wi == len(starts) - 1:
            print("[audit]   %d/%d 窗口完成  %.0fs" % (wi + 1, len(starts), time.time() - t0))

    if not rows:
        print("[audit] 没有有效窗口，审计未执行")
        return None, []

    df = pd.DataFrame(rows)
    csv_path = os.path.join(outdir, "hermer2_audit.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # 4) 统计
    order = list(VERDICT_ORDER)   # 新标签：结构分化偏高 > 结构略分化 > 结构中性 > 结构略聚合 > 结构聚合
    n_all = len(df)
    cnt = {v: int((df["verdict"] == v).sum()) for v in order}
    risky = cnt["结构分化偏高"] + cnt["结构略分化"]
    oppo = cnt["结构略聚合"] + cnt["结构聚合"]

    d_risk = df[df["verdict"].isin(RISK_SIDE)]
    d_val = d_risk.dropna(subset=["fwd10"])
    up_risk = int((d_val["fwd10"] > 0).sum())
    up_risk_pct = (up_risk / len(d_val) * 100) if len(d_val) else float("nan")
    avg_risk_fwd = float(d_val["fwd10"].mean()) if len(d_val) else float("nan")

    d_all_val = df.dropna(subset=["fwd10"])
    up_all = int((d_all_val["fwd10"] > 0).sum())
    up_all_pct = (up_all / len(d_all_val) * 100) if len(d_all_val) else float("nan")

    # 逐 verdict 的未来 10 日表现（检验方向性有没有区分度）
    per_verdict = {}
    for v in order:
        dd = df[df["verdict"] == v].dropna(subset=["fwd10"])
        per_verdict[v] = dict(
            n=len(dd),
            up_pct=(float((dd["fwd10"] > 0).mean() * 100) if len(dd) else float("nan")),
            avg_fwd=(float(dd["fwd10"].mean()) if len(dd) else float("nan")),
        )
    d_opp = df[df["verdict"].isin(OPPO_SIDE)].dropna(subset=["fwd10"])
    opp_up_pct = float((d_opp["fwd10"] > 0).mean() * 100) if len(d_opp) else float("nan")
    opp_avg = float(d_opp["fwd10"].mean()) if len(d_opp) else float("nan")

    # 基准对照：全样本未来 10 日上涨占比
    summary = dict(
        n_windows=n_all, days_requested=days, days_used=used_days,
        n_stocks=int(close.shape[1]), win=win, step=step, fwd=fwd,
        bench=bench, csv=csv_path,
        count=cnt, risky=risky, risky_pct=risky / n_all * 100,
        oppo=oppo, oppo_pct=oppo / n_all * 100,
        neutral=cnt["结构中性"], neutral_pct=cnt["结构中性"] / n_all * 100,
        risk_up=up_risk, risk_n=len(d_val), risk_up_pct=up_risk_pct,
        risk_avg_fwd=avg_risk_fwd,
        all_up_pct=up_all_pct, all_n=len(d_all_val),
        opp_up_pct=opp_up_pct, opp_avg_fwd=opp_avg, per_verdict=per_verdict,
        b0_mean=float(df["b0"].mean()), b0_max=int(df["b0"].max()),
        curv_mean=float(np.nanmean(df["curv"].astype(float))),
        score_mean=float(df["score"].mean()),
    )

    # 5) 图
    png_path = os.path.join(outdir, "hermer2_audit.png")
    try:
        fig, axes = plt.subplots(1, 2, figsize=(17.5, 8.2), dpi=110, facecolor="white")
        ax = axes[0]
        vals = [cnt[v] for v in order]
        pcts = [v / n_all * 100 for v in vals]
        cols5 = [RED, "#ee9b9b", BLUE, "#8fc9a8", GREEN]
        bars = ax.bar(range(len(order)), vals, color=cols5, edgecolor="white", lw=1.2)
        for i, (v, p) in enumerate(zip(vals, pcts)):
            ax.text(i, v + max(vals) * 0.015, "%d\n%.0f%%" % (v, p), ha="center",
                    va="bottom", fontsize=13, color=INK)
        ax.set_xticks(range(len(order)))
        ax.set_xticklabels([o.replace("结构", "结构\n") for o in order], fontsize=11.5)
        ax.set_ylabel("窗口数", fontsize=13, color=INK)
        ax.set_title("hermer2 口径判定分布（%d 个滚动窗口）" % n_all, fontsize=15, color=INK)
        ax.grid(axis="y", color=GRID, lw=0.8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)

        ax2 = axes[1]
        dfh = d_val.copy()
        if len(dfh):
            bins = np.arange(-16, 17, 2.0)
            h_up = dfh[dfh["fwd10"] > 0]["fwd10"]
            h_dn = dfh[dfh["fwd10"] <= 0]["fwd10"]
            ax2.hist([h_dn, h_up], bins=bins, color=["#d98c8c", "#5da57a"],
                     edgecolor="white", lw=0.9, label=["未来10日下跌", "未来10日上涨"])
            ax2.axvline(0, color=INK, lw=1.1, ls="--")
            ax2.set_xlabel("窗口结束后未来 10 日指数涨跌幅（%）", fontsize=12.5, color=INK)
            ax2.set_ylabel("窗口数", fontsize=13, color=INK)
            ax2.set_title("判为「结构分化偏高/略分化」的窗口：未来 10 日实际涨跌（上涨 %d/%d = %.0f%%）"
                          % (up_risk, len(d_val), up_risk_pct), fontsize=14.5, color=INK)
            ax2.legend(frameon=False, fontsize=12)
            ax2.grid(axis="y", color=GRID, lw=0.8)
            for sp in ("top", "right"):
                ax2.spines[sp].set_visible(False)
        else:
            ax2.text(0.5, 0.5, "无带未来收益的有效窗口", ha="center", va="center",
                     transform=ax2.transAxes, fontsize=15, color=MUTED)

        fig.suptitle("hermer2 口径偏误审计 · %s（源码不可得，按公布分项反推的阈值口径）" % target,
                     fontsize=18, color=TITLE_RED, weight="bold", y=0.975)
        fig.text(0.5, 0.015,
                 "窗口 %d 日 / 步长 %d / 未来 %d 日 · 基准 %s · 样本 %d 交易日 · 结构分化侧合计 %.0f%%"
                 % (win, step, fwd, bench, total_days, risky / n_all * 100),
                 ha="center", fontsize=12, color=MUTED)
        fig.tight_layout(rect=[0, 0.035, 1, 0.945])
        fig.savefig(png_path, facecolor="white", bbox_inches="tight")
        plt.close(fig)
        summary["png"] = png_path
    except Exception as e:
        print("[audit] 画图失败：%s" % str(e)[:120])
        summary["png"] = None

    # 6) 打印
    print("\n" + "=" * 66)
    print("hermer2 口径偏误审计（%s）" % target)
    print("=" * 66)
    print("样本：%d 只 × %d 交易日（申请 %d）· 窗口 %d · 步长 %d · 未来 %d 日"
          % (close.shape[1], total_days, days, win, step, fwd))
    print("基准：%s" % bench)
    print("窗口总数：%d" % n_all)
    print("-" * 66)
    print("%-14s %8s %8s" % ("verdict", "窗口数", "占比"))
    for v in order:
        print("%-14s %8d %7.1f%%" % (v, cnt[v], cnt[v] / n_all * 100))
    print("-" * 66)
    print("「结构分化偏高」+「结构略分化」合计：%d / %d = %.1f%%" % (risky, n_all, risky / n_all * 100))
    print("「结构略聚合」+「结构聚合」合计：%d / %d = %.1f%%" % (oppo, n_all, oppo / n_all * 100))
    print("-" * 66)
    print("判为结构分化侧（分化偏高/略分化）的窗口中，未来 10 日上涨：%d / %d = %.1f%%"
          % (up_risk, len(d_val), up_risk_pct))
    print("  这些窗口未来 10 日平均涨跌幅：%+.2f%%" % avg_risk_fwd)
    print("  对照：全样本窗口未来 10 日上涨占比 %.1f%%（%d/%d）" % (up_all_pct, up_all, len(d_all_val)))
    print("  对照：结构聚合侧窗口未来 10 日上涨占比 %.1f%%（平均 %+.2f%%）" % (opp_up_pct, opp_avg))
    print("-" * 66)
    print("逐判定未来 10 日表现（看方向区分度）：")
    print("%-14s %6s %8s %10s" % ("verdict", "窗口", "上涨占比", "平均涨跌"))
    for v in order:
        pv = per_verdict[v]
        if pv["n"]:
            print("%-14s %6d %7.1f%% %+9.2f%%" % (v, pv["n"], pv["up_pct"], pv["avg_fwd"]))
        else:
            print("%-14s %6d %8s %10s" % (v, 0, "—", "—"))
    print("结构均值：Betti_0 均 %.1f（最大 %d）· 平均曲率均值 %.2f · 打分均值 %+.2f"
          % (summary["b0_mean"], summary["b0_max"], summary["curv_mean"], summary["score_mean"]))
    print("=" * 66)
    print("CSV：%s" % csv_path)
    if summary.get("png"):
        print("PNG：%s" % summary["png"])

    # 7) 阈值敏感性：Betti_0 的阈值分位是「高度分化」这一风险项的隐含开关
    #    曲率复用缓存（可重现），只重算 Betti，几秒内完成。
    print("\n" + "-" * 66)
    print("阈值敏感性：Betti_0 随阈值分位变化（这是「高度分化」风险项的隐含开关）")
    print("-" * 66)
    sens = []
    for pct in (5, 10, 15, 20, 35, 50):
        b0s = []
        for ci, corr in enumerate(corr_cache):
            d = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
            np.fill_diagonal(d, 0.0)
            pos = d[d > 0]
            th = float(np.percentile(pos, pct))
            b0, b1 = betti_numbers(d, th)
            b0s.append((b0, b1))
        frac5 = 100.0 * np.mean([1 if b0 >= 5 else 0 for b0, _ in b0s])
        sens.append(dict(pct=pct, b0_mean=float(np.mean([b for b, _ in b0s])),
                         b0_med=float(np.median([b for b, _ in b0s])),
                         rate5=frac5))
        print("  分位 %2d%%：Betti_0 均 %.1f / 中位 %.0f / ≥5（高度分化）窗口占比 %.0f%%"
              % (pct, sens[-1]["b0_mean"], sens[-1]["b0_med"], frac5))
    print("  实跑口径 = 35%（与画图一致）· hermer2 公布的 Betti_0=12 对应约 10~15% 分位")
    print("  含义：阈值越低→簇拆得越细→Betti_0 越大→>5 风险项越容易触发；")
    print("        阈值取高时几乎恒为 1（单一簇）→ 打分反而长期偏多。但无论哪种，方向区分度都很差。")
    summary["sens"] = sens

    # 8) 反事实：把 Betti_0 阈值校准到 hermer2 公布的量级（≈12），重跑整个审计
    #    目的：直接验证老板那句「它一直说是风险」——校准到它的量级后是不是就变成年年偏风险。
    print("\n" + "-" * 66)
    print("反事实对照：把 Betti_0 阈值校准到 hermer2 公布的量级（目标 Betti_0≈12）")
    print("-" * 66)
    target_b0 = 12.0
    best = None
    for pct in (2, 3, 4, 5, 6, 8, 10, 12, 15, 20):
        b0s = []
        for corr in corr_cache:
            d = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
            np.fill_diagonal(d, 0.0)
            pos = d[d > 0]
            b0, _b1 = betti_numbers(d, float(np.percentile(pos, pct)))
            b0s.append(b0)
        m = float(np.mean(b0s))
        if best is None or abs(m - target_b0) < abs(best[1] - target_b0):
            best = (pct, m)
    pct_c, b0_c = best
    print("  校准阈值：%d%% 分位（该口径下 Betti_0 均值 %.1f，接近其公布的 12）" % (pct_c, b0_c))

    cf = []
    for i, corr in enumerate(corr_cache):
        d = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
        np.fill_diagonal(d, 0.0)
        pos = d[d > 0]
        b0, b1 = betti_numbers(d, float(np.percentile(pos, pct_c)))
        b0p = b0n = None
        if i < len(corr_h1) and corr_h1[i] is not None and corr_h2[i] is not None:
            for cc, which in ((corr_h1[i], "p"), (corr_h2[i], "n")):
                dd = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - cc)))
                np.fill_diagonal(dd, 0.0)
                pp = dd[dd > 0]
                v, _ = betti_numbers(dd, float(np.percentile(pp, pct_c)))
                if which == "p":
                    b0p = v
                else:
                    b0n = v
        sc = hermer2_score(b0, b1, curv_cache[i], b0p, b0n)
        ff = rows[i]["fwd10"]
        cf.append(dict(verdict=sc["verdict"], score=sc["score"],
                       fwd10=(np.nan if ff is None else float(ff)), b0=b0, b1=b1))
    cdf = pd.DataFrame(cf)
    c_cnt = {v: int((cdf["verdict"] == v).sum()) for v in order}
    c_risky = c_cnt["结构分化偏高"] + c_cnt["结构略分化"]
    c_oppo = c_cnt["结构略聚合"] + c_cnt["结构聚合"]
    c_dr = cdf[cdf["verdict"].isin(RISK_SIDE)].dropna(subset=["fwd10"])
    c_up = int((c_dr["fwd10"] > 0).sum())
    print("  %-14s %8s %8s" % ("verdict", "窗口数", "占比"))
    for v in order:
        print("  %-14s %8d %7.1f%%" % (v, c_cnt[v], c_cnt[v] / len(cdf) * 100))
    print("  结构分化侧合计：%d / %d = %.1f%%" % (c_risky, len(cdf), c_risky / len(cdf) * 100))
    print("  结构聚合侧合计：%d / %d = %.1f%%" % (c_oppo, len(cdf), c_oppo / len(cdf) * 100))
    print("  结构分化侧窗口未来 10 日上涨：%d / %d = %.1f%%"
          % (c_up, len(c_dr), (c_up / len(c_dr) * 100) if len(c_dr) else float("nan")))
    summary["counterfactual"] = dict(pct=pct_c, b0_mean=b0_c, count=c_cnt,
                                     risky_pct=c_risky / len(cdf) * 100,
                                     oppo_pct=c_oppo / len(cdf) * 100,
                                     risk_up_pct=(c_up / len(c_dr) * 100) if len(c_dr) else float("nan"),
                                     risk_n=len(c_dr), risk_up=c_up)
    cdf.insert(0, "win_start", [r["win_start"] for r in rows[:len(cdf)]])
    cf_path = os.path.join(outdir, "hermer2_audit_crosscheck.csv")
    cdf.to_csv(cf_path, index=False, encoding="utf-8-sig")
    print("  校准版明细：%s" % cf_path)
    return summary, rows


# ─────────────────────── main ───────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="科创50", help="指数名（科创50/沪深300/中证500/上证50…）")
    ap.add_argument("--index", default="", help="指数代码，如 000688.SH")
    ap.add_argument("--name", default="", help="指数显示名")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--k", type=int, default=5, help="相关网络近邻数 / 曲率图边数")
    ap.add_argument("--csv", default="", help="本地收盘面板 CSV（列=股票代码，索引=交易日）")
    ap.add_argument("--out", default="", help="输出 PNG 路径")
    ap.add_argument("--score-mode", default="hermer2", choices=["hermer2", "multi"],
                    help="打分口径：hermer2（默认，按 hermer2 公布分项反推）/ multi（原多维加权）")
    ap.add_argument("--audit", action="store_true",
                    help="hermer2 口径偏误审计：滚窗 → hermer2_score → 未来 10 日涨跌统计")
    ap.add_argument("--audit-days", type=int, default=250, help="审计取数天数（默认 250）")
    a = ap.parse_args()

    if a.audit:
        audit_hermer2(target=a.target, index_code=a.index or "000688.SH",
                      days=a.audit_days, k=a.k)
        return

    index_code, index_name = a.index, a.name
    if not index_code:
        index_code, index_name = INDEX_ALIAS.get(a.target, ("", a.target))
        if not index_code:
            # 直接当代码用
            index_code = a.target if a.target[:6].isdigit() else ""
    if not index_name:
        index_name = a.target
    if not index_code:
        sys.exit("无法解析指数：%s（用 --index 000688.SH 指定）" % a.target)

    t0 = time.time()
    names = {}
    if a.csv:
        close = pd.read_csv(a.csv, index_col=0)
        codes = list(close.columns)
        print("[manifold] 本地 CSV：%s %s" % (a.csv, close.shape))
        if names is None or not names:
            names = stock_names(codes)
    else:
        try:
            dates, close, names = fetch_by_datahub(a.target, a.days)
            codes = list(close.columns)
            print("[manifold] data_hub 取数成功 %s" % (close.shape,))
        except Exception as e:
            print("[manifold] data_hub 不可用（%s），改走 tushare 直连" % str(e)[:80])
            dates, close, names = fetch_by_tushare(index_code, a.days)
            codes = list(close.columns)

    close = close.dropna(axis=1, how="any")
    codes = list(close.columns)
    ret = np.log(close / close.shift(1)).dropna()
    corr = ret.corr().values
    n = len(codes)
    print("[manifold] 成分 %d 只，收益窗口 %d 日" % (n, len(ret)))

    mom5 = (close.iloc[-1] / close.iloc[-6] - 1).values * 100  # 近5日动量 %
    curv, deg, G, ec = ollivier_ricci(corr, k=a.k)
    print("[manifold] 曲率区间 [%.2f, %.2f]" % (curv.min(), curv.max()))

    if a.score_mode == "hermer2":
        # 口径：hermer2（源码不可得，按其公布分项反推）
        mf = corr_manifold_inputs(close, k=a.k)
        b0, b1 = mf["b0"], mf["b1"]
        half = len(ret) // 2
        b0p, _b1p, _c1 = _betti0_of_window(ret.iloc[:half])
        b0n, _b1n, _c2 = _betti0_of_window(ret.iloc[half:])
        if b0p < 0 or b0n < 0:
            b0p = b0n = None
            print("[manifold] 警告：Betti_0 前后段趋势算不出，退化为 0（图上标注「不可算」）")
        curv_mean = float(np.mean(curv))
        mkt = hermer2_score(b0, b1, curv_mean, b0p, b0n)
        print("[manifold] 口径：hermer2（源码不可得，按他公布分项反推）")
        print("[manifold] 结构分（不含方向）%+.2f → %s  | Betti_0=%s Betti_1=%s 平均曲率=%.3f 趋势 %s→%s(%s)"
              % (mkt["score"], mkt["verdict"], b0, b1, curv_mean,
                 "—" if b0p is None else b0p, "—" if b0n is None else b0n,
                 mkt["trend_txt"]))
        print("[manifold] 分项：%s" % "  ·  ".join(
            "%s %+.2f" % (nm, s) for nm, _v, s in mkt["items"]))
    else:
        # 指数收盘（多维打分用）：能取则取，取不到用成分等权合成
        idx_close = None
        if index_code:
            idx_close = _index_close_series(index_code, days=200)
        mkt = market_score(close, idx_close, corr)
        print("[manifold] 口径：多维加权")
        print("[manifold] 多维打分 %+.2f → %s" % (mkt["score"], mkt["verdict"]))

    # ── 趋势层（含方向）：与结构分物理隔离，单独一行 ──
    trend = trend_layer(close, index_code=index_code)
    print("[manifold] %s" % trend_layer_text(trend))
    print("[manifold] 趋势层口径：%s（%d 个交易日）" % (trend.get("basis", "成分等权合成"),
                                                trend.get("days", len(close) - 1)))

    diag = structure_diag(close, corr)
    pcp = pc1_profile(close, corr, codes, names)
    print("[manifold] 第一主成分：解释 %.1f%% | 正权重 %d 只 / 负 %d 只 | 与等权指数相关 %.3f"
          % (pcp["share1"] * 100, pcp["n_pos"], pcp["n_neg"], pcp["corr_pc_eq"]))
    print("[manifold] 结构诊断：%s | 抱团度 %.0f%%（%s）· 撕裂度 %.0f%%（%s）· 阵营 %d 个"
          % (diag["label"], diag["hug"] * 100, diag["hug_lv"],
             diag["tear"] * 100, diag["tear_lv"], diag["n_bloc"]))

    dmat = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - corr)))
    np.fill_diagonal(dmat, 0.0)
    emb = umap_embed(dmat, n_neighbors=10, min_dist=0.3)
    print("[manifold] UMAP done %.1fs" % (time.time() - t0))

    out = a.out or os.path.join(
        _WS, "reports", "topology",
        "%s_流形拓扑图_%s.png" % (index_name, datetime.now().strftime("%Y%m%d")),
    )
    info = plot(emb, mom5, curv, deg, corr, close, codes, names, index_code, index_name, out,
                k=a.k, mkt=mkt, diag=diag, pcp=pcp, trend=trend)
    print("[manifold] 输出：%s" % out)
    print("[manifold] %s" % info)
    print("[manifold] 总耗时 %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
