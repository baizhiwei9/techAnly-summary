#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔍 概念机会三层过滤（同花顺概念 · 纯脚本 0 token）
================================================================================
大佬 2026-10-28 15:18 需求（原话）：
  「不能把这个行业拓扑加产率过滤这两个变成一个注册成一个技能，因为我想把所有的概念都过滤一遍，
   看看有没有机会。现在行业过滤了，但是概念还没有过滤。概念的数据，你从那个新浪或者是同花顺
   爬取，最好是同花顺优先。还是这么着，就是先是抓行业动量大于 0，就是 20 日的行业动量大于 0
   过滤一遍。然后再加上当日的涨幅大于 0，这两个条件过滤，选出符合条件的概念。
   然后再加上行业拓扑过滤一遍，选出那个资金聚集的那个概念。
   然后再加缠论过滤一遍，选出符合买点的概念，列出来。」

════════════════════════════════════════════════════════════════════
三层漏斗（严格按顺序，逐层收窄）
  第 1 层 · 动量条件：20 日动量 > 0  AND  当日涨幅 > 0
  第 2 层 · 拓扑聚集：κR > 0 AND κI > 0   （资金聚集，结构没撕裂）
  第 3 层 · 缠论买点：三买区 / 二买区·底背驰 / 突破中枢（排除趋势破坏、顶背驰、卖点区）

数据源（同花顺优先，tushare 兜底）
  · 概念列表 & 指数日线：akshare `stock_board_concept_name_ths` / `stock_board_concept_index_ths`
  · 兜底：tushare `ths_index` / `ths_daily`（同源同花顺）
  · 概念成分股：tushare `ths_member`

════════════════════════════════════════════════════════════════════
⚠️ 踩坑记录
  1. `chan_smc_index.fetch_daily()` 走的是 `index_daily`（指数接口），个股/概念都要自己取数
  2. 概念指数日线缓存目录 `.cache/tda_concept_ak/<概念名>.csv`
  3. 拓扑用 `topolib.run_topo`（124 节点 60 秒，已并行化）；概念数量多时用 pairwise 相关
  4. akshare 拉概念列表常超时（15s），已配 tushare 兜底

用法：
  python3 strategies/concept_scan.py                # 三层过滤 + 推飞书/QQ
  python3 strategies/concept_scan.py --no-send      # 只打印
  python3 strategies/concept_scan.py --layer 1      # 只跑到第 1 层看池子多大
  python3 strategies/concept_scan.py --top 20       # 第1层最多取动量前 20 个（默认全取）
================================================================================
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import re
import sys
import json
import time
import argparse
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

CACHE = Path(os.path.join(WS, ".cache", "concept_scan"))
CACHE.mkdir(parents=True, exist_ok=True)
IDX_CACHE = Path(os.path.join(WS, ".cache", "tda_concept_ak"))
IDX_CACHE.mkdir(parents=True, exist_ok=True)
OUT_DIR = Path(os.path.join(WS, "memory", "concept_reports"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

QQ_TARGET = os.environ.get("CS_QQ_TARGET") or os.environ.get("QQ_DEFAULT_TARGET", "")
FEISHU_CHAT = os.environ.get("CS_FEISHU_CHAT") or os.environ.get("FEISHU_CHAT_DM") or os.environ.get("FEISHU_CHAT_ID", "")
SIGN = "📎 来自 OpenClaw.Deepseek"

MOM20_DAYS = 20
TOPO_WIN = 60           # 拓扑相关矩阵窗口
BETA_WIN = 120
MAX_WORKERS = int(os.environ.get("CS_WORKERS", "8"))
STOCK_CACHE = Path(os.path.join(WS, ".cache", "holding_deep", "daily"))
STOCK_CACHE.mkdir(parents=True, exist_ok=True)
NAMES_CSV = os.path.join(WS, ".cache", "stock_names.csv")

# 噪声概念关键词（大而全的、风格/指数型的，过滤掉）
# 2026-10-28 16:38 大佬：「其他的不要给我发了，什么高股息这些个」→ 加风格指数类关键词
NOISE_KW = ("融资融券", "沪股通", "深股通", "标普", "MSCI", "富时", "转融通",
            "预盈预增", "年报", "中报", "季报", "股权转让", "参股", "举牌",
            "ST板块", "次新股", "破净股", "高送转", "低价股", "AH股", "B股",
            "央企", "国企改革", "地方国资",
            # ── 风格/宽基/资金属性类（不是产业概念，大佬明确不要）
            "高股息", "股息", "中特估", "漂亮100", "证金持股", "汇金持股",
            "社保重仓", "基金重仓", "QFII", "深成500", "上证50", "沪深300",
            "中证500", "中证1000", "创业板综", "科创50", "百元股", "低价股",
            "大盘股", "小盘股", "绩优股", "预亏预减", "扭亏", "增持", "回购",
            "大盘蓝筹", "行业龙头", "核心资产")


def log(*a):
    print(*a, flush=True)


def _f(x, d=float("nan")):
    try:
        v = float(x)
        return v if np.isfinite(v) else d
    except Exception:
        return d


def _p(x, nd=1):
    v = _f(x)
    return "—" if not np.isfinite(v) else "%+.*f%%" % (nd, v * 100)


def _k(x):
    v = _f(x)
    return "—" if not np.isfinite(v) else "%+.2f" % v


# ══════════════════════════════════════════ 第 0 层：概念数据
def concept_names(limit=None):
    """同花顺概念名列表（akshare 优先，tushare 兜底），剔除噪声"""
    names = []
    try:
        from data_hub import ak as ak
        df = _ak_timeout(ak.stock_board_concept_name_ths, 20)
        names = [str(x) for x in df["name"].tolist()]
        log("[概念列表] akshare 成功 %d 个" % len(names))
    except Exception as e:
        log("[概念列表] akshare 失败(%s)，回退 tushare" % str(e)[:50])
        try:
            import tushare_init as ti
            idx = ti.ts_call("ths_index", exchange="A", type="N")
            names = [str(x) for x in idx["name"].tolist()]
            log("[概念列表] tushare 成功 %d 个" % len(names))
        except Exception as e2:
            log("[概念列表] tushare 也失败 %s" % str(e2)[:50])
            names = []
    out = []
    for n in names:
        if len(n) > 12:
            continue
        if any(k in n for k in NOISE_KW):
            continue
        out.append(n)
    if limit:
        out = out[:limit]
    return out


def _ak_timeout(fn, timeout=20, **kw):
    """akshare 调用超时保护"""
    from concurrent.futures import ThreadPoolExecutor as _TE
    import threading
    ex = _TE(max_workers=1)
    try:
        fut = ex.submit(lambda: fn(**kw))
        r = fut.result(timeout=timeout)
        return r
    finally:
        ex.shutdown(wait=False)


def concept_idx(name, ttl_day=True):
    """概念指数日线 → pandas.Series(date→close)，同花顺优先"""
    cf = IDX_CACHE / ("%s.csv" % re.sub(r"[^\w\u4e00-\u9fa5]", "_", name))
    if cf.exists():
        try:
            d = pd.read_csv(cf, parse_dates=["date"])
            if len(d) > 60:
                latest = d["date"].max().date()
                if not ttl_day or latest >= (datetime.date.today() - datetime.timedelta(days=4)):
                    return d.set_index("date")["close"].astype(float)
        except Exception:
            pass
    df = None
    try:
        from data_hub import ak as ak
        df = _ak_timeout(ak.stock_board_concept_index_ths, 20,
                         symbol=name, start_date="20240101", end_date="20261231")
        df = df.rename(columns={"日期": "date", "收盘价": "close"})
        df["date"] = pd.to_datetime(df["date"])
    except Exception:
        df = None
    if df is None or len(df) < 60:
        try:
            import tushare_init as ti
            m = _ths_code_map()
            code = m.get(name)
            if code:
                td = ti.ts_call("ths_daily", ts_code=code,
                                start_date="20240101", end_date="20261231")
                if td is not None and len(td) > 60:
                    td = td.sort_values("trade_date")
                    df = pd.DataFrame({"date": pd.to_datetime(td["trade_date"]),
                                       "close": td["close"].astype(float)})
        except Exception:
            pass
    if df is None or len(df) < 60:
        return None
    df = df[["date", "close"]].dropna().sort_values("date")
    # ⚠️ 2026-10-28 坑：akshare 概念指数返回**不稳定** —— 同一个概念
    #    缓存已有 10-28，强制刷新后反而只返回到 10-27（实测「机器人概念」）。
    #    所以绝不能无脑覆盖：新数据的最后日期更早时，保留旧缓存并取并集。
    try:
        if cf.exists():
            old = pd.read_csv(cf, parse_dates=["date"])
            if len(old) and old["date"].max() > df["date"].max():
                merged = (pd.concat([old[["date", "close"]], df[["date", "close"]]])
                          .drop_duplicates(subset=["date"], keep="last")
                          .sort_values("date"))
                merged.to_csv(cf, index=False)
                return merged.set_index("date")["close"].astype(float)
        df.to_csv(cf, index=False)
    except Exception:
        pass
    return df.set_index("date")["close"].astype(float)


def _ths_code_map():
    fp = Path(os.path.join(WS, ".cache", "ths_index_map.json"))
    if fp.exists():
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            pass
    try:
        import tushare_init as ti
        idx = ti.ts_call("ths_index", exchange="A", type="N")
        m = {str(r["name"]): r["ts_code"] for _, r in idx.iterrows()}
        fp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        return m
    except Exception:
        return {}


def load_concepts(names=None, limit=None):
    """批量拉概念指数 → {name: Series}"""
    names = names or concept_names(limit=limit)
    out = {}
    t0 = time.time()

    def _one(n):
        try:
            cn = concept_idx(n)
            return n, cn
        except Exception:
            return n, None
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as p:
        for n, s in p.map(_one, names):
            if s is not None and len(s) > 60:
                out[n] = s
    log("[概念指数] 成功 %d / %d ｜ %.0fs" % (len(out), len(names), time.time() - t0))
    return out


# ══════════════════════════════════════════ 数据对齐（关键）
def align_to_benchmark(cs):
    """把所有概念对齐到「基准最新交易日」

    ⚠️ 2026-10-28 踩坑：同花顺概念指数更新不同步 —— 实测 356 个概念里
       141 个到 10-28、163 个到 10-27、52 个到 10-26。
       直接各取最后一根算「当日涨幅」，等于拿不同日子的涨跌在比较，
       20 日动量也会错位。必须先把最后一根对齐到同一交易日。

    规则：
      · 基准日 = 沪深300 的最新交易日（tushare 已入库的最后一天）
      · 每个概念截断到 ≤ 基准日；若最后日期 < 基准日，说明该概念落后，
        用前值填充（ffill）到基准日，并在 aligned=False 里标记（报告里注明「数据滞后N天」）
      · 落后超过 5 个交易日的直接剔除（数据陈旧，不可信）
    """
    import cri_topology as CT
    try:
        mkt = CT._bars_wide(CT.MKT_NAME, CT.MKT_CODE).set_index("date")["close"].astype(float)
        mkt.index = pd.to_datetime(mkt.index)
        base_day = pd.Timestamp(mkt.index.max())
    except Exception as e:
        log("[对齐] 基准取数失败，跳过错位修正：%s" % str(e)[:60])
        return cs, {}, None

    # ── 自动选基准日：取「覆盖率 ≥ 70% 的最新交易日」
    #    ⚠️ 2026-10-28 实测：同花顺概念指数更新不同步，收盘后 15:2x 只有 141/356
    #       有当日数据，其余还停在上一交易日。若强行用最新日 + 前值补齐，
    #       那 215 个概念的「当日涨幅」全是 0 → 全被第1层筛掉，漏斗空掉。
    #       正确做法：降到覆盖率够的那个交易日当基准。
    cnt = {}
    for n, s in cs.items():
        d = pd.Timestamp(s.index.max())
        cnt[d] = cnt.get(d, 0) + 1
    total = len(cs)
    cands = sorted(cnt, reverse=True)
    # 优先最新日：只要覆盖率 ≥ 30% 就用最新日 ——
    # ⚠️ 2026-10-28 教训：用「覆盖率≥70%」会退到上一交易日，
    #    结果今天（10-28）133/142 个概念普涨 3~4% 全被漏掉，等于分析昨天的行情。
    #    宁可池子小一点（142 vs 305），也必须分析「最新一天的涨跌」。
    base_day = None
    for th in (0.30, 0.15, 0.0):
        for d in cands:
            cov = sum(v for k, v in cnt.items() if k >= d) / max(total, 1)
            if cov >= th:
                base_day = d
                break
        if base_day is not None:
            break
    if base_day is None:
        base_day = cands[0] if cands else pd.Timestamp(mkt.index.max())
    base_day = min(base_day, pd.Timestamp(mkt.index.max()))
    log("[对齐] 覆盖率：%s" % "、".join(
        "%s=%d(%.0f%%)" % (str(d.date()), sum(v for k, v in cnt.items() if k >= d),
                           sum(v for k, v in cnt.items() if k >= d) / max(total, 1) * 100)
        for d in cands[:4]))
    log("[对齐] 选定基准日 %s" % str(base_day.date()))

    out, lag = {}, {}
    all_days = pd.DatetimeIndex(mkt.index)
    for n, s in cs.items():
        s = s[s.index <= base_day]
        if len(s) < MOM20_DAYS + 2:
            continue
        last = pd.Timestamp(s.index.max())
        if last != base_day:
            # ⚠️ 绝不能用 ffill 补到基准日：ffill 后「当日涨幅」恒等于 0，
            #    51 个滞后概念会被静默判成「当日不涨」，指标失真。
            #    正确做法：只保留基准日**有真实数据**的概念，其余计入 lag 统计后剔除。
            nlag = int((all_days[(all_days > last) & (all_days <= base_day)]).size)
            lag[n] = int(nlag)
            continue
        out[n] = s
    log("[对齐] 基准日有真实数据 %d 个 ｜ 数据滞后剔除 %d 个" % (len(out), len(lag)))
    return out, lag, base_day


# ══════════════════════════════════════════ 第 1 层：动量条件
def layer1_momentum(cs, mom_mode=None, mkt_mom20=None):
    """20 日动量条件 AND 当日涨幅 > 0（调用前必须先 align_to_benchmark）

    ⚠️ 2026-10-28 大佬质疑「人形机器人/机器人概念应该符合，你选不出来就有问题」——
       查下来**条件本身没错，但绝对动量在熊市里等于苛求**：
       当时沪深300 20 日 -10.14%，要求概念 20 日绝对动量 > 0
       = 要求跑赢大盘 10 个百分点以上 → 142 个概念里 0 个通过。
       而「机器人概念」20日 -7.20%、当日 +3.76%、超额 +2.94%，是真实走强。

    所以提供三种口径（`--mom-mode`）：
      abs  ：mom20 > 0                          （绝对，严格；熊市会空）
      rel  ：mom20 - 大盘mom20 > 0              （超额，相对强度；推荐）
      both ：两者都要（默认 abs，保持大佬原始口径）
    """
    mode = mom_mode or os.environ.get("CS_MOM_MODE", "rel")
    mkt = mkt_mom20 if mkt_mom20 is not None else 0.0
    out = []
    for n, s in cs.items():
        if len(s) < MOM20_DAYS + 2:
            continue
        c = s.values.astype(float)
        if not np.isfinite(c[-1]) or not np.isfinite(c[-1 - MOM20_DAYS]):
            continue
        m20 = c[-1] / c[-1 - MOM20_DAYS] - 1
        d1 = c[-1] / c[-2] - 1 if c[-2] else 0.0
        m5 = c[-1] / c[-6] - 1 if (len(c) > 6 and c[-6]) else 0.0
        out.append({"name": n, "mom20": float(m20), "d1": float(d1), "mom5": float(m5),
                    "ex20": float(m20 - mkt), "close": float(c[-1]),
                    "last_date": str(s.index[-1].date())})

    def _ok(x):
        if x["d1"] <= 0:
            return False
        if mode == "rel":
            return x["ex20"] > 0
        if mode == "both":
            return x["mom20"] > 0 and x["ex20"] > 0
        return x["mom20"] > 0            # abs（默认，大佬原始口径）

    passed = [x for x in out if _ok(x)]
    passed.sort(key=lambda x: -x["mom20"])
    return passed, out


# ══════════════════════════════════════════ 第 2 层：拓扑聚集
def layer2_topology(cs, passed):
    """κR>0 AND κI>0（资金聚集，结构没撕裂）

    做法与 cri_topology 一致：收益对沪深300 回归取残差（IDIO），
    两个口径分别建图算节点级 Ollivier-Ricci 曲率。
    """
    if not passed:
        return [], {}
    import topolib
    import cri_topology as CT

    names = [x["name"] for x in passed]
    # ⚠️ 2026-11-09 修 bug：动量层只过 1 个概念时，np.corrcoef(单列) 返回 0 维标量 →
    #    np.fill_diagonal 报 ValueError: array must be at least 2-d，整个任务崩掉、还发失败告警。
    #    实际上当天是「大盘 -1.44% 普跌、几乎没有概念同时满足 当日>0 且 超额>0」= 正常无机会。
    #    拓扑至少要 2 个节点才有意义（真实使用 <3 一律跳过）。
    if len(names) < 3:
        log("[拓扑] 通过概念仅 %d 个，不足 3 个，跳过拓扑层（当日普跌，无板块效应）" % len(names))
        return [], {}
    df = pd.DataFrame({n: cs[n] for n in names if n in cs})
    # 基准
    try:
        mkt = CT._bars_wide(CT.MKT_NAME, CT.MKT_CODE).set_index("date")["close"].astype(float)
        mkt.index = pd.to_datetime(mkt.index)
    except Exception as e:
        log("[拓扑] 基准取数失败 %s" % str(e)[:80])
        return [], {}
    allp = pd.DataFrame({"__mkt": mkt}).join(df, how="inner").dropna()
    if len(allp) < BETA_WIN + 5:
        log("[拓扑] 共同交易日不足 %d" % len(allp))
        return [], {}
    Lret = np.log(allp).diff().dropna()
    R = Lret[names].values
    m = Lret["__mkt"].values
    n = len(names)
    b = np.array([np.cov(m[-BETA_WIN:], R[-BETA_WIN:, i], ddof=1)[0, 1]
                  / max(np.var(m[-BETA_WIN:], ddof=1), 1e-18) for i in range(n)])
    Res = R - np.outer(m, b)
    Res = Res - Res.mean(axis=0, keepdims=True)

    def safe(M):
        with np.errstate(invalid="ignore", divide="ignore"):
            C = np.corrcoef(M.T)
        C = np.nan_to_num(np.asarray(C, float), nan=0.0)
        if C.ndim < 2:
            # 单列输入时 corrcoef 返回 0 维标量，兜底成 1×1
            C = np.ones((1, 1)) * float(C)
            np.fill_diagonal(C, 1.0)
            return np.clip((C + C.T) / 2, -1, 1)
        np.fill_diagonal(C, 1.0)
        return np.clip((C + C.T) / 2, -1, 1)

    t0 = time.time()
    tr = topolib.run_topo(safe(R[-TOPO_WIN:]), names, "RAW", verbose=False)
    ti_ = topolib.run_topo(safe(Res[-TOPO_WIN:]), names, "IDIO", verbose=False)
    log("[拓扑] %d 个概念 RAW/IDIO 计算 %.0fs ｜ κR全局%+.3f κI全局%+.3f" % (
        n, time.time() - t0, tr.get("gk", float("nan")), ti_.get("gk", float("nan"))))

    kr = np.array([(tr or {}).get("node_k", {}).get(i, np.nan) for i in range(n)])
    kl = np.array([(ti_ or {}).get("node_k", {}).get(i, np.nan) for i in range(n)])
    res = []
    for i, x in enumerate(passed):
        x = dict(x)
        x["kappa_r"] = float(kr[i])
        x["kappa_i"] = float(kl[i])
        x["beta"] = float(b[i])
        x["alpha20"] = float(Res[-MOM20_DAYS:, i].sum())
        x["ex_today"] = float(R[-1, i] - b[i] * m[-1])
        res.append(x)
    keep = [x for x in res if _f(x["kappa_r"]) > 0 and _f(x["kappa_i"]) > 0]
    keep.sort(key=lambda x: -x["mom20"])
    return keep, {x["name"]: x for x in res}


# ══════════════════════════════════════════ 第 3 层：缠论
def layer3_chan(keep):
    """概念指数缠论：三买区 / 二买区·底背驰 / 突破中枢"""
    import chan_smc_index as C
    out = []
    for x in keep:
        try:
            s = x.get("_series")
            if s is None:
                continue
            # 概念指数只有 close，用 close 当 high/low（缠论只需相对结构）
            c = s.values.astype(float)
            bars = [(float(v), float(v)) for v in c]
            r = C.chan_read(bars, x["name"])
            if not r.get("ok"):
                continue
            st = r.get("stage") or ""
            zp = r.get("zpos")
            dv = r.get("div")
            buys = [b for b in (r.get("buy_list") or []) if b.get("age", 99) <= 25]
            buys.sort(key=lambda b: b.get("age", 99))
            if zp == "below" or dv == "顶背驰" or "二卖" in st or "三卖" in st:
                continue
            typ = None
            if zp == "above" and "三买区" in st:
                typ = "三买区"
            elif zp == "in" and "二买区" in st and dv == "底背驰":
                typ = "二买区·底背驰"
            elif zp == "above":
                typ = "突破中枢"
            if not typ:
                continue
            y = dict(x)
            y.pop("_series", None)
            y["chan"] = typ
            y["stage"] = st
            y["zs"] = r.get("zs")
            y["buy"] = ("%s@%.2f(%d天前)" % (buys[0]["tag"], buys[0]["level"], buys[0]["age"])
                        if buys else "")
            y["age"] = buys[0]["age"] if buys else 99
            out.append(y)
        except Exception as e:
            log("[缠论] %s 失败 %s" % (x["name"], str(e)[:40]))
    pri = {"三买区": 0, "二买区·底背驰": 1, "突破中枢": 2}
    out.sort(key=lambda x: (pri.get(x["chan"], 9), x["age"], -x["mom20"]))
    return out


# ══════════════════════════════════════════ 第 4 层：概念成分股 → 缠论
def _stock_names():
    p = NAMES_CSV
    if os.path.exists(p):
        try:
            d = pd.read_csv(p)
            return dict(zip(d["ts_code"], d["name"]))
        except Exception:
            pass
    try:
        import tushare_init as ti
        d = ti.ts_call("stock_basic", exchange="", list_status="L", fields="ts_code,name")
        if d is not None and len(d):
            d[["ts_code", "name"]].to_csv(p, index=False)
            return dict(zip(d["ts_code"], d["name"]))
    except Exception:
        pass
    return {}


def _fetch_stock(ts_code, ttl_min=30):
    """个股日线（⚠️ 不能用 chan_smc_index.fetch_daily，那个走 index_daily）"""
    import tushare_init as ti
    p = os.path.join(STOCK_CACHE, ts_code + ".csv")
    if os.path.exists(p):
        try:
            if (time.time() - os.path.getmtime(p)) / 60 <= ttl_min:
                d = pd.read_csv(p, parse_dates=["dt"])
                if len(d) > 120:
                    return d
        except Exception:
            pass
    d = None
    for k in range(2):
        try:
            d = ti.ts_call("daily", ts_code=ts_code, start_date="20250101")
            if d is not None and len(d) > 120:
                break
        except Exception:
            d = None
        time.sleep(2 if k == 0 else 5)
    if d is None or len(d) < 120:
        if os.path.exists(p):
            try:
                dd = pd.read_csv(p, parse_dates=["dt"])
                if len(dd) > 120:
                    return dd
            except Exception:
                pass
        return None
    d = d.rename(columns={"trade_date": "dt"})
    d["dt"] = pd.to_datetime(d["dt"])
    d = d.sort_values("dt").reset_index(drop=True)
    try:
        d.to_csv(p, index=False)
    except Exception:
        pass
    return d


def _stock_series(ts_code):
    """个股收盘序列（供个股层拓扑建图用）"""
    try:
        d = _fetch_stock(ts_code)
        if d is None or len(d) < 80:
            return None
        return pd.Series(d["close"].astype(float).values, index=pd.to_datetime(d["dt"]))
    except Exception:
        return None


def _chan_one(ts_code):
    import chan_smc_index as C
    try:
        d = _fetch_stock(ts_code)
        if d is None:
            return None
        r = C.chan_read(C.bars_of(d), ts_code)
        if not r.get("ok"):
            return None
        close = float(d["close"].iloc[-1])
        r["close"] = close
        r["pct"] = close / float(d["close"].iloc[-2]) - 1 if len(d) > 1 else 0.0
        r["m20"] = close / float(d["close"].iloc[-21]) - 1 if len(d) > 20 else 0.0
        c = d["close"].astype(float)
        r["ma20"] = float(c.tail(20).mean()) if len(c) >= 20 else None
        return r
    except Exception:
        return None


def layer4_stocks(l3, per_ind=50, max_ind=10, verbose=True, max_out=20):
    """每个概念 → 成分股按 20 日涨幅取前 per_ind 只 → 个股动量过滤 → 缠论过滤

    2026-10-28 16:51 大佬纠正口径（原话）：
      「你符合条件的不就是 10 个概念吗？我让你选 TOP 10，然后你 TOP 10 每个顶多选出 50 个股票吧？
        50 个股票你用行业动量一过滤，那你 50 个股票近 20 日涨幅如果小于零，你就又过滤一遍了，
        所以符合条件就没多少了，顶多也就是 500 个过滤一遍就符合条件的就 50 个，
        再拿拓扑概念也就是符合 20 个这样的」

    → 正确流水线（每层都要过滤，不是只取2只就完）：
      ① 概念 Top10 → 每个概念取成分股 **前 50 只**（按 20 日涨幅降序）= 最多 500 只
      ② **个股动量过滤**：20 日涨幅 > 0 且 当日涨幅 > 0（用相对口径：跑赢大盘）
      ③ **个股拓扑过滤**：在入选个股池里建图，κR>0 且 κI>0（资金聚集）
      ④ **个股缠论过滤**：三买区 / 二买区·底背驰 / 突破中枢
      → 去重后最多 max_out（默认20）只
    """
    from concurrent.futures import ThreadPoolExecutor
    import tushare_init as ti

    nm_map = _stock_names()
    idx2code = _ths_code_map()
    stage1 = []          # ① 每概念前 50
    for x in l3[:max_ind]:
        ind = x["name"]
        code = idx2code.get(ind)
        if not code:
            continue
        try:
            mem = ti.ts_call("ths_member", ts_code=code)
        except Exception:
            mem = None
        if mem is None or not len(mem):
            continue
        pool = [(str(r["con_code"]), str(r.get("con_name") or "")) for _, r in mem.iterrows()]
        pool = [(c, n) for c, n in pool if "ST" not in n and "退" not in n]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as p:
            rs = list(p.map(lambda t: (t[0], _chan_one(t[0])), pool))
        cands = [(c, r) for c, r in rs if r]
        cands.sort(key=lambda t: -(t[1].get("m20") or -9))
        take = cands[:per_ind]
        stage1 += [dict(code=c, name=nm_map.get(c, c) or c, concept=ind, r=r) for c, r in take]
        if verbose:
            log("[①成分] %-12s 成分%d只 → 前%d只 ｜ %.0fs" % (
                ind, len(pool), len(take), time.time() - t0))
        time.sleep(0.3)

    FUN = {}
    if not stage1:
        return [], [], FUN
    FUN["s1"] = len(stage1)

    # ② 个股动量过滤：20日 > 0 且 当日 > 0（相对口径 = 跑赢大盘）
    try:
        import cri_topology as CT
        mk = CT._bars_wide(CT.MKT_NAME, CT.MKT_CODE).set_index("date")["close"].astype(float)
        mk.index = pd.to_datetime(mk.index)
        mkt20 = float(mk.iloc[-1] / mk.iloc[-21] - 1) if len(mk) > 21 else 0.0
    except Exception:
        mkt20 = 0.0
    stage2 = [x for x in stage1
              if (x["r"].get("m20") or -9) > 0 and (x["r"].get("pct") or -9) > 0]
    if verbose:
        log("[②动量] %d → %d 只（个股 20日>0 且 当日>0）" % (len(stage1), len(stage2)))
    FUN["s2"] = len(stage2)
    if not stage2:
        return [], [x.get("name") for x in stage1], FUN

    # ③ 个股拓扑过滤：在入选个股池里建图算 κ
    keep = stage2
    try:
        import topolib
        names3 = [x["code"] for x in stage2]
        sers = {}
        for x in stage2:
            s = _stock_series(x["code"])
            if s is not None and len(s) > 80:
                sers[x["code"]] = s
        names3 = [c for c in names3 if c in sers]
        if len(names3) >= 10:
            dfp = pd.DataFrame({c: sers[c] for c in names3}).dropna()
            Lr = np.log(dfp).diff().dropna()
            R = Lr[names3].values
            def _safe(M):
                with np.errstate(invalid="ignore", divide="ignore"):
                    Cm = np.corrcoef(M.T)
                Cm = np.nan_to_num(np.asarray(Cm, float), nan=0.0)
                np.fill_diagonal(Cm, 1.0)
                return np.clip((Cm + Cm.T) / 2, -1, 1)
            t = topolib.run_topo(_safe(R[-TOPO_WIN:]), names3, "STK", verbose=False)
            nk = (t or {}).get("node_k", {})
            kk = {names3[i]: float(nk.get(i, np.nan)) for i in range(len(names3))}
            keep = [x for x in stage2 if _f(kk.get(x["code"])) > 0]
            FUN["s3"] = len(keep)
            if verbose:
                log("[③拓扑] %d → %d 只（个股 κR>0，池内 %d 只建图）" % (
                    len(stage2), len(keep), len(names3)))
        else:
            FUN["s3"] = len(stage2)
            if verbose:
                log("[③拓扑] 池内不足 10 只，跳过拓扑过滤")
    except Exception as e:
        FUN["s3"] = len(stage2)
        log("[③拓扑] ERR %s（跳过）" % str(e)[:100])

    # ④ 缠论过滤
    out = []
    for x in keep:
        r = x["r"]
        st = r.get("stage") or ""
        zp = r.get("zpos")
        dv = r.get("div")
        buys = [b for b in (r.get("buy_list") or []) if b.get("age", 99) <= 25]
        buys.sort(key=lambda b: b.get("age", 99))
        if zp == "below" or dv == "顶背驰" or "二卖" in st or "三卖" in st:
            continue
        if zp == "above" and "三买区" in st:
            typ = "🟢三买区"
        elif zp == "in" and "二买区" in st and dv == "底背驰":
            typ = "🟢二买区·底背驰"
        elif zp == "above":
            typ = "🟢突破中枢"
        else:
            continue
        out.append(dict(name=x["name"], code=x["code"], concept=x["concept"],
                        chan=typ, stage=st,
                        buy=("%s@%.2f(%d天前)" % (buys[0]["tag"], buys[0]["level"], buys[0]["age"])
                             if buys else ""),
                        age=buys[0]["age"] if buys else 99,
                        close=r["close"], pct=r["pct"], m20=r["m20"], zs=r.get("zs")))
    FUN["s4"] = len(out)
    if verbose:
        log("[④缠论] %d → %d 只" % (len(keep), len(out)))

    good = [p for p in out if p["chan"].startswith("🟢")]
    pri = {"🟢三买区": 0, "🟢二买区·底背驰": 1, "🟢突破中枢": 2}
    good.sort(key=lambda x: (pri.get(x["chan"], 9), x["age"], -x["m20"]))

    # ── 去重：同一只股票可能命中多个概念（世纪恒通同时属网络安全/云计算/腾讯概念）
    #    大佬要的是「最多 20 个股票」，所以一只股票只能出现一次，
    #    所属概念合并成「网络安全+云计算+腾讯概念」。
    merged, seen = [], {}
    for x in good:
        c = x["code"]
        if c in seen:
            t = seen[c]
            if x["concept"] not in t["concepts"]:
                t["concepts"].append(x["concept"])
            continue
        y = dict(x)
        y["concepts"] = [x["concept"]]
        y["concept"] = x["concept"]
        seen[c] = y
        merged.append(y)
    for y in merged:
        if len(y["concepts"]) > 1:
            y["concept"] = "+".join(y["concepts"])
        y["concepts"] = y["concepts"]
    log("[个股层] 去重：%d 命中 → %d 只唯一股票%s" % (
        len(good), len(merged),
        ("，截断到上限 %d" % max_out) if max_out and len(merged) > max_out else ""))
    FUN["uniq"] = len(merged)
    if max_out and len(merged) > max_out:
        merged = merged[:max_out]

    # ── ⑤ 成交量小波过滤（大佬 2026-10-28 指定算法，阈值一字不改）
    #    6 层 DWT，中长周期＝D4+D5+D6+A6（16 日以上）是否确认增长
    #    只诊断、不剔除：报告分「无过滤」「有过滤」两栏
    try:
        import vol_wavelet as VW
        def _diag(x):
            try:
                d = _fetch_stock(x["code"])
                if d is None or "vol" not in d.columns:
                    return x["code"], {"pass": False, "reason": "成交量取数失败",
                                       "confirm": "-", "P": {}}
                v = pd.to_numeric(d["vol"], errors="coerce").fillna(0).values[-VW.WINDOW:]
                return x["code"], VW.diagnose(v, name=x["name"], code=x["code"])
            except Exception as e:
                return x["code"], {"pass": False, "reason": "过滤异常 %s" % str(e)[:40],
                                   "confirm": "-", "P": {}}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as p:
            vres = dict(p.map(_diag, merged))
        for x in merged:
            x["vol"] = vres.get(x["code"]) or {"pass": False, "reason": "未诊断", "P": {}}
    except Exception as e:
        log("[⑤小波] ERR %s" % str(e)[:120])
        for x in merged:
            x["vol"] = {"pass": False, "reason": "过滤异常", "P": {}}
    FUN["vpass"] = sum(1 for x in merged if (x.get("vol") or {}).get("pass"))
    if verbose:
        log("[⑤小波] %d 只 → 中长周期量能确认 %d 只" % (len(merged), FUN["vpass"]))
        for x in merged:
            v = x.get("vol") or {}
            log("   %-8s %s %s" % (x["name"], "✅" if v.get("pass") else "❌",
                                   (v.get("reason") or "")[:60]))
    return merged, out, FUN


# ══════════════════════════════════════════ 主流程
def run(layer=4, limit=None, top=None, verbose=True, mom_mode=None,
        per_ind=50, max_ind=10, max_out=20):
    t0 = time.time()
    cs = load_concepts(limit=limit)
    if not cs:
        return {"ok": False, "err": "概念数据为空"}

    # ⚠️ 必须先把最后一根对齐到同一交易日，否则动量/当日涨幅全错位
    cs, lag, base_day = align_to_benchmark(cs)

    # 大盘 20 日动量（用于相对口径）
    try:
        import cri_topology as CT
        _m = CT._bars_wide(CT.MKT_NAME, CT.MKT_CODE).set_index("date")["close"].astype(float)
        _m.index = pd.to_datetime(_m.index)
        _m = _m[_m.index <= base_day] if base_day is not None else _m
        mkt_mom20 = float(_m.iloc[-1] / _m.iloc[-21] - 1) if len(_m) > 21 else 0.0
    except Exception:
        mkt_mom20 = 0.0
    mode = mom_mode or os.environ.get("CS_MOM_MODE", "rel")
    if verbose:
        log("[大盘] 20日动量 %+.2f%% ｜ 动量口径=%s（abs=绝对>0｜rel=超额>0｜both=两者）" % (
            mkt_mom20 * 100, mode))

    l1, allrows = layer1_momentum(cs, mom_mode=mode, mkt_mom20=mkt_mom20)
    if top:
        l1 = l1[:top]
    if verbose:
        log("[第1层 动量] %d/%d 通过（口径 %s）" % (len(l1), len(cs), mode))
        for x in l1[:15]:
            log("   %-14s 20日%+6.1f%% 当日%+6.1f%%" % (
                x["name"], x["mom20"] * 100, x["d1"] * 100))
    r = {"ok": True, "n_all": len(cs), "l1": l1, "lag": lag, "mom_mode": mode,
         "mkt_mom20": mkt_mom20,
         "base_day": str(base_day.date()) if base_day is not None else None,
         "elapsed_l1": round(time.time() - t0, 1)}
    if layer < 2 or not l1:
        r.update({"l2": [], "l3": [], "elapsed": round(time.time() - t0, 1)})
        return r

    l2, allmap = layer2_topology(cs, l1)
    for x in l2:
        x["_series"] = cs.get(x["name"])
    if verbose:
        log("[第2层 拓扑] %d/%d 通过（κR>0 且 κI>0）" % (len(l2), len(l1)))
        for x in l2:
            log("   %-14s 20日%+6.1f%% κR%+.2f κI%+.2f" % (
                x["name"], x["mom20"] * 100, x["kappa_r"], x["kappa_i"]))
    r.update({"l2": l2, "elapsed_l2": round(time.time() - t0, 1)})
    if layer < 3 or not l2:
        r.update({"l3": [], "elapsed": round(time.time() - t0, 1)})
        return r

    l3 = layer3_chan(l2)
    # 2026-10-28 16:46 大佬：结果 > 10 就按 **20 日动量** 取 Top 10 概念
    # ⚠️ 2026-10-28 21:15 修 bug：原先按「缠论买点类型优先级→买点天数→动量」排，
    #    不符合大佬口径（他要的是纯 20 日动量排名）。特斯拉概念因此被错误挤到第 11 名。
    l3_all = sorted(l3, key=lambda x: -(x.get("mom20") or -9))
    l3 = l3_all
    if max_ind and len(l3) > max_ind:
        l3 = l3[:max_ind]
    if verbose:
        log("[第3层 缠论] %d/%d 通过（取 Top %d）" % (
            len(l3), len(l2), min(max_ind or 10, len(l3))))
        for x in l3:
            log("   %-14s %s %s" % (x["name"], x["chan"], x["buy"]))
    r.update({"l3": l3, "l3_all": l3_all, "elapsed": round(time.time() - t0, 1)})
    if layer < 4 or not l3:
        r.update({"stocks": []})
        return r

    stocks, allst, funnel = layer4_stocks(l3, per_ind=per_ind, max_ind=max_ind,
                                          verbose=verbose, max_out=max_out)
    r.update({"stocks": stocks, "all_stocks": allst, "funnel": funnel,
              "n_stage1": funnel.get("s1", 0), "n_stage2": funnel.get("s2", 0),
              "n_stage3": funnel.get("s3", 0), "n_stage4": funnel.get("s4", 0),
              "n_uniq": funnel.get("uniq", 0),
              "elapsed": round(time.time() - t0, 1)})
    if verbose:
        log("[个股层] 最终 %d 只（上限 %d）" % (len(stocks), per_ind * len(l3)))
    return r


# ══════════════════════════════════════════ 渲染
def _vsplit(st):
    """按周期拆分小波通过名单（大佬 2026-10-28 20:57：报告要写中期几个、长期几个）

    返回 (中期通过 list, 长期通过 list)
      一只票两周期都过 → 两边都算进各自计数（列里也分别标）
    """
    mid, lng = [], []
    for x in st:
        g = ((x.get("vol") or {}).get("groups") or {})
        if (g.get("mid") or {}).get("ok"):
            mid.append(x)
        if (g.get("long") or {}).get("ok"):
            lng.append(x)
    return mid, lng


def _vtag(x):
    """一只票通过哪些周期：'中期+长期' / '中期' / '长期'"""
    g = ((x.get("vol") or {}).get("groups") or {})
    a = bool((g.get("mid") or {}).get("ok"))
    b = bool((g.get("long") or {}).get("ok"))
    if a and b:
        return "中期+长期"
    return "中期" if a else ("长期" if b else "")


def _concept_hits(st):
    """统计每个概念的命中只数（2026-10-28 17:39 大佬要求）

    含义：一个概念里命中 N 只，说明是板块整体启动；
         只命中 1 只则可能是个别股票独立走强，不是板块效应。
    """
    from collections import Counter
    c = Counter()
    for x in st:
        for cn in (x.get("concepts") or [x["concept"]]):
            c[cn] += 1
    return c.most_common()


def render_text(r):
    """最终报告（2026-10-28 定稿）：概念买点 + 每个概念的个股买点"""
    L = []
    d = datetime.date.today().strftime("%Y-%m-%d")
    st = r.get("stocks") or []
    L.append("🔍 概念拓扑缠论选股 · %s" % d)
    L.append(SIGN)
    L.append("")
    L.append("基准 %s ｜ 概念池 %d → ①概念动量 %d → ②概念拓扑 %d → ③概念缠论 %d" % (
        r.get("base_day") or "—", r.get("n_all", 0),
        len(r.get("l1") or []), len(r.get("l2") or []), len(r.get("l3") or [])))
    L.append("个股漏斗：成分 %d → ②动量 %d → ③拓扑 %d → ④缠论 %d → 去重 %d → 最终 %d → ⑤小波 %d" % (
        r.get("n_stage1", 0), r.get("n_stage2", 0), r.get("n_stage3", 0),
        r.get("n_stage4", 0), r.get("n_uniq", 0), len(st),
        sum(1 for x in st if (x.get("vol") or {}).get("pass"))))
    L.append("")

    # ── 概念命中数（板块效应强度）
    hits = _concept_hits(st)
    if hits:
        L.append("━━ 概念内命中只数（>1 说明板块整体启动，不是个股独立）━━")
        for cn, n in hits:
            flag = "🔥板块启动" if n >= 3 else ("✓" if n == 2 else "·单一")
            L.append("  %-24s %2d 只 %s" % (cn[:24], n, flag))
        L.append("")

    vpass = [x for x in st if (x.get("vol") or {}).get("pass")]

    if st:
        L.append("━━ 一、个股买点 · 缠论口径（无成交量过滤）%d 只 ━━" % len(st))
        L.append("  %-8s %-24s %-18s %8s %8s" % ("股票", "所属概念", "缠论买点", "20日", "当日"))
        L.append("  " + "-" * 72)
        for x in st:
            L.append("  %-8s %-24s %-18s %+7.1f%% %+7.1f%%" % (
                x["name"][:8], x["concept"][:24],
                (x["chan"].replace("🟢", "") + " " + (x.get("buy") or ""))[:18],
                x["m20"] * 100, x["pct"] * 100))
    else:
        L.append("  今日无符合买点的个股")
    L.append("")

    # ── 二、成交量小波过滤（中期 或 长期 确认增长，任一即可）
    _mid, _lng = _vsplit(st)
    L.append("━━ 二、🔥 缠论 + 成交量小波双重过滤 %d 只 ━━" % len(vpass))
    L.append("  ★ 中期通过 **%d 只** ｜ 长期通过 **%d 只** ｜ 合计去重 **%d 只**"
             % (len(_mid), len(_lng), len(vpass)))
    L.append("  口径：6 层 DWT（db4/sym6/coif3）｜中期＝D4+D5（16~32日）｜长期＝D6+A6（32日以上）")
    L.append("        **中期或长期任一确认增长即通过** ｜中长能量占比>55%｜近20点上行≥70%")
    L.append("        30日斜率>0且R²≥0.6｜最新量≥60日均×1.2｜排除单日≥2倍且同期增量<15%的脉冲")
    if vpass:
        L.append("  %-8s %-22s %-8s %6s %6s %6s %s" % (
            "股票", "所属概念", "通过周期", "上行%", "R²", "量/60", "置信度"))
        L.append("  " + "-" * 76)
        for x in vpass:
            v = x["vol"]
            g = (v.get("groups") or {}).get(v.get("main") or "mid") or {}
            L.append("  %-8s %-22s %-8s %6.1f %6.2f %6.2f %s" % (
                x["name"][:8], x["concept"][:22], _vtag(x),
                g.get("mono") or 0, g.get("r2") or 0, g.get("rel") or 0,
                v.get("confirm") or "-"))
    else:
        L.append("  中期 **0 只** ｜ 长期 **0 只**（阈值不放宽，宁空不凑数）")
    L.append("")

    # ── 被小波过滤掉的个股（按离通过最近的差距排序）
    if st:
        drop = [x for x in st if not (x.get("vol") or {}).get("pass")]
        if drop:
            L.append("━━ 三、被成交量过滤剔除 %d 只（中期 / 长期 分别到哪一步）━━" % len(drop))
            L.append("  %-8s %-26s %-22s %-22s" % ("股票", "所属概念", "中期(16~32日)", "长期(>32日)"))
            L.append("  " + "-" * 74)
            for x in drop:
                v = x.get("vol") or {}
                gs = v.get("groups") or {}
                def _g(k):
                    g = gs.get(k) or {}
                    if not g:
                        return "—"
                    if g.get("ok"):
                        return "✅通过"
                    if (g.get("mono") or 0) < 70:
                        return "上行%.0f%%<70" % (g.get("mono") or 0)
                    if not ((g.get("slope_norm") or 0) > 0 and (g.get("r2") or 0) >= 0.6):
                        return "R²%.2f<0.6" % (g.get("r2") or 0)
                    if (g.get("rel") or 0) < 1.2:
                        return "量/60=%.2f<1.2" % (g.get("rel") or 0)
                    return "脉冲反证"
                L.append("  %-8s %-26s %-22s %-22s" % (
                    x["name"][:8], x["concept"][:26], _g("mid"), _g("long")))
            L.append("")

    if r.get("l3"):
        L.append("━━ 入选概念（%d 个）━━" % len(r["l3"]))
        for x in r["l3"]:
            L.append("  · %-14s %s ｜20日 %+.1f%% 当日 %+.1f%%" % (
                x["name"], x["chan"], x["mom20"] * 100, x["d1"] * 100))
    # 通过缠论但被 Top10 截掉的（大佬 2026-10-28 21:10 追问「特斯拉怎么没了」→ 透明化）
    cut = [x for x in (r.get("l3_all") or []) if x["name"] not in
           {y["name"] for y in (r.get("l3") or [])}]
    if cut:
        L.append("")
        L.append("  ⚠️ 另 %d 个也通过缠论，但按「Top %d」名次截断未入选："
                 % (len(cut), len(r.get("l3") or [])))
        for x in cut:
            L.append("     · %-14s %s ｜20日 %+.1f%%（排名第 %d）" % (
                x["name"], x["chan"], x["mom20"] * 100,
                (r.get("l3_all") or []).index(x) + 1))
    L.append("")
    L.append("（同花顺概念 · 程序生成 0 token ｜ %.0fs）" % r.get("elapsed", 0))
    return L


def render_card(r):
    """飞书卡片 blocks（私聊详细版）"""
    d = datetime.date.today().strftime("%Y-%m-%d")
    st = r.get("stocks") or []
    B = []

    B.append({"type": "md", "text":
              "**基准 %s** ｜ 概念池 %d → ①概念动量 %d → ②概念拓扑 %d → ③概念缠论 %d\n"
              "<font color='grey'>个股漏斗：成分 %d → ②动量 %d → ③拓扑 %d → ④缠论 %d → 去重 %d → 最终 **%d** → ⑤小波 **%d**</font>" % (
                  r.get("base_day") or "—", r.get("n_all", 0),
                  len(r.get("l1") or []), len(r.get("l2") or []), len(r.get("l3") or []),
                  r.get("n_stage1", 0), r.get("n_stage2", 0), r.get("n_stage3", 0),
                  r.get("n_stage4", 0), r.get("n_uniq", 0), len(st),
                  sum(1 for x in (r.get("stocks") or []) if (x.get("vol") or {}).get("pass")))})
    B.append({"type": "hr"})

    # ── 概念命中数
    hits = _concept_hits(st)
    if hits:
        t = ["**🔥 概念内命中只数**",
             "<font color='grey'>命中 ≥3 只 = 板块整体启动；=1 只 = 个股独立走强</font>"]
        for cn, n in hits:
            flag = "🔥 **板块启动**" if n >= 3 else ("✓ 两只" if n == 2 else "<font color='grey'>· 单一</font>")
            t.append("· **%s** ｜ **%d 只** ｜ %s" % (cn, n, flag))
        B.append({"type": "md", "text": "\n".join(t)})
        B.append({"type": "hr"})

    vpass = [x for x in st if (x.get("vol") or {}).get("pass")]

    # ── 个股表（一、缠论口径 无成交量过滤）
    B.append({"type": "md", "text": "**一、个股买点 · 缠论口径（无成交量过滤）%d 只**" % len(st)})
    if st:
        try:
            import wide_ind_report as WIR
            B.append(WIR._cols(("**股票**", "**所属概念**", "**缠论买点**", "**20日**", "**当日**"),
                               (3, 6, 5, 2, 2), ("left", "left", "left", "right", "right")))
            for x in st:
                B.append(WIR._cols(
                    ("**%s**" % x["name"], x["concept"][:26],
                     (x["chan"].replace("🟢", "") + (" " + x["buy"] if x.get("buy") else "")),
                     "%+.1f%%" % (x["m20"] * 100), "%+.1f%%" % (x["pct"] * 100)),
                    (3, 6, 5, 2, 2), ("left", "left", "left", "right", "right")))
        except Exception:
            for x in st:
                B.append({"type": "md", "text": "· **%s** ｜%s ｜%s ｜20日 %+.1f%% ｜当日 %+.1f%%" % (
                    x["name"], x["concept"], x["chan"].replace("🟢", ""),
                    x["m20"] * 100, x["pct"] * 100)})
    else:
        B.append({"type": "md", "text": "今日无符合买点的个股"})
    B.append({"type": "hr"})

    # ── 二、缠论 + 成交量小波双重过滤
    _mid, _lng = _vsplit(st)
    B.append({"type": "md", "text":
              "**二、🔥 缠论 + 成交量小波双重过滤 %d 只**（中长周期成交量确认增长）\n"
              "★ 中期通过 **%d 只** ｜ 长期通过 **%d 只** ｜ 合计去重 **%d 只**"
              % (len(vpass), len(_mid), len(_lng), len(vpass))})
    B.append({"type": "note", "text":
              "6 层 DWT（db4/sym6/coif3）｜中期＝D4+D5（16~32日）｜长期＝D6+A6（32日以上）｜"
              "中期或长期任一确认增长即通过｜中长能量>55%｜近20点上行≥70%｜"
              "30日斜率>0且R²≥0.6｜最新量≥60日均×1.2｜排除≥2倍脉冲"})
    if vpass:
        try:
            import wide_ind_report as WIR
            B.append(WIR._cols(("**股票**", "**所属概念**", "**周期**", "**上行%**", "**R²**", "**量/60**", "**置信**"),
                               (3, 5, 2, 2, 2, 2, 1), ("left", "left", "center", "right", "right", "right", "right")))
            for x in vpass:
                v = x["vol"]
                g = (v.get("groups") or {}).get(v.get("main") or "mid") or {}
                B.append(WIR._cols(
                    ("**%s**" % x["name"], x["concept"][:22], _vtag(x),
                     "%.0f%%" % (g.get("mono") or 0),
                     "%.2f" % (g.get("r2") or 0), "%.2f" % (g.get("rel") or 0),
                     v.get("confirm") or "-"),
                    (3, 5, 2, 2, 2, 2, 1), ("left", "left", "center", "right", "right", "right", "right")))
        except Exception:
            for x in vpass:
                v = x["vol"]
                g = (v.get("groups") or {}).get(v.get("main") or "mid") or {}
                B.append({"type": "md", "text": "· **%s** ｜%s ｜%s ｜上行 %.0f%% ｜R² %.2f ｜量/60 %.2f ｜%s" % (
                    x["name"], x["concept"], _vtag(x), g.get("mono") or 0,
                    g.get("r2") or 0, g.get("rel") or 0, v.get("confirm") or "-")})
    else:
        B.append({"type": "md", "text": "中期 **0 只** ｜ 长期 **0 只**（阈值不放宽，宁空不凑数）"})

    # ── 三、被剔除的（原因）
    drop = [x for x in st if not (x.get("vol") or {}).get("pass")]
    if drop:
        t = ["**三、被成交量过滤剔除 %d 只**（中期 / 长期 分别卡在哪一步）" % len(drop)]
        for x in drop:
            v = x.get("vol") or {}
            gs = v.get("groups") or {}
            def _g(k):
                g = gs.get(k) or {}
                if not g:
                    return "—"
                if g.get("ok"):
                    return "✅"
                if (g.get("mono") or 0) < 70:
                    return "上行%.0f%%<70" % (g.get("mono") or 0)
                if not ((g.get("slope_norm") or 0) > 0 and (g.get("r2") or 0) >= 0.6):
                    return "R²%.2f<0.6" % (g.get("r2") or 0)
                if (g.get("rel") or 0) < 1.2:
                    return "量/60=%.2f" % (g.get("rel") or 0)
                return "脉冲反证"
            t.append("· %s（%s）｜<font color='grey'>中期 %s ｜ 长期 %s</font>" % (
                x["name"], x["code"], _g("mid"), _g("long")))
        B.append({"type": "md", "text": "\n".join(t)})
    B.append({"type": "hr"})

    # ── 入选概念
    if r.get("l3"):
        t = ["**入选概念（%d 个）**" % len(r["l3"])]
        for x in r["l3"]:
            t.append("· **%s** ｜%s ｜20日 %+.1f%% ｜当日 %+.1f%%" % (
                x["name"], x["chan"], x["mom20"] * 100, x["d1"] * 100))
        cut = [x for x in (r.get("l3_all") or []) if x["name"] not in
               {y["name"] for y in (r.get("l3") or [])}]
        if cut:
            t.append("")
            t.append("<font color='grey'>⚠️ 另 %d 个也通过缠论，但按「Top %d」截断未入选：</font>"
                     % (len(cut), len(r.get("l3") or [])))
            for x in cut:
                t.append("<font color='grey'>· %s ｜%s ｜20日 %+.1f%%（第 %d 名）</font>" % (
                    x["name"], x["chan"], x["mom20"] * 100,
                    (r.get("l3_all") or []).index(x) + 1))
        B.append({"type": "md", "text": "\n".join(t)})
        B.append({"type": "hr"})

    B.append({"type": "note", "text":
              "同花顺概念 ｜ 程序生成 0 token ｜ 数据截至 %s ｜ %.0fs" % (
                  r.get("base_day") or d, r.get("elapsed", 0))})
    return B


def render_qq(r):
    """QQ 早间私聊版：两栏（无过滤 / 有小波过滤）"""
    st = r.get("stocks") or []
    vpass = [x for x in st if (x.get("vol") or {}).get("pass")]
    L = ["🔍 概念拓扑缠论选股 · 缠论 %d 只 → 小波过滤后 %d 只" % (len(st), len(vpass))]
    L.append("")
    L.append("【一、缠论口径（无成交量过滤）%d 只】" % len(st))
    if st:
        for x in st:
            L.append("· %s ｜%s ｜%s%s" % (
                x["name"], x["concept"], x["chan"].replace("🟢", ""),
                (" " + x["buy"]) if x.get("buy") else ""))
    else:
        L.append("· 今日无符合买点的个股")
    L.append("")
    _mid, _lng = _vsplit(st)
    L.append("【二、🔥 缠论+成交量小波双重过滤 %d 只】中期 %d 只 ｜ 长期 %d 只" % (
        len(vpass), len(_mid), len(_lng)))
    if vpass:
        for x in vpass:
            v = x["vol"]
            g = (v.get("groups") or {}).get(v.get("main") or "mid") or {}
            L.append("· %s ｜%s ｜%s 上行%.0f%% R²%.2f 量/60 %.2f ｜%s" % (
                x["name"], x["concept"], _vtag(x), g.get("mono") or 0,
                g.get("r2") or 0, g.get("rel") or 0, v.get("confirm") or "-"))
    else:
        L.append("· 中期 0 只 ｜ 长期 0 只（阈值不放宽）")
    hits = _concept_hits(st)
    if hits:
        L.append("")
        L.append("概念命中：%s" % "、".join("%s×%d" % (c, n) for c, n in hits))
    if r.get("l3"):
        L.append("")
        L.append("入选概念：%s" % "、".join(x["name"] for x in r["l3"]))
    return L


def _upload_img(path):
    """上传图片到飞书拿 img_key（必须两步：token() → upload_image(tok, path)）"""
    try:
        import feishu_card as FC
        tok = FC.token()
        key = FC.upload_image(tok, path)
        if key:
            log("[img] upload ok %s" % key[:16])
            return key
        log("[img] upload 返回空")
    except Exception as e:
        log("[img] ERR %s" % str(e)[:120])
    return ""


def push_feishu(blocks, title, imgs=None):
    try:
        import feishu_card as FC
        ok = FC.send_card(title, blocks, imgs=imgs, chat=FEISHU_CHAT,
                          template="blue", sign=SIGN)
        log("[feishu] %s" % ("ok" if ok else "fail"))
        return bool(ok)
    except Exception as e:
        log("[feishu] ERR %s" % str(e)[:150])
        return False


def push_qq(text):
    try:                              # 统一通知（data_hub.notify）优先
        import _notify
        _r = _notify.send(((text or "").strip().splitlines() or [""])[0][:60], text)
        if _r.get("ok"):
            return True
        print("[notify] 统一库未就绪，回落原通道：%s" % _r.get("reason"))
    except Exception as _exc:
        print("[notify] 统一库异常，回落原通道：%s" % _exc)
    try:
        import subprocess
        r = subprocess.run(["openclaw", "message", "send", "--channel", "qqbot",
                            "--target", QQ_TARGET, "--message", text, "--json"],
                           capture_output=True, text=True, timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
        ok = ('"messageId"' in out and '"messageId": ""' not in out) or "receipt" in out
        log("[qq] rc=%s %s" % (r.returncode, "ok" if ok else out[-160:]))
        return ok
    except Exception as e:
        log("[qq] ERR %s" % str(e)[:150])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", type=int, default=4, choices=[1, 2, 3, 4])
    ap.add_argument("--limit", type=int, default=None, help="概念池上限")
    ap.add_argument("--top", type=int, default=None, help="第1层最多取前 N")
    ap.add_argument("--per-ind", type=int, default=50, help="每个概念先取几只成分股（默认50）")
    ap.add_argument("--max-ind", type=int, default=10, help="概念数上限（默认10）")
    ap.add_argument("--max-out", type=int, default=20, help="最终个股上限（默认20）")
    ap.add_argument("--mom-mode", default=None, choices=["abs", "rel", "both"],
                    help="20日动量口径：abs=绝对>0（默认/大佬原口径）｜rel=超额>0（熊市推荐）｜both=都要")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--qq-only", action="store_true")
    ap.add_argument("--feishu-only", action="store_true")
    a = ap.parse_args()

    r = run(layer=a.layer, limit=a.limit, top=a.top, mom_mode=a.mom_mode,
            per_ind=a.per_ind, max_ind=a.max_ind, max_out=a.max_out)
    if not r.get("ok"):
        log("FAIL: %s" % r.get("err"))
        return 1
    lines = render_text(r)
    print("\n".join(lines))

    d8 = datetime.date.today().strftime("%Y%m%d")
    try:
        (OUT_DIR / ("%s_concept.md" % d8)).write_text("\n".join(lines), encoding="utf-8")
        json.dump({"l1": r.get("l1"), "l2": [{k: v for k, v in x.items() if k != "_series"}
                                            for x in (r.get("l2") or [])],
                   "l3": r.get("l3"), "stocks": r.get("stocks") or [],
                   "funnel": r.get("funnel") or {}, "base_day": r.get("base_day")},
                  open(OUT_DIR / ("%s_concept.json" % d8), "w", encoding="utf-8"),
                  ensure_ascii=False, default=float)
    except Exception as e:
        log("[write] %s" % str(e)[:80])

    # ── 统一契约：.cache/picks/concept_scan.json（显示层「自选股」按算法分块）──
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pick_pool as PP
        rows = []
        for st in (r.get("stocks") or []):
            cs = st.get("concepts") or []
            rows.append(PP.pick_row(
                st.get("code"), st.get("name"),
                industry=cs[0] if cs else "",
                reason="%s｜%s" % (st.get("stage") or st.get("chan") or "",
                                   ("20日 %+.1f%%" % (st.get("m20", 0) * 100))
                                   if st.get("m20") is not None else ""),
                score=(st.get("m20") or 0) * 100,
                price=st.get("close"),
                tags=[t for t in [st.get("chan") or "",
                                  ("买点 " + st["buy"].split("@")[0]) if st.get("buy") else ""]
                      if t]))
        note = ''
        fn = r.get("funnel") or {}
        if fn:
            note = "漏斗 " + "｜".join("%s %s" % (k, v) for k, v in list(fn.items())[:4])
        PP.write_picks_fallback(
            "concept_scan", "🎯 概念轮动选股",
            (r.get("base_day") or d8).replace("-", "") if r.get("base_day") else d8,
            rows, cadence="daily", note=note, allow_empty=True)
    except Exception as e:
        log("[picks] %s" % str(e)[:140])

    if a.no_send:
        return 0
    d10 = datetime.date.today().strftime("%Y-%m-%d")
    # 2026-10-28 17:41 大佬：用程序生成这 20 个概念的日线图，缠论分析日线图，每只股票一个子图
    img = None
    try:
        import concept_chart as CC
        img = CC.build(r.get("stocks") or [], date=r.get("base_day") or d8)
        if img:
            log("[chart] 出图 %s" % img)
    except Exception as e:
        log("[chart] ERR %s" % str(e)[:150])

    if not a.qq_only:
        blocks = render_card(r)
        # ⚠️ 发图要用 send_card(imgs=[路径])，不要自己往 blocks 塞 img_key
        #    （feishu_card.build_card 期望 {"type":"img","key":...}，塞 img_key 会 KeyError: 'key'）
        push_feishu(blocks, "🔍 概念拓扑缠论选股 · %s ｜ 日线缠论 · 来自 OpenClaw.Deepseek" % d10,
                    imgs=([img] if img else None))
    if not a.feishu_only:
        push_qq("\n".join(render_qq(r)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
