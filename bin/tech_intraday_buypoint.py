#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中行业买点 Top10 —— 技术分析汇总技能的「盘中精简版」

口径（大佬 2026-11-10 定稿）：
  分层优先级：① 缠论·分型版买点（三买 > 一买 > 二买）
            > ② 缠论·均线版买点
            > ③ SMC 买点（FVG/OB 回踩确认）
            > ④ VWAP 均线向上（MA40 向上且未冲进 +1σ 以上）
  → 按这个顺序把今天有买点的行业排前 10 个输出。

  数据：东财行业板块（100 个）实时日K（含当日盘中那根），所以盘中就能算。
        ⚠️ 申万指数（akshare index_hist_sw）只有 T-1，盘中用不了，故这里不用申万。

全流程纯 Python，0 token。

用法：
  python3 bin/tech_intraday_buypoint.py                # 只看行业，文本
  python3 bin/tech_intraday_buypoint.py --top 10
  python3 bin/tech_intraday_buypoint.py --push         # 推飞书卡片
  python3 bin/tech_intraday_buypoint.py --qq           # 同时推 QQ 文字
  python3 bin/tech_intraday_buypoint.py --with-concept # 行业 + 概念一起排
  python3 bin/tech_intraday_buypoint.py --limit 20     # 调试：只跑前 20 个板块
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime

# ── 全局硬规矩：禁 IPv6（容器 IPv6 不通会静默卡死）──
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import re  # noqa: E402

import pandas as pd  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))
sys.path.insert(0, os.path.join(WS, "smc"))

import chan_smc_index as C        # noqa: E402
import chan_ma_trend_v2 as M      # noqa: E402
import tech_summary as TS         # noqa: E402
import tech_buypoint as TB        # noqa: E402
import wyckoff_today_fetch as F   # noqa: E402
import smc_core as S              # noqa: E402

SIGN = "📎 来自 OpenClaw.Deepseek"
def _qq_target():
    """QQ 推送目标：环境变量 IBP_QQ_TARGET > 本地配置文件（~/.config/openclaw/ibp_qq_target）

    ⚠️ 不写死在脚本里 —— 上传 GitHub 时 openid 会被拦截（属于私密串）。
    """
    t = os.environ.get("IBP_QQ_TARGET")
    if t:
        return t
    p = os.path.expanduser("~/.config/openclaw/ibp_qq_target")
    try:
        t = open(p, encoding="utf-8").read().strip()
        return t or ""
    except Exception:
        return ""


QQ_TARGET = _qq_target()   # 公开版不内置目标，用环境变量或 ~/.config/openclaw/ibp_qq_target
QQ_ACCOUNT = os.environ.get("IBP_QQ_ACCOUNT", "bot2")

TIER_NAME = {1: "① 分型版", 2: "② 均线版", 3: "③ SMC", 4: "④ VWAP均线向上"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ════════════════════════ 取数 ════════════════════════
def board_rows(kind):
    fs = "m:90 t:2" if kind == "行业" else "m:90 t:3"
    return F.board_list(fs)


def to_df(ks):
    d = pd.DataFrame(ks).rename(columns={"vol": "volume"})
    d["dt"] = pd.to_datetime(d["date"])
    d = d[["dt", "open", "high", "low", "close", "volume"]]
    return d.sort_values("dt").reset_index(drop=True)


# ════════════════════════ 单板块判定 ════════════════════════
# ── 级别口径（大佬 2026-11-10 定稿）──
#   行业选股：**周线级别 一买/二买/三买 都算**（周线是主级别，方向确认看周线）
#   **日线级别只认三买**（日线一买/二买 不纳入）
#   → 周线买点是“方向”、日线三买是“入场时机”，两者共振最优。
WEEK_BARS = int(os.environ.get("IBP_WEEK_BARS", "900"))     # 周线要 ~180 周，日线取 900 根
WEEK_YB_MAX_AGE = int(os.environ.get("IBP_WEEK_YB_MAX_AGE", "26"))   # 周线一买最大年龄（周）
WEEK_EB_MAX_AGE = int(os.environ.get("IBP_WEEK_EB_MAX_AGE", "40"))   # 周线二买最大年龄（周）
WEEK_TB_MAX_AGE = int(os.environ.get("IBP_WEEK_TB_MAX_AGE", "60"))   # 周线三买最大年龄（周）
WEEK_TB_NEAR = float(os.environ.get("IBP_WEEK_TB_NEAR", "20"))       # 周线三买：现价高于回踩低点%
WEEK_BUY_NEAR_UP = float(os.environ.get("IBP_WEEK_BUY_NEAR_UP", "30"))  # 周线一买/二买：现价高于买点%
WEEK_BUY_NEAR_DN = float(os.environ.get("IBP_WEEK_BUY_NEAR_DN", "10"))  # 周线一买/二买：现价低于买点%
# ── 个股 VWAP 硬门槛（大佬 2026-11-10 修正：中线 → **−1σ 以上**）──
#   「个股必须战胜 VWAP −1σ 以上」= 允许跌破中线，但不能跌破 −1σ（-1σ 是 VWAP 体系的止损线）
STOCK_VWAP_SIGMA = float(os.environ.get("IBP_STOCK_VWAP_SIGMA", "-1.0"))


def weekly_buys(d, close):
    """周线买点：一买 / 二买 / 三买 都认（大佬 2026-11-10）

    周线“根”= 周，闸门要按周数放宽（默认 一买≤26周 / 二买≤40周 / 三买≤60周）。
    一买仍须周线 MA20 向上（同一口径，只是换成周线级别）。
    返回过闸列表，按 pri 排序（三买1 > 一买2 > 二买3）。
    """
    w = M.resample_week(d)
    if len(w) < 60:
        return [], None, []
    clw = w["close"].astype(float).values
    fxw = C.chan_read(C.bars_of(w), "week")
    ma20w = TS.ma20_state(w)
    out = []
    if fxw.get("ok"):
        for b in (fxw.get("buy_list") or []):
            if b.get("tag") == "一买":
                out.append(_gate_week(b, close, ma20w, "一买"))
            elif b.get("tag") == "二买":
                out.append(_gate_week(b, close, ma20w, "二买"))
    # 周线三买：闸门按周放宽
    for x in TB.find_third_buys(w, clw, max_age=WEEK_TB_MAX_AGE, near=WEEK_TB_NEAR):
        out.append(x)
    acc = [x for x in out if x.get("accept")]
    acc.sort(key=lambda x: (x["pri"], x.get("age", 999)))
    return acc, fxw.get("zs") if fxw.get("ok") else None, out


def _gate_week(b, close, ma20w, tag):
    """周线一买/二买闸门：年龄按周、距离放宽、一买须周线 MA20 向上"""
    lvl = float(b.get("level") or 0)
    pri = 2 if tag == "一买" else 3
    if lvl <= 0:
        return {"tag": tag, "pri": pri, "level": 0.0, "age": 0, "gap": 0.0,
                "accept": False, "why": "买点价格异常（0）"}
    age = int(b.get("age") or 0)
    gap = (close / lvl - 1) * 100
    max_age = WEEK_YB_MAX_AGE if tag == "一买" else WEEK_EB_MAX_AGE
    up = bool((ma20w or {}).get("up"))
    slope = (ma20w or {}).get("slope") or 0.0
    accept, why = True, "周线%s（%s）" % (tag, "MA20 向上 %+.2f%%" % slope if up else "MA20 未向上（仅加分）")
    if tag == "一买" and not up:
        accept, why = False, "周线 MA20 未向上（斜率 %+.2f%%）→ 一买不认" % slope
    elif age > max_age:
        accept, why = False, "买点太老（%d周前 > %d）" % (age, max_age)
    elif gap > WEEK_BUY_NEAR_UP:
        accept, why = False, "已跑远（现价比买点高 %.1f%%）" % gap
    elif gap < -WEEK_BUY_NEAR_DN:
        accept, why = False, "已跌破买点 %.1f%%" % abs(gap)
    return {"tag": tag, "pri": pri, "level": round(lvl, 2), "age": age,
            "gap": gap, "accept": accept, "why": why, "weekly": True}


def analyse(name, code, kind, ks):
    d = to_df(ks)
    if len(d) < 80:
        return None
    cl = d["close"].astype(float).values
    close = float(cl[-1])
    prev = float(cl[-2])
    pct = (close / prev - 1) * 100 if prev else 0.0
    ma20 = TS.ma20_state(d)
    fx = C.chan_read(C.bars_of(d), "day")
    if not fx.get("ok"):
        return None

    # ── 级别口径（大佬 2026-11-10）：
    #     周线：一买/二买/三买 都算（方向）
    #     日线：**只认三买**（入场时机）
    wk_acc, wk_zs, wk_all = weekly_buys(d, close)
    best_wk = wk_acc[0] if wk_acc else None

    # 日线：只认三买（一买/二买 不纳入）
    tb = TB.find_third_buys(d, cl)
    dtb = [x for x in tb if x.get("accept")]
    dtb.sort(key=lambda x: (x["pri"], x.get("age", 999)))
    best_d = dtb[0] if dtb else None

    # ── ② 均线版 ──
    dd = M.add_ind(d)
    sigs = M.signals(dd)
    ma_buy = next((s for s in sigs if s["type"] == "买点"), None)
    ma_sell = next((s for s in sigs if s["type"] == "卖点"), None)
    align = M.ma_alignment(dd)

    # ── ③ SMC（日线 k=3，只认「已回踩 + 方向确认」的看涨区）──
    sm = TS.smc_layer(d, 3)
    smc_buy = [s for s in sm["sigs"] if s["dir"] == "bull" and s["state"] == "buy"]
    smc_best = max(smc_buy, key=lambda s: s["score"]) if smc_buy else None

    # ── ④ VWAP：MA40 向上，且没冲进 +1σ 以上（不追高）──
    vw = S.vwap_tracks(d)
    vw_dir = str(vw["ma_dir"][-1])
    vw_v, vw_sg = float(vw["vwap"][-1]), float(vw["sigma"][-1])
    z = (close - vw_v) / vw_sg if vw_sg > 0 else 0.0
    vw_ok = (vw_dir == "up") and z <= 1.0

    tiers = []
    if best_wk or best_d:
        tiers.append(1)      # 分型版（周线任意买点 / 日线三买）
    if ma_buy:
        tiers.append(2)
    if smc_best:
        tiers.append(3)
    if vw_ok:
        tiers.append(4)
    if not tiers:
        return None
    tier = min(tiers)

    return {
        "kind": kind, "name": name, "code": code, "dt": str(d["dt"].iloc[-1])[:10],
        "close": close, "pct": pct, "tier": tier, "tiers": tiers,
        "n_res": len(tiers),
        "fx": best_wk or best_d, "wk": best_wk, "w_any": any(x["pri"] for x in wk_acc),
        "w_pri": best_wk["pri"] if best_wk else 9,
        "d3": best_d, "d_ok": bool(best_d),
        "both": bool(best_wk and best_d),
        "wk_all": [(x["tag"], x["level"], x["age"], x["accept"]) for x in wk_all[:3]],
        "ma": ma_buy, "ma_sell": ma_sell, "align": align.get("state"),
        "smc": smc_best, "vw_dir": vw_dir, "vw_z": z,
        "zpos": fx.get("zpos"), "stage": fx.get("stage"),
        "ma20_up": bool(ma20.get("up")), "ma20_slope": ma20.get("slope") or 0.0,
        "zs": fx.get("zs"),
    }


def rank_key(r):
    """行业排序（大佬 2026-11-10 定稿）

    级别口径：**周线 一买/二买/三买 都算**，**日线只认三买**。

    排序：
      ① 周线买点有无（有 > 无）→ 周线 pri（三买1 > 一买2 > 二买3）
      ② 日线三买有无（有 > 无）
      ③ 共振层数（分型/均线/SMC/VWAP 命中越多越前）
      ④ 同层内 分型版 > 均线版 > SMC > VWAP（tier）
      ⑤ 涨幅辅助
    """
    w_pri = 9
    if r.get("wk"):
        w_pri = r["wk"]["pri"]
    return (w_pri, 0 if r.get("d3") else 1, -r["n_res"], r["tier"], -r["pct"])


ROMAN = re.compile(r"[ⅠⅡⅢⅣⅤ]+$")


def dedupe(rows):
    """东财板块里有大量同名不同级的重复（IT服务 / IT服务Ⅱ / IT服务Ⅲ、其他家电Ⅱ/Ⅲ），
    只保留排名最前的一个，别占 Top10 名额。"""
    seen, out = {}, []
    for r in rows:
        base = ROMAN.sub("", r["name"]).strip()
        if base in seen:
            continue
        seen[base] = True
        out.append(r)
    return out


# ════════════════════ 个股（行业下钻 + VWAP 中线硬门槛）════════════════════
def _secid(code):
    c = code.split(".")[0]
    return ("1." if c[0] in "659" else "0.") + c


def board_members(board_code, cap=60):
    """板块成分股（东财），按成交额降序（fid=f6）——流动性优先，先扫能买的"""
    d = F._get("/api/qt/clist/get",
               dict(pn=1, pz=300, po=1, np=1, fltt=2, invt=2, fid="f6",
                    fs="b:%s" % board_code, fields="f2,f3,f6,f12,f14"))
    out = []
    for r in (d.get("diff") or []):
        if not isinstance(r.get("f3"), (int, float)):
            continue
        out.append({"code": r["f12"], "name": r["f14"], "pct": float(r["f3"]),
                    "price": r.get("f2"), "amt": (r.get("f6") or 0) / 1e8})
    return out[:cap]


def stock_kline(code, lmt=420):
    d = F._get("/api/qt/stock/kline/get",
               dict(secid=_secid(code), klt=101, fqt=1, lmt=lmt, end="20500101",
                    fields1="f1,f2,f3,f4,f5,f6",
                    fields2="f51,f52,f53,f54,f55,f56,f57"), tries=3, timeout=10)
    ks = []
    for ln in (d.get("klines") or []):
        a = ln.split(",")
        ks.append(dict(date=a[0], open=float(a[1]), close=float(a[2]), high=float(a[3]),
                       low=float(a[4]), vol=float(a[5]), amt=float(a[6])))
    return ks


def analyse_stock(code, name, ks):
    """个股判定（大佬 2026-11-10 口径）

    硬门槛：**现价必须站上 VWAP −1σ 以上**（低于 −1σ 直接淘汰，不看买点）
    → 允许跌破 VWAP 中线，但 −1σ 是 VWAP 体系的止损线，破了就不算「可买」。
    买点级别：**周线 一买/二买/三买 都算**；**日线只认三买**
    → 周线有 + 日线三买 = 双级别共振（最好）
    """
    d = to_df(ks)
    if len(d) < 80:
        return None
    cl = d["close"].astype(float).values
    close = float(cl[-1])
    prev = float(cl[-2])
    pct = (close / prev - 1) * 100 if prev else 0.0
    vw = S.vwap_tracks(d)
    v, sg = float(vw["vwap"][-1]), float(vw["sigma"][-1])
    if not (v > 0):
        return None
    z = (close - v) / sg if sg > 0 else 0.0
    vw_dir = str(vw["ma_dir"][-1])
    # ← 硬门槛：站上 VWAP −1σ（大佬 2026-11-10：允许破中线，不许破 −1σ）
    above_vwap = bool(z >= STOCK_VWAP_SIGMA) if sg > 0 else (close > v)
    if not above_vwap:
        return {"code": code, "name": name, "close": close, "pct": pct,
                "above_vwap": False, "vw_z": z, "vw_dir": vw_dir,
                "vwap": v, "tier": 99, "n_res": 0}

    # 周线：一二三买都认；日线：只认三买
    wk_acc, _wk_zs, _wk_all = weekly_buys(d, close)
    best_wk = wk_acc[0] if wk_acc else None
    dtb = [x for x in TB.find_third_buys(d, cl) if x.get("accept")]
    dtb.sort(key=lambda x: (x["pri"], x.get("age", 999)))
    best_d = dtb[0] if dtb else None
    best_fx = best_wk or best_d

    dd = M.add_ind(d)
    sigs = M.signals(dd)
    ma_buy = next((s for s in sigs if s["type"] == "买点"), None)
    ma_sell = next((s for s in sigs if s["type"] == "卖点"), None)
    align = M.ma_alignment(dd)
    ma20v = float(dd["ma20"].iloc[-1]) if pd.notna(dd["ma20"].iloc[-1]) else None
    ma160v = float(dd["ma160"].iloc[-1]) if pd.notna(dd["ma160"].iloc[-1]) else None

    tiers = []
    if best_wk or best_d:
        tiers.append(1)
    if ma_buy:
        tiers.append(2)
    tier = min(tiers) if tiers else 9      # 9 = 只有 VWAP −1σ 以上，没有缠论买点

    return {"code": code, "name": name, "dt": str(d["dt"].iloc[-1])[:10],
            "close": close, "pct": pct, "above_vwap": True, "vwap": v,
            "vw_z": z, "vw_dir": vw_dir, "tier": tier, "tiers": tiers,
            "n_res": len(tiers), "fx": best_fx, "wk": best_wk, "d3": best_d,
            "both": bool(best_wk and best_d),
            "ma": ma_buy, "ma_sell": ma_sell, "align": align.get("state"),
            "above_ma20": (ma20v is not None and close > ma20v),
            "above_ma160": (ma160v is not None and close > ma160v),
            "zpos": None}


def stock_rank_key(s):
    pri = s["fx"]["pri"] if s.get("fx") else 9
    return (-int(bool(s.get("both"))), s["tier"], pri, -s["n_res"], -s["pct"])


# ════════════════════════ 报告 ════════════════════════
def _res_tags(r):
    t = []
    if r.get("fx"):
        t.append("分型")
    if r.get("ma"):
        t.append("均线")
    if r.get("smc"):
        t.append("SMC")
    if 4 in r["tiers"]:
        t.append("VWAP")
    return "+".join(t) if t else "-"


def _fx_txt(x):
    if not x:
        return ""
    unit = "周" if x.get("weekly") or x.get("tag") in ("周线一买", "周线二买") else "根"
    lv = {"一买": "周线一买", "二买": "周线二买", "三买": "周线三买"}.get(x.get("tag"), x.get("tag"))
    if x.get("weekly"):
        return "%s %.2f（%d%s前，现价%+.1f%%）" % (lv, x["level"], x.get("age", 0), unit, x.get("gap", 0))
    return "%s %.2f（%d根前，现价%+.1f%%）" % (
        TB.PRI_MARK.get(x["pri"], x.get("tag")), x["level"], x.get("age", 0), x.get("gap", 0))


def _wk_buys_txt(r):
    """周线命中的买点列表（一二三买都算）"""
    a = r.get("wk_all") or []
    return "、".join("%s%.2f(%d周)" % (x[0], x[1], x[2]) for x in a if x[3]) or ""


def _stk_line(s, i):
    """个股一行：名称 代码 涨幅 ｜ 买点层 ｜ VWAP"""
    t = {
        1: "① 分型版",
        2: "② 均线版",
        9: "只有VWAP",
    }.get(s["tier"], "-")
    zz0 = s.get("vw_z", 0) or 0
    vtag = ("VWAP中线以上 %+.2fσ" % zz0) if zz0 >= 0 else ("VWAP中线下方 %+.2fσ（仍在−1σ上方）" % zz0)
    bits = ["%d) %s(%s) %+.2f%%" % (i, s["name"], s["code"], s["pct"]), t, vtag]
    wbb = []
    if s.get("wk"):
        wbb.append("周线" + TB.PRI_MARK.get(s["wk"]["pri"], s["wk"]["tag"]).replace("①", "").replace("②", "").replace("③", "")
                   + " %.2f(%d周)" % (s["wk"]["level"], s["wk"].get("age", 0)))
    if s.get("d3"):
        wbb.append("日线三买 %.2f(%d根)" % (s["d3"]["level"], s["d3"].get("age", 0)))
    if wbb:
        bits.append(" + ".join(wbb) + (" ★双级别共振" if s.get("both") else ""))
    elif s.get("ma"):
        bits.append("均线版：%s" % s["ma"]["kind"].split("（")[0])
    else:
        bits.append("无缠论买点")
    tail = []
    if s.get("above_ma20"):
        tail.append("站上MA20")
    if s.get("above_ma160"):
        tail.append("站上MA160")
    if s.get("align") == "多头排列":
        tail.append("多头排列")
    if s.get("ma_sell"):
        tail.append("⚠️均线版卖点")
    zz = s.get("vw_z", 0) or 0
    if zz >= 3:
        tail.append("⚠️+3σ全平区·不追")
    elif zz >= 2:
        tail.append("⚠️+2σ减半区・追高慎")
    elif zz >= 1:
        tail.append("+1σ警戒区")
    elif zz <= -0.8:
        tail.append("⚠️贴近−1σ止损线")
    return "       " + " ｜ ".join(bits) + (("（%s）" % "、".join(tail)) if tail else "")


def build_text(rows, top, kinds, bar_date, n_scan, stock_per=3):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("📊 盘中行业买点 Top%d + 个股下钻 · %s" % (top, now))
    L.append("排序：① 周线买点（三买>一买>二买）→ ② 日线三买（有>无）→ ③ 共振层数（分型/均线/SMC/VWAP 越多越前）")
    L.append("级别：**周线 一买/二买/三买 都算**；**日线只认三买** ｜ 个股：行业选出后下钻，**必须站在 VWAP −1σ 以上**（硬门槛）")
    L.append("数据：东财%s板块 %d 个，最后一根K线 %s（当日盘中）" % ("/".join(kinds), n_scan, bar_date))
    L.append("")
    if not rows:
        L.append("今日无任何行业同时满足上述买点。")
        return "\n".join(L)

    head = rows[:top]
    L.append("═══ 一、行业 + 个股（前 %d 个行业）═══" % len(head))
    for i, r in enumerate(head, 1):
        tag = TIER_NAME[r["tier"]]
        flag = " ⚠️分型/均线分歧" if (r.get("fx") and r.get("ma_sell")) else ""
        L.append("%2d. %-8s %+6.2f%% ｜ %s ｜ 共振 %s（%d层）｜ 位置 %s｜ 日线MA20 %s%s"
                 % (i, r["name"], r["pct"], tag, _res_tags(r), r["n_res"],
                    {"above": "中枢上方", "below": "中枢下方"}.get(r["zpos"], "中枢内"),
                    ("向上 %+.2f%%" % r["ma20_slope"]) if r["ma20_up"] else "未向上", flag))
        # 级别口径：周线一二三买 / 日线只认三买
        wk = r.get("wk")
        d3 = r.get("d3")
        if wk:
            L.append("     └ 周线：%s %.2f（%d周前，现价%+.1f%%）%s"
                     % (TB.PRI_MARK.get(wk["pri"], wk["tag"]).replace("①", "周线").replace("②", "周线").replace("③", "周线"),
                        wk["level"], wk.get("age", 0), wk.get("gap", 0),
                        "  ★日线也有三买·双级别共振" if d3 else ""))
        if d3:
            L.append("     └ 日线三买 %.2f（%d根前，现价%+.1f%%）" % (d3["level"], d3.get("age", 0), d3.get("gap", 0)))
        if not wk and not d3:
            L.append("     └ 缠论：无（周线一二三买 无 / 日线三买 无）")
        if r.get("ma"):
            L.append("     └ 均线版：%s（%d根前）" % (r["ma"]["kind"], r["ma"]["bars_ago"]))
        if r.get("smc"):
            s = r["smc"]
            L.append("     └ SMC：%s %.2f~%.2f（%d根前，方向 %d/%d）"
                     % (s["kind"].upper(), s["bottom"], s["top"], s["age"], s["score"], len(s["conds"])))
        if 4 in r["tiers"]:
            L.append("     └ VWAP：MA40 向上，现价距 VWAP %+.2fσ" % r["vw_z"])
        # ── 个股下钻 ──
        stks = r.get("stocks") or []
        if not stks:
            L.append("     └ 🔍 个股：无（下钻范围内无一只站在 VWAP −1σ 以上）")
        else:
            L.append("     └ 🔍 个股（站在 VWAP −1σ 以上，按买点优先级）：%d 只" % len(stks))
            for j, s in enumerate(stks, 1):
                L.append(_stk_line(s, j))
    L.append("")

    L.append("═══ 二、分层统计 ═══")
    for t in (1, 2, 3, 4):
        n = sum(1 for r in rows if r["tier"] == t)
        if n:
            L.append("  %s 首次命中：%d 个" % (TIER_NAME[t], n))
    L.append("  合计有买点行业 %d 个（已按优先级排序，只展开前 %d）" % (len(rows), len(head)))
    n_wk = sum(1 for r in rows if r.get("wk"))
    n_d3 = sum(1 for r in rows if r.get("d3"))
    n_both = sum(1 for r in rows if r.get("wk") and r.get("d3"))
    L.append("  周线有买点（一二三买）：%d 个｜日线三买：%d 个｜双级别共振：%d 个" % (n_wk, n_d3, n_both))
    n_eb = sum(1 for r in head if r.get("wk") and r["wk"]["pri"] == 3)
    if n_eb:
        L.append("  ⚠️ 上榜里有 %d 个周线买点是「二买」——二买是三档里胜率最低的一档，只作观察/次优信号" % n_eb)
    n_stk = sum(len(r.get("stocks") or []) for r in head)
    n_stk_ok = sum(1 for r in head for s in (r.get("stocks") or []) if s.get("tier") == 1)
    n_stk_both = sum(1 for r in head for s in (r.get("stocks") or []) if s.get("both"))
    L.append("  个股：共 %d 只站上 VWAP −1σ（缠论买点 %d 只，其中周线+日线双共振 %d 只）"
             % (n_stk, n_stk_ok, n_stk_both))
    L.append("")

    warns = [r for r in head if r.get("ma_sell")]
    if warns:
        L.append("═══ 三、⚠️ 上榜里「分型买点 vs 均线版卖点」分歧的行业 ═══")
        L.append("  " + "、".join("%s（%s）" % (r["name"], r["ma_sell"]["kind"].split("（")[0])
                                for r in warns))
        L.append("  说明：分型版看到买点、均线版还在下笔 → 趋势未确认，只宜轻仓试错。")
        L.append("")

    L.append("⚠️ 技术结构扫描，不构成投资建议。")
    return "\n".join(L)


def push_feishu(title, txt):
    ok, chat = TB.push_feishu(title, txt, None, None, "green")
    log("[feishu]", "已推送" if ok else "推送失败", "→", chat)
    return ok


def push_qq(txt):
    if not QQ_TARGET:
        log("[qq] 跳过：未配置 IBP_QQ_TARGET 或 ~/.config/openclaw/ibp_qq_target")
        return False
    cmd = ["openclaw", "message", "send", "--channel", "qqbot",
           "--target", QQ_TARGET, "--message", txt]
    if QQ_ACCOUNT:
        cmd += ["--account", QQ_ACCOUNT]
    try:
        p = subprocess.run(cmd, cwd=WS, capture_output=True, text=True, timeout=120)
        tail = [x for x in (p.stdout or "").splitlines() if x.strip()][-1:] or [""]
        log("[qq] rc=%d %s" % (p.returncode, tail[0].strip()[:160]))
        return p.returncode == 0
    except Exception as e:
        log("[qq] ERR", str(e)[:160])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3,
                    help="选几个行业（大佬 2026-11-10：选 2~3 个就行）")
    ap.add_argument("--limit", type=int, default=0, help="调试：只扫前 N 个板块")
    ap.add_argument("--with-concept", action="store_true", help="行业 + 概念一起排")
    ap.add_argument("--push", action="store_true", help="推飞书卡片")
    ap.add_argument("--qq", action="store_true", help="推 QQ 文字")
    ap.add_argument("--json", default="", help="把结果落盘 json")
    ap.add_argument("--dry", action="store_true", help="只打印不推送")
    ap.add_argument("--stock-per", type=int, default=3,
                    help="每个行业最多列多少只个股（需站在 VWAP −1σ 以上）")
    ap.add_argument("--no-stock", action="store_true", help="只看行业，不扫个股")
    ap.add_argument("--stock-cap", type=int, default=40,
                    help="每个板块最多扫多少只成交额最大的成分股")
    a = ap.parse_args()

    kinds = ["行业"] + (["概念"] if a.with_concept else [])
    rows, n_scan, bar_date = [], 0, ""
    for kind in kinds:
        boards = board_rows(kind)
        if a.limit:
            boards = boards[:a.limit]
        for b in boards:
            name, code = b.get("f14"), b.get("f12")
            if not name or not code:
                continue
            n_scan += 1
            try:
                ks = F.kline(code, lmt=WEEK_BARS)
            except Exception as e:
                log("  kline fail %s %s" % (name, str(e)[:50]))
                continue
            if not ks:
                continue
            bar_date = ks[-1]["date"]
            try:
                r = analyse(name, code, kind, ks)
            except Exception as e:
                log("  analyse fail %s %s" % (name, str(e)[:60]))
                continue
            if r:
                rows.append(r)
        log("[%s] 扫 %d 个，命中 %d" % (kind, len(boards), len(rows)))

    rows.sort(key=rank_key)
    n_raw = len(rows)
    rows = dedupe(rows)
    if n_raw != len(rows):
        log("[去重] %d → %d（同板块不同级重复）" % (n_raw, len(rows)))

    # ── 第二步：行业 → 个股下钻（大佬 2026-11-10：个股必须站在 VWAP −1σ 以上）──
    head = rows[:a.top]
    n_stk_scan = 0
    if not a.no_stock:
        for r in head:
            try:
                mem = board_members(r["code"], cap=a.stock_cap)
            except Exception as e:
                log("  members fail %s %s" % (r["name"], str(e)[:50]))
                r["stocks"] = []
                continue
            picks, below = [], 0
            for m in mem:
                n_stk_scan += 1
                try:
                    sks = stock_kline(m["code"], lmt=WEEK_BARS)
                except Exception:
                    continue
                if not sks:
                    continue
                try:
                    s = analyse_stock(m["code"], m["name"], sks)
                except Exception:
                    continue
                if not s:
                    continue
                if not s.get("above_vwap"):
                    below += 1
                    continue
                picks.append(s)
            picks.sort(key=stock_rank_key)
            r["stocks"] = picks[:a.stock_per]
            r["n_stock_scan"] = len(mem)
            r["n_stock_below_vwap"] = below
            log("  [个股] %s：扫 %d 只，站上 VWAP −1σ 的 %d 只（被 −1σ 挡掉 %d）"
                % (r["name"], len(mem), len(picks), below))
        log("[个股] 共扫 %d 只" % n_stk_scan)

    txt = build_text(rows, a.top, kinds, bar_date, n_scan, stock_per=a.stock_per)
    print(txt)

    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"),
                   "bar_date": bar_date, "n_scan": n_scan,
                   "top": [{k: v for k, v in r.items() if k not in ("fx_all",)} for r in rows[:a.top]],
                   "n_hit": len(rows)}, open(a.json, "w"), ensure_ascii=False, indent=1, default=str)
        log("[json]", a.json)

    if a.dry:
        return 0
    ok = False
    if a.push:
        ok = push_feishu("📊 盘中行业买点+个股 Top%d · %s"
                         % (a.top, datetime.now().strftime("%m-%d %H:%M")), txt) or ok
    if a.qq:
        ok = push_qq(txt) or ok
    return 0 if (ok or (not a.push and not a.qq)) else 1


if __name__ == "__main__":
    sys.exit(main())
