#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宏观 CRI + 行业拓扑几何（双口径）· 纯脚本 0 token

════════════════════════════════════════════════════════════════════
大佬 2026-10-27 需求（原话要点）
  · Part1 宏观风控：三级减仓框架 → CRI 综合风险指数（Hurst/ACF/波动百分位/熵/KL/HMM）
  · Part2 中观结构：行业拓扑几何 → 资金抱团 / 结构撕裂 / 产业链正反馈
  · 4.1 计算流程（20:38 追加，核心）：
      第一步 去市场因子：每个行业收益率对沪深300 回归取残差（禁止直接用原始收益率
             算相关，危机期万物高相关，图上永远一个团块）
      第二步 相关矩阵去噪：Ledoit-Wolf 收缩 或 RMT(Marchenko-Pastur)
      → 目的是区分两类：① 能跟着指数一起涨的（跟随型）② 真正独立强的（独立型）
      → 两个口径的标签都要发给用户
  · 标的池（23:50 追加）：A 组 10 个主流行业 + B 组两个实盘自选股所属行业
      + C 组五大宽基指数
  · ⭐ 重点提醒（20:24 追加，最高优先级）：
      行业同时满足 ① 资金抱团（簇内 κ>0 且较 5 日前上升）
                    ② 20 日动量 >0
                    ③ 今日上涨
      → 放报告最顶部单独一块，重点提醒

════════════════════════════════════════════════════════════════════
窗口口径（重要，别乱改）
  · β 回归窗口     = 120 交易日
  · 相关矩阵窗口   = 60 交易日（T=60 > N≈25，必须 T>N，否则样本相关矩阵奇异、
                     Ledoit-Wolf 与 MP 去噪都失真。20 日窗口 T<N 不可用）
  · 动量窗口       = 20 交易日（mom20）；另给 mom5
  · κ 变化对比     = t vs t-5 交易日
  · CRI 样本       = 沪深300 最近 250 交易日

════════════════════════════════════════════════════════════════════
公开接口
  analyze(groups=None, date=None, with_cri=True) -> dict
  build_block(groups=None, date=None, with_cri=True) -> (lines, card_blocks)
      —— build_block 永不抛异常（内部兜底返回空）

共用库：scripts/cri_topology/topolib.py（角距离图 + 加权 Ollivier-Ricci + ripser PH）
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

# 容器 IPv6 不通：强制 IPv4（参考 MEMORY.md）
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd

CACHE_DIR = os.path.join(WS, ".cache", "cri_topology")
os.makedirs(CACHE_DIR, exist_ok=True)

BETA_WIN, CORR_WIN, MOM_WIN, REF_OFFSET = 120, 60, 20, 5
CRI_SAMPLE = 250

# 全池扫描：申万二级全量（2026-10-28 大佬要求「134 个全过一遍」）
FULL_STALE_DAYS = 15          # 最后交易日落后今天超过这个自然日数 → 判为陈旧剔除
FAST_TOPO_MIN_N = 40          # N≥40 时用 POT 快速最优传输算 Ricci（与 linprog 等价，快 ~200x）

MKT_NAME, MKT_CODE = "沪深300", "000300.SH"

# ── C 组：五大宽基（大佬 2026-10-27 23:50 要求"加上五大宽基指数"）──
WIDE = [("沪深300", "000300.SH"), ("中证500", "000905.SH"), ("中证1000", "000852.SH"),
        ("创业板指", "399006.SZ"), ("科创50", "000688.SH")]

# ── A 组：10 个主流行业（申万二级）──
MAIN10 = ["证券Ⅱ", "白酒Ⅱ", "半导体", "医疗器械", "电池",
          "航空装备Ⅱ", "工业金属", "房地产开发", "游戏Ⅱ", "国有大型银行Ⅱ"]

# 名称容错：传入简称自动补全
ALIAS = {
    "证券": "证券Ⅱ", "白酒": "白酒Ⅱ", "航空装备": "航空装备Ⅱ", "军工": "航空装备Ⅱ",
    "银行": "国有大型银行Ⅱ", "银行Ⅱ": "国有大型银行Ⅱ", "大行": "国有大型银行Ⅱ",
    "地产": "房地产开发", "房地产": "房地产开发", "游戏": "游戏Ⅱ",
    "有色": "工业金属", "有色金属": "工业金属", "新能源车": "电池", "新能源": "电池",
    "医药": "医疗器械", "医疗": "医疗器械", "消费": "白酒Ⅱ",
    "元件": "元件", "通信设备": "通信设备", "化学制药": "化学制药",
    "医疗服务": "医疗服务", "小金属": "小金属", "互联网电商": "互联网电商",
    "乘用车": "乘用车", "本地生活服务": "本地生活服务Ⅱ", "本地生活服务Ⅱ": "本地生活服务Ⅱ",
    "生物制品": "生物制品", "半导体": "半导体",
}


def log(*a):
    print(*a, flush=True)


# ══════════════════════════════════════════ 数据层
def _sw_map():
    import weekly_zs as W
    m = W.sw_l2_map()
    return {v: k for k, v in m.items()}          # 名称 → 代码


def _resolve(names, swmap):
    """行业名 → (标准名, 代码)；跳过匹配不到的（记 log，不报错）"""
    out, miss = [], []
    for n in names:
        nm = ALIAS.get(n, n)
        code = swmap.get(nm) or swmap.get(nm.replace("Ⅱ", ""))
        if code:
            out.append((nm, code))
        else:
            miss.append(n)
    if miss:
        log("[cri_topology] 行业名未匹配，跳过：%s" % "、".join(miss))
    return out


def _bars_sw(code, need=400):
    """申万二级行业指数日线（走 weekly_zs 的 mtime-TTL 缓存）"""
    import weekly_zs as W
    df = W.fetch_sw_daily(code)
    if df is None or len(df) < 80:
        return None
    d = df.copy()
    d = d.rename(columns={"dt": "date"}) if "dt" in d.columns else d
    d["date"] = pd.to_datetime(d["date"])
    return d[["date", "close"]].dropna().sort_values("date").tail(need).reset_index(drop=True)


def _bars_wide(name, code, need=400):
    """宽基指数日线（复用 chan_signal_monitor 的 TTL 缓存）"""
    try:
        import chan_signal_monitor as CSM
        df = CSM.fetch_daily_fresh(code)
    except Exception:
        df = None
    if df is None or len(df) < 80:
        try:
            from tushare_init import ts_call
            df = ts_call("index_daily", ts_code=code)
            if df is not None and len(df):
                df = df.rename(columns={"trade_date": "dt"})
                df["dt"] = pd.to_datetime(df["dt"])
                df = df.sort_values("dt").reset_index(drop=True)
        except Exception as e:
            log("[cri_topology] %s 日线失败 %s" % (name, str(e)[:80]))
            return None
    d = df.copy()
    if "dt" in d.columns:
        d = d.rename(columns={"dt": "date"})
    d["date"] = pd.to_datetime(d["date"])
    return d[["date", "close"]].dropna().sort_values("date").tail(need).reset_index(drop=True)


def _holdings_groups():
    """B 组：两个实盘持仓所属申万二级行业 + 仓位标注"""
    try:
        import weekly_zs as W
        agg = {}          # 行业 → {"moergen": w, "yali": w, "stocks": [...]}
        for lab, f in W.PORTS:
            tag = "默尔根" if "moergen" in str(f) or "默尔根" in str(lab) else "亚丽"
            for ind, v in W.holdings_industries(f).items():
                o = agg.setdefault(ind, {"w": {}, "stocks": []})
                o["w"][tag] = o["w"].get(tag, 0.0) + float(v["w"])
                for s in v.get("stocks") or []:
                    o["stocks"].append((s[0], float(s[1]), tag))
        return agg
    except Exception as e:
        log("[cri_topology] 持仓行业读取失败 %s" % str(e)[:100])
        return {}


def _pos_txt(hold, ind):
    o = hold.get(ind) or {}
    w = o.get("w") or {}
    if not w:
        return ""
    return " ".join("%s%.1f%%" % (k, v) for k, v in w.items() if v > 0)


def _stk_txt(hold, ind, n=3):
    o = hold.get(ind) or {}
    # ⚠️ 同一只股被两个组合同时持有时会重复（中芯国际 默尔根+亚丽），
    #    这里按股票名合并权重，避免“中芯国际17.9%、中芯国际16.1%”这种重复
    agg = {}
    for a, b, _tag in (o.get("stocks") or []):
        agg[a] = agg.get(a, 0.0) + float(b)
    ss = sorted(agg.items(), key=lambda x: -x[1])[:n]
    ss = [(a, b) for a, b in ss if b >= 0.05]      # 滤掉 0.0%（四舍五入后的噪声）
    return "、".join("%s%.1f%%" % (a, b) for a, b in ss)


# ══════════════════════════════════════════ 全池数据层
def _full_sw_pool(need=400, verbose=True):
    """申万二级全量标的（134 个）→ ([(name, code)...], dropped, dfs)

    dropped: [(code, name, reason)...]，reason ∈ {无数据, 数据不足, 陈旧}
    dfs:     {name: DataFrame}  已取好的日线（避免 analyze 里重复取一次）

    剔除规则（2026-10-28）：
      · tushare 无该指数日线（农业综合Ⅱ/其他家电Ⅱ/… 共 9 个）
      · 行数 < 80（不足以算 β120+cost）
      · 最后交易日落后今天 > FULL_STALE_DAYS 自然日（林业Ⅱ 只到 2021-12-10）
    任何单标的异常都 try/except 吞掉并记入 dropped，绝不拖垮整体。
    """
    import weekly_zs as W
    raw = W.sw_l2_map()                       # {code: name}
    today = pd.Timestamp(datetime.date.today())
    out, dropped, dfs = [], [], {}
    items = sorted(raw.items(), key=lambda kv: kv[1])     # 按行业名排序，输出稳定

    # ⚠️ 负缓存：tushare 对这 9 个二级行业根本没有日线，fetch_sw_daily 会
    #    重试 4 次并 sleep(4+8+12+16)=40s/个 → 9 个白烧 360s（实测总耗时 432s
    #    里 360s 全耗在这）。记进 full_nodata.json，7 天内直接跳过。
    _np = os.path.join(CACHE_DIR, "full_nodata.json")
    _bad = {}
    try:
        if os.path.exists(_np) and time.time() - os.path.getmtime(_np) < 7 * 86400:
            _bad = json.load(open(_np, encoding="utf-8"))
    except Exception:
        _bad = {}

    for _i, (code, name) in enumerate(items):
        try:
            if verbose and _i and _i % 20 == 0:
                log("[cri_topology] 全池取数 %d/%d（已剔除 %d）" % (_i, len(items), len(dropped)))
            if code in _bad:
                dropped.append((code, name, "无数据(负缓存)"))
                continue
            df = _bars_sw(code, need=need)
            if df is None or len(df) < 80:
                dropped.append((code, name, "无数据"))
                _bad[code] = datetime.date.today().strftime("%Y-%m-%d")
                continue
            last = pd.Timestamp(df["date"].iloc[-1])
            if (today - last).days > FULL_STALE_DAYS:
                dropped.append((code, name, "陈旧(%s)" % last.strftime("%Y-%m-%d")))
                continue
            out.append((name, code))
            dfs[name] = df
        except Exception as e:                 # 单标的失败不影响整体
            dropped.append((code, name, "异常:%s" % str(e)[:40]))
            log("[cri_topology] 全池 %s(%s) 取数异常 %s" % (name, code, str(e)[:80]))
    try:
        json.dump(_bad, open(_np, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    if verbose and dropped:
        log("[cri_topology] 全池剔除 %d 个：%s" % (
            len(dropped), "、".join("%s(%s)" % (nm, why) for _c, nm, why in dropped)))
    return out, dropped, dfs


# ══════════════════════════════════════════ Part 1 · CRI
def _hurst_dfa(x, scales=None):
    x = np.asarray(x, float)
    y = np.cumsum(x - x.mean())
    N = len(y)
    if scales is None:
        scales = np.unique(np.floor(np.logspace(np.log10(6), np.log10(max(N // 4, 8)), 14)).astype(int))
    F, ok = [], []
    for s in scales:
        if s < 4 or s > N // 2:
            continue
        nseg = N // s
        rms = []
        for i in range(nseg):
            seg = y[i * s:(i + 1) * s]
            t = np.arange(s)
            fit = np.polyval(np.polyfit(t, seg, 1), t)
            rms.append(np.mean((seg - fit) ** 2))
        if rms:
            F.append(np.sqrt(np.mean(rms)))
            ok.append(s)
    if len(F) < 3:
        return np.nan
    return float(np.polyfit(np.log(ok), np.log(F), 1)[0])


def _sampen(x, m=2, r_ratio=0.2):
    x = np.asarray(x, float)
    N = len(x)
    r = r_ratio * x.std(ddof=1)
    if r <= 1e-12:
        return 0.0

    def cnt(mm):
        tmpl = np.array([x[i:i + mm] for i in range(N - mm)])
        c = 0
        for a in range(len(tmpl)):
            dd = np.max(np.abs(tmpl[a + 1:] - tmpl[a]), axis=1)
            c += int((dd <= r).sum())
        return c
    B, A = cnt(m), cnt(m + 1)
    if B == 0 or A == 0:
        return 0.0
    return float(-np.log(A / B))


def _kl(cur, base, bins=15, eps=1e-6):
    lo, hi = float(np.min(base)), float(np.max(base))
    if hi - lo < 1e-9:
        return 0.0
    edges = np.linspace(lo, hi, bins + 1)
    p, _ = np.histogram(cur, bins=edges)
    q, _ = np.histogram(base, bins=edges)
    p = p / max(p.sum(), 1) + eps
    q = q / max(q.sum(), 1) + eps
    p /= p.sum()
    q /= q.sum()
    return float(np.sum(p * np.log(p / q)))


def _clip(x):
    return float(np.clip(x, 0, 100))


def _hmm_regime(ret, n_states=2, lookback=750, n_seeds=(42, 7, 2026)):
    """两状态高斯 HMM（低波动/高波动 regime）。返回 dict，永不抛异常。

    2026-10-28 补齐（之前 "hmm": None 是写死的占位，环境里 hmmlearn 0.3.3 一直可用）。
    · 用 2 状态：实测 3 状态会退化成一个极端的 -7%/日 崩盘簇（vol 9.9%），
      样本里只有寥寥几天，状态概率不稳；2 状态把「平静/高波动」分得干净
    · 多 seed 取对数似然最高的一个（EM 对初值敏感）
    · f_M = 当前处于高波动状态的**后验概率** ×100（0~100）
    """
    try:
        from hmmlearn.hmm import GaussianHMM
        import logging as _lg
        # hmmlearn 的 "Model is not converging" 是 logger 输出，EM 小幅回退很正常，降到 ERROR
        _lg.getLogger("hmmlearn").setLevel(_lg.ERROR)
    except Exception as e:
        return {"ok": False, "err": "hmmlearn 不可用: %s" % str(e)[:60]}
    x = np.asarray(ret[-lookback:], float).reshape(-1, 1)
    if len(x) < 120:
        return {"ok": False, "err": "样本不足 %d" % len(x)}
    best = None
    for sd in n_seeds:
        try:
            m = GaussianHMM(n_components=n_states, covariance_type="diag",
                            n_iter=500, tol=1e-5, random_state=sd).fit(x)
            ll = float(m.score(x))
        except Exception:
            continue
        if best is None or ll > best[0]:
            best = (ll, m)
    if best is None:
        return {"ok": False, "err": "EM 拟合失败"}
    ll, m = best
    try:
        post = m.predict_proba(x)
        st = int(np.argmax(post[-1]))
        vols = np.sqrt(np.maximum(m.covars_[:, 0, 0], 0))
        hi = int(np.argmax(vols))
        return {"ok": True, "state": st, "is_high": bool(st == hi),
                "p_high_now": float(post[-1, hi]),
                "p_high_next": float(m.transmat_[st, hi]),
                "p_stay": float(m.transmat_[st, st]),
                "vols": [float(v) for v in vols],
                "means": [float(v) for v in m.means_.ravel()],
                "f": float(np.clip(post[-1, hi] * 100, 0, 100)),
                "converged": bool(getattr(m.monitor_, "converged", True)),
                "ll": ll, "n": int(len(x)), "n_states": n_states}
    except Exception as e:
        return {"ok": False, "err": "推断失败: %s" % str(e)[:60]}



def _resid_momentum(ret, mom_win=20, trend_win=20):
    """指数自身的**残差动量**（替代 DFA/Hurst）

    大佬 2026-11-07：「我觉得你这个趋势还不如用那个残差呢」「先不用这个 DFA 太麻烦了」

    做法：
      ① 用收益率的 rolling 均值当「自身趋势」（等价于 20 日均线收益）
      ② 残差 = 实际收益 − 趋势收益
      ③ resid_mom = 近 mom_win 日残差累计（对数收益）
      ④ resid_ratio = resid_mom / 同窗口残差标准差（标准化，跨时期可比）

    含义：
      resid_ratio > 0 → 实际走势强于自身趋势 → 趋势有 Edge → 敢加仓
      resid_ratio < 0 → 跑不过自己的趋势 → Edge 走弱 → 减仓
    比 Hurst 直白：Hurst 要估幂律指数、还要 bootstrap CI，噪声大；
    残差动量直接就是「最近跑没跑赢自己的趋势」。

    返回 (resid_mom, resid_ratio, resid_pct)
      resid_pct = resid_ratio 在历史同期窗口中的分位（0-100）
    """
    r = np.asarray(ret, dtype=float)
    r = r[np.isfinite(r)]
    need = max(trend_win + mom_win, mom_win * 3)
    if len(r) < need:
        return float("nan"), float("nan"), float("nan")
    tr = pd.Series(r).rolling(trend_win, min_periods=trend_win).mean().values
    resid = r - tr
    resid = resid[np.isfinite(resid)]
    if len(resid) < mom_win:
        return float("nan"), float("nan"), float("nan")
    # 当前残差动量
    cur = float(np.sum(resid[-mom_win:]))
    sd = float(np.std(resid[-mom_win:], ddof=1))
    ratio = cur / sd if sd > 1e-12 else 0.0
    # 历史分位（滚动的 resid_mom 序列）
    hist = []
    for t in range(mom_win, len(resid) + 1, 1):
        seg = resid[t - mom_win:t]
        s2 = float(np.std(seg, ddof=1))
        hist.append(float(np.sum(seg)) / s2 if s2 > 1e-12 else 0.0)
    pct = float((np.array(hist) <= ratio).mean() * 100) if hist else float("nan")
    return cur, ratio, pct


def calc_cri(idx_df):
    """沪深300 → CRI 各层分数。返回 dict。"""
    px = idx_df["close"].values.astype(float)
    ret = np.diff(np.log(px))
    out = {"n": len(px), "close": float(px[-1]), "ret_last": float(ret[-1])}

    win = 120
    # ⚠️ 2026-11-07 大佬：「先不用这个 DFA 太麻烦了」「趋势还不如用残差」
    #   记忆层不再用 Hurst/DFA，改用**指数自身的残差动量**（相对自身趋势的偏离）
    #   → 下面 H/H_hist 保留为 NaN 占位（其他代码/报告仍读这个字段，不删键）
    H = float("nan")
    H_hist = np.array([])

    acf_s = []
    for t in range(60, len(ret) + 1):
        s = ret[t - 60:t] - ret[t - 60:t].mean()
        den = float(np.sum(s * s))
        acf_s.append(float(np.sum(s[1:] * s[:-1]) / den) if den > 1e-12 else 0.0)
    acf_s = np.array(acf_s)
    acf1 = float(acf_s[-1])

    vol20 = pd.Series(ret).rolling(20).std(ddof=1) * np.sqrt(252)
    vv = vol20.dropna().values
    vol_cur = float(vol20.iloc[-1])
    h100 = vv[-100:]
    vol_pct = float((h100 <= vol_cur).mean() * 100)

    W_ = 100
    ent_h = np.array([_sampen(ret[t - W_:t]) for t in range(W_, len(ret) + 1)])
    ent = float(ent_h[-1])
    ent_p50, ent_p90 = [float(x) for x in np.percentile(ent_h, [50, 90])]
    ent_rank = float((ent_h <= ent).mean() * 100)

    base = ret[-CRI_SAMPLE:] if len(ret) >= CRI_SAMPLE else ret
    kl_h = np.array([_kl(ret[t - W_:t], base) for t in range(W_, len(ret) + 1)])
    kl = float(kl_h[-1])
    kl_p90 = float(np.percentile(kl_h, 90))
    cur100 = ret[-W_:]

    # ── f(x) 映射：严格按大佬提示词给的表 ──
    def _c01(x):
        return float(np.clip(x, 0, 1)) * 100

    # ── 记忆层（2026-11-07 改）：残差动量替代 DFA/Hurst ──
    # 思路：把指数收益对**自身趋势**（20 日均线收益）回归取残差，
    #      残差动量 = 近 20 日残差累计。残差动量 < 0 = 跑不过自己的趋势 = Edge 走弱。
    # 输出两个量：
    #   resid_mom   近 20 日残差累计（对数收益单位）
    #   resid_ratio 残差动量 / 同窗口波动（标准化，跨时期可比）
    resid_mom, resid_ratio, resid_pct = _resid_momentum(ret, MOM_WIN)

    # 残差动量分位 → f_R（0=强势可加仓，100=走弱该减仓）
    #   resid_ratio > 0.5 → 0 分（趋势很顺）
    #   resid_ratio < -0.5 → 100 分（严重跑输自身趋势）
    if np.isfinite(resid_ratio):
        f_R = _c01((0.5 - resid_ratio) / 1.0)
    else:
        f_R = 50.0

    f_H = f_R          # 兼容旧字段名（下游报告/缓存仍读 f_H）
    f_A = _c01(1 - np.clip(acf1 / 0.15, 0, 1))
    f_V = _c01((vol_pct - 50) / 40)
    f_E = _c01((ent - ent_p50) / max(ent_p90 - ent_p50, 1e-9))
    kl_mu_t, kl_sd_t = float(np.mean(kl_h)), float(np.std(kl_h, ddof=1))
    f_K = _c01((kl - kl_mu_t) / max(2 * kl_sd_t, 1e-9))
    # HMM regime（2026-10-28 补齐）：分布层 = KL 突变 + HMM 高波动后验，各半
    hmm = _hmm_regime(ret)
    f_M = float(hmm["f"]) if hmm.get("ok") else 50.0
    L1 = 0.5 * f_R + 0.5 * f_A
    L2 = 0.5 * f_V + 0.5 * f_E
    # HMM 可用 → 0.5/0.5；不可用 → 缺失项按剩余层等比重分配（退回只有 f_K）
    L3 = (0.5 * f_K + 0.5 * f_M) if hmm.get("ok") else f_K

    # 对照：旧版（距离型 / 分位型映射），用于交叉验证
    _fH2 = _clip(100 * (1 - abs(H - 0.62) / 0.30)) if np.isfinite(H) else 50.0
    _fA2 = _clip(abs(acf1) / 0.15 * 100)
    _fE2 = _clip(100 * (1 - ent_rank / 100))
    _L3b = _clip(kl / max(kl_p90, 1e-9) * 100)
    CRI_alt = 0.35 * (0.5 * _fH2 + 0.5 * _fA2) + 0.35 * (0.6 * vol_pct + 0.4 * _fE2) + 0.30 * _L3b

    # ── 三级判断逻辑（原始提示词的硬条件，逐条打「是/否 + 实测值」）──
    kl_mu, kl_sd = float(np.mean(kl_h)), float(np.std(kl_h, ddof=1))
    # 一级·记忆层（改残差动量口径）：残差动量转负 + ACF≤0 → 趋势 Edge 消失
    trig1 = bool(np.isfinite(resid_ratio) and resid_ratio < 0 and acf1 <= 0)
    trig2 = bool(vol_pct > 80 or ent > ent_p90)
    trig3 = bool(kl > kl_mu + 2 * kl_sd or
                 (hmm.get("ok") and hmm["p_high_now"] > 0.5))

    CRI = 0.35 * L1 + 0.35 * L2 + 0.30 * L3
    if CRI < 20:
        st, pos = "🟢", "满仓运行"
    elif CRI < 40:
        st, pos = "🟡", "新开仓减半"
    elif CRI < 70:
        st, pos = "🟠", "总仓≤50%，止损收紧1×ATR"
    else:
        st, pos = "🔴", "趋势仓全平，等CRI回落"

    out.update({"H": H, "resid_mom": resid_mom, "resid_ratio": resid_ratio,
                "resid_pct": resid_pct, "f_R": f_R,
                "acf1": acf1, "vol": vol_cur, "vol_pct": vol_pct,
                "ent": ent, "ent_p50": ent_p50, "ent_p90": ent_p90, "ent_rank": ent_rank,
                "kl": kl, "kl_p90": kl_p90, "kl_mu": kl_mu, "kl_sd": kl_sd,
                "trig1": trig1, "trig2": trig2, "trig3": trig3,
                "f_H": f_H, "f_A": f_A, "f_V": f_V, "f_E": f_E, "f_K": f_K,
                "f_M": f_M, "hmm": hmm,
                "CRI_alt": CRI_alt,
                "mu100": float(cur100.mean()), "sd100": float(cur100.std(ddof=1)),
                "mu_all": float(base.mean()), "sd_all": float(base.std(ddof=1)),
                "L1": L1, "L2": L2, "L3": L3, "CRI": CRI, "state": st, "pos": pos})
    return out


# ══════════════════════════════════════════ Part 2 · 拓扑
def _lw_corr(R):
    R = np.asarray(R, dtype=float)
    ok = R.std(axis=0) > 1e-12                  # 零方差列（基准自身）不参与收缩
    try:
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf().fit(R[:, ok])
        S = lw.covariance_
        s = np.sqrt(np.diag(S))
        Csub = S / np.outer(s, s)
        np.fill_diagonal(Csub, 1.0)
        n = R.shape[1]
        C = np.eye(n)
        idx = np.where(ok)[0]
        C[np.ix_(idx, idx)] = Csub
        return np.nan_to_num(C, nan=0.0), float(lw.shrinkage_)
    except Exception:
        return _safe_corr(R), 0.0


def _safe_corr(M):
    """相关矩阵兜底：NaN/Inf→0，零方差列除对角置 0，保证对称正可用"""
    with np.errstate(invalid="ignore", divide="ignore"):
        C = np.corrcoef(M.T)
    C = np.nan_to_num(np.asarray(C, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    np.fill_diagonal(C, 1.0)
    return np.clip((C + C.T) / 2.0, -1, 1)


def _nan_corr(M, min_periods=20):
    """**pairwise** 相关矩阵：允许 NaN（不同标的缺日不同），
    每对标的只用两者**同时有数据**的样本算相关 —— 全池模式的关键。

    np.corrcoef 遇 NaN 整行变 NaN，不能用于全池（139 个标的求交集可能只剩几十天）。
    pandas.DataFrame.corr() 默认逐对 complete-observation，正是要的语义。
    """
    df = pd.DataFrame(np.asarray(M, dtype=float))
    C = df.corr(min_periods=min_periods).values
    C = np.nan_to_num(np.asarray(C, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    np.fill_diagonal(C, 1.0)
    return np.clip((C + C.T) / 2.0, -1, 1)


def _mp_denoise(C, T):
    n = C.shape[0]
    C = np.nan_to_num(np.asarray(C, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    C = (C + C.T) / 2.0
    np.fill_diagonal(C, 1.0)
    try:
        lam, V = np.linalg.eigh(C)
    except np.linalg.LinAlgError:
        lam, V = np.linalg.eigh(C + np.eye(n) * 1e-8)
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


def _topo(C, names, tag=""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import topolib
    try:
        return topolib.run_topo(C, names, tag, verbose=False)
    except Exception as e:
        log("[cri_topology] topo %s 失败 %s" % (tag, str(e)[:100]))
        return None


# ══════════════════════════════════════════ 大 N 快速拓扑（全池扫描用）
# 背景：topolib.weighted_ricci 对每条边用 scipy.linprog 解一个 19321 变量的大 LP，
#      N=23 时 127 条边要 1.1s；N=139 时 3813 条边 → 估计 6~10 分钟，加上要算
#      4 张图（当日 raw/idio + t-5 raw/idio）必然超时。
# 方案：同一条最优传输问题改用 POT(ot.emd) 的网络单纯形求解——数学上完全等价
#      （同样的 ground metric M、同样的惰性随机游走测度、同样的 W1），实测
#      N=23 时 127 条边的 κ 与 linprog 版最大差 6.66e-16（浮点噪声级），
#      但快 ~200x。仅在 N≥FAST_TOPO_MIN_N 时启用；N 小仍走 topolib 原路径，
#      保证 23 标的模式数值完全不变。
def _fast_ricci(D, A, alpha=0.5):
    """加权 Ollivier-Ricci（POT 版）。返回 (kappa_dict, node_k, gk)，与 topolib 同构。"""
    import ot
    import networkx as nx
    n = A.shape[0]
    deg = A.sum(axis=1)
    meas = np.zeros((n, n))
    for i in range(n):
        if deg[i] == 0:
            meas[i, i] = 1.0
            continue
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
            if a != b and np.isfinite(v):
                M[a, b] = float(v)

    kappa, kpair = {}, {}
    vals = {i: [] for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if not A[i, j]:
                continue
            dij = M[i, j]
            if (not np.isfinite(dij)) or dij <= 1e-9 or dij >= 1e3:
                continue
            try:
                Gt = ot.emd(meas[i], meas[j], M, numItermax=200000)
                v = 1.0 - float((Gt * M).sum()) / dij
            except Exception:
                continue
            kappa[(i, j)] = v
            kpair[(i, j)] = dij
            vals[i].append(v)
            vals[j].append(v)
    node_k = {i: (float(np.mean(vals[i])) if vals[i] else np.nan) for i in range(n)}
    gk = float(np.mean(list(kappa.values()))) if kappa else np.nan
    return kappa, node_k, gk


def _topo_fast(C, names, tag=""):
    """_topo 的大 N 版：同样的角距离图/τ/簇/PH，Ricci 换成 POT 求解。"""
    n = C.shape[0]
    try:
        from ripser import ripser
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import topolib
        C = np.nan_to_num(np.asarray(C, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
        C = np.clip((C + C.T) / 2.0, -1, 1)
        D = np.sqrt(np.maximum(2 * (1 - C), 0))
        iu = np.triu_indices(n, 1)
        dvec = D[iu]
        tau = float(np.median(dvec))
        A = ((D <= tau) & (~np.eye(n, dtype=bool))).astype(int)
        comp, b0 = topolib.components(A)
        kappa, node_k, gk = _fast_ricci(D, A, alpha=0.5)
        dg = ripser(D, distance_matrix=True, maxdim=1)["dgms"]
        h1 = dg[1]
        p75 = float(np.percentile(dvec, 75))
        long_holes = []
        for b, dd in h1:
            dinf = not np.isfinite(dd)
            d2 = dvec.max() * 1.2 if dinf else dd
            if d2 - b > p75:
                long_holes.append((float(b), None if dinf else float(dd), float(d2 - b)))
        dens = A.sum() / (n * (n - 1))
        clu_k = {}
        for ci in range(b0):
            mem = [i for i in range(n) if comp[i] == ci]
            vv = [v for (a, b), v in kappa.items() if a in mem and b in mem]
            clu_k["簇%d" % (ci + 1)] = float(np.mean(vv)) if vv else None
        return {"tag": tag, "tau": tau, "p75": p75, "beta0": b0,
                "beta1_long": len(long_holes), "holes": long_holes,
                "gk": gk, "node_k": node_k, "comp": comp.tolist(),
                "clusters": {"簇%d" % (ci + 1): [names[i] for i in range(n) if comp[i] == ci]
                             for ci in range(b0)},
                "cluster_k": clu_k, "edge": int(A.sum() // 2), "dens": float(dens),
                "corr_mean": float(C[iu].mean()), "D_mean": float(dvec.mean())}
    except Exception as e:
        log("[cri_topology] topo_fast %s 失败 %s" % (tag, str(e)[:100]))
        return None


def _topo_auto(C, names, tag=""):
    """N 大走 POT 快路径，N 小走 topolib 原路径（保证 23 标的模式数值不变）。"""
    if C is not None and C.shape[0] >= FAST_TOPO_MIN_N:
        return _topo_fast(C, names, tag)
    return _topo(C, names, tag)


def _cluster_k_of(t, i):
    """节点 i 所在簇的平均 κ（单节点簇返回 None = 无簇）"""
    if not t:
        return None
    comp = t.get("comp") or []
    if i >= len(comp):
        return None
    ci = comp[i]
    mem = [k for k, c in enumerate(comp) if c == ci]
    if len(mem) < 2:
        return None
    return t.get("cluster_k", {}).get("簇%d" % (ci + 1))


# ══════════════════════════════════════════ 主分析
def analyze(groups=None, date=None, with_cri=True, full_pool=False):
    """groups: {组名: [行业名...]}；None = 默认 A/B。返回结果 dict。

    full_pool=True（2026-10-28 大佬要求）：标的池换成 134 个申万二级全体
    （剔除无数据/陈旧）+ 5 大宽基，并额外产出「全池扫描 · 有机会」A/B/C 三档。

    当日缓存：同一进程/同一天内多次调用直接读 .cache/cri_topology/<date>.json，
    避免 19:40 报告与其它任务重复跑 60 秒级计算（环境变量 CRIT_NO_CACHE=1 禁用）。
    全池结果单独缓存 full_<date>.json（与 23 标的缓存互不覆盖）。
    """
    t0 = time.time()
    _d = date or datetime.date.today().strftime("%Y%m%d")
    _cp = os.path.join(CACHE_DIR, ("full_%s.json" if full_pool else "%s.json") % _d)
    if os.environ.get("CRIT_NO_CACHE", "0") != "1" and os.path.exists(_cp):
        try:
            if time.time() - os.path.getmtime(_cp) < 3600:      # 1 小时内复用
                d = json.load(open(_cp, encoding="utf-8"))
                if d.get("ok") and d.get("rows"):
                    d["cached"] = True
                    return d
        except Exception:
            pass
    hold = _holdings_groups()

    # 默认分组（C 组宽基由 wide=True 标记，走指数取数）
    if groups is None:
        groups = {"A·10个主流行业": list(MAIN10),
                  "B·我的自选股行业": list(hold.keys())}

    # 补：五大宽基单独一组（大佬 23:50 要求）—— 始终加
    gname_wide = "C·五大宽基"
    wide_names = [n for n, _ in WIDE]

    swmap = _sw_map()
    # 统一标的池（行业 + 宽基），保持顺序稳定
    pool, seen_code = [], set()

    def _add(nm, code, kind):
        key = (nm, code)
        if key in seen_code:
            return nm
        seen_code.add(key)
        pool.append({"name": nm, "code": code, "kind": kind})
        return nm

    grp_resolved = {}
    _full_dropped, _full_dfs = [], {}
    if full_pool:
        # ── 全池：134 个申万二级全体（无数据/陈旧自动剔除并计数）
        sw_items, _full_dropped, _full_dfs = _full_sw_pool()
        for nm, code in sw_items:
            _add(nm, code, "sw")
        grp_resolved["全池·申万二级"] = [nm for nm, _ in sw_items]
        for nm, code in WIDE:
            _add(nm, code, "wide")
        grp_resolved[gname_wide] = wide_names
    else:
        for g, lst in (groups or {}).items():
            rr = []
            for nm, code in _resolve(lst, swmap):
                _add(nm, code, "sw")
                rr.append(nm)
            grp_resolved[g] = rr
        for nm, code in WIDE:
            _add(nm, code, "wide")
        grp_resolved[gname_wide] = wide_names

    names = [p["name"] for p in pool]
    kind_of = {p["name"]: p["kind"] for p in pool}
    code_of = {p["name"]: p["code"] for p in pool}
    if len(names) < 5:
        return {"ok": False, "err": "标的数 %d < 5，无法做拓扑" % len(names),
                "lines": ["（行业数不足，跳过拓扑）"], "blocks": []}

    # ── 取数
    ser = {}
    for _i, p in enumerate(pool):
        if _i and _i % 20 == 0:
            log("[cri_topology] 取数进度 %d/%d（%.0fs）" % (_i, len(pool), time.time() - t0))
        if p["name"] in _full_dfs:
            ser[p["name"]] = _full_dfs[p["name"]]
            continue
        df = _bars_sw(p["code"]) if p["kind"] == "sw" else _bars_wide(p["name"], p["code"])
        if df is not None and len(df) > BETA_WIN + 40:
            ser[p["name"]] = df
        else:
            log("[cri_topology] %s 数据不足，剔除" % p["name"])
    names = [n for n in names if n in ser]
    if len(names) < 5:
        return {"ok": False, "err": "有效标的 < 5", "lines": ["（有效行业数不足，跳过拓扑）"],
                "blocks": []}
    n = len(names)

    # 沪深300 市场基准
    mkt_df = _bars_wide(MKT_NAME, MKT_CODE)
    if mkt_df is None or len(mkt_df) < BETA_WIN + 40:
        return {"ok": False, "err": "沪深300 数据不足", "lines": ["（市场基准缺失）"], "blocks": []}

    # ⚠️ 必须按**交易日对齐**（join on date），不能按数组尾部对齐——
    #    行业指数偶有缺日/停牌，尾部对齐会让 β 回归错位（曾算出 β=-0.46 的假值）
    mpan = mkt_df.set_index("date")["close"].astype(float)
    rmap = {}
    for nm in names:
        rmap[nm] = ser[nm].set_index("date")["close"].astype(float)

    corr_mode = "intersect"
    T = 0
    if full_pool:
        # ── 全池：**pairwise** 相关（不强求 139 个标的同时有数据）
        #    139 个标的硬求交集可能只剩几十天 → 相关矩阵失真。这里改成：
        #    ① 日历取「最晚开始日 → 最新交易日」的并集（保证每列开头都有数据）
        #    ② 每列各自的 β/动量/今日涨跌只用**该列自己的**可用样本
        #    ③ 相关矩阵用 pandas 逐对 complete-observation 计算
        PX = pd.DataFrame(rmap)
        PX[MKT_NAME] = mpan
        PX = PX.sort_index()
        starts = [PX[c].dropna().index.min() for c in names]
        starts = [s for s in starts if s is not None and pd.notna(s)]
        start = max(starts) if starts else PX.index.min()
        ann = PX[MKT_NAME].dropna().index
        ann = ann[ann >= start]
        PX = PX.loc[ann]
        Lg = np.log(PX).diff()                 # 缺日 → 该列 NaN，不会产生跨日伪收益
        mret_s = Lg[MKT_NAME].astype(float)
        Rg = Lg[names].astype(float)
        T = len(Rg)
        n_common = int(Rg.notna().all(axis=1).sum())     # 全标的同日都有数据的天数
        win = Rg.tail(CORR_WIN)
        n_win_common = int(win.notna().all(axis=1).sum())
        corr_mode = "pairwise"
        log("[cri_topology] 全池 pairwise：日历 T=%d 日｜窗口 %d 日｜"
            "全标的同日都有数据 %d 日、窗口内 %d 日（不做交集，按列各自可用）"
            % (T, len(win), n_common, n_win_common))
        if T < MOM_WIN + 5:
            return {"ok": False, "err": "全池日历仅 %d 天，不足" % T,
                    "lines": ["（全池日历不足）"], "blocks": []}

        mret_full = mret_s.values                # 长度 T，可能含 NaN
        Rraw_full = Rg.values
        betas = np.full(n, np.nan)
        mom20 = np.full(n, np.nan)
        mom5 = np.full(n, np.nan)
        today = np.full(n, np.nan)
        ex_today = np.full(n, np.nan)
        alpha20 = np.full(n, np.nan)
        Rres_full = np.full_like(Rraw_full, np.nan)
        for i in range(n):
            y = Rraw_full[:, i]
            ok = np.isfinite(y) & np.isfinite(mret_full)
            yi = np.where(ok)[0]
            if len(yi) < MOM_WIN + 10:
                betas[i] = 1.0
                continue
            use = yi[-BETA_WIN:]
            x, yy = mret_full[use], y[use]
            vx = float(np.var(x, ddof=1))
            betas[i] = float(np.cov(x, yy, ddof=1)[0, 1] / vx) if vx > 1e-18 else 1.0
            # 残差要对**全部可用日**算（β 只是用最近 120 日估出来的）
            Rres_full[yi, i] = y[yi] - betas[i] * mret_full[yi]
            lastv = yi[-1]
            today[i] = y[lastv]
            ex_today[i] = y[lastv] - betas[i] * mret_full[lastv]
            own = np.where(np.isfinite(y))[0]
            mom20[i] = float(np.nansum(y[own[-MOM_WIN:]]))
            mom5[i] = float(np.nansum(y[own[-5:]]))
        betas = np.nan_to_num(betas, nan=1.0)

        # 残差去均值（按列，忽略 NaN）
        with np.errstate(invalid="ignore"):
            mu = np.nanmean(Rres_full, axis=0)
        Rres_full = Rres_full - mu
        # ⚠️ alpha20 必须在**去均值之后**算 —— 23 标的模式的口径是
        #    Rres_full[-MOM_WIN:].sum()（已 demean）。先在循环里算会差 mu*20，
        #    实测偏差可达 0.036，与 23 标的模式对不上。
        for i in range(n):
            res_i = Rres_full[:, i]
            ri = np.where(np.isfinite(res_i))[0]
            alpha20[i] = float(np.nansum(res_i[ri[-MOM_WIN:]])) if len(ri) else np.nan
        # 零方差列（沪深300 自身）→ 残差置 0，避免相关矩阵整行 NaN
        _sd = np.nanstd(Rres_full, axis=0)
        zero_var = np.nan_to_num(_sd, nan=0.0) < 1e-12
        if zero_var.any():
            Rres_full[:, zero_var] = 0.0

        # 口径① RAW：pairwise 相关
        Craw = _nan_corr(Rraw_full[-CORR_WIN:])
        t_raw = _topo_auto(Craw, names, "RAW60-pw")
        # 口径② IDIO：pairwise 残差相关 → 窗口无缺失就照旧 LW 收缩，有缺失才退 MP
        #    （LedoitWolf 不接受 NaN；本池实测 60 日窗口 124 列全齐，所以多数时候
        #      走的就是 LW，与 23 标的模式口径一致）
        Rw_res = Rres_full[-CORR_WIN:]
        C_idio_pw = _nan_corr(Rw_res)
        if np.isfinite(Rw_res).all():
            C_lw, shrink = _lw_corr(Rw_res)
            t_lw = _topo_auto(C_lw, names, "IDIO-LW-pw")
            C_mp, nkeep, lplus = _mp_denoise(C_idio_pw, len(win))
            t_mp = _topo_auto(C_mp, names, "IDIO-MP-pw")
        else:
            # 有缺失：LW 不可用 → 只用 MP 去噪（报告里 shrink 记 0 并说明）
            C_lw, shrink = C_idio_pw, 0.0
            t_lw = None
            C_mp, nkeep, lplus = _mp_denoise(C_idio_pw, len(win))
            t_mp = _topo_auto(C_mp, names, "IDIO-MP-pw")
        if t_lw is None:                       # 统一以 t_mp 充当 IDIO 主线
            t_lw = t_mp
            log("[cri_topology] 全池 IDIO 窗口存在缺失样本 → 跳过 LW，直接用 MP 去噪结果")

        # 5 日前对比（同一套 pairwise 规则，日历整体前移 5 行）
        Rw5 = Rraw_full[:-REF_OFFSET]
        Re5 = Rres_full[:-REF_OFFSET]
        t_raw_prev = _topo_auto(_nan_corr(Rw5[-CORR_WIN:]), names, "RAW60-pw-5d")
        _Re5w = Re5[-CORR_WIN:]
        if np.isfinite(_Re5w).all():
            C_lw5, _ = _lw_corr(_Re5w)
        else:
            C_lw5, _, _ = _mp_denoise(_nan_corr(_Re5w), len(win))
        t_lw_prev = _topo_auto(C_lw5, names, "IDIO-pw-5d")
    else:
        C_all = pd.DataFrame(rmap)
        C_all[MKT_NAME] = mpan
        C_all = C_all.dropna()                      # 只保留所有标的都有收盘的共同交易日
        if len(C_all) < BETA_WIN + MOM_WIN:
            return {"ok": False, "err": "共同交易日仅 %d 天，不足" % len(C_all),
                    "lines": ["（共同交易日不足）"], "blocks": []}
        Lret = np.log(C_all).diff().dropna()
        mret_full = Lret[MKT_NAME].values
        Rraw_full = Lret[names].values             # T×N
        T = Rraw_full.shape[0]
        n_common = T
        # β：每行业对市场做 120 日 OLS
        betas = []
        for i in range(n):
            x, y = mret_full[-BETA_WIN:], Rraw_full[-BETA_WIN:, i]
            vx = float(np.var(x, ddof=1))
            b = float(np.cov(x, y, ddof=1)[0, 1] / vx) if vx > 1e-18 else 1.0
            betas.append(b)
        betas = np.array(betas)
        Rres_full = Rraw_full - np.outer(mret_full, betas)
        Rres_full = Rres_full - Rres_full.mean(axis=0, keepdims=True)
        # ⚠️ 沪深300 既是基准又在 C 组池里 → 它对自身的残差恒为 0（std≈1e-18），
        #    相关矩阵整行 NaN → eigh 报 "Eigenvalues did not converge"。
        #    零方差的列（=基准自身）残差置 0，相关行除对角外置 0（无 IDIO 信息）。
        _sd = Rres_full.std(axis=0)
        zero_var = _sd < 1e-12
        if zero_var.any():
            Rres_full[:, zero_var] = 0.0

        mom20 = Rraw_full[-MOM_WIN:].sum(axis=0)
        mom5 = Rraw_full[-5:].sum(axis=0)
        today = Rraw_full[-1]
        ex_today = today - betas * mret_full[-1]      # 今日超额
        alpha20 = Rres_full[-MOM_WIN:].sum(axis=0)

        # ── 口径① RAW（原始收益相关，60 日）
        Craw = _safe_corr(Rraw_full[-CORR_WIN:])
        t_raw = _topo_auto(Craw, names, "RAW60")

        # ── 口径② IDIO（残差相关 + LW 收缩 + MP 去噪）
        C_lw, shrink = _lw_corr(Rres_full)
        t_lw = _topo_auto(C_lw, names, "IDIO-LW")
        C_mp, nkeep, lplus = _mp_denoise(_safe_corr(Rres_full), Rres_full.shape[0])
        t_mp = _topo_auto(C_mp, names, "IDIO-MP")

        # ── 5 日前对比（κ 是否上升）：用 t-5 日的窗口重算
        Rw5 = Rraw_full[:-REF_OFFSET]
        Re5 = Rres_full[:-REF_OFFSET]
        t_raw_prev = _topo_auto(_safe_corr(Rw5[-CORR_WIN:]), names, "RAW60-5d")
        C_lw5, _ = _lw_corr(Re5[-CORR_WIN:])
        t_lw_prev = _topo_auto(C_lw5, names, "IDIO-LW-5d")

    # ⚠️ κ 取**节点级**（node_k），不是簇平均 —— 簇平均会让同一簇内所有行业
    #    拿到同一个数字，失去区分度（实测 23 个标的全部 +0.09，等于没有信息）。
    #    簇级 κ 另存 cluster_k 供「簇是否抱团」判断。
    def _nodek(t):
        return [(t or {}).get("node_k", {}).get(i, np.nan) for i in range(n)]

    k_raw, k_lw = _nodek(t_raw), _nodek(t_lw)
    k_raw5, k_lw5 = _nodek(t_raw_prev), _nodek(t_lw_prev)
    # 每个节点自身所在簇的规模（用于「孤立节点」判定）
    def _csize(t, i):
        comp = (t or {}).get("comp") or []
        if i >= len(comp):
            return 0
        return sum(1 for c in comp if c == comp[i])
    csz_raw = [_csize(t_raw, i) for i in range(n)]
    csz_lw = [_csize(t_lw, i) for i in range(n)]

    def _f(x):
        try:
            return float(x)
        except Exception:
            return float("nan")

    rows = []
    for i, nm in enumerate(names):
        kr, kl = _f(k_raw[i]), _f(k_lw[i])
        kr5, kl5 = _f(k_raw5[i]), _f(k_lw5[i])
        # 孤立节点（自成一簇 / 无边）→ κ 无统计意义
        iso_raw = csz_raw[i] < 2
        iso_idio = csz_lw[i] < 2
        up_raw = (not iso_raw) and np.isfinite(kr) and np.isfinite(kr5) and kr > 0 and kr > kr5
        up_idio = (not iso_idio) and np.isfinite(kl) and np.isfinite(kl5) and kl > 0 and kl > kl5
        # 标签（节点级 κ 判撕裂 / 抱团）
        if iso_raw:
            lab_raw = "⚪孤立"
        elif np.isfinite(kr) and kr < 0:
            lab_raw = "🔴撕裂"
        elif up_raw:
            lab_raw = "🟢跟随抱团" if betas[i] >= 1.0 else "🟡抱团(β<1)"
        elif np.isfinite(kr):
            lab_raw = "⚪中性"
        else:
            lab_raw = "⚪无簇"
        if iso_idio:
            lab_idio = "⚪孤立"
        elif np.isfinite(kl) and kl < 0:
            lab_idio = "🔴撕裂"
        elif up_idio:
            lab_idio = "🟢独立强势" if alpha20[i] > 0 else "🟡抱团(α<0)"
        elif np.isfinite(kl):
            lab_idio = "⚪中性"
        else:
            lab_idio = "⚪无簇"
        rows.append({
            "name": nm, "code": code_of.get(nm, ""), "kind": kind_of.get(nm, "sw"),
            "beta": _f(betas[i]), "mom20": _f(mom20[i]), "mom5": _f(mom5[i]),
            "today": _f(today[i]), "ex_today": _f(ex_today[i]),
            "alpha20": _f(alpha20[i]),
            "k_raw": kr, "k_raw_prev": kr5, "up_raw": bool(up_raw),
            "k_idio": kl, "k_idio_prev": kl5, "up_idio": bool(up_idio),
            "label_raw": lab_raw, "label_idio": lab_idio,
            "pos": _pos_txt(hold, nm), "stocks": _stk_txt(hold, nm),
        })
    # kind 修正（宽基标记）
    for r in rows:
        if r["name"] in wide_names:
            r["kind"] = "wide"

    # ── 📈 行业趋势 Hurst（2026-11-07 18:56 大佬要求）
    #   「有的行业有趋势，有的行业没趋势。我只要找有行业趋势的就行了，
    #     算它的趋势赫斯特指数就行了，而不是算全行业的」
    #   → 每个行业单独算 DFA Hurst，只对有趋势的行业汇总
    hurst = None
    try:
        import industry_hurst as IH
        _hrows = []
        for i, nm in enumerate(names):
            if kind_of.get(nm) == "wide":
                continue
            try:
                rr = Rg[nm].dropna().values[-IH.WIN:]
                # 残差动量标准化：alpha20 / 残差20日标准差
                _res = Rres_full[:, i]
                _res = _res[np.isfinite(_res)][-MOM_WIN:]
                _sd = float(np.std(_res, ddof=1)) if len(_res) > 2 else 0.0
                _a20 = float(alpha20[i])
                _ratio = (_a20 / _sd) if _sd > 1e-12 else 0.0
                _hrows.append({
                    "name": nm,
                    "rets": rr,
                    "mom20": float(mom20[i]),
                    "resid_ratio": _ratio,
                    "k_raw": _f(k_raw[i]),
                    "today": _f(today[i]),
                    "beta": _f(betas[i]),
                })
            except Exception:
                continue
        if _hrows:
            hurst = IH.scan(_hrows)
            log("[cri_topology] 行业 Hurst：%d 个行业 → 有趋势 %d 个（%.0f%%）"
                % (hurst["n_all"], hurst["n_trend"], hurst["ratio"] * 100))
    except Exception as e:
        log("[cri_topology] 行业 Hurst 失败 %s" % str(e)[:120])

    # ── ⭐ 重点提醒（三条同时满足）
    follow = [r for r in rows if r["kind"] == "sw" and r["up_raw"] and r["beta"] >= 1.0
              and r["mom20"] > 0 and r["today"] > 0]
    indep = [r for r in rows if r["kind"] == "sw" and r["up_idio"] and r["alpha20"] > 0
             and r["ex_today"] > 0]

    # ── 🎯 全池扫描 · 有机会（仅 full_pool 模式）
    full_scan = _full_scan(rows) if full_pool else None

    cri = calc_cri(mkt_df) if with_cri else None

    _end_dt = None
    for nm in names:
        try:
            v = pd.Timestamp(ser[nm]["date"].iloc[-1])
            _end_dt = v if _end_dt is None else max(_end_dt, v)
        except Exception:
            pass
    res = {"ok": True, "date": (date or datetime.date.today().strftime("%Y%m%d")),
           "data_end": str(_end_dt.date()) if _end_dt is not None else "-",
           "n": n, "names": names, "rows": rows, "groups": grp_resolved,
           "cri": cri, "key_alerts": {"follow": follow, "indep": indep},
           "full_pool": bool(full_pool), "full_scan": full_scan,
           "hurst": hurst,
           "full_dropped": [{"code": c, "name": m, "why": w}
                            for c, m, w in (_full_dropped or [])],
           "corr_mode": corr_mode, "n_common": int(n_common),
           "topo": {"raw": t_raw, "idio_lw": t_lw, "idio_mp": t_mp,
                    "raw_prev": t_raw_prev, "idio_prev": t_lw_prev,
                    "lw_shrink": shrink, "mp_keep": nkeep, "mp_lambda_plus": lplus},
           "windows": {"beta": BETA_WIN, "corr": CORR_WIN, "mom": MOM_WIN,
                       "ref_offset": REF_OFFSET},
           "elapsed": round(time.time() - t0, 1)}
    try:
        cp = os.path.join(CACHE_DIR, ("full_%s.json" if full_pool else "%s.json")
                          % res["date"])
        # ⚠️ 2026-11-07 踩坑：numpy 的 nan/inf 会写出非法 JSON（NaN 字面量），
        #    json.load 直接报 "Expecting value" → 下游 l3_drill 读不到缓存。
        #    → 先递归清洗成 None
        def _clean(o):
            if isinstance(o, dict):
                return {k: _clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_clean(v) for v in o]
            if isinstance(o, float):
                return o if np.isfinite(o) else None
            if isinstance(o, (np.floating,)):
                v = float(o)
                return v if np.isfinite(v) else None
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, np.bool_):
                return bool(o)
            if isinstance(o, np.ndarray):
                return _clean(o.tolist())
            return o
        json.dump(_clean(res), open(cp, "w", encoding="utf-8"),
                  ensure_ascii=False, default=float, indent=1)
        res["cache"] = cp
    except Exception as e:
        log("[cri_topology] 缓存写入失败 %s" % str(e)[:100])
    return res


def _full_scan(rows):
    """全池「有机会」三档筛选（2026-10-28 大佬口径）。

    A 抱团共振（最强）：κR>0 且较 5 日前上升 + 20日动量>0 + 今日上涨
    B 独立强势：      κI>0 且上升 + α20>0 + 今日超额>0
    C 撕裂反转候选：  κ 撕裂(<0，任一 priority 口径为 RAW) 但 20日动量>0 且今日上涨
    输出按 A > B > C 优先级排列；同一标的多档命中时只保留最高档（A 优先）。
    """
    sw = [r for r in (rows or []) if r.get("kind") == "sw"]

    def _pos(x):
        try:
            return np.isfinite(float(x)) and float(x) > 0
        except Exception:
            return False

    # ── 2026-10-28 13:2x 大佬确认「可以」：B 档收紧门槛，滤掉噪声
    #    原门槛 α20>0 且 超额>0 太松 → 航空机场(超额+0.1%)、汽车零部件(超额+0.0%)
    #    这种刚过零轴的也算「有机会」，实为噪声。改为 α20>1% 且 今日超额>0.3%。
    B_MIN_ALPHA20, B_MIN_EX = 0.010, 0.003

    A = [r for r in sw if r.get("up_raw") and _pos(r.get("mom20")) and _pos(r.get("today"))]
    A.sort(key=lambda r: -(r.get("mom20") or 0))
    inA = {r["name"] for r in A}
    B = [r for r in sw if r["name"] not in inA and r.get("up_idio")
         and (r.get("alpha20") or 0) > B_MIN_ALPHA20
         and (r.get("ex_today") or 0) > B_MIN_EX]
    B.sort(key=lambda r: -(r.get("alpha20") or 0))
    inB = {r["name"] for r in B}

    def _tear(r):
        for k in ("k_raw", "k_idio"):
            try:
                if np.isfinite(float(r.get(k))) and float(r.get(k)) < 0:
                    return True
            except Exception:
                pass
        return False

    C = [r for r in sw if r["name"] not in inA and r["name"] not in inB
         and _tear(r) and _pos(r.get("mom20")) and _pos(r.get("today"))]
    C.sort(key=lambda r: -(r.get("mom20") or 0))

    n_up_mom = sum(1 for r in sw if _pos(r.get("mom20")))
    n_up_raw = sum(1 for r in sw if r.get("up_raw"))
    n_up_idio = sum(1 for r in sw if r.get("up_idio"))
    n_tear = sum(1 for r in sw if _tear(r))
    weak = sorted([r for r in sw if np.isfinite(r.get("mom20") or np.nan)],
                  key=lambda r: r.get("mom20") or 0)[:10]
    return {"A": A, "B": B, "C": C,
            "stat": {"n_sw": len(sw), "mom_up": n_up_mom, "raw_up": n_up_raw,
                     "idio_up": n_up_idio, "tear": n_tear,
                     "nA": len(A), "nB": len(B), "nC": len(C),
                     "b_min_alpha20": B_MIN_ALPHA20, "b_min_ex": B_MIN_EX},
            "weak": weak}


# ══════════════════════════════════════════ 渲染
def _p(x, nd=1, sign=True):
    try:
        if not np.isfinite(float(x)):
            return "—"
        v = float(x) * 100
        return ("%+.*f%%" if sign else "%.*f%%") % (nd, v)
    except Exception:
        return "—"


def _k(x):
    try:
        return "%+.2f" % float(x) if np.isfinite(float(x)) else "—"
    except Exception:
        return "—"


def _hmm_txt(c):
    """HMM regime 一句话（不可用时明说原因，不假装）"""
    h = (c or {}).get("hmm") or {}
    if not h.get("ok"):
        return "HMM 不可用（%s）" % (h.get("err") or "未知")
    tag = "高波动" if h["is_high"] else "平静"
    return "HMM 当前=%s态 p高=%.2f 明日转高=%.2f" % (
        tag, h["p_high_now"], h["p_high_next"])


def _full_lines(res, compact=False):
    """🎯 全池扫描文字块（lines 版）。compact=True 时 A 档只列前 5 条。"""
    fs = res.get("full_scan") or {}
    if not fs:
        return []
    st = fs.get("stat") or {}
    L = ["🎯 全池扫描 · 有机会（申万二级 %d 个有效标的）" % st.get("n_sw", 0)]
    if res.get("corr_mode") == "pairwise":
        L.append("  口径：全池用 pairwise 相关（不要求全部标的同日有数据），窗口 %d 日" % CORR_WIN)
    L.append("  B档门槛（2026-10-28 收紧）：α20 > %.0f%% 且 今日超额 > %.1f%%"
             % (st.get("b_min_alpha20", 0.01) * 100, st.get("b_min_ex", 0.003) * 100))

    def _one(r):
        return ("%s(%s) ｜β%.2f ｜20日 %s ｜今日 %s ｜κR %s%s ｜κI %s%s ｜α20 %s" % (
            r["name"], r.get("code", ""), r.get("beta") or 0,
            _p(r.get("mom20")), _p(r.get("today")),
            _k(r.get("k_raw")), "↑" if r.get("up_raw") else "",
            _k(r.get("k_idio")), "↑" if r.get("up_idio") else "",
            _p(r.get("alpha20"))))

    def _sec(title, arr, lim=None):
        if not arr:
            L.append("【%s】无" % title)
            return
        show = arr[:lim] if lim else arr
        L.append("【%s】%d 个" % (title, len(arr)))
        for r in show:
            L.append("  · " + _one(r))
        if lim and len(arr) > lim:
            L.append("  ……余 %d 个见飞书卡片" % (len(arr) - lim))

    _sec("A 抱团共振", fs.get("A") or [], 5 if compact else None)
    if not compact:                       # QQ 精简版只要 A 档前 5 + 统计
        _sec("B 独立强势", fs.get("B") or [])
        _sec("C 撕裂反转候选", fs.get("C") or [])
    else:
        L.append("（B 独立强势 %d 个 / C 撕裂反转候选 %d 个，明细见飞书卡片）" % (
            st.get("nB", 0), st.get("nC", 0)))
    L.append("▸ 统计：全池 有效 %d 个 ｜ 抱团上升 %d ｜ 独立走强 %d ｜ 撕裂 %d ｜ 20日动量>0 %d 个" % (
        st.get("n_sw", 0), st.get("raw_up", 0), st.get("idio_up", 0),
        st.get("tear", 0), st.get("mom_up", 0)))
    weak = fs.get("weak") or []
    if weak:
        L.append("▸ 最弱的 10 个（20日动量垫底）：%s" % "、".join(
            "%s %s" % (r["name"], _p(r.get("mom20"))) for r in weak))
    dr = res.get("full_dropped") or []
    if dr:
        L.append("▸ 剔除 %d 个（无数据/陈旧）：%s" % (
            len(dr), "、".join("%s(%s)" % (x["name"], x["why"]) for x in dr)))
    return L


# ══════════════════════════════════════════ 持仓行业 · 风险与机会
def _hold_views(res):
    """两个实盘持仓行业的「风险 + 机会」判定（2026-10-28 13:45 大佬要求）

    大佬原话：「我这两个实盘所属的行业，风险和机会你也都说一下子，也放在前面。
    因为我现在找不到我的哪个行业是风险还是机会，我只看到我的行业用缠论来分析了，
    但用你的这个行业拓扑分析，我没看到。」

    → 每个持仓行业给 **风险档** + **机会档**，各自带**可核对的数据理由**，不放空话。

    风险维度（累加）：
      R1 β 杠杆   ：β≥2.0 高（大盘跌 1% 跟跌 2%+）｜1.2~2.0 中｜<0.5 低（弱相关）
      R2 趋势     ：mom20 ≤-25% 极高｜≤-15% 高｜≤-5% 中
      R3 结构撕裂 ：κR<0 或 κI<0 → 簇内资金分歧
      R4 跑输     ：α20 < 0（剔掉大盘因子后仍未跑赢）
      R5 今日杀跌 ：今日 <-3%
    机会维度（累加）：
      O1 独立强势 ：IDIO 🟢 且 κI 上升（自身强、非指数带）
      O2 真超额   ：α20 >1% 且 今日超额 >0.3%
      O3 短期修复 ：mom5 >0 且 mom20 <0（正在拐）
      O4 抱团加强 ：κI>0 且较 5 日前上升
      O5 今日逆势 ：今日 >0 且超额 >0
    """
    def _f(x, d=float("nan")):
        try:
            v = float(x)
            return v if np.isfinite(v) else d
        except Exception:
            return d

    out = []
    for r in (res.get("rows") or []):
        tag = _group_tags(res).get(r["name"], "")
        if "B" not in tag.split("+"):
            continue
        b = _f(r.get("beta"), 1.0)
        m20, m5 = _f(r.get("mom20")), _f(r.get("mom5"))
        td, ex = _f(r.get("today")), _f(r.get("ex_today"))
        a20 = _f(r.get("alpha20"))
        kr, kl = _f(r.get("k_raw")), _f(r.get("k_idio"))

        # ── 风险
        risk, rl = [], 0
        if abs(b) >= 2.0 and b > 0:
            risk.append("β%.2f 杠杆式跟跌（大盘 -1%% → 它约 %.1f%%）" % (b, -b))
            rl += 3
        elif b >= 1.2:
            risk.append("β%.2f 高于大盘" % b)
            rl += 1
        if np.isfinite(m20):
            if m20 <= -0.25:
                risk.append("20日 %.1f%% 深度下跌" % (m20 * 100)); rl += 3
            elif m20 <= -0.15:
                risk.append("20日 %.1f%% 明显走弱" % (m20 * 100)); rl += 2
            elif m20 <= -0.05:
                risk.append("20日 %.1f%% 小幅回落" % (m20 * 100)); rl += 1
        tear = (np.isfinite(kr) and kr < 0) or (np.isfinite(kl) and kl < 0)
        if tear:
            risk.append("结构撕裂（κR %s / κI %s）→ 资金分歧" % (_k(kr), _k(kl))); rl += 2
        if np.isfinite(a20) and a20 < 0:
            risk.append("α20 %.1f%% 剔掉大盘后仍跑输" % (a20 * 100)); rl += 2
        if np.isfinite(td) and td < -0.03:
            risk.append("今日 %.1f%% 补跌" % (td * 100)); rl += 1
        risk_lv = "🔴 高" if rl >= 6 else ("🟠 中" if rl >= 3 else ("🟡 偏低" if rl >= 1 else "🟢 低"))

        # ── 机会
        opp, ol = [], 0
        if "🟢" in (r.get("label_idio") or "") and r.get("up_idio"):
            opp.append("IDIO 独立强势且 κI 上升（自身强，非指数带）"); ol += 3
        if np.isfinite(a20) and a20 > 0.01 and np.isfinite(ex) and ex > 0.003:
            opp.append("真超额：α20 %+.1f%%、今日超额 %+.1f%%" % (a20 * 100, ex * 100)); ol += 3
        if np.isfinite(m5) and np.isfinite(m20) and m5 > 0 and m20 < 0:
            opp.append("短期在修复：5日 %+.1f%% 转正、20日仍 %+.1f%%" % (m5 * 100, m20 * 100)); ol += 2
        # O4「κI 上升」2026-10-28 修：原先单独 +1 分 → 10 个行业全变「🟡弱机会」，
        # 毫无区分度（所有人都"有一点机会"= 等于没说）。改成只作附注，不计分。
        _knote = ("抱团在加强（κI %s↑）" % _k(kl)) if (r.get("up_idio")
                                                        and np.isfinite(kl) and kl > 0) else ""
        if np.isfinite(td) and td > 0 and np.isfinite(ex) and ex > 0:
            opp.append("今日逆势上涨 %+.1f%%、超额 %+.1f%%" % (td * 100, ex * 100)); ol += 1
        if _knote and (ol > 0):
            opp.append(_knote)
        opp_lv = "🟢 有" if ol >= 3 else ("🟡 弱" if ol >= 1 else "⚪ 无")

        verdict = ("🔴 减" if rl >= 6 and ol == 0 else
                   ("🟢 可跟" if ol >= 3 and rl < 6 else
                    ("🟡 观察" if ol >= 1 else "🟠 控仓")))
        out.append({"row": r, "name": r["name"], "pos": r.get("pos") or "",
                    "stocks": r.get("stocks") or "", "beta": b, "mom20": m20,
                    "risk_lv": risk_lv, "risk": risk, "opp_lv": opp_lv, "opp": opp,
                    "verdict": verdict, "rl": rl, "ol": ol})
    out.sort(key=lambda x: (-x["rl"], -x["ol"]))
    return out


def _safehaven_note(res):
    """「为什么银行涨 = 避险」的数据解释（2026-10-28 13:45 大佬要求写进报告）

    大佬原话：「就是这个银行上涨的原因是避险，是为什么是看避险？你得说明白了，在报告里。」
    不空口说，全部用表里能对上的数：
      ① β 为负 → 跟大盘反向，大盘越跌它越容易涨（避险资金主动买出来）
      ② 20 日动量正 → 全池 124 个里只有 12 个动量为正，银行占 4 个
      ③ κ 撕裂（κR<0）→ 增长板块在往外跑、银行在往里进，两边不同步
      ④ 非抱团共振（没有 A 档）→ 没有「跟指数一起涨」的，说明不是行情起来，是资金换阵地
    """
    rows = res.get("rows") or []
    sw = [r for r in rows if r.get("kind") == "sw"]
    banks = [r for r in sw if ("银行" in r["name"]) or ("农商" in r["name"])]
    if not banks:
        return []
    n_pos = sum(1 for r in sw if (r.get("mom20") or -9) > 0)
    bank_pos = [b for b in banks if (b.get("mom20") or -9) > 0]
    bank_neg_beta = [b for b in banks if (b.get("beta") or 1) < 0]
    bank_tear = [b for b in banks if (b.get("k_raw") is not None and
                                      np.isfinite(float(b.get("k_raw") or np.nan)) and
                                      float(b.get("k_raw")) < 0)]
    nA = len(((res.get("full_scan") or {}).get("A")) or [])
    _scope = "全池 %d 个" % len(sw) if res.get("full_scan") else "关注池 %d 个" % len(sw)
    L = ["🏦 为什么「银行涨」要读成避险，而不是读成进攻："]
    L.append("  ① **β 全为负**（%s）→ 与大盘反向：大盘跌得越多，它们被动买盘越强。"
             "这是避险资金的典型脚印，进攻型资金不会买负 β 品种。" % "、".join(
                 "%s %+.2f" % (b["name"], b["beta"]) for b in bank_neg_beta[:4]))
    L.append("  ② **%s里只有 %d 个 20 日动量为正，银行占 %d 个**（%s）→ "
             "不是行业景气回升，是资金在跌市里找少数还站得住的容器。" % (
                 _scope, n_pos, len(bank_pos), "、".join(
                     "%s %+.1f%%" % (b["name"], b["mom20"] * 100) for b in bank_pos[:4])))
    if bank_tear:
        L.append("  ③ **部分银行结构撕裂**（κR<0：%s）→ 银行内部也在分化：大行被买、中小行被卖。"
                 "真正的避险应该是整块同向抱团，出现撕裂说明买盘是「挑着买」，更像防守不是进攻。" % "、".join(
                     "%s %s" % (b["name"], _k(b["k_raw"])) for b in bank_tear[:4]))
    else:
        L.append("  ③ **银行内部结构也不同步**（见上方银行子行业 κ 分歧）：大行被买、中小行被卖，"
                 "买盘是「挑着买」而不是整块抱团 —— 更像防守不是进攻。")
    L.append("  ④ **A 档抱团共振 = %d 个** → %s没有一个行业「跟着指数一起涨」。"
             "行情是起来的吗？不是。是资金从成长板块**换了阵地**，不是新增资金入场。" % (
                 nA, "全池" if res.get("full_scan") else "关注池"))
    L.append("  → 读法：银行涨 = **跌市避险 + 存量资金换防**。"
             "这种上涨的特点是不带指数、不带量、涨得慢；一旦大盘企稳，避险资金会回流成长，"
             "银行反而容易滞涨。**它是防空洞，不是发动机。**")
    return L


def _group_tags(res):
    """标的 → 组别标签（A/B/C），2026-10-28 大佬要求去重显示

    大佬原话：「A 组 10 个，B 组 12 个，再加上我之前的那 10 个，他们仨可能有很多通用的，
    你要取共同的，不要为 A 分析一个，B 分析 12 个，这么着太多了。你只需要分析不同的就行了」
    → 重叠的（半导体/电池/游戏Ⅱ 同时在 A、B）只能出现一行，用 [A+B] 标归属。

    ⚠️ 不能依赖 res["groups"]：全池模式下 groups 只有「全池·申万二级」+「C·五大宽基」，
    A/B 归属会丢（标签全变 [?]）。直接按 MAIN10 / 实盘持仓 / WIDE 常量判，两种模式通用。
    """
    tag = {}
    for nm in MAIN10:
        tag.setdefault(nm, [])
        if "A" not in tag[nm]:
            tag[nm].append("A")
    try:
        for nm in _holdings_groups().keys():
            tag.setdefault(nm, [])
            if "B" not in tag[nm]:
                tag[nm].append("B")
    except Exception:
        pass
    for nm, _c in WIDE:
        tag.setdefault(nm, [])
        if "C" not in tag[nm]:
            tag[nm].append("C")
    return {nm: "+".join(sorted(v)) for nm, v in tag.items()}


def _merged_rows(res):
    """去重后的标的行，每个标的一条。

    排序（2026-10-28 13:25 大佬要求）：
      **持仓优先** —— 先说「我买了哪几个行业」，再说明星池、宽基。
      ① B 组实盘持仓（按仓位从大到小；同名跨组合权重合并，否则中芯国际会重复）
      ② A 组主流行业（按 20 日动量降序）
      ③ C 组宽基
      [A+B] 这种同时属持仓的，归入 ①（持仓优先）

    返回 [(row, tag_str)]；不属 A/B/C 的标的 tag=""（全池模式下不铺开渲染）。
    """
    tags = _group_tags(res)

    def _w(r):
        """该标的在两个实盘里的总仓位（用于持仓组内排序）"""
        tot = 0.0
        for part in (r.get("pos") or "").replace("，", " ").split():
            import re as _re
            m = _re.search(r"([\d.]+)%", part)
            if m:
                try:
                    tot += float(m.group(1))
                except Exception:
                    pass
        return tot

    by_name = {r["name"]: r for r in res["rows"]}
    seen, out = set(), []

    # ① 持仓优先：标签里含 B 的，按仓位降序
    hold = [(by_name[nm], t) for nm, t in tags.items()
            if "B" in t.split("+") and nm in by_name]
    hold.sort(key=lambda x: (-_w(x[0]), -(x[0].get("mom20") or -9)))
    for r, t in hold:
        seen.add(r["name"])
        out.append((r, t))

    # ② A 组（未被持仓覆盖的），按动量降序
    for key in ("A", "C"):
        batch = []
        for nm, t in tags.items():
            if key not in t.split("+"):
                continue
            if nm in seen or nm not in by_name:
                continue
            seen.add(nm)
            batch.append((by_name[nm], t))
        batch.sort(key=lambda x: -(x[0].get("mom20") or -9))
        out += batch

    for r in res["rows"]:
        if r["name"] not in seen:
            seen.add(r["name"])
            out.append((r, ""))
    return out


def render_lines(res, with_cri=True):
    L = []
    d8 = res.get("data_end") or res.get("date")
    # ── ⭐ 重点提醒（最顶部，2026-10-28 13:3x 统一用收紧后的 🎯 口径）
    fs = res.get("full_scan") or _full_scan(res.get("rows") or [])
    L.append("⭐ 重点提醒（有机会的标的）")
    if fs.get("A"):
        L.append(" ① 抱团共振（κ↑ + 20日动量↑ + 今日涨）：")
        for r in fs["A"][:8]:
            L.append("   🌙 %s%s ｜β%.2f ｜20日 %s ｜今日 %s ｜κR %s↑" % (
                r["name"], ("(%s)" % r["pos"]) if r["pos"] else "",
                r["beta"], _p(r["mom20"]), _p(r["today"]), _k(r["k_raw"])))
    else:
        L.append(" ① 抱团共振：无（今天没有任何行业跟着指数一起涨）")
    if fs.get("B"):
        L.append(" ② 独立强势（α20>1% 且 今日超额>0.3%）：")
        for r in fs["B"][:8]:
            L.append("   🌙 %s%s(%s) ｜α20 %s ｜今日超额 %s ｜κI %s↑" % (
                r["name"], ("(%s)" % r["pos"]) if r["pos"] else "",
                r.get("code", ""), _p(r["alpha20"]), _p(r["ex_today"]), _k(r["k_idio"])))
    else:
        L.append(" ② 独立强势：无")
    if fs.get("C"):
        L.append(" ③ 撕裂反转候选（κ<0 但动量↑、今日涨）：")
        for r in fs["C"][:8]:
            L.append("   ⚡ %s%s(%s) ｜20日 %s ｜今日 %s ｜κR %s ｜κI %s" % (
                r["name"], ("(%s)" % r["pos"]) if r["pos"] else "",
                r.get("code", ""), _p(r["mom20"]), _p(r["today"]),
                _k(r["k_raw"]), _k(r["k_idio"])))
    if not (fs.get("A") or fs.get("B") or fs.get("C")):
        L.append(" 今日%s无机会标的" % ("全池" if res.get("full_scan") else "关注池"))
    # 统计口径（不重复列明细）
    if fs:
        st = fs.get("stat") or {}
        L.append(" 🎯 %s：有效 %d 个｜A %d / B %d / C %d｜20日动量>0 %d 个｜撕裂 %d 个" % (
            "全池扫描" if res.get("full_scan") else "关注池扫描",
            st.get("n_sw", 0), st.get("nA", 0), st.get("nB", 0), st.get("nC", 0),
            st.get("mom_up", 0), st.get("tear", 0)))
    L.append("")

    # ── 2026-10-28 13:45 大佬：「我这两个实盘所属的行业，风险和机会你也都说一下子，也放在前面」
    hv = _hold_views(res)
    if hv:
        L.append("💼 我买的行业 · 风险与机会（拓扑口径，非缠论）")
        for x in hv:
            L.append(" %s %s%s ｜风险 %s ｜机会 %s" % (
                x["verdict"], x["name"], ("(%s)" % x["pos"]) if x["pos"] else "",
                x["risk_lv"], x["opp_lv"]))
            if x["risk"]:
                L.append("    ⚠ 风险：%s" % "；".join(x["risk"]))
            if x["opp"]:
                L.append("    ✨ 机会：%s" % "；".join(x["opp"]))
            if not x["opp"]:
                L.append("    ✨ 机会：无（双口径都没给出独立强势或真超额）")
        L.append("")

    # ── 银行避险解释
    sh = _safehaven_note(res)
    if sh:
        L += sh
        L.append("")

    # ── 一、宏观风控
    if with_cri and res.get("cri"):
        c = res["cri"]
        L.append("一、宏观风控")
        L.append("1. 🚦 CRI = %.0f（状态：%s）" % (c["CRI"], c["state"]))
        L.append("2. 📐 层级拆解：记忆层 %.1f / 扰动层 %.1f / 分布层 %.1f" % (
            c["L1"], c["L2"], c["L3"]))
        L.append("   （残差动量 %+.4f（/σ=%+.2f，分位%.0f）acf₁=%+.3f vol%.1f%%分位%.0f "
                 "熵%.2f(分位%.0f) KL=%.3f/P90=%.3f；%s）" % (
                     c.get("resid_mom", float("nan")), c.get("resid_ratio", float("nan")),
                     c.get("resid_pct", float("nan")),
                     c["acf1"], c["vol"] * 100, c["vol_pct"], c["ent"], c["ent_rank"],
                     c["kl"], c["kl_p90"], _hmm_txt(c)))
        def _yn(b):
            return "✅触发" if b else "❌未触发"
        L.append("   一级·记忆层（趋势Edge消失：残差动量/σ<0 且 acf₁≤0）"
                 "【口径：指数对自身 20 日均线的残差，已替代 DFA/Hurst】：%s" % _yn(c["trig1"]))
        L.append("   二级·扰动层（噪音过载：vol分位>80 或 熵>P90）：%s" % _yn(c["trig2"]))
        L.append("   三级·分布层（Regime切换：KL>μ+2σ 或 HMM高波动后验>0.5，μ=%.3f σ=%.3f 阈值%.3f）：%s" % (
            c["kl_mu"], c["kl_sd"], c["kl_mu"] + 2 * c["kl_sd"], _yn(c["trig3"])))
        L.append("3. 💰 仓位指令：%s ｜ 沪深300 基准 %.0f" % (c["pos"], c["close"]))
        L.append("")

    # ── 📈 行业趋势 Hurst（只算有趋势的行业，大佬 2026-11-07 18:56 要求）
    _hz = res.get("hurst")
    if _hz and _hz.get("n_all"):
        _up = [o for o in _hz["trend"] if o.get("trend_dir") == "up"]
        _dn = [o for o in _hz["trend"] if o.get("trend_dir") == "down"]
        L.append("")
        L.append("📈 行业趋势 Hurst（%d 个行业各自独立算 DFA，只汇总有趋势的）"
                 % _hz["n_all"])
        L.append("   口径：H 用 120 日 + 60 日双窗口（取高者），>%.2f 且 |20日动量|>%.0f%%"
                 " 且 |残差动量/σ|>%.1f → 有趋势" % (0.55, 2.0, 0.5))
        L.append("   📈有趋势 %d 个（上升 %d ｜下降 %d）｜⚡强动量非平滑 %d ｜⚪无趋势 %d"
                 % (_hz["n_trend"], len(_up), len(_dn),
                    _hz.get("n_jump", 0), len(_hz["flat"])))
        if _up:
            L.append("   【📈 上升趋势 · 可按趋势做】H 均值 %.3f"
                     % (np.mean([o["H_use"] for o in _up]) if _up else 0))
            for o in _up:
                L.append("     📈 %-12s H %.3f/H60 %.3f ｜20日 %+.1f%% ｜残差 %+.2f"
                         % (o["name"], o["H"], o.get("H60") or 0,
                            (o.get("mom20") or 0) * 100,
                            o.get("resid_ratio") or 0))
        if _dn:
            L.append("   【📉 下降趋势 · 规避】%d 个：%s" % (
                len(_dn), "、".join(o["name"] for o in _dn[:12])))
            if len(_dn) > 12:
                L.append("     …（其余 %d 个略）" % (len(_dn) - 12))
        if _hz.get("jump"):
            L.append("   【⚡ 强动量但非平滑（V型/锯齿，Hurst 天生低）】")
            for o in _hz["jump"][:8]:
                L.append("     ⚡ %-12s H %.3f/H60 %.3f ｜20日 %+.1f%% ｜残差 %+.2f"
                         % (o["name"], o["H"], o.get("H60") or 0,
                            (o.get("mom20") or 0) * 100,
                            o.get("resid_ratio") or 0))

    # ── 二、行业结构诊断（双口径）——2026-10-28 合并成一张表，重叠标的只出现一次
    L.append("")
    L.append("二、行业结构诊断（双口径：RAW=原始相关 / IDIO=去市场+去噪）")
    L.append("窗口：相关矩阵 %d日（T>N）｜β %d日｜动量 %d日｜κ对比 t-5" % (
        CORR_WIN, BETA_WIN, MOM_WIN))
    if res.get("corr_mode") == "pairwise":
        L.append("全池模式：相关矩阵 %d日 pairwise（各标的按自身可用样本，不做全交集）" % CORR_WIN)
    _tg = _group_tags(res)
    _dup = [k for k, v in _tg.items() if "+" in v]
    L.append("标的池：并集 %d 个%s" % (
        len(_tg),
        "（重叠 %d 个：%s，已去重只算一次）" % (len(_dup), "、".join(_dup)) if _dup else ""))
    L.append("组别：[B]=实盘持仓（排最前，按仓位） ｜[A]=主流行业 ｜[C]=宽基指数")
    _mr = _merged_rows(res)
    # 全池模式：124 行明细不铺开，只列 A/B/C 组里的（原 23 标的池）
    if res.get("full_pool"):
        _mr = [(r, t) for (r, t) in _mr if t and t != "?"]
    # 2026-10-28 13:2x 大佬：「去重后要放前面，要不然我不知道哪几个行业是我买的」
    #   → 分成两块：① 我买的（标签含 B）摆最前 ② 其余关注标的
    _hold = [(r, t) for (r, t) in _mr if "B" in (t or "").split("+")]
    _rest = [(r, t) for (r, t) in _mr if "B" not in (t or "").split("+")]

    def _line(r, tag):
        return " %s [%s]%s ｜20日 %s ｜今日 %s ｜β%.2f ｜κR %s %s ｜κI %s %s" % (
            r["name"], tag, ("(%s)" % r["pos"]) if r["pos"] else "",
            _p(r["mom20"]), _p(r["today"]), r["beta"],
            _k(r["k_raw"]), r["label_raw"], _k(r["k_idio"]), r["label_idio"])

    if _hold:
        L.append("【① 我买的 %d 个行业（按仓位）】" % len(_hold))
        for r, tag in _hold:
            L.append(_line(r, tag))
            if r["stocks"]:
                L.append("    持股：%s" % r["stocks"])
    if _rest:
        L.append("【② 其他关注 %d 个】" % len(_rest))
        for r, tag in _rest:
            L.append(_line(r, tag))
            if r["stocks"]:
                L.append("    持股：%s" % r["stocks"])
    L.append("")

    # ── 三、综合结论
    c = res.get("cri") or {}
    # 全池模式：结论里的计数只算申万二级（宽基另有 C 组块与 📐 指数判定），
    # 否则会与 🎯 全池扫描的统计（124 个）对不上（61 vs 64 那种）
    if res.get("full_pool"):
        _cnt = [r for r in res["rows"] if r.get("kind") == "sw"]
        _tail = "（申万二级）"
    else:
        _cnt, _tail = res["rows"], ""
    n_up_raw = sum(1 for r in _cnt if r["up_raw"])
    n_up_idio = sum(1 for r in _cnt if r["up_idio"])
    n_risk = sum(1 for r in _cnt if "撕裂" in r["label_raw"] or "撕裂" in r["label_idio"])
    n_opp = len(res["key_alerts"]["follow"]) + len(res["key_alerts"]["indep"])
    L.append("三、综合结论")
    CRIv = c.get("CRI", float("nan"))
    tag = ""
    if np.isfinite(CRIv) and CRIv > 40 and n_opp > 0:
        tag = " → 防御性抱团"
    if np.isfinite(CRIv) and CRIv < 20 and n_up_idio >= 3:
        tag = " → 共振做多窗口"
    L.append("· CRI=%.0f（%s）｜RAW 口径抱团上升 %d 个%s ｜IDIO 口径独立走强 %d 个｜撕裂 %d 个%s" % (
        CRIv if np.isfinite(CRIv) else -1, c.get("state", "—"), n_up_raw, _tail,
        n_up_idio, n_risk, tag))
    only_raw = [r["name"] for r in res["rows"] if r["kind"] == "sw"
                and "🟢" in r["label_raw"] and "🟢" not in r["label_idio"]]
    only_idio = [r["name"] for r in res["rows"] if r["kind"] == "sw"
                 and "🟢" in r["label_idio"] and "🟢" not in r["label_raw"]]
    if only_raw:
        L.append("· 只靠指数带涨（RAW绿/IDIO不绿）：%s" % "、".join(only_raw[:8]))
    if only_idio:
        L.append("· 真正自身强（IDIO绿/RAW不绿）：%s" % "、".join(only_idio[:8]))
    if res.get("full_pool"):
        L.append("· 全池口径：申万二级 %d 个有效标的全过一遍（剔除 %d 个无数据/陈旧），"
                 "A/B/C 三档见上方 🎯 块" % (
                     (res.get("full_scan") or {}).get("stat", {}).get("n_sw", 0),
                     len(res.get("full_dropped") or [])))
    L.append("· 说明：RAW 看「跟指数一起涨」的联动；IDIO 剔掉市场因子后看谁真正独立强。"
             "两口径都绿才是「既跟涨又自身强」。")
    L.append("")
    L.append("CRI|%.0f|%s|%d|%d" % (CRIv if np.isfinite(CRIv) else -1,
                                     c.get("pos", "—"), n_opp, n_risk))
    L.append("数据截止：%s" % d8)
    return L


def render_blocks(res, with_cri=True):
    """飞书卡片 blocks（精简版，控制体积）"""
    B = []
    m = []
    # 2026-10-28 13:3x：重点提醒统一用**收紧后**的 🎯 口径（与下方全池扫描同源），
    # 不再用 key_alerts 的宽口径（会把超额 +0.0%/+0.1% 的噪声放最顶部）
    fs = res.get("full_scan") or _full_scan(res.get("rows") or [])
    m.append("**⭐ 重点提醒**")
    # 2026-10-28 14:54 大佬：「你把你选的这几个放在前面，然后也放在重点提醒里边」
    dp = _deep_picks()
    if dp and dp.get("rows"):
        m.append("**🎯 缠论买点个股**（%d 只）" % len(dp["rows"]))
        if dp.get("A"):
            m.append("<font color='grey'>　口径A 超额动量：%s</font>" % "、".join(dp["A"]))
        if dp.get("B"):
            m.append("<font color='grey'>　口径B 拓扑聚集：%s</font>" % "、".join(dp["B"]))
        for it in dp["rows"][:14]:
            m.append("· **%s** ｜%s · %s ｜%s" % (
                it["name"], it["ind"], it["tag"], it["chan"] or "—"))
        m.append("")
    elif dp is None:
        m.append("<font color='grey'>🎯 缠论买点个股：未生成（跑 bin/holding_deep.py）</font>")
        m.append("")
    m.append("**行业机会（按口径）**")
    if fs.get("A"):
        m.append("**① 抱团共振**（κ↑ + 20日动量↑ + 今日涨）")
        for r in fs["A"][:6]:
            m.append("· 🌙**%s**%s ｜β%.2f｜20日 %s｜今日 %s｜κR %s↑" % (
                r["name"], (" %s" % r["pos"]) if r["pos"] else "", r["beta"],
                _p(r["mom20"]), _p(r["today"]), _k(r["k_raw"])))
    else:
        m.append("**① 抱团共振**：无（今天没有任何行业跟着指数一起涨）")
    if fs.get("B"):
        m.append("**② 独立强势**（α20>1% 且 今日超额>0.3%）")
        for r in fs["B"][:6]:
            m.append("· 🌙**%s**%s ｜α20 %s｜超额 %s｜κI %s↑" % (
                r["name"], (" %s" % r["pos"]) if r["pos"] else "",
                _p(r["alpha20"]), _p(r["ex_today"]), _k(r["k_idio"])))
    else:
        m.append("**② 独立强势**：无")
    if fs.get("C"):
        m.append("**③ 撕裂反转候选**（κ<0 但动量↑、今日涨）")
        for r in fs["C"][:6]:
            m.append("· ⚡**%s**%s ｜20日 %s｜今日 %s｜κR %s" % (
                r["name"], (" %s" % r["pos"]) if r["pos"] else "",
                _p(r["mom20"]), _p(r["today"]), _k(r["k_raw"])))
    if not (fs.get("A") or fs.get("B") or fs.get("C")):
        m.append("今日%s无机会标的" % ("全池" if res.get("full_scan") else "关注池"))
    # 2026-10-28 大佬：重点提醒要带对指数的看法
    m.append("")
    iv = index_views(res)
    if iv:
        m.append("**📐 指数结构判定**（聚集=抱团同向 / 撕裂=资金分歧）")
        for x in iv:
            nm = "**%s**%s" % (x["name"], "(基准)" if x["is_bench"] else "")
            m.append("· %s %s %s %s ｜20日 %s ｜今日 %s ｜κR %s / κI %s%s" % (
                nm, x["bull"], x["st"], x["bnot"], _p(x["mom20"]), _p(x["today"]),
                _k(x["k_raw"]), _k(x["k_idio"]),
                ("  ⚠ %s" % x["note"]) if x["note"] else ""))
        s = index_summary(res)
        if s:
            m.append("▸ %s" % s)
    # ── 2026-10-28 13:45 持仓行业风险与机会（进飞书卡片最前）
    hv = _hold_views(res)
    if hv:
        m.append("")
        m.append("**💼 我买的行业 · 风险与机会**（拓扑口径，非缠论）")
        for x in hv:
            m.append("**%s %s**%s ｜风险 %s ｜机会 %s" % (
                x["verdict"], x["name"], (" %s" % x["pos"]) if x["pos"] else "",
                x["risk_lv"], x["opp_lv"]))
            if x["risk"]:
                m.append("<font color='grey'>　⚠ %s</font>" % "；".join(x["risk"]))
            if x["opp"]:
                m.append("<font color='grey'>　✨ %s</font>" % "；".join(x["opp"]))
            else:
                m.append("<font color='grey'>　✨ 机会：无</font>")
    # ── 银行避险解释
    sh = _safehaven_note(res)
    if sh:
        m.append("")
        for ln in sh:
            m.append(ln.replace("**", "**"))
    B.append({"type": "md", "text": "\n".join(m)})
    B.append({"type": "hr"})

    # ── 🎯 全池扫描：明细已并入顶部「重点提醒」，此处只留口径与统计（避免同一批股票列两遍）
    if res.get("full_scan"):
        st = (res["full_scan"] or {}).get("stat") or {}
        f = ["**🎯 全池扫描 · 统计**（申万二级 %d 个有效标的）" % st.get("n_sw", 0),
             "<font color='grey'>口径：pairwise 相关 %d 日；B档门槛 α20>%.0f%% 且 超额>%.1f%%"
             "（2026-10-28 收紧，滤掉刚过零轴的噪声）</font>" % (
                 CORR_WIN, st.get("b_min_alpha20", 0.01) * 100, st.get("b_min_ex", 0.003) * 100),
             "· A 抱团共振 **%d** ｜ B 独立强势 **%d** ｜ C 撕裂反转 **%d**" % (
                 st.get("nA", 0), st.get("nB", 0), st.get("nC", 0)),
             "· 全池 有效 %d ｜ 抱团上升 %d ｜ 独立走强 %d ｜ 撕裂 %d ｜ 20日动量>0 %d" % (
                 st.get("n_sw", 0), st.get("raw_up", 0), st.get("idio_up", 0),
                 st.get("tear", 0), st.get("mom_up", 0))]
        B.append({"type": "md", "text": "\n".join(f)})
        B.append({"type": "hr"})

    # ── 二、行业结构（2026-10-28 合并成一张表，重叠标的只出现一次）
    _tg = _group_tags(res)
    _dup = [k for k, v in _tg.items() if "+" in v]
    _mr = _merged_rows(res)
    # 2026-10-28 13:2x：**全池模式也要显示「我买的」**——原来 len>20 整块跳过，
    # 结果大佬在全池报告里看不到自己持仓行业（已修）
    _mr = [(r, t) for (r, t) in _mr if t and t != "?"]     # 全池只留 A/B/C 组
    _hold = [(r, t) for (r, t) in _mr if "B" in (t or "").split("+")]
    _rest = [(r, t) for (r, t) in _mr if "B" not in (t or "").split("+")]
    if True:
        t = ["**二、行业结构**（并集 %d 个%s）" % (
            len(_tg), "｜重叠已去重：%s" % "、".join(_dup) if _dup else "")]
        t.append("<font color='grey'>[B]=实盘持仓（排最前，按仓位）｜[A]=主流行业｜[C]=宽基</font>")
        if _hold:
            t.append("**① 我买的 %d 个行业（按仓位）**" % len(_hold))
            for r, tag in _hold:
                t.append("· **%s** [%s]%s ｜20日 %s ｜今日 %s ｜β%.2f ｜κR %s %s ｜κI %s %s" % (
                    r["name"], tag, (" %s" % r["pos"]) if r["pos"] else "",
                    _p(r["mom20"]), _p(r["today"]), r["beta"],
                    _k(r["k_raw"]), r["label_raw"], _k(r["k_idio"]), r["label_idio"]))
        if _rest:
            t.append("**② 其他关注 %d 个**" % len(_rest))
            for r, tag in _rest:
                t.append("· **%s** [%s]%s ｜20日 %s ｜今日 %s ｜β%.2f ｜κR %s %s ｜κI %s %s" % (
                    r["name"], tag, (" %s" % r["pos"]) if r["pos"] else "",
                    _p(r["mom20"]), _p(r["today"]), r["beta"],
                    _k(r["k_raw"]), r["label_raw"], _k(r["k_idio"]), r["label_idio"]))
        B.append({"type": "md", "text": "\n".join(t)})
        B.append({"type": "hr"})
    return B


def index_views(res):
    """五大宽基的结构判定 + 多空标识（2026-10-28 大佬：重点提醒要带对指数的看法）

    结构判定（按优先级）：
      撕裂   = 任一口径 κ<0（资金分歧，簇内不再同向）
      聚集↑  = κ>0 且较 5 日前上升（抱团在加强）
      聚集   = κ>0 但未上升（抱团存在但在肘）
      孤立   = 两个口径都自成一簇（没参与任何抱团）
      中性   = 其余
    多空标识（按 MEMORY 规范：看空⭐ / 看多🌙 / 平衡不打）：
      mom20 ≤-10% → ⭐⭐⭐ 强看空｜≤-3% → ⭐⭐ 看空｜<0 → ⭐ 偏空
      mom20 ≥+10% → 🌙🌙🌙 强看多｜≥+3% → 🌙🌙 看多｜>0 → 🌙 偏多
      另：撕裂 + 看多 → 标「结构不配合」；聚集↑ + 看空 → 标「跌时聚」
    """
    out = []
    for r in res.get("rows") or []:
        if r.get("kind") != "wide":
            continue
        kr, kl = r.get("k_raw"), r.get("k_idio")
        kr = float(kr) if kr is not None else float("nan")
        kl = float(kl) if kl is not None else float("nan")
        iso_r = "孤立" in (r.get("label_raw") or "")
        iso_i = "孤立" in (r.get("label_idio") or "")
        tear = (np.isfinite(kr) and kr < 0) or (np.isfinite(kl) and kl < 0)
        if tear:
            st = "🔴撕裂"
        elif r.get("up_raw") or r.get("up_idio"):
            st = "🟢聚集↑"
        elif np.isfinite(kr) and kr > 0:
            st = "🟡聚集(未升)"
        elif iso_r and iso_i:
            st = "⚪孤立"
        else:
            st = "⚪中性"
        m = float(r.get("mom20") or 0)
        if m <= -0.10:
            bull, btxt = "⭐⭐⭐", "强看空"
        elif m <= -0.03:
            bull, btxt = "⭐⭐", "看空"
        elif m < 0:
            bull, btxt = "⭐", "偏空"
        elif m >= 0.10:
            bull, btxt = "🌙🌙🌙", "强看多"
        elif m >= 0.03:
            bull, btxt = "🌙🌙", "看多"
        elif m > 0:
            bull, btxt = "🌙", "偏多"
        else:
            bull, btxt = "", "平衡"
        note = ""
        if "撕裂" in st and bull.startswith("🌙"):
            note = "结构不配合"
        elif "聚集" in st and bull.startswith("⭐"):
            note = "跌时聚"
        out.append({"name": r["name"], "st": st, "bull": bull, "bnot": btxt,
                    "note": note, "mom20": m, "today": float(r.get("today") or 0),
                    "k_raw": kr, "k_idio": kl, "beta": float(r.get("beta") or 0),
                    "is_bench": r["name"] == MKT_NAME})
    return out


def index_summary(res):
    """指数结构一句话总结"""
    iv = index_views(res)
    if not iv:
        return ""
    n_tear = sum(1 for x in iv if "撕裂" in x["st"])
    n_clu = sum(1 for x in iv if "聚集" in x["st"])
    n_iso = sum(1 for x in iv if "孤立" in x["st"])
    n_bear = sum(1 for x in iv if x["bull"].startswith("⭐"))
    n_bull = sum(1 for x in iv if x["bull"].startswith("🌙"))
    side = "全线看空" if n_bear == len(iv) else (
        "全线看多" if n_bull == len(iv) else "多空分化")
    tail = ""
    if n_clu >= 3 and n_bear >= 3:
        tail = " → 下跌中的抱团（跌时聚、涨不聚）"
    elif n_tear >= 3:
        tail = " → 结构碎裂（无统一共识）"
    return "结构：聚集 %d / 撕裂 %d / 孤立 %d ｜ %s%s" % (n_clu, n_tear, n_iso, side, tail)


def _idx_lines(res, compact=False):
    """指数看法几行（重点提醒专用）"""
    iv = index_views(res)
    if not iv:
        return []
    L = ["📐 指数结构判定（聚集=抱团同向 / 撕裂=资金分歧）："]
    for x in iv:
        nm = x["name"] + ("(基准)" if x["is_bench"] else "")
        if compact:
            L.append("  %s %s %s %s ｜20日 %s" % (
                nm, x["bull"], x["st"], x["bnot"], _p(x["mom20"])))
        else:
            L.append("  %s %s %s %s ｜20日 %s ｜今日 %s ｜β%.2f ｜κR %s / κI %s%s" % (
                nm, x["bull"], x["st"], x["bnot"], _p(x["mom20"]), _p(x["today"]),
                x["beta"], _k(x["k_raw"]), _k(x["k_idio"]),
                ("  ⚠%s" % x["note"]) if x["note"] else ""))
    s = index_summary(res)
    if s:
        L.append("  ▸ %s" % s)
    return L


def _deep_picks():
    """读 holding_deep 缓存的「缠论买点个股」（2026-10-28 14:54 大佬要求放进重点提醒）

    大佬原话：「你把你选的这几个放在前面，然后也放在重点提醒里边」
    由 `bin/holding_deep.py` 跑完写 `.cache/holding_deep/picks_<date>.json`，
    这里只读缓存（30 分钟 TTL），**不重复跑 200 只个股的缠论**（那个要 30 秒）。
    缓存不存在就返回 None，报告中注明「未生成」。
    """
    try:
        import glob
        d = os.environ.get("HD_CACHE_DIR") or os.path.join(WS, ".cache", "holding_deep")
        fs = sorted(glob.glob(os.path.join(d, "picks_*.json")))
        if not fs:
            return None
        f = fs[-1]
        if time.time() - os.path.getmtime(f) > 3600:
            return None
        j = json.load(open(f, encoding="utf-8"))
        return j if j.get("rows") is not None else None
    except Exception:
        return None


def render_brief(res, with_cri=True):
    """QQ 单发版「重点提醒」（2026-10-28 13:45 大佬要求）

    大佬原话：「这个重点提醒，就我所属的行业的那个风险和机会，你放在重点提醒里边，
    重点提醒以后单发 QQ。然后详细信息就发的那个飞书报告」

    → 本函数只产出 **QQ 单发的那一条**：
      ① 💼 我买的行业 · 风险与机会（最优先，一眼看哪个该减、哪个能拿）
      ② 🏦 银行避险解释（为什么银行涨 = 避险）
      ③ 📐 指数结构判定
      ④ 🎯 全池机会统计（一句话）
    飞书详细报告走 render_lines / render_blocks。
    """
    L = []
    L.append("⭐ 重点提醒 · 缠论买点个股 + 持仓风险机会")
    # ① 缠论买点个股（2026-10-28 14:54 大佬：放最前、也放进重点提醒）
    dp = _deep_picks()
    if dp and dp.get("rows"):
        L.append("")
        L.append("🎯 缠论买点个股（%d 只）" % len(dp["rows"]))
        if dp.get("A"):
            L.append("　口径A 超额动量：%s" % "、".join(dp["A"]))
        if dp.get("B"):
            L.append("　口径B 拓扑聚集：%s" % "、".join(dp["B"]))
        for it in dp["rows"][:14]:
            L.append("· %s ｜%s · %s ｜%s" % (
                it["name"], it["ind"], it["tag"], it["chan"] or "—"))
    elif dp is not None:
        L.append("")
        L.append("🎯 缠论买点个股：今日无符合的")
    else:
        L.append("")
        L.append("🎯 缠论买点个股：未生成（跑 bin/holding_deep.py）")
    L.append("")
    L.append("——— 我买的行业 风险与机会 ———")

    # ① 持仓行业（最优先）
    hv = _hold_views(res)
    if hv:
        n_risk = sum(1 for x in hv if x["rl"] >= 6)
        n_ok = sum(1 for x in hv if x["verdict"].startswith("🟢"))
        L.append("（%d 个持仓行业：高风险 %d 个 ｜ 可跟 %d 个）" % (len(hv), n_risk, n_ok))
        for x in hv:
            L.append("")
            L.append("%s %s%s ｜风险 %s ｜机会 %s" % (
                x["verdict"], x["name"], ("(%s)" % x["pos"]) if x["pos"] else "",
                x["risk_lv"], x["opp_lv"]))
            if x["risk"]:
                L.append("  ⚠ %s" % "；".join(x["risk"]))
            if x["opp"]:
                L.append("  ✨ %s" % "；".join(x["opp"]))
            else:
                L.append("  ✨ 机会：无（双口径都没给出独立强势或真超额）")
    else:
        L.append("（未识别到持仓行业）")

    # ② 银行避险解释
    sh = _safehaven_note(res)
    if sh:
        L.append("")
        L += sh

    # ③ 指数结构
    L.append("")
    L += _idx_lines(res, compact=True)

    # ④ 全池机会统计
    fs = res.get("full_scan") or {}
    st = fs.get("stat") or {}
    L.append("")
    if st:
        L.append("🎯 全池 %d 个：A 抱团共振 %d ｜B 独立强势 %d ｜C 撕裂反转 %d ｜20日动量>0 %d 个" % (
            st.get("n_sw", 0), st.get("nA", 0), st.get("nB", 0), st.get("nC", 0),
            st.get("mom_up", 0)))
        oth = [(fs.get(k) or []) for k in ("A", "B", "C")]
        names = []
        for arr in oth:
            for r in arr:
                if r["name"] not in names:
                    names.append(r["name"])
        if names:
            L.append("　非持仓的机会：%s" % "、".join(names[:10]))
        else:
            L.append("　非持仓的机会：无")

    # ⑤ 行业趋势 Hurst（只列上升趋势 + 强动量非平滑）
    #   大佬 2026-11-07 18:56：每行业独立算，只汇总有趋势的
    _hz = res.get("hurst")
    if _hz and _hz.get("n_all"):
        _up = [o for o in _hz["trend"] if o.get("trend_dir") == "up"]
        _dn = [o for o in _hz["trend"] if o.get("trend_dir") == "down"]
        L.append("")
        L.append("📈 行业趋势 Hurst（%d 个行业各自算 DFA，只汇总有趋势的）"
                 % _hz["n_all"])
        L.append("　📈有趋势 %d（上升 %d ｜下降 %d）｜⚡强动量非平滑 %d ｜⚪无趋势 %d" % (
            _hz["n_trend"], len(_up), len(_dn),
            _hz.get("n_jump", 0), len(_hz["flat"])))
        if _up:
            L.append("　【📈 上升趋势 · 可按趋势做】")
            for o in _up:
                L.append("　　📈 %s ｜H %.3f/H60 %.3f ｜20日 %+.1f%% ｜残差 %+.2f" % (
                    o["name"], o["H"], o.get("H60") or 0,
                    (o.get("mom20") or 0) * 100, o.get("resid_ratio") or 0))
        if _hz.get("jump"):
            L.append("　【⚡ 强动量但非平滑（V型，Hurst天生低）】")
            for o in _hz["jump"][:5]:
                L.append("　　⚡ %s ｜H %.3f ｜20日 %+.1f%% ｜残差 %+.2f" % (
                    o["name"], o["H"], (o.get("mom20") or 0) * 100,
                    o.get("resid_ratio") or 0))
        if _dn:
            L.append("　【📉 下降趋势 · 规避】%d 个：%s%s" % (
                len(_dn), "、".join(o["name"] for o in _dn[:8]),
                "…" if len(_dn) > 8 else ""))

    # ⑥ CRI 一句话
    if with_cri and res.get("cri"):
        c = res["cri"]
        L.append("")
        L.append("🚦 CRI = %.0f（%s）→ %s ｜%s" % (
            c["CRI"], c["state"], c["pos"], _hmm_txt(c)))
    return L


def build_block(groups=None, date=None, with_cri=True, full_pool=False):
    """对外唯一入口：(text_lines, card_blocks)。永不抛异常。"""
    try:
        res = analyze(groups=groups, date=date, with_cri=with_cri, full_pool=full_pool)
        if not res.get("ok"):
            return ([res.get("err") or "（拓扑计算失败，跳过）"], [])
        return (render_lines(res, with_cri=with_cri),
                render_blocks(res, with_cri=with_cri))
    except Exception as e:
        log("[cri_topology] build_block 失败 %s" % str(e)[:160])
        return ([], [])


# ══════════════════════════════════════════ 单口径卡片（飞书，标颜色）
_LBL_COLOR = {"🟢": "green", "🔴": "red", "🟡": "orange", "⚪": "grey"}


def _lab(txt):
    """给标签上色（飞书卡片 markdown 支持 <font color=...>）"""
    c = _LBL_COLOR.get(txt[:1], "grey")
    return "<font color='%s'>%s</font>" % (c, txt)


def _num(x, nd=1):
    """涨跌数字（A股习惯：红涨绿跌）"""
    try:
        v = float(x) * 100
    except Exception:
        return "—"
    if not np.isfinite(v):
        return "—"
    c = "red" if v > 0 else ("green" if v < 0 else "grey")
    return "<font color='%s'>%+.1f%%</font>" % (c, v)


def render_one_side(res, side, with_cri=True):
    """side='raw' → 原始口径；side='idio' → 残差口径。返回 (title_suffix, text)"""
    rows = res["rows"]
    ka = res["key_alerts"]
    d10 = res.get("data_end") or res.get("date")
    L = []

    # ── ⭐ 重点提醒
    if side == "raw":
        title = "原始口径 RAW（不去市场因子·不去噪）"
        pick = [r for r in rows if "🟢" in r["label_raw"] and r["mom20"] > 0 and r["today"] > 0]
        L.append("**⭐ 重点提醒**：抱团 + 20日动量↑ + 今日涨")
        if pick:
            for r in sorted(pick, key=lambda x: -x["mom20"]):
                L.append("· 🌙 **%s**%s ｜β%.2f ｜20日 %s ｜今日 %s ｜κ %s" % (
                    r["name"], (" %s" % r["pos"]) if r["pos"] else "", r["beta"],
                    _num(r["mom20"]), _num(r["today"]), _k(r["k_raw"])))
        else:
            L.append("· 今日无三项共振的行业")
        # 2026-10-28：重点提醒带对指数的看法
        L.append("")
        L.append("**📐 指数结构判定**（聚集/撕裂/多空）")
        for x in index_views(res):
            L.append("· **%s**%s %s %s %s ｜20日 %s ｜κR %s / κI %s" % (
                x["name"], "(基准)" if x["is_bench"] else "", x["bull"], x["st"],
                x["bnot"], _num(x["mom20"]), _k(x["k_raw"]), _k(x["k_idio"])))
        _s = index_summary(res)
        if _s:
            L.append("▸ %s" % _s)
    else:
        title = "残差口径 IDIO（去市场因子+去噪）"
        pick = [r for r in rows if "🟢" in r["label_idio"] and r["alpha20"] > 0 and r["ex_today"] > 0]
        L.append("**⭐ 重点提醒**：抱团 + 残差α20↑ + 今日超额↑")
        if pick:
            for r in sorted(pick, key=lambda x: -x["alpha20"]):
                L.append("· 🌙 **%s**%s ｜α20 %s ｜超额 %s ｜κ %s" % (
                    r["name"], (" %s" % r["pos"]) if r["pos"] else "",
                    _num(r["alpha20"]), _num(r["ex_today"]), _k(r["k_idio"])))
        else:
            L.append("· 今日无三项共振的行业")
        # 2026-10-28：重点提醒带对指数的看法
        L.append("")
        L.append("**📐 指数结构判定**（聚集/撕裂/多空）")
        for x in index_views(res):
            L.append("· **%s**%s %s %s %s ｜20日 %s ｜κR %s / κI %s" % (
                x["name"], "(基准)" if x["is_bench"] else "", x["bull"], x["st"],
                x["bnot"], _num(x["mom20"]), _k(x["k_raw"]), _k(x["k_idio"])))
        _s = index_summary(res)
        if _s:
            L.append("▸ %s" % _s)
    L.append("")

    # ── 宏观 CRI
    if with_cri and res.get("cri"):
        c = res["cri"]
        L.append("**一、宏观风控**")
        L.append("🚦 **CRI = %.0f（%s）**  〔记忆 %.0f / 扰动 %.0f / 分布 %.0f〕" % (
            c["CRI"], c["state"], c["L1"], c["L2"], c["L3"]))
        L.append("残差动量/σ=%+.2f（分位%.0f）acf₁=%+.3f vol分位%.0f 熵分位%.0f KL=%.3f（μ=%.3f σ=%.3f）｜%s" % (
            c.get("resid_ratio", float("nan")), c.get("resid_pct", float("nan")),
            c["acf1"], c["vol_pct"], c["ent_rank"], c["kl"], c["kl_mu"], c["kl_sd"],
            _hmm_txt(c)))
        L.append("一级记忆层 %s ｜ 二级扰动层 %s ｜ 三级分布层 %s" % (
            "✅" if c["trig1"] else "❌", "✅" if c["trig2"] else "❌", "✅" if c["trig3"] else "❌"))
        L.append("💰 仓位 **%s**" % c["pos"])
        L.append("")

    # ── 行业表（单口径）
    L.append("**二、%s**" % ("原始收益率相关（跟指数一起涨的联动）" if side == "raw"
                            else "残差相关（剔掉指数后真正独立强的）"))
    L.append("<font color='grey'>窗口：相关 %d日(T>N)｜β %d日｜动量 %d日｜κ对比 t-5</font>" % (
        CORR_WIN, BETA_WIN, MOM_WIN))
    for g, gl in (res.get("groups") or {}).items():
        rr = [r for r in rows if r["name"] in gl]
        if not rr:
            continue
        L.append("")
        L.append("**【%s】**" % g)
        for r in rr:
            nm = "**%s**%s" % (r["name"], (" %s" % r["pos"]) if r["pos"] else "")
            if side == "raw":
                L.append("· %s ｜20日 %s ｜今日 %s ｜β%.2f ｜κ %s %s" % (
                    nm, _num(r["mom20"]), _num(r["today"]), r["beta"],
                    _k(r["k_raw"]), _lab(r["label_raw"])))
            else:
                L.append("· %s ｜20日 %s ｜超额 %s ｜α20 %s ｜κ %s %s" % (
                    nm, _num(r["mom20"]), _num(r["ex_today"]), _num(r["alpha20"]),
                    _k(r["k_idio"]), _lab(r["label_idio"])))
            if r["stocks"]:
                L.append("<font color='grey'>　持股：%s</font>" % r["stocks"])

    # ── 结论
    L.append("")
    L.append("**三、结论**")
    c = res.get("cri") or {}
    if side == "raw":
        up = [r["name"] for r in rows if r["up_raw"]]
        risk = [r["name"] for r in rows if "撕裂" in r["label_raw"]]
        L.append("· 原始口径抱团上升 %d 个：%s" % (len(up), "、".join(up) or "无"))
        L.append("· 原始口径撕裂 %d 个：%s" % (len(risk), "、".join(risk) or "无"))
        L.append("<font color='grey'>原始口径的抱团＝大家跟着指数一起动，不代表行业自身强</font>")
    else:
        up = [r["name"] for r in rows if r["up_idio"]]
        risk = [r["name"] for r in rows if "撕裂" in r["label_idio"]]
        L.append("· 残差口径独立走强 %d 个：%s" % (len(up), "、".join(up) or "无"))
        L.append("· 残差口径撕裂 %d 个：%s" % (len(risk), "、".join(risk) or "无"))
        only_i = [r["name"] for r in rows if r["kind"] == "sw"
                  and "🟢" in r["label_idio"] and "🟢" not in r["label_raw"]]
        if only_i:
            L.append("· 真正自身强（残差绿/原始不绿）：%s" % "、".join(only_i))
        L.append("<font color='grey'>残差口径已剔除市场因子并 Ledoit-Wolf + MP 去噪</font>")
    L.append("<font color='grey'>数据截止 %s</font>" % d10)
    return title, "\n".join(L)


def push_feishu(res, chat=None, with_cri=True):
    """把「原始口径」和「残差口径」各发一张飞书卡片。返回 [(side, title, code), ...]"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from feishu_push_card import send
    d10 = res.get("data_end") or res.get("date")
    out = []
    for side, tpl in (("raw", "blue"), ("idio", "turquoise")):
        sub, body = render_one_side(res, side, with_cri=with_cri)
        title = "📐 行业拓扑 · %s · %s · 来自 OpenClaw.Deepseek" % (sub, d10)
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": tpl},
            "elements": [
                {"tag": "markdown", "content": "📎 来自 OpenClaw.Deepseek"},
                {"tag": "markdown", "content": body},
            ],
        }
        try:
            r = send(card) if chat is None else send(card, chat)
            out.append((side, title, r.get("code"), r.get("msg")))
            log("[feishu] %s → code=%s msg=%s" % (side, r.get("code"), r.get("msg")))
        except Exception as e:
            out.append((side, title, -1, str(e)[:120]))
            log("[feishu] %s 失败 %s" % (side, str(e)[:140]))
        time.sleep(1.2)
    return out


# ══════════════════════════════════════════ CLI
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--no-cri", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="全池扫描：134 个申万二级 + 5 大宽基，输出 A/B/C 三档「有机会」")
    a = ap.parse_args()
    res = analyze(date=a.date, with_cri=not a.no_cri, full_pool=a.full)
    if not res.get("ok"):
        print("FAIL:", res.get("err"))
        return 1
    print("=" * 72)
    print("%s标的 %d 个 ｜ 数据截止 %s ｜ 耗时 %.1fs" % (
        "【全池】" if res.get("full_pool") else "", res["n"],
        res["data_end"], res["elapsed"]))
    if res.get("full_pool"):
        print("相关矩阵口径：%s（共同交易日 %d）｜剔除 %d 个无数据/陈旧" % (
            res.get("corr_mode"), res.get("n_common", 0),
            len(res.get("full_dropped") or [])))
    if res.get("topo"):
        t = res["topo"]
        print("LW 收缩系数 δ=%.4f ｜ MP λ+=%.3f 保留 %d/%d 个信号特征值" % (
            t["lw_shrink"] or 0, t["mp_lambda_plus"], t["mp_keep"], res["n"]))
        for k in ("raw", "idio_lw", "idio_mp"):
            tt = t.get(k) or {}
            print("  [%s] τ=%.3f β0=%s β1long=%s 全局κ=%s" % (
                k, tt.get("tau", float('nan')), tt.get("beta0"), tt.get("beta1_long"),
                _k(tt.get("gk"))))
    print("=" * 72)
    print("\n".join(render_lines(res, with_cri=not a.no_cri)))
    if a.full:
        try:
            _b = render_blocks(res, with_cri=not a.no_cri)
            _sz = len(json.dumps({"elements": _b}, ensure_ascii=False).encode())
            print("=" * 72)
            print("飞书卡片 blocks 体积：%.1f KB（上限 40KB）%s" % (
                _sz / 1024.0, "✅" if _sz <= 40 * 1024 else "❌ 超限"))
        except Exception as e:
            print("卡片体积检查失败 %s" % str(e)[:100])
    if a.json:
        print(json.dumps(res, ensure_ascii=False, default=float, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
