#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基行业监控 · 晚间合并报告（一个任务、一份报告、一次发送）
================================================================================
大佬 2026-10-26 定稿需求（原话）：
  「5大宽基指数，还有两个实盘所属的行业合并成一个报告，晚上发。合并成一个任务」
  + 前面几次的约束：
    · 先周度，再日度
    · 先发五大宽基指数，再发行业
    · 有小时的话单独抽出来
    · 已跌破/已突破 → 只标星标月；**正在发生的转折点 → 发邮件**
    · 「和小时那个预警放在一块，不要新写一个任务」

→ 本脚本 = 把原来「小时线买卖点监控」+「中枢突破/跌破」+「两个实盘行业」**合成一份报告**，
  晚上（收盘后）发一次。系统 crontab 只留这一条。

报告结构（固定）：
  【🏭 实盘行业拓扑 · CRI + 拓扑几何 + 两个实盘行业】
  ━━ 🔔 本次新发生 · 转折点（发邮件）━━
  ━━ 一、实盘所属行业 · 默尔根（周线 ｜ 日线 并排）━━
  ━━ 一、实盘所属行业 · 亚丽 ━━
  ━━ 二、配图说明（行业 周线/日线 两张）━━

  ⚠️ 2026-10-26 17:55 大佬最终分工（**三个任务，各管各的**）：
    · 大盘（20:15）：PCR + 五大宽基 周线/日线 缠论拆解 + 监控（**不含小时级别**）
    · 早晨实盘（07:50）：两个实盘股票所属行业 —— 是否在中枢上下 + 有没有买卖点（日线+周线）
    · 晚上实盘（19:40，本脚本）：**股票的买卖机会/风险/监控，不做技术拆解**；
      **小时线60m 也归这里**（宽基报告下挂一块，2026-10-26 22:00 大佬确认）
    → 所以本报告**不再有**：五大宽基指数段（归大盘）、PCR 段（归大盘）；
      但宽基报告保留「小时线60m · 五大宽基买卖点」一节。

口径：缠论·分型版（包含处理→顶底分型→笔→中枢ZG/ZD→背驰）
      周线 = 日线 W-FRI 重采样，只用已收盘周
      行业 = 各实盘持仓的申万二级 + 三级（合计权重≥L3_MIN_W），A股 index_member_all / 港股同业映射
      买卖点只认 二买/类二买/三买 · 二卖/类二卖/三卖

用法：
  python3 strategies/wide_ind_report.py                  # 只打印
  python3 strategies/wide_ind_report.py --push           # 发送（QQ私聊 + 飞书私聊）
  python3 strategies/wide_ind_report.py --push --mail    # 另外在有转折时发邮件
  python3 strategies/wide_ind_report.py --no-cache       # 强制重算
  python3 strategies/wide_ind_report.py --date 2026-10-23  # 指定数据截止日（回放）
全程纯脚本，0 token。
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
import argparse
import datetime

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ══════════════════════════════════════════ 进程内缓存（避免重复算）
# _cri_brief / _cri_block 原先各跑一次 CRIT.analyze（全池 124 标的，一次几十秒）
# → 缓存后只跑一次。末行固定格式也要用 CRI 值，正好共用。
_CRI_RES = None
_L3_RES = None
_L3_TEXT = None


_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import pandas as pd

import chan_smc_index as C
import chan_signal_monitor as CSM
import weekly_zs as WZS
import xq_ind_monitor as XIM
import xq_portfolio as PF

OUT_DIR = os.path.join(WS, "memory", "wide_ind_reports")
CACHE = os.path.join(WS, ".cache", "wide_ind_report")
for _p in (OUT_DIR, CACHE):
    os.makedirs(_p, exist_ok=True)

FEISHU_CHAT = os.environ.get("WIR_FEISHU_CHAT", "oc_0e4e3335f97551eba50a32d6ccf8c12c")
QQ_TARGET = os.environ.get("WIR_QQ_TARGET", "qqbot:c2c:D3056302911060130E3DEEC57C618084")
SIGN = "📎 来自 OpenClaw.Deepseek"

# 配图（4 张：宽基周/日 + 行业周/日）——由 main() 填充
CHARTS = []
CHART_CAP = {
    ("wide", "week"): "五大宽基 · 周线中枢突破/跌破",
    ("wide", "day"): "五大宽基 · 日线中枢突破/跌破",
    ("ind", "week"): "实盘所属行业 · 周线中枢突破/跌破",
    ("ind", "day"): "实盘所属行业 · 日线中枢突破/跌破",
}


def log(*a):
    print(*a, flush=True)


def _weekly(daily):
    """日线 → 周线（W-FRI），返回已收盘周"""
    d = daily.set_index("dt")
    w = pd.DataFrame({
        "open": d["open"].resample("W-FRI").first(),
        "high": d["high"].resample("W-FRI").max(),
        "low": d["low"].resample("W-FRI").min(),
        "close": d["close"].resample("W-FRI").last()}).dropna().reset_index()
    return w[w["dt"] <= daily["dt"].max()]


def _read_level(bars, lv, price, want_points=False, closes=None):
    """一个级别的判定：中枢位置 + （可选）六个买卖点 + 一买预备

    closes: 该级别的收盘序列（给了就顺带算「一买预备 · MA40↑」，2026-10-26 大佬要求）
    """
    if not bars or len(bars) < 25:
        return {"ok": False}
    r = C.chan_read(bars, lv)
    if not r.get("ok"):
        return {"ok": False}
    zs = r.get("zs")
    if not zs:
        return {"ok": False}
    zd, zg = round(float(zs[0]), 2), round(float(zs[1]), 2)
    zpos = "above" if price > zg else ("below" if price < zd else "in")
    out = {"ok": True, "zpos": zpos, "zg": zg, "zd": zd,
           "stage": r.get("stage"), "div": r.get("div")}
    # 一买预备（可能有一买机会）—— 缠论一买未成立、但 MA40 向上且已回踩到前低附近
    if closes is not None:
        try:
            import y1_watch as Y1W
            out["prep"] = Y1W.y1_prepare(r, closes, price)
        except Exception as e:
            log("[prep] %s" % str(e)[:80])
            out["prep"] = None
    if want_points:
        raw_b, raw_s = [], []
        for b in (r.get("buy_list") or []):
            if b.get("tag") in CSM.IND_TAGS and b.get("fresh"):
                raw_b.append(b)
        for s in (r.get("sell_list") or []):
            if s.get("tag") in CSM.IND_TAGS and s.get("fresh"):
                raw_s.append(s)
        # 类二买 / 类二卖
        lb, ls = XIM.lei_points(bars, price)
        raw_b += [{"tag": t, "level": px, "age": ag} for t, px, ag in lb]
        raw_s += [{"tag": t, "level": px, "age": ag} for t, px, ag in ls]
        bu, se = XIM.merge_points(raw_b, raw_s)
        uniq = [("buy",) + x for x in bu] + [("sell",) + x for x in se]
        out["points"] = uniq
    return out


def collect(no_cache=False, ttl_min=180):
    """采集：五大宽基（日/周）+ 每个实盘行业（日/周）+ 小时线"""
    today = datetime.date.today().strftime("%Y%m%d")
    cpath = os.path.join(CACHE, "res_%s.json" % today)
    if not no_cache and os.path.exists(cpath):
        try:
            if time.time() - os.path.getmtime(cpath) < ttl_min * 60:
                d = json.load(open(cpath, encoding="utf-8"))
                if d.get("wide"):
                    return d
        except Exception:
            pass

    res = {"date": today, "wide": [], "ports": [], "h1": [], "errs": []}

    # ① 五大宽基
    for name, code in C.IDX:
        try:
            dd = CSM.fetch_daily_fresh(code)
            if dd is None or len(dd) < 80:
                res["errs"].append("%s 日线不足" % name)
                continue
            close = float(dd["close"].iloc[-1])
            wk = _weekly(dd)
            wd = _read_level(C.bars_of(wk), "week", close, closes=wk["close"].values)
            dl = _read_level(C.bars_of(dd), "day", close, closes=dd["close"].values)
            res["wide"].append({"name": name, "close": round(close, 2),
                                "week": wd, "day": dl,
                                "last_day": str(dd["dt"].max().date()),
                                "last_week": str(wk["dt"].iloc[-1].date())})
        except Exception as e:
            res["errs"].append("%s: %s" % (name, str(e)[:60]))

    # ② 小时线60m（宽基买卖点，单独一块）
    for name, _code in C.IDX:
        try:
            h6 = CSM.fetch_60_fresh(C.IDX_SH.get(name, ""))
            if h6 is None or len(h6) <= 40:
                res["h1"].append({"name": name, "ok": False})
                continue
            P = float(h6["close"].iloc[-1])
            ck = C.chan_read(C.bars_of(h6), "h1")
            if not ck.get("ok"):
                res["h1"].append({"name": name, "ok": False})
                continue
            sig, _t, _px, _age, is_buy = CSM.point_sig(ck, CSM.KEY_H1, P)
            res["h1"].append({"name": name, "ok": True, "close": round(P, 2),
                              "zpos": ck.get("zpos"), "stage": ck.get("stage"),
                              "sig": sig, "is_buy": is_buy,
                              "zs": [round(float(x), 2) for x in (ck.get("zs") or [0, 0])],
                              "last": str(h6["dt"].iloc[-1])})
        except Exception as e:
            res["h1"].append({"name": name, "ok": False, "why": str(e)[:60]})

    # ③ 每个实盘的持仓行业（先算持仓→申万，再取日线）
    l2m = WZS.sw_l2_map()
    rev2 = {v: k for k, v in l2m.items()}
    rev3 = {v: k for k, v in WZS.sw_l3_map().items()}
    for prof in PF.all_profiles():
        try:
            inds = WZS.holdings_industries(os.path.join(WS, prof["snap"]))
        except Exception as e:
            res["errs"].append("%s 持仓: %s" % (prof["label"], str(e)[:60]))
            continue
        rows = []
        targets = []
        l3agg = {}
        for ind, v in sorted(inds.items(), key=lambda x: -x[1]["w"]):
            if rev2.get(ind):
                # 2026-10-26 大佬要求：行业要带**相关股票**（不然不知道「电池」是哪只票）
                stk = [(a, round(b, 2)) for a, b, _l2, _l3 in
                       sorted(v["stocks"], key=lambda z: -z[1])[:6]]
                targets.append((ind, rev2[ind], "ind", v["w"], stk))
            for l3n, l3w in (v.get("l3") or {}).items():
                l3agg.setdefault(l3n, {"w": 0.0, "stocks": []})
                l3agg[l3n]["w"] += l3w
                for a, b, _l2, l3 in v["stocks"]:
                    if l3 == l3n:
                        l3agg[l3n]["stocks"].append((a, b))
        for l3n, ov in sorted(l3agg.items(), key=lambda x: -x[1]["w"]):
            if ov["w"] >= XIM.L3_MIN_W and rev3.get(l3n):
                stk = [(a, round(b, 2)) for a, b in
                       sorted(ov["stocks"], key=lambda z: -z[1])[:6]]
                targets.append((l3n, rev3[l3n], "ind3", ov["w"], stk))
        for nm, code, ik, w, stk in targets:
            try:
                df = WZS.fetch_sw_daily(code)
                if df is None or len(df) < 80:
                    res["errs"].append("%s(%s) 无数据" % (nm, code))
                    continue
                close = float(df["close"].iloc[-1])
                wk = _weekly(df)
                rows.append({"name": nm, "kind": ik, "code": code,
                             "weight": round(w, 2), "close": round(close, 2),
                             "stocks": stk,          # ← 相关股票 + 仓位%
                             "week": _read_level(C.bars_of(wk), "week", close, True,
                                                 closes=wk["close"].values),
                             "day": _read_level(C.bars_of(df), "day", close, True,
                                                closes=df["close"].values)})
            except Exception as e:
                res["errs"].append("%s: %s" % (nm, str(e)[:60]))
            time.sleep(0.3)
        res["ports"].append({"key": prof["key"], "label": prof["label"], "rows": rows})

    try:
        json.dump(res, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, default=str)
    except Exception:
        pass
    return res


TAG = {"above": "🌙突破中枢", "below": "⭐跌破中枢", "in": "中枢内"}


def _find_stocks(res, r):
    """从 res 里找到某行业对应的持仓股票（转折日志里只存了名字，要回查）"""
    name = r.get("name")
    pf = r.get("pf")
    for port in res.get("ports") or []:
        if pf and port.get("label") != pf:
            continue
        for row in port.get("rows") or []:
            if row.get("name") == name:
                return row.get("stocks") or []
    return []


def _stk_txt(r, n=3, sep="、"):
    """相关股票 + 仓位：宁德时代13.1%、亿纬锂能0.8%"""
    st = (r.get("stocks") or [])[:n]
    return sep.join("%s%.1f%%" % (a, b) for a, b in st)


# 港股 → 申万二级（补了 weekly_zs.HK_SW 漏掉的蒙牛/恒瑞H/中际旭创H）
_HK_SW_X = {
    "00981": "半导体", "00522": "半导体", "06809": "半导体", "06082": "半导体",
    "01801": "化学制药", "06160": "化学制药", "09926": "化学制药", "01276": "化学制药",
    "09995": "生物制品", "02269": "医疗服务",
    "00700": "游戏Ⅱ", "02400": "游戏Ⅱ", "00451": "游戏Ⅱ",
    "09988": "互联网电商", "09618": "互联网电商",
    "03690": "本地生活服务Ⅱ", "09863": "乘用车",
    "06869": "通信设备", "03308": "通信设备",
    "02476": "元件", "02319": "饮料乳品",
}
_ETF_PFX = ("159", "161", "164", "501", "588", "510", "512", "513")


def _merged_ports(res):
    """两个实盘 → 按股票去重后汇总行业（行业权重=成分股权重和，结构上不会重复计）

    2026-11-08 01:46 大佬：「两个实盘你有行业，但要把股票名加上」
    ⚠️ 不能从 res["ports"] 汇总：那里「元件36.4%」和三级「印制电路板27.2%」并列，
       相加会变成 63.6% 的假数字。
    返回 {items:[{name,w,by,stocks,[week,day]}...], tot:{组合:权重}, other:"...", cash:"..."}
    """
    sw = {}
    try:
        import json as _j
        sw = _j.load(open(os.path.join(WS, ".cache", "sw_member_all.json"), encoding="utf-8"))
    except Exception:
        sw = {}
    # 行业 → 申万二级（三级也归到二级）
    lv = {}
    for port in (res.get("ports") or []):
        for r in (port.get("rows") or []):
            lv.setdefault(r.get("name"), r)
    try:
        import xq_portfolio as PF
        profs = PF.all_profiles()
    except Exception:
        profs = []
    stk, inds, other, cash, labels = {}, {}, {}, {}, []
    for pr in profs:
        lb = pr["label"].replace("实盘", "")[:2]
        labels.append(lb)
        try:
            d = json.load(open(os.path.join(WS, pr["snap"]), encoding="utf-8"))
        except Exception:
            continue
        cash[lb] = d.get("cash")
        for h in (d.get("holdings") or []):
            w = float(h.get("weight") or 0)
            if w <= 0:
                continue
            sym = str(h.get("symbol") or "")
            nm = h.get("name") or sym
            l2 = None
            if sym[:2] in ("SH", "SZ"):
                if sym[2:5] not in _ETF_PFX:
                    l2 = (sw.get(sym[2:] + "." + sym[:2]) or {}).get("l2")
            elif "ETF" not in nm and "LOF" not in nm:
                l2 = _HK_SW_X.get(sym)
            if not l2:
                other[nm] = other.get(nm, 0) + w
                continue
            stk.setdefault((l2, nm), {})
            stk[(l2, nm)][lb] = stk[(l2, nm)].get(lb, 0) + w
            inds.setdefault(l2, {})
            inds[l2][lb] = inds[l2].get(lb, 0) + w
    items = []
    for l2n, by in inds.items():
        src = lv.get(l2n) or {}
        stocks = sorted(((nm, wb) for (l2x, nm), wb in stk.items() if l2x == l2n),
                        key=lambda x: -sum(x[1].values()))
        items.append({"name": l2n, "w": sum(by.values()), "by": by, "stocks": stocks,
                      "week": src.get("week"), "day": src.get("day")})
    items.sort(key=lambda x: -x["w"])
    tot = {lb: sum(v for it in items for k, v in it["by"].items() if k == lb)
           for lb in labels}
    _o = "、".join("%s%.2f%%" % (k, v) for k, v in sorted(other.items(), key=lambda x: -x[1])[:6])
    _c = " / ".join("%s %.1f%%" % (k, float(v)) for k, v in cash.items() if v is not None)
    return {"items": items, "tot": tot, "other": _o, "cash": _c}


def _pts_txt(lv):
    if not lv.get("ok"):
        return ""
    out = []
    for side, tag, px, _age in (lv.get("points") or []):
        out.append(("🌙" if side == "buy" else "⭐") + tag)
    return "｜" + " ".join(out) if out else ""


def _lv_cell(lv, with_pts=True, nl="\n"):
    """单元格：中枢位置 +（可选）买卖点 +（可选）可能一买

    2026-10-26 大佬要求：「如果要出现一买的机会，并且 MA40 是向上，你也要提示
    可能有一买机会出现」→ 表里加一个 🌙可能一买(MA40↑) 标记，紧跟买卖点。
    """
    if not lv or not lv.get("ok"):
        return "数据不足"
    txt = TAG.get(lv.get("zpos"), "—")
    if with_pts:
        pts = [("🌙" if side == "buy" else "⭐") + tag
               for side, tag, _px, _age in (lv.get("points") or [])]
        if lv.get("prep", {}).get("ok"):
            pts.append("🌙可能一买(MA40↑)")     # 一买预备：尚未成一买但条件快齐
        if pts:
            txt += nl + (" ".join(pts) if nl == "\n" else nl.join(pts))
    return txt


def _pcr_data():
    """成交量 PCR（从实盘报告迁到本报告，2026-10-26 大佬要求）"""
    try:
        import opt_pcr_idx as PCR
        dt = PCR.anchor_date(None, "evening")
        return PCR.compute(dt)
    except Exception as e:
        log("[pcr] ERR %s" % str(e)[:120])
        return None


def _pcr_text():
    d = _pcr_data()
    L = ["【📊 成交量 PCR · 分指数 · 5日均 · 剔除当月合约】"]
    if not d or not d.get("ok"):
        L.append("  数据暂不可用")
        return "\n".join(L)
    for r in d.get("rows") or []:
        if not r.get("ok"):
            L.append("  %-6s ：暂无期权数据" % r.get("label", ""))
            continue
        s = "  %-6s：%.4f ｜ 近%d日分位 %s%% ｜ 较前日 %s" % (
            r["label"], r["pcr"], d["window"], r["pct"],
            ("%+.4f" % r["diff"]) if r.get("diff") is not None else "—")
        L.append(s)
    m = d.get("market")
    if m:
        L.append("  %-6s：%.4f ｜ 近%d日分位 %s%% ｜ 较前日 %s" % (
            "全市场", m["pcr"], d["window"], m["pct"],
            ("%+.4f" % m["diff"]) if m.get("diff") is not None else "—"))
    return "\n".join(L)


def _dw(s):
    """显示宽度（CJK 算 2）"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in str(s))


def _pad(s, w, left=True):
    s = str(s)
    k = max(0, w - _dw(s))
    return (s + " " * k) if left else (" " * k + s)


def _tbl_text(headers, widths, rows):
    """纯文本表格（给 QQ）"""
    out = ["  " + " ".join(_pad(h, w) for h, w in zip(headers, widths)).rstrip()]
    out.append("  " + " ".join("-" * w for w in widths))
    for r in rows:
        out.append("  " + " ".join(_pad(c, w) for c, w in zip(r, widths)).rstrip())
    return out


def _cols(cells, weights, aligns=None):
    """卡片一行 column_set（表格式）"""
    aligns = aligns or ["left"] * len(cells)
    return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "small",
            "columns": [
                {"tag": "column", "width": "weighted", "weight": w,
                 "vertical_align": "center",
                 "elements": [{"tag": "div", "text": {"tag": "lark_md",
                                                       "content": str(t), "text_align": a}}]}
                for t, w, a in zip(cells, weights, aligns)]}


def chart_status(res, kinds=("ind",)):
    """配图状态：有信号的列出名字，没信号的标明「全部还在中枢内」

    kinds=("ind",) → 只报行业的 2 张（晚上实盘报告）
    kinds=("wide",) → 只报宽基的 2 张（宽基报告）
    返回 [{"title":.., "ok":bool, "n":int, "names":[..]}]
    """
    d = {("%s|%s" % (c["kind"], c["lv"])): c for c in (CHARTS or [])}
    out = []
    pool = {"wide": [("wide", "week", "五大宽基 · 周线"),
                     ("wide", "day", "五大宽基 · 日线")],
            "ind": [("ind", "week", "实盘所属行业 · 周线"),
                    ("ind", "day", "实盘所属行业 · 日线")]}
    for kind, lv, title in [x for k in kinds for x in pool[k]]:
        c = d.get("%s|%s" % (kind, lv))
        if c and c.get("n"):
            out.append({"title": title, "ok": True, "n": c["n"], "names": c["names"]})
        else:
            out.append({"title": title, "ok": False, "n": 0, "names": []})
    return out


def _cri_block(with_cri=True):
    """宏观 CRI + 行业拓扑几何（纯脚本，0 token）。永不抛异常。

    2026-10-28 大佬：「你可以合并到一块，然后把这个 CRI 放在最前面」
    → 原独立脚本 bin/cri_topology.py 的内容并进实盘行业报告最前面。
      QQ 文字走精简版（render_brief：重点提醒+CRI+名单），飞书卡片走全量。
    返回 (text_lines, card_blocks)。
    """
    if os.environ.get("WIDE_IND_CRI", "1") == "0":
        return [], []
    # 2026-10-28 大手：19:40 报告默认跑全池（124 个申万二级），WIDE_IND_FULL=0 退回 23 标的
    full = os.environ.get("WIDE_IND_FULL", "1") == "1"
    try:
        import cri_topology as CRIT
        global _CRI_RES
        res = _CRI_RES or CRIT.analyze(with_cri=with_cri, full_pool=full)
        if res.get("ok"):
            _CRI_RES = res
        if not res.get("ok"):
            return ([res.get("err") or "（拓扑计算失败，跳过）"], [])
        return (CRIT.render_lines(res, with_cri=with_cri),
                CRIT.render_blocks(res, with_cri=with_cri))
    except Exception as e:
        log("[cri] ERR %s" % str(e)[:150])
        return ["（CRI + 拓扑计算失败，跳过）"], []


def _cri_brief(with_cri=True):
    """QQ 精简版：⭐重点提醒 + CRI 结论 + 双口径名单（全量明细放飞书）。"""
    if os.environ.get("WIDE_IND_CRI", "1") == "0":
        return []
    full = os.environ.get("WIDE_IND_FULL", "1") == "1"
    try:
        import cri_topology as CRIT
        global _CRI_RES
        res = _CRI_RES or CRIT.analyze(with_cri=with_cri, full_pool=full)
        if res.get("ok"):
            _CRI_RES = res
        if not res.get("ok"):
            return ["（拓扑：%s）" % (res.get("err") or "计算失败")]
        return CRIT.render_brief(res, with_cri=with_cri)
    except Exception as e:
        log("[cri-brief] ERR %s" % str(e)[:150])
        return ["（CRI 精简版失败）"]


def _l3_drill(run=True):
    """第四步：子行业下钻（申万三级 · 等权合成 · 只挖第二步选中的机会行业）

    2026-11-07 大佬口径：「用等权重，然后只挖选中的机会行业」
    开关 WIDE_IND_L3=0 跳过。永不抛异常。返回 (text_lines, card_blocks)。
    """
    if os.environ.get("WIDE_IND_L3", "1") == "0" or not run:
        return [], []
    global _L3_RES, _L3_TEXT
    if _L3_TEXT is not None:
        return _L3_TEXT
    try:
        import l3_drill as L3
        res = L3.drill()          # None = 自动取全池扫描的 A/B/C 机会行业
        if not res.get("ok"):
            _L3_TEXT = (["（子行业下钻：%s）" % res.get("err", "无数据")], [])
            return _L3_TEXT
        _L3_RES = res
        _L3_TEXT = (L3.render_lines(res), L3.render_blocks(res))
        return _L3_TEXT
    except Exception as e:
        log("[l3] ERR %s" % str(e)[:150])
        _L3_TEXT = (["（子行业下钻失败，跳过）"], [])
        return _L3_TEXT



def _fixed_tail(part="ind"):
    """V3 末行固定格式：CRI|XX|仓位建议|可观察子环节数|风险子环节数

    大佬 2026-11-07 17:31：「2，固定」
    → V3 提示词要求的最后一行固定格式。只出现在行业报告（part=ind），
      宽基报告不掺。CRI 取 _CRI_RES，子环节数取 _L3_RES。
    """
    if part != "ind":
        return None
    cri_v, pos = "—", "—"
    cr = _CRI_RES or {}
    c = cr.get("cri") or {}
    if c:
        try:
            cri_v = "%.0f" % float(c.get("CRI"))
        except Exception:
            cri_v = "—"
        pos = (c.get("pos") or "—").split("，")[0]
    l3 = _L3_RES or {}
    n_obs = l3.get("n_obs") if l3.get("ok") else "—"
    n_risk = l3.get("n_risk") if l3.get("ok") else "—"
    return "CRI|%s|%s|%s|%s" % (cri_v, pos, n_obs, n_risk)


def build_report(res, part="ind"):
    """返回 (text, blocks)：text 给 QQ，blocks 给飞书卡片（表格式 column_set）

    part="ind"  → 🏭 实盘行业拓扑（CRI+拓扑+两个实盘行业，行业 2 张图）
    part="wide" → 📐 宽基中枢监控（五大宽基 周线/日线 中枢，宽基 2 张图）

    2026-10-26 18:11 大佬：「你也可以拆成两个报告，行业的一个报告，宽基的一个报告」
    → 同一份采集数据，出两份独立报告、各自推送、各自带图。
    """
    d = res["date"]
    d10 = "%s-%s-%s" % (d[:4], d[4:6], d[6:])
    L, B = [], []
    if part == "wide":
        L.append("📐 宽基中枢监控 · %s" % d10)
    else:
        L.append("🏭 实盘行业拓扑 · %s" % d10)
    L.append("%s" % SIGN)          # 消息头（QQ 文字里必须显式带；飞书卡片由 sign 自动插第一行）
    L.append("")

    # ── 0. 宏观 CRI + 行业拓扑（只进行业报告，放最前面）
    #  QQ 文字走精简版（重点提醒 + CRI + 名单），飞书卡片走全量
    if part != "wide":
        cri_l = _cri_brief()
        cri_b = _cri_block()[1]
        if cri_l:
            L += cri_l
            if not L or L[-1] != "":
                L.append("")
        # 第四步：子行业下钻（机会行业 → 申万三级）
        l3_l, l3_b = _l3_drill()
        if l3_l:
            L += l3_l
            L.append("")
        if l3_b:
            B += l3_b
        if cri_b:
            B += cri_b

    L.append("口径：%s ｜拓扑：子行业拓扑 V3（申万二级全池 → 三级等权下钻）" % CSM.VER_FULL)
    if part == "wide":
        L.append("级别：周线（W-FRI，只用已收盘周）+ 日线 + 小时线60m（下挂一块）"
                 " ｜ 只讲中枢突破/跌破 + 六个买卖点")
        L.append("表格：①指数×周线/日线 ｜ ②小时线60m 买卖点")
    else:
        L.append("级别：周线（W-FRI，只用已收盘周）+ 日线 ｜ 只讲买卖机会/风险/监控，不做技术拆解")
        L.append("表格：第一列=行业(仓位) ｜ 第二列=周线级别 ｜ 第三列=日线级别 ｜ 第四列=主要持股")
    L.append("标记：⭐跌破中枢/卖点（风险）｜🌙突破中枢/买点（机会）｜中枢内不着色")
    L.append("买卖点只认：二买 / 类二买 / 三买 / 二卖 / 类二卖 / 三卖"
             "（类二=抬高低点/压低高点的次级别二买二卖）")
    L.append("🌙可能一买 = 一买预备：缠论一买还没成立，但 MA40 已向上 + 已回踩到前低附近"
             "（力度也在衰减）→ 提示「可能有一买机会」，确认背驰即成真一买")
    L.append("")
    _tip = ("（本报告只看五大宽基的中枢突破/跌破；大盘 PCR 与技术拆解在 20:15 大盘任务）"
            if part == "wide" else
            "（本报告只看两个实盘所属行业，大盘/PCR/技术拆解在 20:15 大盘任务）")
    B.append({"type": "md", "text": "口径：%s%s\n周线=W-FRI，只用已收盘周 ｜ 日线%s\n"
                                     "标记：⭐跌破中枢/卖点（风险）｜🌙突破中枢/买点（机会）｜中枢内不着色\n"
                                     "买卖点只认：二买/类二买/三买/二卖/类二卖/三卖\n"
                                     "🌙可能一买 = 一买预备（MA40↑ + 回踩前低附近，还没成真一买）\n%s"
                                     % (CSM.VER_FULL,
                                        "" if part == "wide" else " ｜拓扑：子行业拓扑 V3（申万二级全池 → 三级等权下钻）",
                                        " + 小时线60m（下挂一块）" if part == "wide" else "",
                                        _tip)})

    # ── ① 转折（本报告只列自己范围内的：宽基报告只列宽基，行业报告只列行业）──
    turns = [t for t in CSM.load_turns_today()
             if (t[1].get("kind") == "wide") == (part == "wide")]
    L.append("━━ 🔔 本次新发生 · 转折点（会发邮件）━━")
    B.append({"type": "hr"})
    B.append({"type": "md", "text": "**🔔 本次新发生 · 转折点**（只有这部分发邮件）"})
    tl = []
    if turns:
        turns = sorted(turns, key=lambda t: (0 if t[1].get("level") == "week" else 1,
                                             0 if t[1].get("kind") == "wide" else 1,
                                             t[1].get("pf") or "", t[1].get("name") or ""))
        for side, r, prev, now in turns:
            who = ("%s·%s" % (r.get("pf"), r.get("name"))) if r.get("pf") else r.get("name")
            # 2026-10-26：转折点也带相关股票（不然不知道是哪个票的事）
            stk = ""
            try:
                hit = _find_stocks(res, r)
                if hit:
                    stk = "（%s）" % "、".join("%s%.1f%%" % (a, b) for a, b in hit[:3])
            except Exception:
                stk = ""
            if side == "zs":
                tl.append("  %s ｜%s：%s → %s%s" % (r.get("lv_name", ""), who,
                                                     TAG.get(prev, prev), TAG.get(now, now), stk))
            else:
                tl.append("  %s ｜%s：%s 新出现%s" % (r.get("lv_name", ""), who, now, stk))
    else:
        tl.append("  无（所有标的位置未变、无新买卖点）")
    L += tl
    B.append({"type": "md", "text": "\n".join(tl)})
    L.append("")

    # ── 一、五大宽基指数：三列表格（指数 ｜ 周线 ｜ 日线）──（仅宽基报告）
    if part == "wide":
        wrows, wrowsL = [], []
        for name, _code in C.IDX:
            w = next((x for x in res["wide"] if x["name"] == name), None)
            if not w:
                wrows.append((name, "数据不足", "数据不足"))
                wrowsL.append((name, "数据不足", "数据不足"))
                continue
            wrows.append((name, _lv_cell(w.get("week")), _lv_cell(w.get("day"))))
            wrowsL.append((name, _lv_cell(w.get("week"), nl=" ｜"),
                           _lv_cell(w.get("day"), nl=" ｜")))
        L.append("━━ 一、五大宽基指数（周线 ｜ 日线）━━")
        L += _tbl_text(("指数", "周线级别", "日线级别"), (10, 18, 18), wrowsL)
        L.append("")
        B.append({"type": "hr"})
        B.append({"type": "md", "text": "**一、五大宽基指数**（周线 ｜ 日线）"})
        B.append(_cols(("**指数**", "**周线级别**", "**日线级别**"), (3, 5, 5),
                       ("left", "left", "left")))
        for row in wrows:
            B.append(_cols(row, (3, 5, 5), ("left", "left", "left")))

        # ── 一.5、小时线60m · 五大宽基（买卖点）──
        # 2026-10-26 大佬：「小时的还是监控吧」→ 小时级别只在 19:40 宽基监控报告出现
        L.append("━━ 二、小时线60m · 五大宽基（买卖点）━━")
        h1rows = []
        for h in (res.get("h1") or []):
            if not h.get("ok"):
                L.append("  %-8s 数据不足" % h.get("name", ""))
                h1rows.append((h.get("name", ""), "数据不足", "—", "—"))
                continue
            zpos = CSM.ZPOS_TXT.get(h.get("zpos"), "") or "—"
            pt = ""
            if h.get("sig"):
                pt = "%s%s" % ("🌙" if h.get("is_buy") else "⭐", h["sig"].split("@")[0])
            zs = h.get("zs") or [0, 0]
            L.append("  %-8s %9.2f  %s｜中枢 %.2f~%.2f%s" % (
                h["name"], h["close"], zpos, zs[0], zs[1],
                ("｜" + pt) if pt else ""))
            h1rows.append((h["name"], "%.2f" % h["close"], zpos, pt or "—"))
        L.append("")
        B.append({"type": "hr"})
        B.append({"type": "md", "text": "**二、小时线60m · 五大宽基**（买卖点）"})
        B.append(_cols(("**指数**", "**收盘**", "**位置**", "**买卖点**"), (3, 3, 4, 4),
                       ("left", "center", "left", "left")))
        for row in h1rows:
            B.append(_cols(row, (3, 3, 4, 4), ("left", "center", "left", "left")))
        B.append({"type": "note",
                  "text": "🌙买点（越多越强）｜⭐卖点（越多越重）｜中枢内=还在中枢里晃、无买卖点"})

    # ── 二、实盘所属行业：三列表格（行业 ｜ 周线 ｜ 日线）──（宽基报告跳过）
    # 2026-10-28：行业报告已并进 CRI（一/二/三），行业段落顺延为四、五，配图说明为六
    # 2026-11-08 01:46 大佬：「像之前那一版就可以了，你就合并吧。另外那个报告里边，
    #   就我的这两个实盘，你有行业，但是你要把股票名加上。我都不知道你是说的是哪个股票」
    #   → 两个实盘**合并成一段**（同名行业去重、股票权重相加），每行带**全部持股名+仓位**
    _n_ind = "四" if part != "wide" else "一"
    if part != "wide":
        mp = _merged_ports(res)
        rows, rowsL = [], []
        for it in mp["items"]:
            head = "%s(%.1f%%)" % (it["name"], it["w"])
            if len(it["by"]) > 1:
                head += "默+亚"
            stk = "、".join("%s%.1f%%" % (nm, sum(by.values()))
                            for nm, by in it["stocks"][:6])
            rows.append((head, _lv_cell(it.get("week")), _lv_cell(it.get("day")), stk))
            rowsL.append((head, _lv_cell(it.get("week"), nl=" ｜"),
                          _lv_cell(it.get("day"), nl=" ｜"), stk))
        L.append("━━ %s、两个实盘 · 合并去重（%d 个行业 · %s）━━" % (
            _n_ind, len(rowsL),
            " / ".join("%s %.1f%%" % (k, v) for k, v in mp["tot"].items())))
        if not rowsL:
            L.append("  未识别到行业")
        else:
            L += _tbl_text(("行业(仓位)", "周线级别", "日线级别", "持股(仓位)"),
                           (22, 16, 16, 34), rowsL)
        L.append("")
        B.append({"type": "hr"})
        B.append({"type": "md", "text": "**%s、两个实盘 · 合并去重**（%d 个行业 · %s · 按仓位）" % (
            _n_ind, len(rowsL),
            " / ".join("%s %.1f%%" % (k, v) for k, v in mp["tot"].items()))})
        if rows:
            B.append(_cols(("**行业(仓位)**", "**周线**", "**日线**", "**持股**"),
                           (4, 4, 4, 10), ("left", "left", "left", "left")))
            for row in rows:
                B.append(_cols(row, (4, 4, 4, 10), ("left", "left", "left", "left")))
        else:
            B.append({"type": "md", "text": "未识别到行业"})
        if mp.get("other"):
            L.append("  非个股（ETF/LOF/未归类）：%s" % mp["other"])
            B.append({"type": "note", "text": "非个股（ETF/LOF/未归类，不计行业）：%s" % mp["other"]})
        if mp.get("cash"):
            L.append("  现金：%s" % mp["cash"])
            B.append({"type": "note", "text": "现金：%s" % mp["cash"]})

    # ── 配图说明（2026-10-26 大佬要求：报告和图都要有；没图 = 都还在中枢内）──
    # 2026-10-28 13:45 大佬：「行业分析的那几个图片不要发了」→ 行业报告整段配图说明去掉
    _has_chart_sec = (part == "wide") or os.environ.get("WIR_IND_CHART", "0") == "1"
    if not _has_chart_sec:
        if res.get("errs"):
            L.append("⚠️ 数据缺口：%s" % "；".join(res["errs"][:5]))
            L.append("")
        # V3 末行固定格式（只有行业报告有）——这条 return 是行业报告的**主路径**
        _tail_a = _fixed_tail(part)
        if _tail_a:
            L.append(_tail_a)
        L.append("（数据截至 %s 收盘｜程序生成，0 token）" % (
            res["wide"][0]["last_day"] if res["wide"] else d10))
        _note_a = "数据截至 %s 收盘 ｜ 程序生成，0 token" % (
            res["wide"][0]["last_day"] if res["wide"] else d10)
        if _tail_a:
            _note_a = _tail_a + "\n" + _note_a
        B.append({"type": "note", "text": _note_a})
        return "\n".join(L), B
    _ct = "宽基 × 周线/日线，共 2 张" if part == "wide" else "行业 × 周线/日线，共 2 张"
    _sec = "三" if part == "wide" else "六"
    L.append("━━ %s、配图说明（%s）━━" % (_sec, _ct))
    B.append({"type": "hr"})
    B.append({"type": "md", "text": "**%s、配图说明**（%s）" % (_sec, _ct)})
    cst = chart_status(res, kinds=("wide",) if part == "wide" else ("ind",))
    L.append("  规则：每个标的 1 个子图，**只画有中枢突破/跌破 或 可能一买(MA40↑) 的**；"
             "纯中枢内的标的不进图")
    for it in cst:
        if it["ok"]:
            L.append("  ✅ %s ｜ %d 个子图：%s" % (
                it["title"], it["n"],
                "、".join(it["names"][:8]) + ("…" if len(it["names"]) > 8 else "")))
            B.append({"type": "md", "text": "✅ **%s** ｜ %d 个子图：%s" % (
                it["title"], it["n"],
                "、".join(it["names"][:8]) + ("…" if len(it["names"]) > 8 else ""))})
        else:
            L.append("  ⬜ %s ｜ **无图 = 该级别全部标的都还在中枢内**（无突破、无跌破）" % it["title"])
            B.append({"type": "md", "text": "⬜ **%s** ｜ 无图 = 该级别全部标的都还在中枢内" % it["title"]})
    _who = "宽基" if part == "wide" else "行业"
    L.append("  （报告与 2 张配图一起发；图里 ◯圈+月亮 = 可能一买(MA40↑)；"
             "哪张没图，就说明那个级别既没有%s破中枢、也没有一买预备）" % _who)
    L.append("")
    B.append({"type": "note", "text": "报告与 2 张配图一起发；图里 ◯圈+月亮 = 可能一买(MA40↑)；"
                                       "哪张没图，就说明那个级别既没有%s破中枢、也没有一买预备" % _who})

    if res.get("errs"):
        L.append("⚠️ 数据缺口：%s" % "；".join(res["errs"][:5]))
        L.append("")
    # V3 末行固定格式（只有行业报告有）
    _tail = _fixed_tail(part)
    if _tail:
        L.append(_tail)
    L.append("（数据截至 %s 收盘｜程序生成，0 token）" % (res["wide"][0]["last_day"]
                                                if res["wide"] else d10))
    _note = "数据截至 %s 收盘 ｜ 程序生成，0 token" % (
        res["wide"][0]["last_day"] if res["wide"] else d10)
    if _tail:
        _note = _tail + "\n" + _note
    B.append({"type": "note", "text": _note})
    return "\n".join(L), B


# ────────────────────────── 推送 ──────────────────────────
def push_feishu(blocks_in, title):
    """飞书私聊：一条 interactive 卡片（表格式 blocks）+ 配图内嵌

    ⚠️ 2026-11-07 踩坑：`l3_drill.render_blocks()` 返回的是 **markdown 字符串列表**，
       而 build_report 里的 B 是 **block dict 列表**，两者拼在一起直接进来
       → feishu_card 里 `blk.get(...)` 报 `'str' object has no attribute 'get'`。
       这里统一归一化：字符串 ⇒ {"type":"md","text":<str>}。
    """
    try:
        import feishu_card as FC
        blocks = []
        for b in list(blocks_in or []):
            if isinstance(b, str):
                if b.strip():
                    blocks.append({"type": "md", "text": b})
            elif isinstance(b, dict):
                blocks.append(b)
        imgs = [c["path"] for c in (CHARTS or []) if c.get("path") and os.path.exists(c["path"])]
        ok = FC.send_card(title, blocks, imgs=(imgs or None),
                          chat=FEISHU_CHAT, template="turquoise", sign=SIGN)
        log("[feishu] %s（%d blocks）" % ("ok" if ok else "fail", len(blocks)))
        return bool(ok)
    except Exception as e:
        log("[feishu] ERR %s" % str(e)[:120])
        return False


def push_qq(text, charts=None):
    """QQ 私聊：文字一条 + 每张图单独一条（QQ 不支持卡片内嵌图）"""
    try:
        import subprocess
        r = subprocess.run(["openclaw", "message", "send", "--channel", "qqbot",
                            "--target", QQ_TARGET, "--message", text, "--json"],
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        ok = ('"messageId"' in out and '"messageId": ""' not in out) or "receipt" in out
        log("[qq] rc=%s %s" % (r.returncode, "ok" if ok else out[-160:]))
        # 配图
        for c in (charts or []):
            try:
                import push_image as PI
                r2 = PI.push(c["path"], text=CHART_CAP.get((c["kind"], c["lv"]), ""),
                             qq=True, fs=False)
                log("[qq-img] %s %s" % (os.path.basename(c["path"]), "ok" if r2 else "fail"))
                time.sleep(1.2)
            except Exception as e:
                log("[qq-img] ERR %s" % str(e)[:100])
        return ok
    except Exception as e:
        log("[qq] ERR %s" % str(e)[:120])
        return False


def push_qq_brief(rep=None):
    """QQ 发报告

    2026-11-07 20:26 大佬：「发到飞书的是交互式卡片。你另外一个，你这报告统一生成，
    统一给我呀。你这个一个个算，一个个给我，我啥时候看完啊？」
    → QQ 不再只发精简版「重点提醒」，改成**发完整报告全文**（就是飞书卡片那份 text）。
      传了 rep 就直接用（同一次生成，不重复计算）；没传才回退精简版。
    """
    try:
        import subprocess
        if rep:
            text = rep
        else:
            import cri_topology as CRIT
            full = os.environ.get("WIDE_IND_FULL", "1") == "1"
            res = CRIT.analyze(full_pool=full)
            if not res.get("ok"):
                log("[qq-brief] 跳过：%s" % (res.get("err") or "拓扑失败"))
                return False
            text = "\n".join(CRIT.render_brief(res))
        # QQ 单条有长度限制 → 按字数切段逐段发（内容一字不删）
        LIM = int(os.environ.get("QQ_MSG_LIMIT", "3500"))
        parts, cur = [], ""
        for ln in text.split("\n"):
            if len(cur) + len(ln) + 1 > LIM and cur:
                parts.append(cur)
                cur = ln
            else:
                cur = (cur + "\n" + ln) if cur else ln
        if cur:
            parts.append(cur)
        if len(parts) > 1:
            parts = ["【%d/%d】\n%s" % (i + 1, len(parts), pp)
                     for i, pp in enumerate(parts)]
        log("[qq-brief] 全文 %d 字 → %d 段" % (len(text), len(parts)))
        okn = 0
        for i, pt in enumerate(parts):
            r = subprocess.run(["openclaw", "message", "send", "--channel", "qqbot",
                                "--target", QQ_TARGET, "--message", pt, "--json"],
                               capture_output=True, text=True, timeout=180)
            out = (r.stdout or "") + (r.stderr or "")
            good = ('"messageId"' in out and '"messageId": ""' not in out) or "receipt" in out
            okn += 1 if good else 0
            log("[qq-brief] 段%d/%d rc=%s %s" % (
                i + 1, len(parts), r.returncode, "ok" if good else out[-120:]))
            time.sleep(1.5)
        return okn == len(parts)
    except Exception as e:
        log("[qq-brief] ERR %s" % str(e)[:150])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--mail", action="store_true", help="有转折时同时发邮件")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="不写存档文件")
    ap.add_argument("--no-chart", action="store_true", help="不出配图")
    ap.add_argument("--chart-only", action="store_true", help="只出图不发文字")
    ap.add_argument("--qq-only", action="store_true")
    ap.add_argument("--feishu-only", action="store_true")
    ap.add_argument("--only-wide", action="store_true", help="只发宽基报告")
    ap.add_argument("--only-ind", action="store_true", help="只发行业报告")
    a = ap.parse_args()

    t0 = time.time()
    # 先让中枢状态机跑一遍（唯一发邮件的地方），拿到本次真转折
    state = CSM.load_state()
    turns, cur_zs = [], {}
    try:
        zs_rows = CSM.scan_zs_all()
        turns, _marks, cur_zs = CSM.zs_signals(state, zs_rows)
        state.update(cur_zs)
        CSM.save_state(state)
        CSM.save_turns(turns)
    except Exception as e:
        log("[WARN] 转折判定失败 %s" % str(e)[:140])

    res = collect(no_cache=a.no_cache)
    res["turns"] = [{"side": t[0], "level": t[1].get("level"), "kind": t[1].get("kind"),
                     "pf": t[1].get("pf"), "name": t[1].get("name"),
                     "prev": t[2], "now": t[3]} for t in turns]

    global CHARTS
    import wide_ind_charts as WC

    # ── 拆成两份报告：① 宽基中枢监控 ② 实盘行业监控（2026-10-26 18:11 大佬要求）──
    jobs = []
    if not a.only_ind:
        jobs.append(("wide", "📐 宽基中枢监控"))
    if not a.only_wide:
        jobs.append(("ind", "🏭 实盘行业拓扑"))

    sent = []
    for part, base_title in jobs:
        # 每份报告只带自己的 2 张图
        # 2026-10-28 13:45 大佬：「最后那个我行业的那个行业分析的那几个图片你不要发了，不需要了」
        #   → 行业报告（part="ind"）默认不出图；宽基报告照旧。WIR_IND_CHART=1 可恢复。
        _want_chart = (not a.no_chart) and (
            part != "ind" or os.environ.get("WIR_IND_CHART", "0") == "1")
        try:
            CHARTS = (WC.build(res, kinds=(part,)) if _want_chart else [])
        except Exception as e:
            CHARTS = []
            log("[chart] ERR %s" % str(e)[:150])
        for c in CHARTS:
            log("[chart] %s %s → %s（%d 子图：%s）" % (
                c["kind"], c["lv"], os.path.basename(c["path"]), c["n"],
                "、".join(c["names"][:6]) + ("…" if len(c["names"]) > 6 else "")))
        if not CHARTS and not a.no_chart and _want_chart:
            log("[chart] %s：该级别无中枢突破/跌破 → 不出图" % base_title)
        if not _want_chart and part == "ind":
            log("[chart] %s：行业配图已按大佬要求关闭（WIR_IND_CHART=1 可恢复）" % base_title)

        rep, blocks = build_report(res, part=part)
        print(rep)
        if not a.no_write:
            try:
                fn = os.path.join(OUT_DIR, "%s_%s.md" % (res["date"], part))
                open(fn, "w", encoding="utf-8").write(rep)
            except Exception:
                pass
        sent.append((part, base_title, rep, blocks, list(CHARTS)))

    if not a.no_write:
        try:
            json.dump(res, open(os.path.join(OUT_DIR, "%s.json" % res["date"]), "w",
                                encoding="utf-8"), ensure_ascii=False, default=str)
        except Exception:
            pass

    log("")
    log("[耗时 %.1fs ｜ 宽基 %d ｜ 组合 %d ｜ 行业 %d ｜ 转折 %d]" % (
        time.time() - t0, len(res["wide"]), len(res["ports"]),
        sum(len(p["rows"]) for p in res["ports"]), len(turns)))

    # 转折才发邮件（一份邮件里包含宽基 + 行业所有转折）
    if a.mail and turns:
        try:
            body = CSM.fmt_turn_mail(turns)
            ok = CSM.send_mail("🔔 宽基行业监控 · 转折 %d 条 · %s" % (
                len(turns), datetime.datetime.now().strftime("%m-%d %H:%M")), body)
            log("[mail] %s（转折 %d 条）" % ("ok" if ok else "FAIL", len(turns)))
        except Exception as e:
            log("[mail] ERR %s" % str(e)[:120])
    elif a.mail:
        log("[mail] 无转折，不发邮件")

    if not a.push:
        return 0

    d8 = "%s-%s-%s" % (res["date"][:4], res["date"][4:6], res["date"][6:])
    for part, base_title, rep, blocks, charts in sent:
        if part == "wide":
            title = "📐 宽基中枢监控 · %s ｜ 五大宽基指数 · 来自 OpenClaw.Deepseek" % d8
        else:
            title = "🏭 实盘行业拓扑 V3 · %s ｜ CRI+拓扑几何+三级下钻 · 来自 OpenClaw.Deepseek" % d8
        n = len(charts)
        if not n and part == "wide":
            log("[push] %s：0 张图 —— 该级别全在中枢内，报告已注明" % base_title)
        ok_fs = ok_qq = False
        if not a.qq_only:
            ok_fs = push_feishu(blocks, title)
        if not a.feishu_only:
            if part == "ind":
                # 2026-10-28 13:45 大佬：「重点提醒以后单发 QQ。然后详细信息就发那个飞书报告」
                #   → QQ 只发「重点提醒：我买的行业 风险与机会」，详细报告只走飞书
                # 2026-11-07 20:44 大佬：QQ 发**精简版**（4 块：宽基/机会行业›三级/可买股票/实盘持仓）
                #   纯脚本生成 0 token，原完整报告仍发飞书
                try:
                    import brief_report as BR
                    brief = "\n".join(BR.build(l3_res=_L3_RES, crit_res=_CRI_RES))
                except Exception as e:
                    log("[brief] 生成失败，回退全文 %s" % str(e)[:100])
                    brief = rep
                ok_qq = push_qq_brief(brief)
            else:
                ok_qq = push_qq(rep, charts)
        log("[push] %s ｜ 报告=%s ｜ 图=%d 张%s" % (
            base_title, "ok" if (ok_fs or ok_qq) else "FAIL", n,
            "" if n else ("（行业配图已关闭）" if part == "ind" else "（无图=全在中枢内）")))
        time.sleep(1.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
