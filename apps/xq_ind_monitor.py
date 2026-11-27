#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实盘所属行业 · 日线/周线 中枢 + 买卖点监控（每个实盘各自一份）
================================================================================
大佬 2026-10-26 需求原话：
  「就关注我这个所属行业，它出现日线级别或者周线级别的突破中枢，或者是涨破中枢，
   都要发邮件提醒。如果这两个行业出现买卖点，二买、类二买、三买、二卖、类二卖、三卖，
   也要发邮件提醒。就是晚上不有一个那个实盘总结吗？那里边不说了所属行业了吗？
   你把这个东西改一下子，就每一个实盘都加上对当前实盘的那个所属行业的进行监控，
   买卖机会风险监控哦……把这几个行业的风险和机会放在前面，放在指数的后面。」

口径（唯一，别改）：
  · 范围    = **当前实盘（默尔根 / 亚丽 各自独立）持仓股票所属行业**
              A股 → tushare `index_member_all` 取申万二级 + 三级
              港股 → 本机无港股申万分类，按同业对标表 `xq_ind_map.HK_SW` 映射
              ETF/LOF 跳过（不是行业）
  · 级别    = **日线 + 周线**（周线由日线 W-FRI 重采样，只用已收盘周）
  · 版本    = 缠论·分型版（包含处理→顶底分型→笔→中枢ZG/ZD→背驰），复用 `chan_smc_index.chan_read`
  · 信号①   = **中枢突破/跌破**：现价 > ZG → 🌙突破中枢；< ZD → ⭐跌破中枢（日/周分别判）
  · 信号②   = **买卖点**（只认这六个，且必须"新鲜"，即最近 MAX_AGE 根K线内）：
              🌙 二买 / 类二买 / 三买      ⭐ 二卖 / 类二卖 / 三卖
  · 类二买/类二卖定义（本机口径，代码里写死，报告里会注明）：
              **类二买** = 连续两个下跌笔，后一个低点 **高于** 前一个低点（抬高低点），
                          且不是严格意义的二买（没有一买/离一买太远），现价在该低点 +3% 以内
              **类二卖** = 对称：后一个高点 **低于** 前一个高点（压低高点），现价在该高点 -3% 以内
              → 也就是「次级别二买/二卖结构」，缠论里常叫类二买/类二卖

去重：
  · 状态文件 `memory/xq_ind_monitor_state.json`，键 `<组合>|<行业>|<级别>`
  · **只在出现"新"信号时发邮件**：新买卖点签名（如 二买@1234.56）或中枢位置变化
  · 同一信号不会重复发；信号消失后再次出现会重新发（真实发生的事）

用法：
  python3 bin/xq_ind_monitor.py                       # 全部组合，只打印
  python3 bin/xq_ind_monitor.py --portfolio moergen    # 只看一个组合
  python3 bin/xq_ind_monitor.py --mail                 # 有新信号就发邮件
  python3 bin/xq_ind_monitor.py --mail --force-mail    # 无新信号也发（发全量快照）
  python3 bin/xq_ind_monitor.py --dry-mail             # 打印邮件正文不发
"""
import os
import sys
import json
import time
import socket
import argparse
import datetime

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import pandas as pd

import chan_smc_index as C
import xq_portfolio as PF
import weekly_zs as W           # 复用：持仓→申万映射、sw_daily 取数、申万名称表

STATE = os.path.join(WS, "memory", "xq_ind_monitor_state.json")
CACHE = os.path.join(WS, ".cache", "xq_ind_monitor")
os.makedirs(CACHE, exist_ok=True)

# 只监控这六个买卖点（用户点名）
BUY_TAGS = ("二买", "类二买", "三买")
SELL_TAGS = ("二卖", "类二卖", "三卖")
# 类二买/类二卖：现价与结构的偏离容忍度（%）
NEAR_PCT = 3.0
# 申万三级：合计权重 ≥ 该值才单独监控（控制请求数与噪声）
L3_MIN_W = float(os.environ.get("XQ_INDL3_MIN_W", "8"))

LEVELS = (("day", "日线"), ("week", "周线"))
MAX_AGE = {"day": 25, "week": 12}     # 与 chan_smc_index 一致：多少根K线内算"新鲜"

MAIL_TO = os.environ.get("XQ_IND_MAIL_TO") or None   # 默认走 scripts/email_config.json


def log(*a):
    print(*a, flush=True)


# ────────────────────────── 类二买 / 类二卖 ──────────────────────────
def _segs(bars):
    """返回缠论笔段（复用 chan_smc_index 的分型版实现）"""
    mb = C.merge_bars(bars)
    if len(mb) < 12:
        return None, None
    bi = C.build_bi(mb)
    if len(bi) < 4:
        return None, None
    return C.segs_from_bi(bi), mb


def lei_points(bars, cur, near=NEAR_PCT):
    """类二买 / 类二卖（本机口径：抬高低点 / 压低高点，且非严格二买二卖）

    ⚠️ 2026-10-26 收紧（原来太松，同一个行业会同时出「类二买」和「类二卖」，自相矛盾）：
      · **类二买** 只在「当前是下跌笔（正在回调）」时才算 —— 回调低点高于前一个下跌笔低点
      · **类二卖** 只在「当前是上涨笔（正在反抽）」时才算 —— 反抽高点低于前一个上涨笔高点
      这正是缠论里类二买/类二卖的原意：**当前这一笔就是那个次级别二买/二卖点**。

    返回 (类二买list[(tag,price,age)], 类二卖list[(tag,price,age)])
    """
    segs, mb = _segs(bars)
    if not segs:
        return [], []
    n_mb = len(mb) - 1
    downs = [(k, s) for k, s in enumerate(segs) if not s["up"]]
    ups = [(k, s) for k, s in enumerate(segs) if s["up"]]
    out_b, out_s = [], []
    last_is_up = bool(segs[-1]["up"])

    # 类二买：连续两个下跌笔，后低 > 前低（抬高低点），且现价在低点 +near% 以内
    if downs and not last_is_up:                   # 当前笔必须是下跌笔（正在回调）
        for i in range(1, len(downs)):
            _, prev = downs[i - 1]
            k, cur_d = downs[i]
            if k != len(segs) - 1:                 # 必须是**当前**这一笔
                continue
            if cur_d["to"] > prev["to"]:
                d = (cur - cur_d["to"]) / cur_d["to"] * 100
                if 0 <= d <= near:                 # 现价就在这个抬高的低点上方一点
                    out_b.append(("类二买", round(cur_d["to"], 2),
                                  max(0, n_mb - cur_d["i1"])))

    # 类二卖：连续两个上涨笔，后高 < 前高（压低高点），且现价在高点 -near% 以内
    if ups and last_is_up:                         # 当前笔必须是上涨笔（正在反抽）
        for i in range(1, len(ups)):
            _, prev = ups[i - 1]
            k, cur_u = ups[i]
            if k != len(segs) - 1:
                continue
            if cur_u["to"] < prev["to"]:
                d = (cur_u["to"] - cur) / cur_u["to"] * 100
                if 0 <= d <= near:
                    out_s.append(("类二卖", round(cur_u["to"], 2),
                                  max(0, n_mb - cur_u["i1"])))

    return out_b, out_s


def merge_points(buy_list, sell_list):
    """合并 chan_read 的真买卖点 + 类二买/类二卖，并做优先级去重。

    去重规则（避免同一方向重复刷）：
      · 有「二买」就不再显示「类二买」（二买是严格点，类二是它的弱化版）
      · 有「二卖」就不再显示「类二卖」
    返回 (buys, sells) 均为 [(tag, price, age)]，按 age 升序
    """
    def _norm(lst):
        out = []
        for x in lst or []:
            if isinstance(x, dict):
                if x.get("fresh") is False:
                    continue
                out.append((x.get("tag"), x.get("level"), x.get("age", 0)))
            else:
                out.append(tuple(x[:3]))
        return [t for t in out if t[0] and t[1] is not None]

    def _dedup(lst):
        tags = {t[0] for t in lst}
        out, seen = [], set()
        for tag, px, age in sorted(lst, key=lambda x: x[2]):
            if tag == "类二买" and "二买" in tags:
                continue
            if tag == "类二卖" and "二卖" in tags:
                continue
            if tag in seen:
                continue
            seen.add(tag)
            out.append((tag, px, age))
        return out

    return _dedup(_norm(buy_list)), _dedup(_norm(sell_list))


# ────────────────────────── 单个标的 × 单个级别 ──────────────────────────
def analyse_level(bars, lv, cur):
    """返回该级别判定：{ok, zpos, zg, zd, buys:[(tag,px,age)], sells:[...]}"""
    if not bars or len(bars) < 25:
        return {"ok": False, "why": "K线不足 %d 根" % (0 if not bars else len(bars))}
    r = C.chan_read(bars, lv)
    if not r.get("ok"):
        return {"ok": False, "why": "无中枢"}
    zs = r.get("zs")
    zg = round(float(zs[1]), 2) if zs else None
    zd = round(float(zs[0]), 2) if zs else None
    zpos = None
    if zs:
        zpos = "above" if cur > zg else ("below" if cur < zd else "in")

    maxage = MAX_AGE.get(lv, 25)
    buys, sells = [], []

    # ① 真买点里的 二买 / 三买
    for b in r.get("buy_list") or []:
        if b["tag"] in ("二买", "三买") and b.get("fresh"):
            buys.append((b["tag"], b["level"], b["age"]))
    for s in r.get("sell_list") or []:
        if s["tag"] in ("二卖", "三卖") and s.get("fresh"):
            sells.append((s["tag"], s["level"], s["age"]))

    # ② 类二买 / 类二卖（本机口径）
    lb, ls = lei_points(bars, cur)
    for t, px, age in lb:
        if age <= maxage:
            buys.append((t, px, age))
    for t, px, age in ls:
        if age <= maxage:
            sells.append((t, px, age))

    # 去重（同 tag 只留最新）
    def _dedup(lst):
        seen, out = set(), []
        for t, px, age in sorted(lst, key=lambda x: x[2]):
            if t in seen:
                continue
            seen.add(t)
            out.append((t, px, age))
        return out

    return {"ok": True, "zpos": zpos, "zg": zg, "zd": zd,
            "buys": _dedup(buys), "sells": _dedup(sells),
            "n": len(bars)}


def analyse_target(df, label, kind, weight=None, extra=None):
    """df=日线（含 dt/open/high/low/close）。日线 + 周线各判一次"""
    close = float(df["close"].iloc[-1])
    day_bars = C.bars_of(df)
    _w, done = W.weekly_of(df) if hasattr(W, "weekly_of") else (None, None)
    if done is None:
        d = df.set_index("dt")
        w = pd.DataFrame({
            "open": d["open"].resample("W-FRI").first(),
            "high": d["high"].resample("W-FRI").max(),
            "low": d["low"].resample("W-FRI").min(),
            "close": d["close"].resample("W-FRI").last()}).dropna().reset_index()
        done = w[w["dt"] <= df["dt"].max()]
    week_bars = C.bars_of(done)

    o = {"label": label, "kind": kind, "close": round(close, 2),
         "weight": None if weight is None else round(weight, 2),
         "last_day": str(df["dt"].max().date()),
         "last_week": str(done["dt"].iloc[-1].date()) if len(done) else "-"}
    if extra:
        o.update(extra)
    o["day"] = analyse_level(day_bars, "day", close)
    o["week"] = analyse_level(week_bars, "week", close)
    return o


# ────────────────────────── 组合 → 行业列表 ──────────────────────────
def industries_of(profile):
    """返回 [(申万名, code, kind('ind'|'ind3'), 权重, 说明dict)]"""
    f = os.path.join(WS, profile["snap"])
    inds = W.holdings_industries(f)
    l2m = W.sw_l2_map()
    rev2 = {v: k for k, v in l2m.items()}
    out = []
    for ind, v in sorted(inds.items(), key=lambda x: -x[1]["w"]):
        code = rev2.get(ind)
        if not code:
            continue
        out.append((ind, code, "ind", v["w"],
                    {"stocks": [(a, round(b, 2)) for a, b, _, _ in
                                sorted(v["stocks"], key=lambda z: -z[1])[:5]],
                     "l3w": {k: round(x, 2) for k, x in v["l3"].items()}}))
    # 申万三级（权重够大才单独盯）
    l3m = W.sw_l3_map()
    rev3 = {v: k for k, v in l3m.items()}
    l3agg = {}
    for ind, v in inds.items():
        for l3n, l3w in (v.get("l3") or {}).items():
            o = l3agg.setdefault(l3n, {"w": 0.0, "own2": ind, "stocks": []})
            o["w"] += l3w
            for nm, w, l2, l3 in v["stocks"]:
                if l3 == l3n:
                    o["stocks"].append((nm, w))
    for l3n, v in sorted(l3agg.items(), key=lambda x: -x[1]["w"]):
        if v["w"] < L3_MIN_W:
            continue
        code = rev3.get(l3n)
        if not code:
            continue
        out.append((l3n, code, "ind3", v["w"],
                    {"parent": v["own2"],
                     "stocks": [(a, round(b, 2)) for a, b in
                                sorted(v["stocks"], key=lambda z: -z[1])[:5]]}))
    return out


# ────────────────────────── 状态 / 新信号 ──────────────────────────
def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def diff_new(rows, pfkey, st):
    """对比状态，返回新信号列表 [(kind,label,level,lv_name,type,text)]
    type: 'zs' = 中枢位置变化；'buy'/'sell' = 买卖点新增
    """
    ev = []
    for row in rows:
        for lv, lv_name in LEVELS:
            a = row.get(lv) or {}
            if not a.get("ok"):
                continue
            key = "%s|%s|%s" % (pfkey, row["label"], lv)
            prev = st.get(key) or {}
            # ① 中枢位置
            pz = prev.get("zpos")
            if a.get("zpos") and pz and pz != a["zpos"]:
                ev.append((row["kind"], row["label"], lv, lv_name, "zs", (pz, a["zpos"])))
            # ② 买卖点（按签名去重）
            pb = {tuple(x[:2]) for x in (prev.get("buys") or [])}
            ps = {tuple(x[:2]) for x in (prev.get("sells") or [])}
            for t, px, age in a.get("buys") or []:
                if (t, px) not in pb:
                    ev.append((row["kind"], row["label"], lv, lv_name, "buy", (t, px, age)))
            for t, px, age in a.get("sells") or []:
                if (t, px) not in ps:
                    ev.append((row["kind"], row["label"], lv, lv_name, "sell", (t, px, age)))
            st[key] = {"zpos": a.get("zpos"), "zg": a.get("zg"), "zd": a.get("zd"),
                       "buys": [[t, px, age] for t, px, age in (a.get("buys") or [])],
                       "sells": [[t, px, age] for t, px, age in (a.get("sells") or [])],
                       "ts": int(time.time())}
    return ev


def save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        log("[WARN] 状态写入失败 %s" % str(e)[:80])


# ────────────────────────── 渲染 ──────────────────────────
TAG = {"above": "🌙突破中枢", "below": "⭐跌破中枢", "in": "↪中枢内"}
LVSHORT = {"day": "日", "week": "周"}


def _lv_brief(a):
    if not a.get("ok"):
        return "数据不足"
    s = TAG.get(a.get("zpos"), "-")
    bs = "、".join("%s@%.2f" % (t, px) for t, px, _ in (a.get("buys") or []))
    ss = "、".join("%s@%.2f" % (t, px) for t, px, _ in (a.get("sells") or []))
    extra = []
    if bs:
        extra.append("🌙" + bs)
    if ss:
        extra.append("⭐" + ss)
    return s + ("｜" + " ".join(extra) if extra else "")


def section_text(profile, rows, evs=None, max_n=10):
    """晚报里用的段落：放在五大宽基之后"""
    lab = profile["label"]
    L = ["【🏭 持仓行业监控 · %s · 日线+周线】" % lab]
    L.append("  口径：缠论·分型版｜日线 + 周线各判一次｜🌙突破中枢/买点 ⭐跌破中枢/卖点")
    L.append("  买卖点只认：二买 / 类二买 / 三买 / 二卖 / 类二卖 / 三卖"
             "（类二=抬高低点/压低高点的次级别二买二卖结构）")
    if not rows:
        L.append("  （未识别到持仓行业）")
        return "\n".join(L)

    # 机会：突破中枢 + 买点；风险：跌破中枢 + 卖点
    opp, risk = [], []
    for r in rows:
        for lv, lv_name in LEVELS:
            a = r.get(lv) or {}
            if not a.get("ok"):
                continue
            if a.get("zpos") == "above":
                opp.append((r, lv_name, "🌙突破中枢（%s）" % lv_name))
            if a.get("zpos") == "below":
                risk.append((r, lv_name, "⭐跌破中枢（%s）" % lv_name))
            for t, px, age in a.get("buys") or []:
                opp.append((r, lv_name, "🌙%s %s @%.2f（%d根前）" % (t, lv_name, px, age)))
            for t, px, age in a.get("sells") or []:
                risk.append((r, lv_name, "⭐%s %s @%.2f（%d根前）" % (t, lv_name, px, age)))

    if evs:
        L.append("  🔔 本次新触发：")
        for kind, nm, lv, lv_name, tp, val in evs[:12]:
            if tp == "zs":
                L.append("    · %s %s %s → %s" % (lv_name, nm, TAG.get(val[0], val[0]),
                                                  TAG.get(val[1], val[1])))
            elif tp == "buy":
                L.append("    · %s %s 🌙%s @%.2f" % (lv_name, nm, val[0], val[1]))
            else:
                L.append("    · %s %s ⭐%s @%.2f" % (lv_name, nm, val[0], val[1]))

    L.append("  ── 🌙 机会（%d）──" % len(opp))
    if opp:
        for r, lv_name, txt in opp[:max_n]:
            w = "仓%.1f%%" % r["weight"] if r.get("weight") is not None else ""
            L.append("    %s %s%s ｜%s" % (r["label"], w, "", txt))
    else:
        L.append("    暂无明显机会（无突破中枢、无买点）")

    L.append("  ── ⭐ 风险（%d）──" % len(risk))
    if risk:
        for r, lv_name, txt in risk[:max_n]:
            w = "仓%.1f%%" % r["weight"] if r.get("weight") is not None else ""
            L.append("    %s %s%s ｜%s" % (r["label"], w, "", txt))
    else:
        L.append("    暂无明显风险（无跌破中枢、无卖点）")

    # 状态矩阵（一览）
    L.append("  ── 中枢位置一览 ──")
    for r in rows[:max_n]:
        w = "仓%.1f%%" % r["weight"] if r.get("weight") is not None else ""
        L.append("    %-12s %s ｜日：%s ｜周：%s" % (
            r["label"], w, _lv_brief(r.get("day") or {}), _lv_brief(r.get("week") or {})))
    if len(rows) > max_n:
        L.append("    ……另有 %d 个行业（权重更小）" % (len(rows) - max_n))
    return "\n".join(L)


def mail_body(profile, rows, evs, d10):
    L = []
    L.append("实盘行业监控 · %s · %s" % (profile["label"], d10))
    L.append("=" * 60)
    L.append("口径：缠论·分型版（包含处理→顶底分型→笔→中枢ZG/ZD→背驰）")
    L.append("级别：日线 + 周线（周线由日线 W-FRI 重采样，只用已收盘周）")
    L.append("信号：① 中枢突破(🌙上破ZG)/跌破(⭐下破ZD) ② 买卖点 二买/类二买/三买 · 二卖/类二卖/三卖")
    L.append("类二买 = 连续两跌笔后低点抬高且现价在低点+3%内；类二卖 = 对称（高点压低）")
    L.append("个股来源：申万二级（A股 index_member_all + 港股同业映射）；三级单独盯权重≥%.0f%%" % L3_MIN_W)
    L.append("")
    if evs:
        L.append("【🔔 本次新触发 %d 条】" % len(evs))
        for kind, nm, lv, lv_name, tp, val in evs:
            if tp == "zs":
                L.append("  · %s %s：%s → %s" % (lv_name, nm, TAG.get(val[0], val[0]),
                                                 TAG.get(val[1], val[1])))
            elif tp == "buy":
                L.append("  · %s %s：🌙%s @%.2f（%d根前）" % (lv_name, nm, val[0], val[1], val[2]))
            else:
                L.append("  · %s %s：⭐%s @%.2f（%d根前）" % (lv_name, nm, val[0], val[1], val[2]))
        L.append("")
    L.append(section_text(profile, rows, evs=None, max_n=30))
    L.append("")
    L.append("— 全程程序生成，0 token")
    return "\n".join(L)


# ────────────────────────── 主流程 ──────────────────────────
def _res_path(pfkey):
    return os.path.join(CACHE, "%s_%s.json" % (pfkey, datetime.date.today().strftime("%Y%m%d")))


def run(profile, use_cache=True, ttl_min=180):
    """取该组合所有行业的日/周判定。当天结果缓存 ttl_min 分钟，避免早报+晚报+邮件重复跑 tushare"""
    p = _res_path(profile["key"])
    if use_cache and os.path.exists(p):
        try:
            if time.time() - os.path.getmtime(p) < ttl_min * 60:
                d = json.load(open(p, encoding="utf-8"))
                if d.get("rows"):
                    return d["rows"], d.get("errs") or []
        except Exception:
            pass
    rows, errs = [], []
    for nm, code, kind, w, extra in industries_of(profile):
        try:
            df = W.fetch_sw_daily(code)
            if df is None or not len(df):
                errs.append("%s(无数据)" % nm)
                continue
            rows.append(analyse_target(df, nm, kind, weight=w, extra=extra))
        except Exception as e:
            errs.append("%s: %s" % (nm, str(e)[:60]))
        time.sleep(0.35)
    try:
        json.dump({"rows": rows, "errs": errs}, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, default=str)
    except Exception:
        pass
    return rows, errs


def report_section(profile, max_n=10, show_new=True):
    """晚报里用的段落：**只看不消费状态**（状态由 20:10 的邮件任务推进）

    → 晚报（19:50）能报出「本次新触发」，20:10 邮件再发同一批并推进状态；
      第二天早报就不会重复报了。
    show_new=False → 早报用：只给「机会/风险/中枢位置一览」，不重复报「新触发」
    """
    rows, _errs = run(profile)
    evs = diff_new([dict(r) for r in rows], profile["key"], load_state()) if show_new else None
    return section_text(profile, rows, evs=evs, max_n=max_n)


def send_mail(subject, body, to=None):
    import subprocess
    sender = os.path.join(WS, "scripts/send_email.py")
    cmd = [sys.executable, sender, "--subject", subject, "--body", body, "--plain"]
    if to:
        cmd += ["--to", to]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    ok = r.returncode == 0 and "sent ->" in out
    log("[mail] %s %s" % ("ok" if ok else "FAIL", "" if ok else out.strip()[-200:]))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", default=None, help="moergen / yali，默认全部")
    ap.add_argument("--mail", action="store_true", help="有新信号发邮件")
    ap.add_argument("--force-mail", action="store_true", help="无新信号也发全量快照")
    ap.add_argument("--dry-mail", action="store_true", help="只打印邮件正文")
    ap.add_argument("--no-save", action="store_true", help="不更新状态（调试）")
    a = ap.parse_args()

    profs = ([PF.PROFILES[a.portfolio]] if a.portfolio in PF.PROFILES else PF.all_profiles())
    st = load_state()
    d10 = datetime.date.today().strftime("%Y-%m-%d")

    for prof in profs:
        t0 = time.time()
        rows, errs = run(prof)
        evs = diff_new(rows, prof["key"], st)
        log(section_text(prof, rows, evs=evs))
        if errs:
            log("  ⚠️ 取数问题：%s" % "；".join(errs[:5]))
        log("[%s] 行业 %d ｜ 新信号 %d ｜ 耗时 %.1fs" % (
            prof["label"], len(rows), len(evs), time.time() - t0))

        if a.mail:
            body = mail_body(prof, rows, evs, d10)
            subj = "🔔 实盘行业监控 · %s · 日线+周线 · %s" % (prof["label"], d10)
            if not evs:
                subj = "✅ 实盘行业监控 · %s · 无新信号 · %s" % (prof["label"], d10)
            if evs or a.force_mail:
                if a.dry_mail:
                    log("\n--- MAIL DRAFT ---\n%s\n--- END ---" % body)
                else:
                    send_mail(subj, body, MAIL_TO)
        log("")

    if not a.no_save:
        save_state(st)


if __name__ == "__main__":
    main()
