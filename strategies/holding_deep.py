#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🏭 持仓深度 + 机会行业个股缠论（纯脚本 0 token）
================================================================================
大佬 2026-10-28 14:00~14:40 需求（原话要点）：
  · 「我的持仓行业、重仓行业，你做一份深度报告，代码里都要有」
  · 「如果你选出独立强势的行业了，然后你把它那个个股每一份用缠论分析一下子」
  · 「出现买点的个股，你推荐一下那里边。因为有好几个股票出现买点，或者出现突破中枢
     或者什么的，都要说一下的。这几个都要用程序来生成」
  · 14:40 定稿口径：「两种口径都要，你列出来…每个行业都有两个股票吧？
     然后用缠论过滤一遍，这样的话你就总结出符合条件的股票，你列个表。
     股票名称第一列，第二列是所属哪一类的行业？是那个超额动量的、拓扑聚集的，
     还是什么行业？然后第三点是缠论的什么买点类型」
  · 「股票你要发名字，不要发代码」

════════════════════════════════════════════════════════════════════
两个口径（都列，不互相替代）
  口径 A · 超额动量：up_idio（κI>0 且较 5 日上升） & α20 > 1% & 今日超额 > 0.3%
      → 含义「剔掉大盘因子后仍独立跑赢，且今天还在赢」= 当下就有资金在买
  口径 B · 拓扑聚集：20日动量 > 0 & κR > 0 & κI > 0
      → 含义「20 天在涨 + 两个口径都成簇（资金抱团、结构没撕裂）」

个股层（每个行业取前 2 只，缠论过滤）
  🟢 推荐：
      · 三买区：zpos=='above' 且 stage 含「三买区」（买点 tag 有三买/一买都算）
      · 二买区·底背驰：zpos=='in' 且 stage 含「二买区」且 div=='底背驰'
      · 突破中枢：zpos=='above' 但无新鲜买点（纯位置突破）
  🔴 排除：zpos=='below'（趋势破坏）/ div=='顶背驰' / stage 含「二卖区」「三卖结构」
  排序：三买区 > 二买区·底背驰 > 突破中枢；同类按买点 age 升序（越新越前）

════════════════════════════════════════════════════════════════════
⚠️ 2026-10-28 踩坑记录（别重犯）
  1. `chan_smc_index.fetch_daily()` 内部写死 `pro.index_daily()`（指数接口），
     传个股代码返回 None → 个股取数必须自己写，用 `tushare_init.ts_call('daily')`
  2. `chan_read` 返回的 `cur` 是「合并K线的高点」不是收盘价，
     现价一律从 `df['close'].iloc[-1]` 取
  3. `chan_read` 的 `active` 字段阈值太严（实测 24 个命中只有 1 个 True），
     新鲜度改用 `age`：≤25 才算有效买点
  4. `.cache/weekly_zs/sw_member.json` 只有 15 只成分股（残缺，别用）；
     完整映射用 `longxia_pick.load_members()`（131 个行业）

用法：
  python3 strategies/holding_deep.py                  # 出报告 + 推飞书/QQ
  python3 strategies/holding_deep.py --no-send        # 只打印
  python3 strategies/holding_deep.py --per-ind 3      # 每行业取 3 只（默认 2）
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
from concurrent.futures import ThreadPoolExecutor

WS = "/root/.openclaw/workspace"
sys.path.insert(0, os.path.join(WS, "bin"))

import numpy as np
import pandas as pd

CACHE = os.path.join(WS, ".cache", "holding_deep")
DAILY = os.path.join(CACHE, "daily")
os.makedirs(DAILY, exist_ok=True)
OUT_DIR = os.path.join(WS, "memory", "holding_deep_reports")
os.makedirs(OUT_DIR, exist_ok=True)

NAMES_CSV = os.path.join(WS, ".cache", "stock_names.csv")
QQ_TARGET = os.environ.get("HD_QQ_TARGET", "qqbot:c2c:D3056302911060130E3DEEC57C618084")
FEISHU_CHAT = os.environ.get("HD_FEISHU_CHAT", "oc_0e4e3335f97551eba50a32d6ccf8c12c")
SIGN = "📎 来自 OpenClaw.Deepseek"
DAILY_TTL_MIN = int(os.environ.get("HD_DAILY_TTL_MIN", "30"))
MAX_WORKERS = int(os.environ.get("HD_WORKERS", "10"))
NEAR = 3.0          # chan_read 的 active 阈值（沿用其默认语义）


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


# ══════════════════════════════════════════ 数据层
def name_map():
    """ts_code → 股票名称（大佬要求：只发名字不发代码）"""
    if os.path.exists(NAMES_CSV) and (time.time() - os.path.getmtime(NAMES_CSV)) / 86400 < 7:
        try:
            d = pd.read_csv(NAMES_CSV)
            return dict(zip(d["ts_code"], d["name"]))
        except Exception:
            pass
    try:
        import tushare_init as ti
        d = ti.ts_call("stock_basic", exchange="", list_status="L", fields="ts_code,name")
        if d is not None and len(d):
            d[["ts_code", "name"]].to_csv(NAMES_CSV, index=False)
            return dict(zip(d["ts_code"], d["name"]))
    except Exception as e:
        log("[names] ERR %s" % str(e)[:100])
    return {}


def fetch_stock_daily(ts_code, ttl_min=None):
    """个股日线（⚠️ 不能用 chan_smc_index.fetch_daily —— 那个走指数接口）"""
    import tushare_init as ti
    p = os.path.join(DAILY, ts_code + ".csv")
    ttl = DAILY_TTL_MIN if ttl_min is None else ttl_min
    if os.path.exists(p):
        try:
            if (time.time() - os.path.getmtime(p)) / 60 <= ttl:
                d = pd.read_csv(p, parse_dates=["dt"])
                if len(d) > 120:
                    return d
        except Exception:
            pass
    d = None
    for k in range(2):            # 2026-10-28：3 次退避 → 2 次（退避 2s/5s）
        try:
            d = ti.ts_call("daily", ts_code=ts_code, start_date="20250101")
            if d is not None and len(d) > 120:
                break
        except Exception:
            d = None
        if k == 0:
            time.sleep(2)
        else:
            time.sleep(5)
    if d is None or len(d) < 120:
        # 回退旧缓存
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


def chan_of(ts_code, nm=""):
    """单只股票缠论读 + 均线（永不抛异常）"""
    import chan_smc_index as C
    try:
        d = fetch_stock_daily(ts_code)
        if d is None:
            return None, "无数据"
        r = C.chan_read(C.bars_of(d), ts_code)
        if not r.get("ok"):
            return None, "缠论失败"
        close = float(d["close"].iloc[-1])
        prev = float(d["close"].iloc[-2]) if len(d) > 1 else close
        r["close"] = close
        r["pct"] = close / prev - 1 if prev else 0.0
        r["m20"] = close / float(d["close"].iloc[-21]) - 1 if len(d) > 21 else 0.0
        r["m5"] = close / float(d["close"].iloc[-6]) - 1 if len(d) > 6 else 0.0
        c = d["close"].astype(float)
        r["ma"] = {n: float(c.tail(n).mean()) for n in (20, 40, 80, 160) if len(c) >= n}
        r["hi60"] = float(d["high"].tail(60).max())
        r["lo60"] = float(d["low"].tail(60).min())
        r["name"] = nm
        return r, None
    except Exception as e:
        return None, str(e)[:40]


def batch_chan(items):
    """items=[(ts_code, name)] → {(ts_code): (r, err)}"""
    def _one(it):
        ts, nm = it
        r, e = chan_of(ts, nm)
        return ts, r, e
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as p:
        out = list(p.map(_one, items))
    return {ts: (r, e) for ts, r, e in out}


# ══════════════════════════════════════════ 口径筛行业
def pick_industries(res):
    """两个口径的机会行业（都列）

    A · 超额动量：up_idio & α20>1% & 今日超额>0.3%
    B · 拓扑聚集：20日动量>0 & κR>0 & κI>0
    """
    sw = [r for r in (res.get("rows") or []) if r.get("kind") == "sw"]
    A = [r for r in sw
         if r.get("up_idio") and _f(r.get("alpha20")) > 0.010
         and _f(r.get("ex_today")) > 0.003]
    A.sort(key=lambda r: -_f(r.get("alpha20")))
    inA = {r["name"] for r in A}
    B = [r for r in sw
         if r["name"] not in inA
         and _f(r.get("mom20")) > 0
         and _f(r.get("k_raw")) > 0 and _f(r.get("k_idio")) > 0]
    B.sort(key=lambda r: -_f(r.get("mom20")))
    return A, B


def ind_code_map():
    import weekly_zs as W
    m = W.sw_l2_map()
    return {v: k for k, v in m.items()}


# ══════════════════════════════════════════ 缠论过滤
def chan_classify(r):
    """返回 (推荐档位, 买点描述) 或 (None, 原因)

    档位：三买区 / 二买区·底背驰 / 突破中枢
    """
    if not r:
        return None, "无数据"
    st = r.get("stage") or ""
    zp = r.get("zpos")
    dv = r.get("div")
    buys = [b for b in (r.get("buy_list") or []) if b.get("age", 99) <= 25]
    buys.sort(key=lambda b: b.get("age", 99))
    b0 = buys[0] if buys else None

    # 排除：趋势破坏 / 顶背驰 / 卖点区
    if zp == "below":
        return None, "中枢下方（趋势破坏）"
    if dv == "顶背驰":
        return None, "顶背驰"
    if "二卖" in st or "三卖" in st:
        return None, "卖点区"

    if zp == "above" and "三买区" in st:
        if b0:
            return "三买区", "%s@%.2f(%d天前)" % (b0["tag"], b0["level"], b0["age"])
        return "三买区", "已离开中枢（无新鲜买点）"
    if zp == "in" and "二买区" in st and dv == "底背驰":
        if b0:
            return "二买区·底背驰", "%s@%.2f(%d天前)" % (b0["tag"], b0["level"], b0["age"])
        return "二买区·底背驰", "回踩不破一买低点"
    if zp == "above":
        return "突破中枢", "中枢上方（无买点标记）"
    return None, "无买点"


PRI = {"三买区": 0, "二买区·底背驰": 1, "突破中枢": 2}


# ══════════════════════════════════════════ 持仓深度
def hold_deep(res, nm_map):
    """持仓行业深度诊断：拓扑打分 + 趋势位置 + 捕获率 + 内部宽度 + 重仓股穿透"""
    import cri_topology as CT
    import longxia_pick as LX
    import weekly_zs as W

    hv = CT._hold_views(res)
    if not hv:
        return []
    mem = LX.load_members()
    code_map = ind_code_map()
    holdings = {}
    try:
        for pf in (W.merge_ports() or []):
            pass
    except Exception:
        pass
    # 各实盘持仓明细（用于重仓股穿透）
    try:
        import glob
        for f in glob.glob(os.path.join(WS, "memory", "portfolio*.json")):
            pass
    except Exception:
        pass

    # 行业 → 成分股宽度
    out = []
    for x in hv:
        nm = x["name"]
        code = code_map.get(nm)
        codes = mem.get(code, []) if code else []
        width = None
        if codes:
            # 2026-10-28：内部宽度加日缓存 —— 半导体 180 只、电池 108 只，
            # 每个都拉一遍日线要 5 分钟（全流程 391s 的元凶）。同日复用。
            _wf = os.path.join(CACHE, "width_%s_%s.json" % (
                res.get("data_end") or res.get("date"), re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]", "_", nm)))
            if os.path.exists(_wf):
                try:
                    _w = json.load(open(_wf, encoding="utf-8"))
                    width = (int(_w["a"]), int(_w["b"]))
                except Exception:
                    width = None
            if width is None:
                sub = batch_chan([(c, "") for c in codes])
                n = 0
                n_ma20 = 0
                for c, (r, e) in sub.items():
                    if not r:
                        continue
                    n += 1
                    if r["close"] > r["ma"].get(20, 0):
                        n_ma20 += 1
                width = (n_ma20, n)
                try:
                    json.dump({"a": n_ma20, "b": n, "ind": nm}, open(_wf, "w", encoding="utf-8"))
                except Exception:
                    pass
        out.append(dict(x, width=width, n_mem=len(codes)))
    return out


# ══════════════════════════════════════════ 渲染
def render_text(res, hold, picks, drop_stats):
    """飞书/文字版完整报告"""
    L = []
    d10 = "%s-%s-%s" % (res["date"][:4], res["date"][4:6], res["date"][6:])
    L.append("🏭 持仓深度 + 机会行业个股缠论 · %s" % d10)
    L.append(SIGN)
    L.append("")

    # ── 一、缠论买点个股（2026-10-28 14:54 大佬：「你把你选的这几个放在前面」）
    L.append("━━ 一、缠论买点个股（每行业前 %d 只）━━" % picks["per_ind"])
    A0, B0 = picks["A"], picks["B"]
    L.append("口径 A 超额动量 %d 个：%s" % (
        len(A0), "、".join(r["name"] for r in A0) or "无"))
    L.append("口径 B 拓扑聚集 %d 个：%s" % (
        len(B0), "、".join(r["name"] for r in B0) or "无"))
    L.append("")
    if picks["rows"]:
        L.append("  %-10s %-26s %s" % ("股票", "所属行业（口径）", "缠论买点"))
        L.append("  " + "-" * 78)
        for it in picks["rows"]:
            L.append("  %-10s %-26s %s" % (
                it["name"][:10],
                ("%s · %s" % (it["ind"], it["tag"]))[:26], it["chan"] or "—"))
    else:
        L.append("  （今日无符合的个股）")
    L.append("")

    # ── 二、持仓行业 风险与机会
    L.append("━━ 二、我买的行业 · 风险与机会（拓扑口径）━━")
    if hold:
        for x in hold:
            L.append(" %s %s%s ｜风险 %s ｜机会 %s" % (
                x["verdict"], x["name"], ("(%s)" % x["pos"]) if x["pos"] else "",
                x["risk_lv"], x["opp_lv"]))
            if x["risk"]:
                L.append("    ⚠ %s" % "；".join(x["risk"]))
            if x["opp"]:
                L.append("    ✨ %s" % "；".join(x["opp"]))
            if x.get("width"):
                a, b = x["width"]
                L.append("    📊 内部宽度：成分股 %d 只里 %d 只站上 MA20（%.0f%%）%s" % (
                    b, a, (a / b * 100) if b else 0,
                    "→ 普涨" if b and a / b >= 0.6 else "→ 极少数撑，不是普涨"))
    else:
        L.append("  （未识别到持仓行业）")
    L.append("")

    # ── 三、机会行业（两口径）
    A, B = picks["A"], picks["B"]
    L.append("━━ 三、机会行业（两种口径，都列）━━")
    L.append("【口径 A · 超额动量】α20>1%% 且 今日超额>0.3%% 且 κI上升 → %d 个" % len(A))
    for r in A:
        L.append("  · %s ｜α20 %s ｜今日超额 %s ｜κI %s ｜20日 %s" % (
            r["name"], _p(r["alpha20"]), _p(r["ex_today"]),
            _k(r["k_idio"]), _p(r["mom20"])))
    if not A:
        L.append("  （今日无）")
    L.append("【口径 B · 拓扑聚集】20日动量>0 且 κR>0 且 κI>0 → %d 个" % len(B))
    for r in B:
        L.append("  · %s ｜20日 %s ｜κR %s ｜κI %s ｜今日超额 %s" % (
            r["name"], _p(r["mom20"]), _k(r["k_raw"]), _k(r["k_idio"]),
            _p(r["ex_today"])))
    if not B:
        L.append("  （今日无）")
    L.append("")

    # ── 排除说明
    if drop_stats:
        L.append("━━ 排除统计 ━━")
        for k, v in drop_stats.items():
            L.append("  %s：%d 只" % (k, v))
        L.append("")

    _de = res.get("data_end") or res["date"]
    _de10 = "%s-%s-%s" % (_de[:4], _de[4:6], _de[6:]) if "-" not in str(_de) else str(_de)
    L.append("（数据截至 %s 收盘｜缠论·分型版｜程序生成，0 token）" % _de10)
    return L


def render_card(res, hold, picks, drop_stats):
    """飞书卡片 blocks"""
    d10 = "%s-%s-%s" % (res["date"][:4], res["date"][4:6], res["date"][6:])
    B = []
    # ── 2026-10-28 14:54 大佬：「你把你选的这几个放在前面」→ 个股表提到第一节
    A0, B0 = picks["A"], picks["B"]
    h = ["**一、缠论买点个股**（每行业前 %d 只）" % picks["per_ind"],
         "<font color='grey'>口径 A 超额动量 %d 个：%s</font>" % (
             len(A0), "、".join(r["name"] for r in A0) or "无"),
         "<font color='grey'>口径 B 拓扑聚集 %d 个：%s</font>" % (
             len(B0), "、".join(r["name"] for r in B0) or "无")]
    B.append({"type": "md", "text": "\n".join(h)})
    if picks["rows"]:
        try:
            import wide_ind_report as WIR
            B.append(WIR._cols(("**股票**", "**所属行业（口径）**", "**缠论买点**"),
                               (3, 6, 7), ("left", "left", "left")))
            for it in picks["rows"]:
                B.append(WIR._cols(
                    ("**%s**" % it["name"], "%s · %s" % (it["ind"], it["tag"]),
                     it["chan"] or "—"),
                    (3, 6, 7), ("left", "left", "left")))
        except Exception:
            for it in picks["rows"]:
                B.append({"type": "md", "text": "· **%s** ｜%s · %s ｜%s" % (
                    it["name"], it["ind"], it["tag"], it["chan"] or "—")})
    else:
        B.append({"type": "md", "text": "今日无符合的个股"})
    B.append({"type": "hr"})

    B.append({"type": "md", "text": "**二、我买的行业 · 风险与机会**（拓扑口径）"})
    if hold:
        for x in hold:
            L = ["**%s %s**%s ｜风险 %s ｜机会 %s" % (
                x["verdict"], x["name"], (" %s" % x["pos"]) if x["pos"] else "",
                x["risk_lv"], x["opp_lv"])]
            if x["risk"]:
                L.append("<font color='grey'>　⚠ %s</font>" % "；".join(x["risk"]))
            if x["opp"]:
                L.append("<font color='grey'>　✨ %s</font>" % "；".join(x["opp"]))
            if x.get("width"):
                a, b = x["width"]
                L.append("<font color='grey'>　📊 内部宽度 %d/%d 站上 MA20（%.0f%%）%s</font>" % (
                    a, b, (a / b * 100) if b else 0,
                    "→ 普涨" if b and a / b >= 0.6 else "→ 极少数撑"))
            B.append({"type": "md", "text": "\n".join(L)})
    B.append({"type": "hr"})

    A, Bk = picks["A"], picks["B"]
    t = ["**三、机会行业（两种口径）**",
         "**口径 A · 超额动量**（α20>1%% 且 今日超额>0.3%% 且 κI上升）：**%d 个**" % len(A)]
    for r in A:
        t.append("· 🌙 **%s** ｜α20 %s ｜今日超额 %s ｜κI %s ｜20日 %s" % (
            r["name"], _p(r["alpha20"]), _p(r["ex_today"]),
            _k(r["k_idio"]), _p(r["mom20"])))
    if not A:
        t.append("· 今日无")
    t.append("**口径 B · 拓扑聚集**（20日动量>0 且 κR>0 且 κI>0）：**%d 个**" % len(Bk))
    for r in Bk:
        t.append("· 🌙 **%s** ｜20日 %s ｜κR %s ｜κI %s ｜今日超额 %s" % (
            r["name"], _p(r["mom20"]), _k(r["k_raw"]), _k(r["k_idio"]),
            _p(r["ex_today"])))
    if not Bk:
        t.append("· 今日无")
    B.append({"type": "md", "text": "\n".join(t)})
    B.append({"type": "hr"})

    _de = res.get("data_end") or res["date"]
    _de10 = "%s-%s-%s" % (_de[:4], _de[4:6], _de[6:]) if "-" not in str(_de) else str(_de)
    B.append({"type": "note", "text": "数据截至 %s 收盘 ｜ 缠论·分型版 ｜ 程序生成，0 token" % _de10})
    return B


def render_qq(res, hold, picks):
    """QQ 精简：**缠论买点个股放最前** + 高风险/可跟行业（2026-10-28 14:54 大佬要求）"""
    L = ["⭐ 重点提醒 · 缠论买点个股（程序生成）"]
    A0, B0 = picks["A"], picks["B"]
    L.append("机会行业｜A超额动量 %d 个：%s" % (
        len(A0), "、".join(r["name"] for r in A0) or "无"))
    L.append("机会行业｜B拓扑聚集 %d 个：%s" % (
        len(B0), "、".join(r["name"] for r in B0) or "无"))
    L.append("")
    if picks["rows"]:
        for it in picks["rows"][:14]:
            L.append("· %s ｜%s · %s ｜%s" % (
                it["name"], it["ind"], it["tag"], it["chan"] or "—"))
    else:
        L.append("· 今日无符合的个股")
    L.append("")
    L.append("——— 持仓风险与机会 ———")
    if hold:
        red = [x for x in hold if x["verdict"].startswith("🔴")]
        ok = [x for x in hold if x["verdict"].startswith("🟢")]
        L.append("")
        if red:
            L.append("🔴 该减：%s" % "、".join(
                "%s(%s)" % (x["name"], x["pos"]) if x["pos"] else x["name"] for x in red))
        if ok:
            L.append("🟢 可跟：%s" % "、".join(x["name"] for x in ok))
    return L


# ══════════════════════════════════════════ 推送
def push_feishu(blocks, title):
    try:
        import feishu_card as FC
        ok = FC.send_card(title, blocks, chat=FEISHU_CHAT, template="turquoise", sign=SIGN)
        log("[feishu] %s" % ("ok" if ok else "fail"))
        return bool(ok)
    except Exception as e:
        log("[feishu] ERR %s" % str(e)[:150])
        return False


def push_qq(text):
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


# ══════════════════════════════════════════ 主流程
def run(date=None, per_ind=2, max_ind=8, verbose=True):
    t0 = time.time()
    import cri_topology as CT
    import longxia_pick as LX

    res = CT.analyze(date=date, full_pool=True)
    if not res.get("ok"):
        return {"ok": False, "err": res.get("err")}

    nm_map = name_map()
    mem = LX.load_members()
    code_map = ind_code_map()

    A, B = pick_industries(res)
    if verbose:
        log("[口径A 超额动量] %d 个：%s" % (len(A), "、".join(r["name"] for r in A) or "无"))
        log("[口径B 拓扑聚集] %d 个：%s" % (len(B), "、".join(r["name"] for r in B) or "无"))

    # 每行业取前 N 只（缠论过滤后）
    rows, drop_stats = [], {}
    targets = [(r, "A 超额动量") for r in A[:max_ind]] + [(r, "B 拓扑聚集") for r in B[:max_ind]]
    for r, tag in targets:
        nm = r["name"]
        code = code_map.get(nm)
        codes = mem.get(code, []) if code else []
        if not codes:
            log("[%s] 无成分股映射，跳过" % nm)
            continue
        sub = batch_chan([(c, nm_map.get(c, c)) for c in codes])
        cand = []
        for c, (rr, e) in sub.items():
            if e:
                drop_stats[e] = drop_stats.get(e, 0) + 1
                continue
            lvl, why = chan_classify(rr)
            if not lvl:
                drop_stats[why] = drop_stats.get(why, 0) + 1
                continue
            age = 99
            bl = [b for b in (rr.get("buy_list") or []) if b.get("age", 99) <= 25]
            if bl:
                age = min(b.get("age", 99) for b in bl)
            cand.append(dict(name=nm_map.get(c, c), code=c, ind=nm, tag=tag,
                             chan=lvl, why=why, pri=PRI.get(lvl, 9), age=age,
                             close=rr["close"], pct=rr["pct"], m20=rr["m20"]))
        cand.sort(key=lambda x: (x["pri"], x["age"], -x["m20"]))
        take = cand[:per_ind]
        if verbose:
            log("[%s·%s] 成分 %d → 符合 %d → 取 %d：%s" % (
                nm, tag, len(codes), len(cand), len(take),
                "、".join(x["name"] for x in take) or "无"))
        rows += take

    # 汇总排序
    rows.sort(key=lambda x: (PRI.get(x["chan"], 9), x["age"], -x["m20"]))
    picks = {"A": A, "B": B, "rows": rows, "per_ind": per_ind}

    hold = []
    try:
        hold = hold_deep(res, nm_map)
    except Exception as e:
        log("[hold] ERR %s" % str(e)[:150])

    out = {"ok": True, "res": res, "hold": hold, "picks": picks,
           "drop": drop_stats, "elapsed": round(time.time() - t0, 1)}
    if verbose:
        log("[耗时 %.1fs ｜ 持仓行业 %d ｜ 最终个股 %d]" % (
            out["elapsed"], len(hold), len(rows)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--per-ind", type=int, default=2, help="每行业取几只（默认 2）")
    ap.add_argument("--max-ind", type=int, default=8, help="每口径最多几个行业")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--qq-only", action="store_true")
    ap.add_argument("--feishu-only", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    r = run(date=a.date, per_ind=a.per_ind, max_ind=a.max_ind)
    if not r.get("ok"):
        log("FAIL: %s" % r.get("err"))
        return 1

    res, hold, picks = r["res"], r["hold"], r["picks"]
    lines = render_text(res, hold, picks, r["drop"])
    print("\n".join(lines))

    if not a.no_write:
        d8 = res["date"]
        # 2026-10-28：把 picks 缓存下来，供 19:40 报告重点提醒复用（避免重复跑 200 只缠论）
        try:
            _cp = os.path.join(CACHE, "picks_%s.json" % d8)
            json.dump({"date": d8, "rows": picks["rows"],
                       "A": [r["name"] for r in picks["A"]],
                       "B": [r["name"] for r in picks["B"]],
                       "ts": time.time()},
                      open(_cp, "w", encoding="utf-8"), ensure_ascii=False, default=float)
            log("[cache] picks → %s" % _cp)
        except Exception as e:
            log("[cache] ERR %s" % str(e)[:100])
        try:
            open(os.path.join(OUT_DIR, "%s_deep.md" % d8), "w", encoding="utf-8").write(
                "\n".join(lines))
            json.dump({"hold": [{k: v for k, v in x.items() if k != "risk" and k != "opp"}
                                for x in hold],
                       "picks": picks["rows"], "drop": r["drop"]},
                      open(os.path.join(OUT_DIR, "%s_deep.json" % d8), "w",
                           encoding="utf-8"), ensure_ascii=False, default=float)
        except Exception as e:
            log("[write] ERR %s" % str(e)[:100])

    if a.no_send:
        return 0

    d10 = "%s-%s-%s" % (res["date"][:4], res["date"][4:6], res["date"][6:])
    if not a.qq_only:
        blocks = render_card(res, hold, picks, r["drop"])
        title = "🏭 持仓深度 + 机会个股缠论 · %s ｜ 来自 OpenClaw.Deepseek" % d10
        try:
            sz = len(json.dumps({"card": blocks}, ensure_ascii=False).encode())
            log("[card] %d blocks / %.1f KB" % (len(blocks), sz / 1024))
        except Exception:
            pass
        push_feishu(blocks, title)
    if not a.feishu_only:
        push_qq("\n".join(render_qq(res, hold, picks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
