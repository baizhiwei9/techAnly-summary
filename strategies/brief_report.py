#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📋 精简版报告（纯脚本 0 token）

════════════════════════════════════════════════════════════════════
大佬 2026-11-07 20:44 原话
  「如果不是我买的，我这两个实盘里边的所属行业，你就只要给我过滤我要的行业就行了。
    其他的这些有机会的，你就只要提示我有机会的行业就行了，有机会的三级行业就行了，
    而不是那个相关股票。这样的话能省好多字。另外这些东西全都要程序生成，
    不消耗 token。就是精简版的，因为精简版的就是你要告诉我那 5 大宽基指数，
    还有你选出来当前有机会的那几个行业下钻的股票。然后还有我那两个实盘里的
    那个行业和股票有没有机会？」

════════════════════════════════════════════════════════════════════
精简版只要 4 块（就这 4 块，别的不给）：
  ① 五大宽基指数 —— 一句话状态
  ② 有机会的行业 › 三级行业 —— **只报行业名和三级子行业名，不列个股**
  ③ 选出来的可买股票 —— 只有 ✅ 买点成立的（带 行业›三级 归属）
  ④ 两个实盘（默尔根 / 亚丽）—— 只保留**我自己持有的行业**，
     给 风险档/机会档 + 该行业有没有 ✅ 可买股票

数据来源：全用已有缓存/内存结果，不重新取数、不过模型
  · 宽基：memory/wide_ind_reports/<date>.json 的 wide
  · 行业 Hurst + 下钻：.cache/cri_topology/full_<date>.json / .cache/l3_drill/drill_<date>.json
  · 实盘持仓：同一份 wide_ind json 的 ports
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
import datetime

WS = "/root/.openclaw/workspace"
sys.path.insert(0, os.path.join(WS, "bin"))

REPORT_DIR = os.path.join(WS, "memory", "wide_ind_reports")
CRIT_CACHE = os.path.join(WS, ".cache", "cri_topology")
L3_CACHE = os.path.join(WS, ".cache", "l3_drill")


def log(*a):
    print(*a, flush=True)


def _load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _latest(pattern, ext=".json"):
    try:
        fs = [f for f in os.listdir(pattern[0]) if f.startswith(pattern[1]) and f.endswith(ext)]
        import re
        fs = [f for f in fs if re.match(r"^%s\d{8}%s$" % (pattern[1], ext.replace(".", r"\.")), f)]
        if not fs:
            return None
        return os.path.join(pattern[0], sorted(fs)[-1])
    except Exception:
        return None


def _p(x, nd=1):
    try:
        v = float(x)
        return "%+.*f%%" % (nd, v * 100) if abs(v) < 5 else "%+.*f%%" % (nd, v)
    except Exception:
        return "—"


def _pct(x, nd=1):
    """x 已经是百分数（如 3.5）"""
    try:
        return "%+.*f%%" % (nd, float(x))
    except Exception:
        return "—"


# ══════════════════════════════════════════ ① 五大宽基
def sec_wide(wir):
    """五大宽基 —— 一行一个，极简"""
    L = ["📐 五大宽基"]
    if not wir:
        L.append("　（无数据）")
        return L
    for w in (wir.get("wide") or []):
        nm = w.get("name", "?")
        close = w.get("close")
        wk = (w.get("week") or {}).get("stage") or ""
        dy = (w.get("day") or {}).get("stage") or ""

        def _tag(s):
            if "跌破中枢" in s or "卖" in s:
                return "⭐险"
            if "突破中枢" in s or "买" in s:
                return "🌙机"
            return "中枢内"
        pts = []
        for k in ("week", "day"):
            st = (w.get(k) or {}).get("stage") or ""
            for t in ("三买", "二买", "类二买", "一买", "三卖", "二卖", "类二卖"):
                if t in st:
                    pts.append(t)
                    break
        s = "　%-6s %s ｜周%s ｜日%s" % (
            nm, ("%.0f" % close) if close else "—", _tag(wk), _tag(dy))
        if pts:
            s += " ｜" + "/".join(dict.fromkeys(pts))
        L.append(s)
    return L


# ══════════════════════════════════════════ ② 有机会的行业 › 三级
def sec_ind_l3(crit, l3):
    """有机会的行业 › 三级 —— 只报名字，不列个股、不堆明细"""
    L = ["", "🎯 有机会的行业 › 三级"]
    if not l3 or not l3.get("ok"):
        L.append("　（无数据）")
        return L
    from collections import OrderedDict
    g = OrderedDict()
    rp = OrderedDict()
    for it in l3.get("ind") or []:
        if not it.get("ok"):
            continue
        for r in it.get("rows") or []:
            if r.get("l3_qualified"):
                g.setdefault(it["l2"], []).append(r["l3"])
            elif r.get("l3_tier") == "repair":
                rp.setdefault(it["l2"], []).append(r["l3"])
    if not g and not rp:
        L.append("　今日无")
        return L
    for l2, l3s in g.items():
        L.append("　▪ %s：%s" % (l2, "、".join(l3s)))
    if rp:
        L.append("　🟡 修复中（只观察）：%s" % "；".join(
            "%s %s" % (k, "、".join(v)) for k, v in rp.items()))
    hz = (crit or {}).get("hurst") or {}
    ups = [o["name"] for o in (hz.get("trend") or [])
           if o.get("trend_dir") == "up" and o["name"] not in g]
    if ups:
        L.append("　▪ 涨但无合格三级：%s" % "、".join(ups))
    return L


# ══════════════════════════════════════════ ③ 可买股票
def sec_watch(l3):
    """可买股票 —— 一行一只，最简"""
    L = ["", "⭐ 可买股票"]
    w = (l3 or {}).get("watch") or []
    if not w:
        L.append("　今日无")
        return L
    for x in w:
        s = "　✅ %-5s %s›%s %s@%.2f(%s根)" % (
            x.get("name"), x.get("l2"), x.get("l3"),
            x.get("buy_tag") or "—", x.get("buy_px") or 0, x.get("age") or 0)
        if x.get("active"):
            s += "·现价在点上"
        L.append(s)
    L.append("　共 %d 只" % len(w))
    return L


# ══════════════════════════════════════════ ④ 两个实盘
def _hold_verdict(r):
    """一个持仓行业 → (风险档, 一句风险, 机会档, 一句机会)

    档位：R1-R5 / O1-O5（同 cri_topology._hold_views 口径，这里精简成一句话）
    """
    f = lambda x, d=0.0: (float(x) if isinstance(x, (int, float)) else d)
    b = f(r.get("beta"), 1.0)
    m20, m5 = f(r.get("mom20")), f(r.get("mom5"))
    td, ex = f(r.get("today")), f(r.get("ex_today"))
    a20 = f(r.get("alpha20"))
    kr, kl = f(r.get("k_raw")), f(r.get("k_idio"))

    # ── 风险
    risk_n, risk_t = 0, ""
    if b >= 2.0:
        risk_n += 3
        risk_t = "β%.2f 杠杆跟跌（大盘-1%%→约%.1f%%）" % (b, -b)
    elif b >= 1.2:
        risk_n += 1
        risk_t = "β%.2f 高于大盘" % b
    if m20 <= -0.25:
        risk_n += 3
        risk_t = (risk_t + "；" if risk_t else "") + "20日 %.0f%% 深度下跌" % (m20 * 100)
    elif m20 <= -0.15:
        risk_n += 2
        risk_t = (risk_t + "；" if risk_t else "") + "20日 %.0f%% 明显走弱" % (m20 * 100)
    elif m20 <= -0.05:
        risk_n += 1
        risk_t = (risk_t + "；" if risk_t else "") + "20日 %.0f%% 小幅回落" % (m20 * 100)
    if kr < 0 or kl < 0:
        risk_n += 1
        risk_t = (risk_t + "；" if risk_t else "") + "结构撕裂"
    if a20 < 0:
        risk_n += 1
        risk_t = (risk_t + "；" if risk_t else "") + "α20 %.1f%% 跑输大盘" % (a20 * 100)
    if td < -0.03:
        risk_n += 1
        risk_t = (risk_t + "；" if risk_t else "") + "今日 %.1f%% 杀跌" % (td * 100)
    risk_lv = ("🔴高" if risk_n >= 5 else "🟠中" if risk_n >= 3 else
               "🟡偏低" if risk_n >= 1 else "🟢低")

    # ── 机会
    opp_n, opp_t = 0, ""
    if kl > 0 and kl > f(r.get("k_idio_prev")):
        opp_n += 2
        opp_t = "抱团加强（κI %+.2f↑）" % kl
    if a20 > 0.01 and ex > 0.003:
        opp_n += 2
        opp_t = (opp_t + "；" if opp_t else "") + "真超额 α20 %+.1f%%" % (a20 * 100)
    if m5 > 0 and m20 < 0:
        opp_n += 1
        opp_t = (opp_t + "；" if opp_t else "") + "短期修复 5日%+.1f%%" % (m5 * 100)
    if m20 > 0 and a20 > 0:
        opp_n += 2
        opp_t = (opp_t + "；" if opp_t else "") + "20日%+.1f%% 且跑赢" % (m20 * 100)
    opp_lv = ("🟢有" if opp_n >= 3 else "🟡弱" if opp_n >= 1 else "⚪无")
    return risk_lv, risk_t, opp_lv, opp_t


# 港股 → 申万二级（weekly_zs.HK_SW 的超集，补了漏掉的蒙牛/恒瑞H/中际旭创H）
HK_SW_X = {
    "00981": "半导体", "00522": "半导体", "06809": "半导体", "06082": "半导体",
    "01801": "化学制药", "06160": "化学制药", "09926": "化学制药", "01276": "化学制药",
    "09995": "生物制品",
    "02269": "医疗服务",
    "00700": "游戏Ⅱ", "02400": "游戏Ⅱ", "00451": "游戏Ⅱ",
    "09988": "互联网电商", "09618": "互联网电商",
    "03690": "本地生活服务Ⅱ",
    "09863": "乘用车",
    "06869": "通信设备", "03308": "通信设备",
    "02476": "元件",
    "02319": "饮料乳品",     # 蒙牛乳业
}
# ETF / LOF 不算行业（前缀判断 + 名称兜底）
_ETF_PREFIX = ("159", "161", "164", "501", "588", "510", "512", "513")


def _sw_all():
    """ts_code → {l1,l2,l3}（申万 2021，5922 只，缓存文件）"""
    try:
        return json.load(open(os.path.join(WS, ".cache", "sw_member_all.json"),
                              encoding="utf-8"))
    except Exception:
        return {}


def _l2_of(sym, name, sw):
    """一只持仓 → 申万二级名（拿不到返回 None）"""
    sym = str(sym or "")
    if sym[:2] in ("SH", "SZ"):
        if sym[2:5] in _ETF_PREFIX:
            return None
        v = sw.get(sym[2:] + "." + sym[:2])
        return (v or {}).get("l2")
    if "ETF" in (name or "") or "LOF" in (name or ""):
        return None
    return HK_SW_X.get(sym)


def _load_ports():
    """两个实盘的原始快照（含全部持仓 + 现金），读不到返回 []"""
    out = []
    try:
        import xq_portfolio as PF
        profs = PF.all_profiles()
    except Exception:
        return out
    for pr in profs:
        fp = os.path.join(WS, pr["snap"])
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        out.append({"label": pr["label"], "cash": d.get("cash"),
                    "holdings": d.get("holdings") or []})
    return out


def sec_ports(wir, l3, crit):
    """实盘持仓 —— **按股票去重 → 再汇总行业**，每个行业都带持股名+仓位

    大佬 2026-11-08 01:46：「就我的这两个实盘，你有行业，但是你要把股票名加上。
    我都不知道你是说的是哪个股票」

    ⚠️ 2026-11-08 修：以前从 wide_ind 的 ports 汇总会把「元件(36.4%)」和它的三级
    「印制电路板(27.2%)」相加 → 出现 63.6% 的假数字。现在**直接读快照持仓**，
    先按股票合并（同一只票跨组合/跨板只算一次，权重相加），再把股票挂到申万二级，
    行业权重 = 该行业所有股票权重之和 → 结构上不可能重复计。
    """
    L = ["", "💼 我的实盘 · 按股票去重后汇总行业（每个行业带持股名+仓位）"]
    rmap = {r["name"]: r for r in ((crit or {}).get("rows") or [])}
    # 可买股票：按「二级行业 + 三级子行业名」索引
    have = {}
    for x in ((l3 or {}).get("watch") or []):
        have.setdefault(x.get("l2"), []).append(x.get("name"))
        if x.get("l3"):
            have.setdefault(x["l3"], []).append(x.get("name"))

    ports = _load_ports()
    if not ports:
        L.append("　（实盘快照读不到）")
        return L
    sw = _sw_all()
    labels = [p["label"] for p in ports]
    short = {lb: lb.replace("实盘", "")[:2] for lb in labels}

    # 股票 → {组合: 权重}；行业 → {组合: 权重}；未归类
    stk = {}      # (l2, 股票) → {label: w}
    inds = {}     # l2 → {label: w}
    other = []    # 未归类的（ETF/LOF/无映射）
    cash = {}
    for pt in ports:
        lb = pt["label"]
        cash[lb] = pt.get("cash")
        for h in pt["holdings"]:
            w = float(h.get("weight") or 0)
            if w <= 0:
                continue
            nm = h.get("name") or h.get("symbol")
            l2 = _l2_of(h.get("symbol"), nm, sw)
            if not l2:
                other.append((lb, nm, w))
                continue
            stk.setdefault((l2, nm), {})
            stk[(l2, nm)][lb] = stk[(l2, nm)].get(lb, 0) + w
            inds.setdefault(l2, {})
            inds[l2][lb] = inds[l2].get(lb, 0) + w

    items = []
    for l2, by in inds.items():
        src = rmap.get(l2) or {}
        rl, rt, ol, ot = _hold_verdict(src) if src else ("—", "", "—", "")
        stocks = sorted(((nm, wby) for (l2x, nm), wby in stk.items() if l2x == l2),
                        key=lambda x: -sum(x[1].values()))
        buys = set(have.get(l2) or [])
        items.append({"name": l2, "w": sum(by.values()), "by": by, "stocks": stocks,
                      "rl": rl, "rt": rt, "ol": ol, "ot": ot, "buy": buys})
    items.sort(key=lambda x: -x["w"])

    _tot = []
    for lb in labels:
        ssum = sum(v for it in items for k, v in it["by"].items() if k == lb)
        _tot.append("%s %.1f%%" % (short[lb], ssum))
    L.append("　（%d 个行业 · %s · 股票已去重）" % (len(items), " / ".join(_tot)))

    for it in items:
        tag = "默+亚" if len(it["by"]) > 1 else short.get(list(it["by"])[0], "")
        head = "　%s %.1f%%[%s] ｜风险%s ｜机会%s" % (
            it["name"], it["w"], tag, it["rl"], it["ol"])
        held_buy = [nm for nm, _w in it["stocks"] if nm in it["buy"]]
        if held_buy:
            head += " ｜⭐持仓有买点"
        L.append(head)
        st = []
        for nm, wby in it["stocks"][:8]:
            w = sum(wby.values())
            extra = ""
            if len(wby) > 1:
                extra = "(%s)" % "+".join("%s%.1f" % (short.get(k, k), v)
                                          for k, v in wby.items())
            st.append("%s%.1f%%%s%s" % (nm, w, extra, "⭐" if nm in it["buy"] else ""))
        L.append("　　　持股：%s" % "、".join(st))
        if it["rl"].startswith("🔴") and it["rt"]:
            L.append("　　　⚠ %s" % it["rt"])

    if other:
        _o = {}
        for _lb, nm, w in other:
            _o[nm] = _o.get(nm, 0) + w
        L.append("　非个股（ETF/LOF/未归类，不计行业）：%s ｜合计 %.1f%%" % (
            "、".join("%s%.2f%%" % (k, v) for k, v in sorted(_o.items(), key=lambda x: -x[1])[:6]),
            sum(_o.values())))
    if any(v is not None for v in cash.values()):
        L.append("　现金：%s" % " / ".join("%s %.1f%%" % (short.get(k, k), float(v))
                                          for k, v in cash.items() if v is not None))

    held_buys = []
    for it in items:
        for nm, _w in it["stocks"]:
            if nm in it["buy"]:
                held_buys.append(nm)
    held_buys = list(dict.fromkeys(held_buys))
    if held_buys:
        L.append("　⭐ **我持仓里有买点**（%d 只）：%s"
                 % (len(held_buys), "、".join(held_buys)))
    return L


# ══════════════════════════════════════════ 组装
def build(date=None, max_chars=None, l3_res=None, crit_res=None):
    """生成精简版报告（list[str]）

    l3_res / crit_res：调用方可以直接传内存里的结果（最新最准），
    不传才回退读缓存文件。
    """
    d8 = date or datetime.date.today().strftime("%Y%m%d")
    # 找最近一份
    wir_p = os.path.join(REPORT_DIR, "%s.json" % d8)
    if not os.path.exists(wir_p):
        wir_p = _latest((REPORT_DIR, ""), ".json")
    crit_p = os.path.join(CRIT_CACHE, "full_%s.json" % d8)
    if not os.path.exists(crit_p):
        crit_p = _latest((CRIT_CACHE, "full_"), ".json")
    l3_p = os.path.join(L3_CACHE, "drill_%s.json" % d8)
    if not os.path.exists(l3_p):
        l3_p = _latest((L3_CACHE, "drill_"), ".json")

    wir = _load(wir_p) if wir_p else None
    crit = crit_res if crit_res else (_load(crit_p) if crit_p else None)
    l3 = l3_res if l3_res else (_load(l3_p) if l3_p else None)

    dd = (wir or {}).get("date") or d8
    d10 = "%s-%s-%s" % (dd[:4], dd[4:6], dd[6:])

    L = ["📋 精简版 · %s ｜ 📎 来自 OpenClaw.Deepseek" % d10]
    L += sec_wide(wir)
    L += sec_ind_l3(crit, l3)
    L += sec_watch(l3)
    L += sec_ports(wir, l3, crit)
    L.append("")
    # 末行固定格式
    cri_v, pos = "—", "—"
    c = (crit or {}).get("cri") or {}
    if c:
        try:
            cri_v = "%.0f" % float(c.get("CRI"))
        except Exception:
            pass
        pos = (c.get("pos") or "—").split("，")[0]
    L.append("CRI|%s|%s|%s|%s" % (
        cri_v, pos, (l3 or {}).get("n_obs", "—"), (l3 or {}).get("n_risk", "—")))
    L.append("（数据截至 %s ｜ 纯程序生成 0 token）" % d10)
    return L


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else None
    txt = "\n".join(build(d))
    print(txt)
    log("\n--- 字数 %d ---" % len(txt))
