#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔬 申万三级下钻 · 等权合成 + 拓扑曲率 + 价格资金验证（纯脚本 0 token）

════════════════════════════════════════════════════════════════════
大佬 2026-11-07 16:31 定稿口径
  「用等权重，然后只挖选中的机会行业」
  → ① 三级子行业指数 = 成分股 **等权** 平均涨跌幅（不是市值加权）
    ② 只对第二步（全池扫描）选出的机会行业（A/B/C 三档）往下挖一层

════════════════════════════════════════════════════════════════════
为什么不能直接用现成指数
  · tushare `index_daily` 拿 L3 代码（如 851251.SI 白酒Ⅲ）**返回 None**
  · tushare `sw_daily` 只支持 L2 代码
  · 所以三级必须用成分股自己合成：`.cache/sw_member_all.json`（5922 只票
    的 l1/l2/l3 完整映射）→ 取 L3 成分 → 全市场 pct_chg 面板等权平均

════════════════════════════════════════════════════════════════════
口径（对齐 V3 提示词 Part 2）
  · 距离        d = sqrt(2 × (1 − R))，R = 20 日收益率 Pearson 相关
  · 建图        阈值法，τ = median(D) × 0.95
  · 曲率        加权 Ollivier-Ricci（topolib），α=0.5，**节点级** κ
  · 采信        **滚动 3 个 20 日窗口方向一致**才定性；单窗口反转只标「待确认」
  · 节点门槛    L3 子行业数 < 5 → 不报 Betti_1，只报 Betti_0 + 平均 κ
  · PH          Vietoris-Rips，persistence > D 的 P75 → 长寿命孔洞（产业链闭环）
  · 价格验证    近 5 日 / 20 日收益 ｜ 主力资金周净流入 ｜ 是否站稳 20 日均线

标签（几何信号必须过价格/资金校验，严禁抱团=看多）
  🟢【可观察】        κ>0（3窗口一致）+ 站稳 MA20 + 周净流入>0
  🟡【高位抱团·衰减】  κ>0 + （破 MA20 或 周净流出）
  🔴【结构撕裂】       κ<0（3窗口一致）+ 价格走弱（近5日<0 或 破 MA20）
  🟢【产业链正反馈】   Betti_1 长寿命闭环 + 价格确认（mom20>0 且 站稳 MA20）
  ⚪【待确认】         3 窗口方向不一致 / 样本不足

用法：
  python3 strategies/l3_drill.py                      # 自动取最近一次全池扫描的机会行业
  python3 strategies/l3_drill.py --ind 半导体 元件     # 指定行业
  python3 strategies/l3_drill.py --date 20261106
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
import argparse
import datetime
from collections import defaultdict

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import socket
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd

CACHE = os.path.join(WS, ".cache", "l3_drill")
os.makedirs(CACHE, exist_ok=True)

SW_MEMBER = os.path.join(WS, ".cache", "sw_member_all.json")
MKT_PANEL = os.path.join(WS, ".cache", "xsec", "mkt_panel.csv")
CRIT_CACHE = os.path.join(WS, ".cache", "cri_topology")

KAPPA_WIN = 20          # 曲率相关窗口（V3：20 日）
N_WIN = 3               # 滚动 3 窗口方向一致才采信
TAU_SHRINK = 0.95       # τ = median(D) × 0.95（仅大行业层阈值图对照用）
KNN_K = 2               # 子行业层建图：kNN(k=2)（V3 Part2 Step3.2）
MIN_MEMBERS = 5         # L3 成分股 < 5 只 → 样本不足
MIN_NODES_B1 = 5        # 子行业节点 < 5 → 不报 Betti_1
MIN_NODES_TOPO = 5      # 子环节 < 5 → 不做拓扑（大佬 2026-11-07 18:07：1~3 个节点建图没意义）
# 2026-11-07 18:07：已废弃「个股层建图凑节点」方案（1~3 个三级做拓扑本就是自欺欺人）
# stock_curv() 已删除；节点 < MIN_NODES_TOPO 时只判涨/资金/残差动量，不做拓扑。
STOCK_K = 3             # 仅保留给 topo_curv 的 kNN k 自适应用
KAPPA_EPS = 0.01        # κ 死区：|κ| < 0.01 → 几何无信号（实测零轴附近摆动无统计意义）
MF_DAYS = 5             # 主力资金周净流入 = 近 5 个交易日累计

# ══════════════════════════════════════════ 版本标识
VER = "V3"
VER_FULL = "子行业拓扑 V3 ｜ 申万三级等权合成 + kNN(k=2) + κ滚动3窗口 + 价格/资金校验"
_TECH_VER = "缠论买卖点 + SMC结构 + 均线排列 三重过滤（bin/tech_confirm.py）"

# ══════════════════════════════════════════ 环节补充（申万三级覆盖不到的）
# 大佬 2026-11-07 16:44 问「申万三级包不包含我说的那几个行业？」
# → 实测申万三级是**分类学**，它有「半导体设备/材料」但没有「功率(SiC/IGBT)、存储、
#   先进封装、固态材料、创新药、航空发动机」这些**产业链环节**，因为这几类在申万里
#   被拆散在多个三级里、或根本不成一个三级。
#   → 用**同花顺概念指数**补（ts_index/ths_daily 有真·指数日线，不用合成）
# 键 = 申万二级行业名；值 = [(补充环节名, 同花顺概念代码)]
CONCEPT_SUB = {
    "半导体": [("功率半导体(第三代)", "885908.TI"),
               ("存储芯片", "886042.TI"),
               ("先进封装", "886009.TI")],
    "电池": [("固态电池", "886032.TI"),
             ("储能", "885921.TI")],
    "化学制药": [("创新药", "886015.TI")],
    "生物制品": [("创新药", "886015.TI")],
    "航空装备Ⅱ": [("航空发动机", "885884.TI")],
}


def log(*a):
    print(*a, flush=True)


# ══════════════════════════════════════════ 数据层
def load_sw():
    """{l2_name: {l3_name: [ts_code,...]}}"""
    if not os.path.exists(SW_MEMBER):
        raise SystemExit("缺少 %s，先跑申万映射回补" % SW_MEMBER)
    raw = json.load(open(SW_MEMBER, encoding="utf-8"))
    out = defaultdict(lambda: defaultdict(list))
    for code, v in raw.items():
        l2, l3 = v.get("l2"), v.get("l3")
        if not l2 or not l3:
            continue
        out[l2][l3].append(code)
    return {k: dict(v) for k, v in out.items()}


def load_panel():
    """全市场 pct_chg 宽表（date × ts_code），date 为 str YYYYMMDD

    ⚠️ 这个面板由 `bin/xsec_lmax.py` 维护，但它里面日期写死到 20261103，
       直接依赖会拿到陈旧数据（三级动量/MA20 全错）。所以这里自己补当日：
       面板最后一日 < 今天 → 用 tushare `daily` 把缺口补上并写回。
    """
    if not os.path.exists(MKT_PANEL):
        raise SystemExit("缺少 %s（bin/xsec_lmax.py 生成）" % MKT_PANEL)
    d = pd.read_csv(MKT_PANEL, index_col=0)
    d.index = d.index.astype(str)
    d = d.sort_index()
    d = _ensure_today(d)
    return d


def _ensure_today(panel, max_fill=8):
    """把面板补到今天（最多补 max_fill 个交易日），成功则写回磁盘"""
    try:
        import tushare_init  # noqa: F401
        from tushare_init import ts_call
    except Exception:
        return panel
    today8 = datetime.date.today().strftime("%Y%m%d")
    last = str(panel.index[-1])
    # ── hub 优先：本地 .cache/market/<date>/daily.csv 已抓好的直接拼（0 网络）
    try:
        base = os.path.join(WS, ".cache", "market")
        have = [x for x in os.listdir(base) if x.isdigit() and x > last]
        have.sort()
        for dd in have[:max_fill]:
            fp = os.path.join(base, dd, "daily.csv")
            if not os.path.exists(fp):
                continue
            t = pd.read_csv(fp)
            if t is None or not len(t) or "ts_code" not in t.columns:
                continue
            col = "pct_chg" if "pct_chg" in t.columns else None
            if not col:
                continue
            sr = pd.Series(t[col].astype(float).values, index=t["ts_code"])
            if sr.notna().sum() > 100:
                panel.loc[dd] = sr
                log("[l3] 面板补 %s ← data hub（%d 只）" % (dd, sr.notna().sum()))
        if str(panel.index[-1]) > last:
            panel = panel.sort_index()
            try:
                panel.to_csv(MKT_PANEL)
            except Exception:
                pass
            return panel
    except Exception as e:
        log("[l3] hub 补面板失败 %s" % str(e)[:80])
    if last >= today8:
        return panel
    # 周末/节假日：先看交易日历，避免无意义调用
    dates = []
    try:
        cal = ts_call("trade_cal", exchange="SSE",
                      start_date=last, end_date=today8, is_open="1")
        if cal is not None and len(cal):
            dates = [str(x) for x in cal["cal_date"] if str(x) > last]
    except Exception:
        dates = []
    if not dates:
        return panel
    dates = dates[-max_fill:]
    add = {}
    for dd in dates:
        try:
            df = ts_call("daily", trade_date=dd)
            if df is not None and len(df):
                add[dd] = df.set_index("ts_code")["pct_chg"]
        except Exception:
            pass
    if not add:
        return panel
    tmp = pd.DataFrame(add).T
    m = pd.concat([panel, tmp])
    m = m[~m.index.duplicated(keep="last")].sort_index()
    try:
        m.to_csv(MKT_PANEL)
        log("[l3] 面板已补 %s → %s（%d 天）" % (last, m.index[-1], len(add)))
    except Exception:
        pass
    return m


def _mf_cache_path(d8):
    return os.path.join(CACHE, "mf5_%s.json" % d8)


def moneyflow_5d(dates):
    """近 N 个交易日 每只票 net_mf_amount 累计（万元）→ {ts_code: 万元}

    缓存 .cache/l3_drill/mf5_<最后一日>.json（当日有效）
    """
    d8 = dates[-1]
    cp = _mf_cache_path(d8)
    if os.path.exists(cp) and time.time() - os.path.getmtime(cp) < 43200:
        try:
            return json.load(open(cp, encoding="utf-8"))
        except Exception:
            pass
    agg = defaultdict(float)
    ok_days = 0
    # ── L0：先看 data hub 有没有当日已抓好的 moneyflow（三层降级产物）
    for d in dates[-MF_DAYS:]:
        try:
            import market_hub as MH
            hp = os.path.join(WS, ".cache", "market", str(d), "moneyflow.csv")
            if os.path.exists(hp):
                hd = pd.read_csv(hp)
                if len(hd) and "ts_code" in hd.columns:
                    col = "net_mf_amount" if "net_mf_amount" in hd.columns else None
                    if col:
                        for c, v in zip(hd["ts_code"], hd[col]):
                            try:
                                agg[c] += float(v)
                            except Exception:
                                pass
                        ok_days += 1
                        continue
        except Exception:
            pass
        break        # hub 没有就退回下面的逐日拉取
    try:
        from tushare_init import ts_call
        for d in (dates[-MF_DAYS:] if ok_days == 0 else []):
            # ts_call 内部已带 8s 硬超时 → 只试 1 次，空就跳过该日
            df = ts_call("moneyflow", trade_date=d)
            if df is None or not len(df):
                log("[l3] moneyflow %s 空/超时，跳过该日" % d)
                continue
            ok_days += 1
            for code, v in zip(df["ts_code"], df["net_mf_amount"]):
                try:
                    agg[code] += float(v)
                except Exception:
                    pass
    except Exception as e:
        log("[l3] 资金流取数失败 %s" % str(e)[:100])
    out = {"days": ok_days, "flow": dict(agg)}
    if ok_days:
        try:
            json.dump(out, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return out


def load_ths_index(ts_code, n_days=90):
    """同花顺概念指数日线（真·指数，不用合成）→ (ret_series, price_series)

    用于补申万三级覆盖不到的产业链环节（功率/存储/先进封装/固态/创新药/航发）。
    返回 ret 为涨跌幅（%），index 为 YYYYMMDD str，与 mkt_panel 对齐。
    """
    cp = os.path.join(CACHE, "ths_%s_%s.json" % (ts_code.replace(".", "_"),
                                                  datetime.date.today().strftime("%Y%m%d")))
    if os.path.exists(cp) and time.time() - os.path.getmtime(cp) < 43200:
        try:
            j = json.load(open(cp, encoding="utf-8"))
            r = pd.Series(j["ret"], index=j["idx"], dtype=float)
            px = pd.Series(j["px"], index=j["idx"], dtype=float)
            return r, px
        except Exception:
            pass
    try:
        import tushare_init  # noqa: F401
        from tushare_init import ts_call
        beg = (datetime.date.today() - datetime.timedelta(days=n_days * 2)).strftime("%Y%m%d")
        df = ts_call("ths_daily", ts_code=ts_code, start_date=beg)
        if df is None or not len(df):
            return None, None
        df = df.sort_values("trade_date")
        r = pd.Series(df["pct_change"].astype(float).values,
                      index=[str(x) for x in df["trade_date"]])
        px = pd.Series(df["close"].astype(float).values, index=r.index)
        try:
            json.dump({"ret": r.tolist(), "px": px.tolist(), "idx": list(r.index)},
                      open(cp, "w", encoding="utf-8"))
        except Exception:
            pass
        return r, px
    except Exception as e:
        log("[l3] 概念 %s 取数失败 %s" % (ts_code, str(e)[:80]))
        return None, None


def _ths_members(ts_code):
    """同花顺概念成分股（用于下挖到个股）"""
    try:
        import tushare_init  # noqa: F401
        from tushare_init import ts_call
        m = ts_call("ths_member", ts_code=ts_code)
        if m is None or not len(m):
            return []
        return [str(x) for x in m["con_code"]]
    except Exception:
        return []


def synth(panel, codes, n_days):
    """L3 等权合成：返回 (ret_series, price_series)

    ret = 成分股当日 pct_chg 的等权平均（%）
    price = 由 ret 累乘出的合成指数（起点 100），用于 MA20 判定
    """
    cols = [c for c in codes if c in panel.columns]
    if len(cols) < MIN_MEMBERS:
        return None, None, len(cols)
    sub = panel[cols].tail(n_days + 30)
    # 只保留至少一半成分有数据的交易日（避免节假日/停牌把均值拉空）
    cnt = sub.notna().sum(axis=1)
    sub = sub[cnt >= max(3, len(cols) // 2)]
    if len(sub) < KAPPA_WIN + 5:
        return None, None, len(cols)
    r = sub.mean(axis=1, skipna=True)
    px = 100.0 * (1.0 + r / 100.0).cumprod()
    return r, px, len(cols)


# ══════════════════════════════════════════ 拓扑
def _corr(R):
    """R: DataFrame(win × nodes) → 相关矩阵

    ⚠️ 2026-11-07：混了同花顺概念指数后，不同标的不一定同日有数据（交易日历不完全一致），
       np.corrcoef 遇 NaN 会整行整列变 NaN → 必须改 pandas 的 **pairwise** corr。
    """
    if isinstance(R, pd.DataFrame):
        C = R.corr(min_periods=max(8, int(len(R) * 0.4)))
    else:
        M = np.asarray(R, dtype=float)
        sd = np.nanstd(M, axis=0)
        bad = (~np.isfinite(sd)) | (sd < 1e-12)
        if bad.all():
            return None
        M = M.copy()
        if bad.any():
            M[:, bad] = np.nan
        C = pd.DataFrame(M).corr(min_periods=max(8, int(len(M) * 0.4)))
    if C is None:
        return None
    C = C.values.astype(float)
    C = np.nan_to_num(C, nan=0.0, posinf=0.0, neginf=0.0)
    C = np.clip((C + C.T) / 2.0, -1, 1)
    np.fill_diagonal(C, 1.0)
    return C


def _knn_adj(D, k=2):
    """kNN 图（V3：子行业层建图法）。每节点连最近 k 个，再对称化"""
    n = D.shape[0]
    A = np.zeros((n, n), dtype=int)
    for i in range(n):
        dd = D[i].copy()
        dd[i] = np.inf
        idx = np.argsort(dd)[:min(k, n - 1)]
        A[i, idx] = 1
    A = ((A + A.T) > 0).astype(int)
    np.fill_diagonal(A, 0)
    return A


def topo_curv(C, names, want_b1=True, graph="knn"):
    """子行业/个股层拓扑：kNN 图 + 节点级加权 OR 曲率 + (可选) 持久同调

    ⚠️ 2026-11-07 实测坑：用 τ=median(D)×0.95 阈值图在 2~7 个节点的小图上
       会直接断连（实测 3 节点 → 边数 0、b0=3、κ 全 NaN）。
       V3 提示词 Part 2 Step 3.2 明确：**大行业层用阈值法，子行业层用 kNN(k=2)**。
       所以这里默认 graph="knn"；tau_shrink 只保留在元信息里作对照。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import topolib
    n = C.shape[0]
    D = np.sqrt(np.maximum(2 * (1 - C), 0))
    iu = np.triu_indices(n, 1)
    dvec = D[iu]
    tau_med = float(np.median(dvec)) * TAU_SHRINK
    kk = KNN_K if n <= 10 else STOCK_K     # 节点多时 k 相应放大
    if graph == "knn":
        A = _knn_adj(D, k=kk)
    else:
        A = ((D <= tau_med) & (~np.eye(n, dtype=bool))).astype(int)
    comp, b0 = topolib.components(A)
    try:
        kappa, node_k, gk = topolib.weighted_ricci(D, A, alpha=0.5)
    except Exception as e:
        log("[l3] ricci 失败 %s" % str(e)[:80])
        node_k, gk = {}, np.nan
    # ⚠️ topolib.weighted_ricci 返回的 node_k 是 **dict**（key=节点下标），
    #    直接迭代 dict 拿到的是 key（0,1,2…）而不是曲率值 → 必须先按下标取
    if isinstance(node_k, dict):
        nk = [node_k.get(i) for i in range(n)]
    else:
        nk = list(node_k)[:n]
    out = {"tau_med": tau_med, "graph": graph, "knn_k": kk if graph == "knn" else None,
           "beta0": int(b0), "gk": float(gk) if np.isfinite(gk) else None,
           "node_k": [float(x) if (x is not None and np.isfinite(x)) else None for x in nk],
           "comp": np.asarray(comp).tolist(), "edge": int(A.sum() // 2)}
    if want_b1:
        try:
            from ripser import ripser
            dg = ripser(D, distance_matrix=True, maxdim=1)["dgms"]
            h1 = dg[1]
            p75 = float(np.percentile(dvec, 75))
            longs = []
            for b, dd in h1:
                dinf = not np.isfinite(dd)
                d2 = dvec.max() * 1.2 if dinf else dd
                if d2 - b > p75:
                    longs.append({"birth": float(b),
                                  "death": None if dinf else float(dd),
                                  "persist": float(d2 - b)})
            out["beta1_long"] = len(longs)
            out["holes"] = longs
        except Exception as e:
            out["beta1_long"] = None
            out["holes"] = []
            log("[l3] PH 失败 %s" % str(e)[:80])
    return out


def kappa_3win(R, names, tag="", verbose=False):
    """滚动 3 个 20 日窗口的节点级 κ；返回 (κ合并, 一致性, 每窗口κ)

    一致性：3 个窗口同号 → True（采信）；否则 False（只标待确认）
    """
    T = len(R)
    ks, per = [], []
    for k in range(N_WIN):
        end = T - k * KAPPA_WIN
        beg = end - KAPPA_WIN
        if beg < 0:
            continue
        seg = R.iloc[beg:end]
        C = _corr(seg)
        if C is None:
            continue
        t = topo_curv(C, names, want_b1=False)
        ks.append(t)
        per.append(t["node_k"])
    if not ks:
        return None, False, []
    n = len(names)
    merged = []
    for i in range(n):
        vals = [p[i] for p in per if i < len(p) and p[i] is not None]
        merged.append(float(np.mean(vals)) if vals else None)
    ok = False
    if len(per) >= N_WIN:
        signs = []
        for i in range(n):
            vals = [p[i] for p in per if i < len(p) and p[i] is not None]
            if len(vals) < N_WIN:
                signs.append(None)
                continue
            sg = [1 if v > KAPPA_EPS else (-1 if v < -KAPPA_EPS else 0) for v in vals]
            signs.append(bool(len(set(sg)) == 1 and sg[0] != 0))
        ok = signs  # 逐节点一致性标记
    return merged, ok, per




def _ak_timeout(fn, *a, timeout=15, **k):
    """akshare 调用带硬超时（超时立刻返回 None，交给上层降级）"""
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(1)
    f = ex.submit(fn, *a, **k)
    try:
        r = f.result(timeout=timeout)
        ex.shutdown(wait=False)
        return r
    except Exception:
        ex.shutdown(wait=False)
        return None

# ══════════════════════════════════════════ 最后一道：残差动量
_BENCH = {"date": None, "ret": None}


def bench_ret(date8, n_days=160):
    """沪深300 日收益率序列（对数），按 panel 的日期索引对齐

    大佬 2026-11-07 17:58：「最后再选出来三级行业来，最后再判断有没有残差动量就行了」
    """
    if _BENCH["date"] == date8 and _BENCH["ret"] is not None:
        return _BENCH["ret"]
    df = None
    beg = (datetime.date.today() - datetime.timedelta(days=n_days * 2)).strftime("%Y%m%d")
    try:
        # ── L0：data hub 的 index_daily.csv（本地 0 网络）
        try:
            import glob as _g
            fs = sorted(_g.glob(os.path.join(WS, ".cache", "market", "*", "index_daily.csv")))
            for fp in reversed(fs[-3:]):
                t = pd.read_csv(fp)
                if t is None or not len(t):
                    continue
                if "ts_code" not in t.columns or "close" not in t.columns:
                    continue
                t = t[t["ts_code"] == "000300.SH"]
                if len(t) > 100:
                    df = t.rename(columns={"trade_date": "trade_date"})
                    log("[l3] 沪深300 ← data hub（%d 行）" % len(df))
                    break
        except Exception:
            df = None
    except Exception:
        pass
    # ── L1 tushare（异常也吞掉，必须能降级）
    if df is None or not len(df):
        try:
            from tushare_init import ts_call
            df = ts_call("index_daily", ts_code="000300.SH", start_date=beg)
        except Exception as e:
            log("[l3] 沪深300 L1 tushare 失败 %s → 降级" % str(e)[:60])
            df = None
    # ── L2 akshare
    if df is None or not len(df):
        try:
            from data_hub import ak as ak
            # ⚠️ index_zh_a_hist 逐月翻页太慢（108 行要 27s），
            #    stock_zh_index_daily_em 一次拉全量（5304 行 / 1.4s）
            a = _ak_timeout(ak.stock_zh_index_daily_em, symbol="sh000300", timeout=20)
            if a is not None and len(a):
                a = a.rename(columns={"date": "trade_date"})
                a["trade_date"] = a["trade_date"].astype(str).str.replace("-", "")
                df = a[["trade_date", "close"]]
                log("[l3] 沪深300 ← L2 akshare（%d 行）" % len(df))
        except Exception as e:
            log("[l3] 沪深300 L2 akshare 失败 %s" % str(e)[:60])
    if df is None or not len(df):
        log("[l3] 沪深300 三层全失败")
        return None
    try:
        df = df.sort_values("trade_date").reset_index(drop=True)
        r = pd.Series(np.log(df["close"].astype(float)).diff().values,
                      index=[str(x) for x in df["trade_date"]]).dropna()
        _BENCH.update({"date": date8, "ret": r})
        return r
    except Exception as e:
        log("[l3] 沪深300 解析失败 %s" % str(e)[:80])
        return None


def resid_mom(series_ret, bret, win=20):
    """子环节/个股的**残差动量**：对沪深300 回归取残差，累计近 win 日

    series_ret: pd.Series（%）日收益率，index=日期
    bret      : pd.Series（对数收益）沪深300
    返回 (resid_mom, resid_ratio, beta)  resid_ratio = 残差累计 / 残差标准差
    """
    try:
        s = series_ret.dropna()
        if bret is None or len(s) < win * 2:
            return None, None, None
        # 统一成对数收益并对齐日期
        lr = np.log1p(s.astype(float) / 100.0)
        j = pd.concat([lr.rename("y"), bret.rename("x")], axis=1, join="inner").dropna()
        if len(j) < win * 2:
            return None, None, None
        x = j["x"].values[-120:]
        y = j["y"].values[-120:]
        vx = float(np.var(x, ddof=1))
        beta = float(np.cov(x, y, ddof=1)[0, 1] / vx) if vx > 1e-18 else 1.0
        # ⚠️ 2026-11-07 踩坑：这里**不能**做全样本去均值
        #    res.mean() 是全窗口日均漂移（本例 +0.0023/天），
        #    从近 20 日残差(+0.012)里减掉 20×0.0023=+0.046 → 变成 -0.034，
        #    方向直接反转（真实 +0.68 → 算出 -1.95）。
        #    残差动量 = 实际收益 − β×大盘，本来就是超额，不需要再中心化。
        res = j["y"].values - beta * j["x"].values
        seg = res[-win:]
        sd = float(np.std(seg, ddof=1))
        cum = float(np.sum(seg))
        return cum, (cum / sd if sd > 1e-12 else 0.0), beta
    except Exception:
        return None, None, None


def _attach_resid(rows, panel, verbose=False):
    """给每个子环节算残差动量（对沪深300 OLS 取残差，近20日累计/σ）

    大佬 2026-11-07 18:03：「只选有机会的，其他的你不要瞎钻，浪费精力、浪费钱」
    → 放在**建图和评龙头之前**，resid_ratio ≤ 0 的子环节后面一律跳过。
    """
    if os.environ.get("L3_RESID", "1") == "0" or not rows:
        return
    try:
        bret = bench_ret(str(panel.index[-1]))
    except Exception:
        bret = None
    if bret is None:
        return
    for r in rows:
        rm, rr, bt = resid_mom(r.get("_rets"), bret, win=20)
        r["resid_mom"] = rm
        r["resid_ratio"] = rr
        r["resid_beta"] = bt
        r["resid_ok"] = bool(rr is not None and rr > 0)
    if verbose:
        n_ok = sum(1 for r in rows if r.get("resid_ok"))
        log("[l3] 残差动量：%d/%d 个子环节有独立行情" % (n_ok, len(rows)))


def _attach_tech_and_leaders(rows, pending, panel, mf, snap, verbose=True):
    """① 先按三级判「上涨+资金聚集」→ ② 过门槛的才评龙头 → ③ 龙头走技术确认

    大佬 2026-11-07 16:52：
      「你下挖到三级行业之后，你要告诉我龙头股了。三级行业必须是有上涨的时候，
        经过资金的聚集，对吧？然后你告诉我龙头股，然后技术形态还确认」
    → 没上涨/没资金聚集的三级，**不给龙头**，直接标原因。
    """
    try:
        import l3_leaders as LD
    except Exception as e:
        log("[l3] 龙头模块不可用 %s" % str(e)[:80])
        LD = None

    # ── ① 门槛判定 + ② 龙头
    qual_codes = []          # [(code, l3)]
    for r in rows:
        # ⚠️ 2026-11-07 22:20 大佬：「至少它 MA20 是向上的」——**第一道硬门槛**
        #   MA20 向下 = 中期趋势向下，后面动量/资金/残差再好也是逆势，一律不做。
        #   放在最前面判：先看 MA20 斜率，不看就别往下算。（修复中通道也受此限）
        if not r.get("ma20_up", True):
            ok = False
            why = "MA20 向下 %.2f%%（中期趋势下行，先否决）" % (r.get("ma20_slope") or 0)
            r["l3_qualified"] = False
            r["l3_qualify_why"] = why
            r["leaders"] = []
            continue
        if LD:
            ok, why = LD.qualify_l3(r.get("mom20"), r.get("today"), r.get("flow_5d"))
            # ⚠️ 2026-11-08 01:38 大佬确认新增「修复中」通道：
            #   5日>0 + 资金>0 + 20日>-5% → 🟡可观察，**不跑龙头/不给⭐可买**
            tier, two = LD.qualify_tier(r.get("mom20"), r.get("today"),
                                        r.get("flow_5d"), r.get("mom5"))
            r["l3_tier"] = tier
            if tier == "repair":
                r["label"] = "🟡修复中"
                r["l3_qualified"] = False
                r["l3_qualify_why"] = two + " → 只观察不给买"
                r["leaders"] = []
                continue
            why = two or why
        else:
            ok, why = True, ""
        r["l3_qualified"] = bool(ok)
        r["l3_qualify_why"] = why
        # ⚠️ 2026-11-07 18:03 大佬：「只选有机会的，其他不要瞎钻，浪费精力浪费钱」
        #   残差动量这一道必须在**评龙头之前**就卡掉，
        #   否则会给一堆「无独立动量」的子环节白算龙头分、白跑技术确认。
        if ok and not r.get("resid_ok", True):
            ok = False
            why = "%s ｜残差动量%s（无独立行情）" % (
                why, _kf(r.get("resid_ratio")))
            r["l3_qualified"] = False
            r["l3_qualify_why"] = why
        if not ok or not LD:
            r["leaders"] = []
            continue
        pool = _stocks_pool(r.get("_codes") or [], panel, mf, snap)
        lead = LD.pick_leaders(pool)
        # ⚠️ 2026-11-07 22:43 / 11-08 01:38 大佬确认：**所有龙头都跑技术确认**
        #   不只前 3 名；且「有资金流入 + 上涨」的即便综合分名次靠后也纳入
        if hasattr(LD, "pick_candidates"):
            cand = LD.pick_candidates(lead)
        else:
            keep_n = LD.CAND_N if hasattr(LD, "CAND_N") else 10
            cand = lead[:max(keep_n, LD.TOP_N)]
        r["leaders"] = cand
        for x in r["leaders"]:
            qual_codes.append((x["code"], r["l3"]))

    # ── ③ 技术确认（只对龙头跑，省时间）
    if os.environ.get("L3_TECH", "1") == "0":
        return
    codes = list(dict.fromkeys([c for c, _ in (qual_codes or pending)]))
    if not codes:
        return
    try:
        import tech_confirm as TC
        import holding_deep as HD
        nmmap = HD.name_map()
        res = TC.confirm_batch(codes, nmmap)
        # 回填到 leaders
        for r in rows:
            for x in (r.get("leaders") or []):
                t = res.get(x["code"])
                x["name"] = nmmap.get(x["code"], x["code"])
                if t:
                    x["tech"] = {"verdict": t["verdict"],
                                 "why": (t.get("why") or [])[:2],
                                 "buy_tag": t.get("buy_tag"), "age": t.get("age"),
                                 "buy_px": t.get("buy_px"),
                                 "active": t.get("buy_active")}
        # 回填到旧的 top（保留原「成分前几」展示，也带上名字/确认）
        for r in rows:
            for x in (r.get("top") or []):
                t = res.get(x["code"])
                x["name"] = nmmap.get(x["code"], x["code"])
                if t:
                    x["tech"] = {"verdict": t["verdict"],
                                 "why": (t.get("why") or [])[:2],
                                 "buy_tag": t.get("buy_tag"), "age": t.get("age"),
                                 "buy_px": t.get("buy_px"),
                                 "active": t.get("buy_active")}
        if verbose:
            nok = sum(1 for r in rows for x in (r.get("leaders") or [])
                      if ((x.get("tech") or {}).get("verdict") or "").startswith("✅"))
            ntot = sum(1 for r in rows for x in (r.get("leaders") or []))
            nq = sum(1 for r in rows if r.get("l3_qualified"))
            log("[l3] 龙头：%d/%d 个三级过门槛 ｜技术确认 ✅ 龙头 %d/%d 只"
                % (nq, len(rows), nok, ntot))
    except Exception as e:
        log("[l3] 技术确认失败 %s" % str(e)[:120])
        for r in rows:
            r["tech_err"] = str(e)[:60]



# ══════════════════════════════════════════ 龙头股所需的量能/市值
_MKT_SNAP = {"date": None, "amount": {}, "circ_mv": {}}


def market_snapshot(date8):
    """当日全市场 amount（千元）+ circ_mv（万元）→ dict（进程内缓存）

    龙头分需要成交额占比（资金聚集度）和流通市值占比（行业地位）。
    ⚠️ 口径：amount 来自 daily（千元），circ_mv 来自 daily_basic（万元），
       只用来算**行业内相对占比**，绝对单位不影响排序。
    """
    if _MKT_SNAP["date"] == date8 and _MKT_SNAP["amount"]:
        return _MKT_SNAP
    amt, mv = {}, {}
    try:
        import tushare_init  # noqa: F401
        from tushare_init import ts_call
        # 先看 data hub（三层降级已抓好的当日 daily.csv）
        d = None
        try:
            _hp = os.path.join(WS, ".cache", "market", str(date8), "daily.csv")
            if os.path.exists(_hp):
                _hd = pd.read_csv(_hp)
                if len(_hd) and "amount" in _hd.columns:
                    d = _hd
                    log("[l3] 成交额 ← data hub（%d 行）" % len(d))
        except Exception:
            d = None
        if d is None or not len(d):
            d = ts_call("daily", trade_date=date8)   # 8s 硬超时，超时即放弃
        if d is not None and len(d):
            amt = dict(zip(d["ts_code"], d["amount"].astype(float)))
        else:
            log("[l3] daily %s 三次全空（成交额缺失，龙头分该权重记 0）" % date8)
        # ⚠️ 2026-11-07 踩坑：daily_basic 偶发返回 0 行（限流），必须重试，
        #    否则 circ_mv 全空 → 龙头分的市值项全 0 分（不影响排序但丢信息）
        b = None
        try:
            _bp = os.path.join(WS, ".cache", "market", str(date8), "daily_basic.csv")
            if os.path.exists(_bp):
                b = pd.read_csv(_bp)
        except Exception:
            b = None
        if b is None or not len(b):
            b = ts_call("daily_basic", trade_date=date8)   # 8s 硬超时
        if b is not None and len(b):
            mv = dict(zip(b["ts_code"], b["circ_mv"].astype(float)))
        if not mv:
            log("[l3] daily_basic 缺失（市值项记 0）")
    except Exception as e:
        log("[l3] 全市场快照失败 %s" % str(e)[:100])
    _MKT_SNAP.update({"date": date8, "amount": amt, "circ_mv": mv})
    return _MKT_SNAP


def _stocks_pool(codes, panel, mf, snap):
    """把一个子环节的成分股整理成 l3_leaders 需要的格式"""
    cols = [c for c in codes if c in panel.columns]
    out = []
    if not cols:
        return out
    sub = panel[cols].tail(20)
    m20 = ((1 + sub / 100).prod() - 1) * 100
    d1 = sub.iloc[-1]
    for c in cols:
        out.append({"code": c,
                    "mom20": float(m20.get(c) or 0),
                    "today": float(d1.get(c) or 0),
                    "amount": snap["amount"].get(c),
                    "circ_mv": snap["circ_mv"].get(c),
                    "flow_5d": (mf.get("flow") or {}).get(c)})
    return out


# ══════════════════════════════════════════ 单行业下钻
def _is_repair(r):
    """修复中判定（大佬 2026-11-08 01:38 确认的通道）

      5日 > 0 且 近5日主力净流入 > 0 且 20日 > -5%
      → 「跌过之后资金先进入、短周期已转正」的早期修复
      → 标 🟡可观察，**不给 ⭐可买**（不评龙头、不跑技术确认）
    """
    try:
        moc20 = r.get("mom20")
        moc5 = r.get("mom5")
        flow = r.get("flow_5d")
        if moc20 is None or moc5 is None or flow is None:
            return False
        return (moc5 > 0) and (flow > 0) and (moc20 > -5.0)
    except Exception:
        return False


def drill_one(l2, l3map, panel, dates, mf, snap=None, verbose=True):
    """对一个 L2 行业往下挖一层 → dict"""
    kids = l3map.get(l2) or {}
    if not kids:
        return {"l2": l2, "ok": False, "why": "无三级分类"}
    if snap is None:
        snap = {"amount": {}, "circ_mv": {}}
    n_days = 60
    rows, rets, warns = [], {}, []
    pending = []          # [(ts_code, 子环节名)] → 下钻出来的个股，待技术确认
    for l3, codes in sorted(kids.items()):
        r, px, nmem = synth(panel, codes, n_days)
        if r is None:
            warns.append("%s(成分%d只/数据不足)" % (l3, nmem))
            continue
        rets[l3] = r
        mom5 = float((1 + r.tail(5) / 100).prod() - 1) * 100
        mom20 = float((1 + r.tail(20) / 100).prod() - 1) * 100
        today = float(r.iloc[-1])
        ma20 = float(px.tail(20).mean())
        above = bool(float(px.iloc[-1]) >= ma20)
        # ⚠️ 2026-11-07 22:20 大佬：「至少它 MA20 是向上的，对不对？
        #    你 20 日向都不向上，你再有趋势也没用，你肯定是向下趋势」
        #    → MA20 斜率 = 今日 MA20 相对 5 日前 MA20 的涨跌幅（%）
        ma20_prev = float(px.iloc[:-5].tail(20).mean()) if len(px) > 25 else ma20
        ma20_slope = ((ma20 / ma20_prev - 1) * 100) if ma20_prev > 0 else 0.0
        ma20_up = bool(ma20_slope > 0)
        # 资金流：成分股 5 日累计 net_mf_amount（万元）求和
        flow = None
        if mf.get("flow"):
            tot = 0.0
            hit = 0
            for c in codes:
                v = mf["flow"].get(c)
                if v is not None:
                    tot += float(v)
                    hit += 1
            if hit:
                flow = tot
        # 成分股：20 日动量前 3 + 当日涨幅前 3
        cols = [c for c in codes if c in panel.columns]
        top = []
        if cols:
            sub = panel[cols].tail(20)
            m20 = ((1 + sub / 100).prod() - 1) * 100
            d1 = sub.iloc[-1]
            for c in m20.sort_values(ascending=False).head(3).index:
                top.append({"code": c, "mom20": float(m20[c]),
                            "today": float(d1.get(c) or 0), "why": "20日动量前3"})
            for c in d1.sort_values(ascending=False).head(2).index:
                if any(x["code"] == c for x in top):
                    continue
                top.append({"code": c, "mom20": float(m20.get(c) or 0),
                            "today": float(d1.get(c) or 0), "why": "当日涨幅前2"})
        rows.append({"l3": l3, "n_members": nmem, "mom5": mom5, "mom20": mom20,
                     "today": today, "ma20": ma20, "above_ma20": above,
                     "ma20_slope": ma20_slope, "ma20_up": ma20_up,
                     "flow_5d": flow, "top": top, "src": "sw_l3",
                     "_codes": [c for c in codes if c in panel.columns],
                     "_rets": r})          # 子环节收益率序列（残差动量用）
        for x in top:
            pending.append((x["code"], l3))

    # ── 环节补充：申万三级覆盖不到的产业链环节，用同花顺概念指数补（真·指数，不合成）
    for cname, ccode in (CONCEPT_SUB.get(l2) or []):
        try:
            r, px = load_ths_index(ccode, 90)
            if r is None or len(r) < 25:
                warns.append("%s(概念指数无数据)" % cname)
                continue
            r = r.tail(60)
            px = px.tail(60)
            mom5 = float((1 + r.tail(5) / 100).prod() - 1) * 100
            mom20 = float((1 + r.tail(20) / 100).prod() - 1) * 100
            today = float(r.iloc[-1])
            ma20 = float(px.tail(20).mean())
            above = bool(float(px.iloc[-1]) >= ma20)
            ma20_prev = float(px.iloc[:-5].tail(20).mean()) if len(px) > 25 else ma20
            ma20_slope = ((ma20 / ma20_prev - 1) * 100) if ma20_prev > 0 else 0.0
            ma20_up = bool(ma20_slope > 0)
            codes = _ths_members(ccode)
            flow = None
            if mf.get("flow") and codes:
                tot, hit = 0.0, 0
                for c in codes:
                    v = mf["flow"].get(c)
                    if v is not None:
                        tot += float(v)
                        hit += 1
                if hit:
                    flow = tot
            # 个股：概念成分股 × 全市场面板（取能对上号的）
            top = []
            cols = [c for c in codes if c in panel.columns]
            if cols:
                sub = panel[cols].tail(20)
                m20 = ((1 + sub / 100).prod() - 1) * 100
                d1 = sub.iloc[-1]
                for c in m20.sort_values(ascending=False).head(3).index:
                    top.append({"code": c, "mom20": float(m20[c]),
                                "today": float(d1.get(c) or 0), "why": "20日动量前3"})
                for c in d1.sort_values(ascending=False).head(2).index:
                    if any(x["code"] == c for x in top):
                        continue
                    top.append({"code": c, "mom20": float(m20.get(c) or 0),
                                "today": float(d1.get(c) or 0), "why": "当日涨幅前2"})
            rows.append({"l3": cname, "n_members": len(codes), "mom5": mom5, "mom20": mom20,
                         "today": today, "ma20": ma20, "above_ma20": above,
                         "ma20_slope": ma20_slope, "ma20_up": ma20_up,
                         "flow_5d": flow, "top": top, "src": "ths_concept",
                         "ths_code": ccode,
                         "_codes": [c for c in codes if c in panel.columns],
                         "_rets": r})      # 概念指数收益率序列（残差动量用）
            for x in top:
                pending.append((x["code"], cname))
            rets[cname] = r              # 同花顺概念指数（真指数）参与同一张相关矩阵
        except Exception as e:
            warns.append("%s(补充失败:%s)" % (cname, str(e)[:40]))
    if not rows:
        return {"l2": l2, "ok": False, "why": "子环节全部无有效数据", "warns": warns}

    # ── ⓪ 残差动量先算（2026-11-07 18:03 大佬：只选有机会的，别瞎钻）
    #    resid_ratio 是「有没有独立行情」的第一道判据，放在建图/评龙头之前
    #    → 没独立动量的子环节直接跳过后面所有重活（个股建图 + 龙头 + 技术确认）
    _attach_resid(rows, panel, verbose)

    # ══════════════════════════════════════════════════════════════
    # 子环节 < 5 → **不做拓扑**（大佬 2026-11-07 18:07）
    #   「二级行业有一至三个三级，两个三级行业有啥用啊？你下钻的时候，
    #     只要找到这个三级行业是能涨的就行了，对吧？我只要涨，更精确的涨的行业，
    #     不是要你其他的呀。你只是这 1~3 个行业，你要检查它的拓扑有没有那个结构洞吗？」
    #
    # 结论：1~3 个节点做 kNN 图只有两三条边，κ 曲率/Betti 数全没统计意义。
    #   → 下钻的唯一目的是「在一堆子环节里挑出涨得更精确的那个」，不是做拓扑。
    #   → 这一层只判三件事：① 涨不涨 ② 有没有资金聚集 ③ 有没有独立残差动量
    #   → 够了就选龙头 + 技术确认，不够就标原因跳过。
    # 之前那套「个股层建图凑节点」已删（纯属自欺欺人）。
    # ══════════════════════════════════════════════════════════════
    if len(rows) < MIN_NODES_TOPO:
        for r in rows:
            broke = (not r["above_ma20"]) or r["mom5"] < 0
            up = (r.get("mom20") or 0) > 0 or (r.get("today") or 0) > 0
            money = (r.get("flow_5d") or 0) > 0
            resid = r.get("resid_ratio")
            resid_ok = (resid is not None and resid > 0)
            r["k"] = None
            r["k_ok"] = False
            r["k_src"] = None
            r["k_note"] = "子环节仅 %d 个，不做拓扑（节点<5 无统计意义）" % len(rows)
            # 标签：只用价格 + 资金 + 残差动量，不用 κ
            if not up and _is_repair(r) and r.get("ma20_up", True):
                # ⚠️ 2026-11-08 01:38 大佬确认：修复中通道（5日>0 + 资金>0 + 20日>-5%）
                r["label"] = "🟡修复中"
                r["l3_tier"] = "repair"
            elif not up:
                r["label"] = "⚪不涨"
            elif broke and (r.get("mom20") or 0) < 0:
                r["label"] = "🔴走弱"
            elif up and money and resid_ok:
                r["label"] = "🟢可观察"
            elif up and (money or resid_ok):
                r["label"] = "🟡部分满足"
            else:
                r["label"] = "⚪待确认"
        obs = [r for r in rows if r["label"].startswith("🟢")]
        repair = [r for r in rows if r.get("l3_tier") == "repair"]
        risk = [r for r in rows if (r["label"].startswith("🔴") or r["label"].startswith("🟡"))
                and r.get("l3_tier") != "repair"]
        if verbose:
            log("[l3] %s → %d 个子环节（不做拓扑，只判涨/资金/残差）" % (l2, len(rows)))
        _attach_tech_and_leaders(rows, pending, panel, mf, snap, verbose)
        return {"l2": l2, "ok": True, "rows": rows, "warns": warns,
                "topo": None, "n_nodes": len(rows), "beta1_skip": True,
                "n_obs": len(obs), "n_risk": len(risk), "no_topo": True,
                "n_repair": len(repair),
                "k_win": KAPPA_WIN, "k_nwin": N_WIN, "tau_shrink": TAU_SHRINK}

    # 建 L3 相关矩阵（20 日）×3 窗口
    df = pd.DataFrame(rets)                 # index=date, cols=l3/concept
    # ⚠️ 同花顺概念指数的日期索引是 str，申万合成的是面板的 str 索引，但两者交易日历
    #    不完全重合（概念指数偶缺日）→ 取并集后按行对齐，缺失保留 NaN（_corr 走 pairwise）
    df.index = df.index.astype(str)
    df = df.sort_index()
    df = df.tail(KAPPA_WIN * N_WIN + 5)
    names = [c for c in df.columns if df[c].notna().sum() >= 15]
    df = df[names]
    if len(names) < 2:
        return {"l2": l2, "ok": False, "why": "可用子环节 < 2 个", "warns": warns}
    km, cons, perwin = kappa_3win(df, names)
    C = _corr(df.tail(KAPPA_WIN))
    t_main = topo_curv(C, names, want_b1=(len(names) >= MIN_NODES_B1)) if C is not None else None

    by = {r["l3"]: r for r in rows}
    for i, nm in enumerate(names):
        r = by.get(nm)
        if not r:
            continue
        k = km[i] if (km and i < len(km)) else None
        r["k"] = k
        r["k_ok"] = bool(isinstance(cons, list) and i < len(cons) and cons[i])
        r["k_wins"] = [p[i] for p in perwin if i < len(p)] if perwin else []
        if r.get("k_ok"):
            if k is not None and k < 0 and (r["mom5"] < 0 or not r["above_ma20"]):
                r["label"] = "🔴结构撕裂"
            elif k is not None and k > 0 and r["above_ma20"] and (r["flow_5d"] or 0) > 0:
                r["label"] = "🟢可观察"
            elif k is not None and k > 0:
                r["label"] = "🟡高位抱团·衰减"
            else:
                r["label"] = "⚪中性"
            # ⚠️ 2026-11-08 01:38 修复中通道：中期未转正但短周期已修复 → 覆盖标签
            if r["label"] != "🟢可观察" and _is_repair(r) and r.get("ma20_up", True):
                r["label"] = "🟡修复中"
                r["l3_tier"] = "repair"
        else:
            # 3 窗口方向不一致 → 不单独定性，**必须靠价格破位确认**（V3 铁律 4）
            broke = (not r["above_ma20"]) or r["mom5"] < 0
            if k is not None and k > 0 and broke:
                r["label"] = "🟡高位抱团·衰减"
                r["k_note"] = "κ 3窗不一致，价格破位确认"
            elif k is not None and k < 0 and broke and r["mom20"] < 0:
                r["label"] = "🔴结构撕裂"
                r["k_note"] = "κ 3窗不一致，价格走弱确认"
            else:
                r["label"] = "⚪待确认"
                r["k_note"] = "κ 3窗不一致，价格未确认"
            # ⚠️ 2026-11-08 01:38 修复中通道
            if r["label"] != "🟢可观察" and _is_repair(r) and r.get("ma20_up", True):
                r["label"] = "🟡修复中"
                r["l3_tier"] = "repair"
        r.pop("k_wins", None)

    # 产业链正反馈：长寿命闭环 + 价格确认（覆盖单节点标签）
    if t_main and t_main.get("beta1_long"):
        holes = t_main.get("holes") or []
        # 孔洞落在哪个节点：用 birth 半径内是否包含该节点近似——按 κ 最高且动量为正的节点标
        pos = [r for r in rows if r["mom20"] > 0 and r["above_ma20"]]
        if pos:
            best = max(pos, key=lambda r: (r["k"] or -9))
            best["label"] = "🟢产业链正反馈"
            best["b1_note"] = "长寿命孔洞 %d 个 → 环节闭环" % t_main["beta1_long"]

    obs = [r for r in rows if r["label"].startswith("🟢")]
    repair = [r for r in rows if r.get("l3_tier") == "repair"]
    risk = [r for r in rows if (r["label"].startswith("🔴") or r["label"].startswith("🟡"))
            and r.get("l3_tier") != "repair"]
    if verbose:
        log("[l3] %s → %d 个三级（可观察 %d / 风险 %d / 修复中 %d）"
            % (l2, len(rows), len(obs), len(risk), len(repair)))
    _attach_tech_and_leaders(rows, pending, panel, mf, snap, verbose)
    return {"l2": l2, "ok": True, "rows": rows, "warns": warns,
            "topo": t_main, "n_nodes": len(names),
            "beta1_skip": len(names) < MIN_NODES_B1,
            "n_obs": len(obs), "n_risk": len(risk), "n_repair": len(repair),
            "k_win": KAPPA_WIN, "k_nwin": N_WIN, "tau_shrink": TAU_SHRINK}


# ══════════════════════════════════════════ 主入口
def pick_opportunities(date=None, strict=True):
    """从 cri_topology 全池缓存取**真正有机会**的行业，用于下钻

    ⚠️ 2026-11-07 18:03 大佬：「不是，你只选有机会的，其他的你不要瞎钻，浪费精力、浪费钱」

    旧逻辑把 A/B/C 三档**全部**拿去下钻（11-06 实测 15 个），
    其中 8 个根本不满足「上涨」：航空装备 -2.2%、电网设备 -4.5%、
    电视广播 -3.0%、轨交设备 -2.1%、照明设备 -1.0% 全是逆势。
    C 档（农商行/股份制银行/保险）虽然涨，但 κR 全负 = 撕裂，那是风险不是机会。

    新口径（strict=True）：
      A 档：全收（抱团共振：κR>0↑ + 20日动量>0 + 今日涨）
      B 档：**只收 mom20 > 0 的**（独立强势 + 实际在涨；动量负的直接丢）
      C 档：**不收**（撕裂反转 = 风险，不下钻）
      → 预计 6~8 个，省一大半时间
    """
    d8 = date or datetime.date.today().strftime("%Y%m%d")
    cp = os.path.join(CRIT_CACHE, "full_%s.json" % d8)
    if not os.path.exists(cp):
        # ⚠️ 不能直接 sorted(所有 full_*)：full_nodata.json 的 "n" 排在数字后，
        #    会被当成最新一份，里面没有 full_scan → 空结果。只认 8 位日期文件。
        import re as _re
        fs = [f for f in os.listdir(CRIT_CACHE)
              if _re.match(r"^full_\d{8}\.json$", f)]
        if not fs:
            return [], None, {}
        cp = os.path.join(CRIT_CACHE, sorted(fs)[-1])
    try:
        j = json.load(open(cp, encoding="utf-8"))
    except Exception:
        return [], None, {}
    fs = j.get("full_scan") or {}
    hz = j.get("hurst") or {}
    out = []
    src = {}          # 来源计数（报告要写明每层过滤了多少）
    if strict:
        # ══ 2026-11-07 19:44 大佬：「黄金、AI视频、牛奶这些应该都是可以买的呀」
        #    问题：下钻名单原来只从 A/B/C 三档来，**Hurst 上升趋势名单完全没接进来**，
        #    导致 数字媒体(+14.6%)、影视院线(+9.4%)、广告营销(+4.1%) 这些明明在涨的
        #    行业一个都没被下钻。
        #    → 改成「Hurst 上升趋势 ∪ A/B 档（mom20>0）」的并集。
        # ① Hurst 上升趋势（全收：平滑上涨，H 够 + 涨 + 跑赢）
        n = 0
        for o in (hz.get("trend") or []):
            if o.get("trend_dir") == "up" and o["name"] not in out:
                out.append(o["name"]); n += 1
        src["hurst_up"] = n
        # ② Hurst「强动量非平滑」（V型上涨，Hurst 天生低但涨得凶）
        n = 0
        for o in (hz.get("jump") or []):
            if o["name"] not in out:
                out.append(o["name"]); n += 1
        src["hurst_jump"] = n
        # ③ A 档全收（抱团共振）
        n = 0
        for r in (fs.get("A") or []):
            if r.get("kind") == "sw" and r["name"] not in out:
                out.append(r["name"]); n += 1
        src["abc_A"] = n
        # ④ B 档只收 20 日动量 > 0
        n = 0
        nB_all = len([r for r in (fs.get("B") or []) if r.get("kind") == "sw"])
        for r in (fs.get("B") or []):
            if r.get("kind") != "sw" or r["name"] in out:
                continue
            if (r.get("mom20") or 0) > 0:
                out.append(r["name"]); n += 1
        src["abc_B_keep"] = len([r for r in (fs.get("B") or [])
                                 if r.get("kind") == "sw" and (r.get("mom20") or 0) > 0])
        src["abc_B_new"] = n
        src["abc_B_all"] = nB_all
        # C 档（撕裂）不收
        src["abc_C_drop"] = len([r for r in (fs.get("C") or []) if r.get("kind") == "sw"])
    else:
        for k in ("A", "B", "C"):
            for r in (fs.get(k) or []):
                if r.get("kind") == "sw" and r["name"] not in out:
                    out.append(r["name"])
    # Hurst 各层计数（报告漏斗要用）
    _up = [o for o in (hz.get("trend") or []) if o.get("trend_dir") == "up"]
    src["hz_n_all"] = hz.get("n_all")
    src["hz_n_trend"] = hz.get("n_trend")
    src["hz_n_up"] = len(_up)
    src["hz_n_dn"] = (hz.get("n_trend") or 0) - len(_up)
    src["hz_n_jump"] = hz.get("n_jump")
    src["hz_n_flat"] = len(hz.get("flat") or [])
    return out, j.get("date"), src


def drill(names=None, date=None, verbose=True):
    sw = load_sw()
    panel = load_panel()
    src = {}
    # ⚠️ 2026-11-07 踩坑：原本下面直接 `names, _d, src = pick_opportunities(date)`
    #   把 names 覆盖成列表，导致后面 `if names is None:` 永远 False →
    #   缓存分支永远不执行（文件停在 19:54 却没人发现）。
    #   → 单独用 auto 标记
    auto = (names is None)
    if auto:
        names, _d, src = pick_opportunities(date)
        if date is None:
            date = _d
    if not names:
        return {"ok": False, "err": "没有可下钻的机会行业"}
    dates = list(panel.index)
    mf = moneyflow_5d(dates)
    snap = market_snapshot(str(dates[-1]))
    res = []
    for nm in names:
        try:
            res.append(drill_one(nm, sw, panel, dates, mf, snap=snap, verbose=verbose))
        except Exception as e:
            log("[l3] %s 下钻失败 %s" % (nm, str(e)[:120]))
            res.append({"l2": nm, "ok": False, "why": str(e)[:120]})
    tot_obs = sum(x.get("n_obs", 0) for x in res if x.get("ok"))
    tot_risk = sum(x.get("n_risk", 0) for x in res if x.get("ok"))
    # ── 可关注个股（只留 ✅买点成立）
    #   大佬 2026-11-07：「你都没当买卖点，我肯定不要买啊」
    # 只留「过门槛的龙头里 ✅买点成立」的（大佬：没买点肯定不买）
    watch = []
    for it in res:
        if not it.get("ok"):
            continue
        for r in it.get("rows") or []:
            if not r.get("l3_qualified", True):
                continue
            # 最后一道：残差动量必须为正（大佬 2026-11-07 17:58）
            if r.get("resid_ratio") is not None and not r.get("resid_ok"):
                continue
            for x in r.get("leaders") or []:
                tc = x.get("tech") or {}
                if (tc.get("verdict") or "").startswith("✅"):
                    watch.append({"name": x.get("name") or x["code"],
                                  "code": x["code"],
                                  "l2": it["l2"], "l3": r["l3"],
                                  "resid_ratio": r.get("resid_ratio"),
                                  "rank": x.get("rank"), "score": x.get("score"),
                                  "amt_share": (x.get("parts") or {}).get("amt_share"),
                                  "buy_tag": tc.get("buy_tag"),
                                  "buy_px": tc.get("buy_px"),
                                  "age": tc.get("age"),
                                  "active": tc.get("active"),
                                  "why": tc.get("why") or []})
    _pr = {"三买": 0, "二买": 1, "一买": 2}
    watch.sort(key=lambda w: (_pr.get(w.get("buy_tag") or "", 9), w.get("age") or 99))
    n_tech = sum(1 for it in res if it.get("ok")
                 for r in (it.get("rows") or [])
                 for x in (r.get("leaders") or []) if x.get("tech"))
    n_qual = sum(1 for it in res if it.get("ok")
                 for r in (it.get("rows") or []) if r.get("l3_qualified"))
    n_repair = sum(1 for it in res if it.get("ok")
                   for r in (it.get("rows") or []) if r.get("l3_tier") == "repair")
    # ── 漏斗统计（报告要写明每层过滤了多少、留下什么）
    funnel = {"src": src or {},
              "hz_n_trend": (src or {}).get("hz_n_trend"),
              "hz_n_up": (src or {}).get("hz_n_up"),
              "hz_n_dn": (src or {}).get("hz_n_dn"),
              "hz_n_jump": (src or {}).get("hz_n_jump"),
              "hz_n_flat": (src or {}).get("hz_n_flat")}
    funnel["n_ind_in"] = len(names)              # 进下钻的行业数
    funnel["n_ind_ok"] = len([x for x in res if x.get("ok")])
    funnel["n_l3"] = sum(len(it.get("rows") or []) for it in res if it.get("ok"))
    # 各子环节被卡在哪一层
    stop_up = stop_money = stop_resid = 0
    for it in res:
        if not it.get("ok"):
            continue
        for r in it.get("rows") or []:
            if r.get("l3_qualified"):
                continue
            why = r.get("l3_qualify_why") or ""
            if "无上涨" in why:
                stop_up += 1
            elif "无资金聚集" in why:
                stop_money += 1
            elif "残差动量" in why:
                stop_resid += 1
    funnel["stop_no_up"] = stop_up
    funnel["stop_no_money"] = stop_money
    funnel["stop_resid"] = stop_resid
    funnel["n_coarse_obs"] = tot_obs + tot_risk
    funnel["n_watch"] = len(watch)
    # ── 🟡 修复中（只观察，不给 ⭐可买）大佬 2026-11-08 01:38
    repair = []
    for it in res:
        if not it.get("ok"):
            continue
        for r in it.get("rows") or []:
            if r.get("l3_tier") != "repair":
                continue
            repair.append({"l2": it["l2"], "l3": r["l3"],
                           "mom20": r.get("mom20"), "mom5": r.get("mom5"),
                           "today": r.get("today"), "flow_5d": r.get("flow_5d"),
                           "resid_ratio": r.get("resid_ratio"),
                           "ma20_up": r.get("ma20_up"),
                           "why": r.get("l3_qualify_why")})
    repair.sort(key=lambda x: -(x.get("flow_5d") or 0))
    out = {"ok": True, "date": date or (dates[-1] if dates else None),
           "ind": res, "n_ind": len([x for x in res if x.get("ok")]),
           "n_obs": tot_obs, "n_risk": tot_risk, "n_repair": n_repair,
           "repair": repair,
           "watch": watch, "n_watch": len(watch), "n_tech": n_tech,
           "n_qual": n_qual, "funnel": funnel,
           "_hz_n_trend": (src or {}).get("hz_n_trend"),
           "_hz_n_up": (src or {}).get("hz_n_up"),
           "_hz_n_dn": (src or {}).get("hz_n_dn"),
           "_hz_n_jump": (src or {}).get("hz_n_jump"),
           "_hz_n_flat": (src or {}).get("hz_n_flat"),
           "mf_days": mf.get("days", 0),
           "windows": {"kappa": KAPPA_WIN, "n_win": N_WIN, "tau_shrink": TAU_SHRINK}}
    # ⚠️ 只在「自动模式」才写日期缓存，
    #    否则 --ind 单测会把正式结果覆盖掉（2026-11-07 踩过）
    if auto:
        try:
            # ⚠️ 2026-11-07 踩坑：rows 里 `_rets` 是 pandas Series、`_codes` 是 list，
            #   json.dump 序列化 Series 会抛 TypeError，又被 except 吞掉 →
            #   缓存永远写不进去（文件时间停在 19:54 却没人发现）。
            #   → 先剔除不可序列化字段 + 递归清洗 NaN/inf
            def _clean(o):
                if isinstance(o, dict):
                    return {k: _clean(v) for k, v in o.items()
                            if not str(k).startswith("_")}
                if isinstance(o, (list, tuple)):
                    return [_clean(v) for v in o]
                if isinstance(o, float):
                    return o if np.isfinite(o) else None
                if isinstance(o, np.floating):
                    v = float(o)
                    return v if np.isfinite(v) else None
                if isinstance(o, np.integer):
                    return int(o)
                if isinstance(o, np.bool_):
                    return bool(o)
                if isinstance(o, np.ndarray):
                    return _clean(o.tolist())
                if isinstance(o, pd.Series):
                    return None
                return o
            cp2 = os.path.join(CACHE, "drill_%s.json" % out["date"])
            with open(cp2, "w", encoding="utf-8") as f:
                json.dump(_clean(out), f, ensure_ascii=False,
                          default=float, indent=1)
            log("[l3] 缓存已写 %s" % os.path.basename(cp2))
        except Exception as e:
            log("[l3] 缓存写入失败 %s" % str(e)[:120])
    return out


# ══════════════════════════════════════════ 渲染
def _p(x, nd=1):
    try:
        v = float(x)
        if not np.isfinite(v):
            return "—"
        return "%+.*f%%" % (nd, v)
    except Exception:
        return "—"


def _k(x):
    try:
        v = float(x)
        if not np.isfinite(v):
            return "—"
        return "%+.3f" % v
    except Exception:
        return "—"


def _mf(x):
    if x is None:
        return "—"
    try:
        v = float(x) / 1e4          # 万元 → 亿元
    except Exception:
        return "—"
    return "%+.2f亿" % v


def _kf(x):
    """残差动量比值格式化"""
    try:
        v = float(x)
        if not np.isfinite(v):
            return "—"
        return "%+.2f" % v
    except Exception:
        return "—"


def _ktag(r):
    """κ 显示：没做拓扑直接返回空串（不占版面）；不一致加标注"""
    if r.get("k") is None:
        return ""
    return _k(r.get("k")) + ("" if r.get("k_ok") else "(3窗不一致)")


def render_lines(res):
    """文字版（QQ / 报告正文）"""
    L = []
    if not res.get("ok"):
        return ["（子行业下钻 [%s]：%s）" % (VER, res.get("err", "无数据"))]
    L.append("🔬 子行业下钻诊断 · %s" % VER_FULL)
    L.append("口径：三级指数=成分股等权平均 ｜κ 窗口 %d 日×%d 窗口方向一致才采信"
             % (res["windows"]["kappa"], res["windows"]["n_win"]))
    if res.get("mf_days"):
        L.append("资金：主力净流入 = 成分股近 5 日累计（已取到 %d 天）" % res["mf_days"])
    else:
        L.append("资金：⚠ 主力资金取数失败，标签降级为「价格+曲率」双条件")
    L.append("拓扑：子环节≥%d 才做 kNN 建图；<%d 个只判涨/资金聚集/残差动量（不做拓扑）" % (MIN_NODES_TOPO, MIN_NODES_TOPO))
    L.append("死区：|κ|<%.2f 视为几何无信号（零轴摆动）" % KAPPA_EPS)
    L.append("最后一道：残差动量（子环节对沪深300 回归取残差，近20日累计/σ>0 才算有机会）")
    L.append("技术确认：%s" % _TECH_VER)
    # ══ 漏斗明细（大佬 2026-11-07 19:55：每一步怎么过滤的，要写明白）
    _fn = res.get("funnel") or {}
    _src = _fn.get("src") or {}
    L.append("")
    L.append("━━ 🎯 筛选漏斗 · 每一步滤掉多少 ━━")
    L.append("　【第1步】全池扫描：申万二级 124 个（剔除无数据/陈旧的 10 个）")
    L.append("　【第2步】行业 Hurst 分组（每行业独立算 DFA）")
    def _n(v):
        try:
            return int(v)
        except Exception:
            return 0
    L.append("　　　📈有趋势 %d（上升 %d ｜下降 %d）｜⚡强动量非平滑 %d ｜⚪无趋势 %d"
             % (_n(res.get("_hz_n_trend")), _n(res.get("_hz_n_up")),
                _n(res.get("_hz_n_dn")), _n(res.get("_hz_n_jump")),
                _n(res.get("_hz_n_flat"))))
    L.append("　【第3步】下钻名单 = Hurst上升 ∪ 强动量非平滑 ∪ A档 ∪ (B档&20日>0)")
    L.append("　　　① Hurst上升趋势 → %s 个（全收）"
             % _src.get("hurst_up", "—"))
    L.append("　　　② Hurst强动量非平滑 → 新增 %s 个（去重后）"
             % _src.get("hurst_jump", "—"))
    L.append("　　　③ 全池A档 抱团共振 → 新增 %s 个（去重后）"
             % _src.get("abc_A", "—"))
    L.append("　　　④ 全池B档 独立强势 %s 个 → 只留20日动量>0 的 %s 个"
             "（→ 最终新增 %s 个）"
             % (_src.get("abc_B_all", "—"), _src.get("abc_B_keep", "—"),
                _src.get("abc_B_new", 0)))
    L.append("　　　⑤ 全池C档 撕裂反转 %s 个 → 全弃（是风险不是机会）"
             % _src.get("abc_C_drop", "—"))
    L.append("　　　→ 合并去重后下钻 %d 个行业" % _fn.get("n_ind_in", res["n_ind"]))
    L.append("　【第4步】下钻到子环节（申万三级等权合成）：共 %d 个" % _fn.get("n_l3", 0))
    L.append("　　　❌ 无上涨 滤掉 %d ｜❌ 无资金聚集 滤掉 %d ｜❌ 残差动量为负 滤掉 %d"
             % (_fn.get("stop_no_up", 0), _fn.get("stop_no_money", 0),
                _fn.get("stop_resid", 0)))
    L.append("　　　→ 过门槛（上涨+资金+残差）剩 %d 个 ｜🟡修复中单列 %d 个"
             "（5日>0 且 资金>0 且 20日跌幅小于5个点，只观察）"
             % (res.get("n_qual", 0), res.get("n_repair", 0)))
    L.append("　【第5步】过门槛的才评龙头（综合分=资金35%+成交额25%+市值20%+动量20%）")
    L.append("　　　→ 筛选候选 %d 只（前3只标👑；非前3但「资金流入+上涨」也一并纳入）"
             % (res.get("n_tech", 0)))
    L.append("　【第6步】所有候选都过技术确认（不止前3；缠论买点+SMC结构+均线排列）")
    L.append("　　　✅买点成立 %d ｜其余为⚠️等确认/❌不碰/⛔无买卖点"
             % res.get("n_watch", 0))
    L.append("　【结果】%d 个行业 → %d 子环节 → %d 过门槛 → %d 龙头 → ✅%d 只可买"
             % (_fn.get("n_ind_in", 0), _fn.get("n_l3", 0),
                res.get("n_qual", 0), res.get("n_tech", 0), res.get("n_watch", 0)))
    L.append("")
    L.append("下钻 %d 个行业 → 子环节 %d 个（可观察 %d ｜🟡修复中 %d ｜风险 %d）｜过门槛 %d 个"
             % (res["n_ind"], res["n_obs"] + res["n_risk"] + res.get("n_repair", 0),
                res["n_obs"], res.get("n_repair", 0),
                res["n_risk"], res.get("n_qual", 0)))
    L.append("候选龙头股 %d 只（全部已技术确认，前3标👑）→ ✅有买点 %d 只"
             % (res.get("n_tech", 0), res.get("n_watch", 0)))

    # ⭐ 可以关注（只列 ✅买点成立）
    w = res.get("watch") or []
    if w:
        L.append("")
        L.append("⭐ 可以关注 · 龙头股有买点（上涨+资金聚集 → 龙头 → 技术确认）")
        for x in w[:12]:
            s = "  ✅ %s（%s第%d名）｜%s › %s ｜%s@%.2f(%s根前%s)" % (
                x["name"], "龙头" if (x.get("rank") or 9) <= 3 else "", x.get("rank") or 0,
                x["l2"], x["l3"],
                x.get("buy_tag") or "—", x.get("buy_px") or 0, x.get("age") or 0,
                "·现价在点位上" if x.get("active") else "")
            if x.get("resid_ratio") is not None:
                s += " ｜残差动量 %s" % _kf(x["resid_ratio"])
            L.append(s)
            if x.get("why"):
                L.append("      ↳ %s" % "；".join(x["why"][:2]))
    elif res.get("n_tech"):
        L.append("")
        L.append("⭐ 可以关注：今日无 —— 龙头股 %d 只全部未过技术确认" % res["n_tech"])
    # 🟡 修复中（5日>0 + 资金>0 + 20日>-5%）—— 只观察，不给 ⭐可买
    rp = res.get("repair") or []
    if rp:
        L.append("")
        L.append("🟡 修复中 · 只观察不买（跌过之后资金先进入、5日均已转正）")
        for x in rp[:10]:
            L.append("  🟡 %s › %s ｜20日 %s ｜5日 %s ｜今日 %s ｜资金 %s"
                     % (x["l2"], x["l3"], _p(x.get("mom20")), _p(x.get("mom5")),
                        _p(x.get("today")), _mf(x.get("flow_5d"))))
    for it in res.get("ind") or []:
        if not it.get("ok"):
            L.append("")
            L.append("━━ %s ━━ 跳过（%s）" % (it["l2"], it.get("why", "无数据")))
            continue
        t = it.get("topo") or {}
        L.append("")
        if it.get("no_topo"):
            L.append("━━ %s ━━ %d 个子环节（不做拓扑：节点<5 无统计意义）" % (
                it["l2"], it["n_nodes"]))
        else:
            L.append("━━ %s ━━ %d 个子环节 ｜β0=%s%s%s" % (
                it["l2"], it["n_nodes"], t.get("beta0", "—"),
                (" ｜β1长寿孔洞 %d" % t["beta1_long"]) if t.get("beta1_long") else "",
                " ｜（节点<5 不判 β1）" if it.get("beta1_skip") else ""))
        for r in sorted(it["rows"], key=lambda x: -(x.get("k") or -9)):
            tag = "【环节】" if r.get("src") == "ths_concept" else ""
            _rm = r.get("resid_ratio")
            _rt = ("残差动量%s" % _kf(_rm)) if _rm is not None else "残差动量—"
            _kt = _ktag(r)
            _kpart = (" ｜κ %s" % _kt) if _kt else ""
            _ma = "%s(%+.1f%%)" % (
                "↑" if r.get("ma20_up") else "↓", r.get("ma20_slope") or 0)
            L.append("  %s %s%s%s ｜%s ｜20日 %s ｜5日 %s ｜今日 %s ｜MA20 %s ｜资金 %s" % (
                r["label"], tag, r["l3"], _kpart, _rt,
                _p(r["mom20"]), _p(r["mom5"]), _p(r["today"]),
                _ma, _mf(r.get("flow_5d"))))
            if r.get("ths_code"):
                L.append("      ↳ 同花顺概念指数 %s（申万三级无此环节）" % r["ths_code"])
            if r.get("k_top"):
                L.append("      ↳ 个股 κ：%s" % "、".join(
                    "%s %s%s" % (x["code"], _k(x["k"]), "" if x["ok"] else "(不一致)")
                    for x in r["k_top"]))
            if r.get("k_note") and not it.get("no_topo"):
                L.append("      ↳ %s" % r["k_note"])
            if r.get("b1_note"):
                L.append("      ↳ %s" % r["b1_note"])
            # ── 龙头股（必须先过「上涨+资金聚集」门槛）
            if r.get("l3_tier") == "repair":
                L.append("      🟡 %s —— 只观察，不给龙头/不给⭐可买"
                         % (r.get("l3_qualify_why") or "修复中"))
            elif not r.get("l3_qualified", True):
                L.append("      ⛔ 不给龙头：%s" % (r.get("l3_qualify_why") or "未过门槛"))
            else:
                lds = r.get("leaders") or []
                if lds:
                    L.append("      👑 龙头（%s）：共 %d 只候选，全部已跑技术确认"
                             % (r.get("l3_qualify_why") or "已过门槛", len(lds)))
                    _show = lds[:12]
                    for x in _show:
                        tc = x.get("tech") or {}
                        s = "        %s%s %s ｜综合 %.2f" % (
                            "👑" if x.get("is_leader") else "  ",
                            tc.get("verdict") or "⛔未确认",
                            x.get("name") or x["code"], x.get("score") or 0)
                        p = x.get("parts") or {}
                        s += " ｜成交额占%s%%" % p.get("amt_share", "—")
                        s += " ｜20日 %s" % _p(x.get("mom20"))
                        if x.get("flow_5d") is not None:
                            s += " ｜近5日 %+.2f亿" % (x["flow_5d"] / 1e4)
                        if tc.get("buy_tag"):
                            s += " ｜%s@%.2f(%s根前)" % (
                                tc["buy_tag"], tc.get("buy_px") or 0, tc.get("age") or 0)
                        L.append(s)
                        if tc.get("why"):
                            L.append("            ↳ %s" % "；".join(tc["why"]))
                    if len(lds) > len(_show):
                        L.append("        …另 %d 只候选已确认（未展开）" % (len(lds) - len(_show)))
                else:
                    L.append("      ⚠ 已过门槛但无成分股数据")
        if it.get("warns"):
            L.append("  ⚠ 样本不足：%s" % "、".join(it["warns"]))
    return L


def render_blocks(res):
    """飞书卡片（markdown 段）"""
    B = []
    if not res.get("ok"):
        return B
    B.append("**🔬 子行业下钻诊断 · %s**" % VER_FULL)
    B.append("<font color='grey'>三级=成分股等权合成 ｜ 子环节≥%d 才做 kNN(k=%d) 拓扑，"
             "<%d 只判涨/资金/残差 ｜ 资金=近5日主力净流入</font>"
             % (MIN_NODES_TOPO, KNN_K, MIN_NODES_TOPO))
    B.append("下钻 **%d** 个行业 → 🟢可观察 **%d** ｜🟡修复中 **%d** ｜⚠风险 **%d**"
             % (res["n_ind"], res["n_obs"], res.get("n_repair", 0), res["n_risk"]))
    for it in res.get("ind") or []:
        if not it.get("ok"):
            B.append("<font color='grey'>━━ %s ━━ 跳过（%s）</font>" % (it["l2"], it.get("why", "")))
            continue
        t = it.get("topo") or {}
        B.append("")
        B.append("**━━ %s ━━** %d 个子环节%s ｜β0=%s%s" % (
            it["l2"], it["n_nodes"],
            ("（个股层 %d 只）" % it.get("n_stock", 0)) if it.get("stock_mode") else "",
            t.get("beta0", "—"),
            (" ｜β1长寿孔洞 %d" % t["beta1_long"]) if t.get("beta1_long") else ""))
        for r in sorted(it["rows"], key=lambda x: -(x.get("k") or -9)):
            col = "red" if r["label"].startswith("🔴") else (
                "green" if r["label"].startswith("🟢") else "grey")
            tag = "【环节】" if r.get("src") == "ths_concept" else ""
            B.append("%s **%s%s** ｜κ %s ｜20日 <font color='%s'>%s</font> ｜5日 %s ｜"
                     "今日 %s ｜MA20 %s ｜资金 <font color='%s'>%s</font>" % (
                         r["label"], tag, r["l3"], _ktag(r),
                         "red" if r["mom20"] > 0 else "green", _p(r["mom20"]),
                         _p(r["mom5"]), _p(r["today"]),
                         "站上" if r["above_ma20"] else "破位",
                         "red" if (r.get("flow_5d") or 0) > 0 else "green",
                         _mf(r.get("flow_5d"))))
    return B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ind", nargs="*", help="指定 L2 行业名（默认自动取机会行业）")
    ap.add_argument("--date", help="YYYYMMDD（用于读全池缓存）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = drill(names=a.ind, date=a.date)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, default=float, indent=1))
    else:
        print("\n".join(render_lines(res)))


if __name__ == "__main__":
    main()
