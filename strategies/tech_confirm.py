#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""✅ 技术形态确认 · 缠论 + SMC + 均线 三重过滤（纯脚本 0 token）

════════════════════════════════════════════════════════════════════
大佬 2026-11-07 16:50 原话
  「下钻，然后要有这个就是买卖点的过滤。之前应该有吧？买卖点的过滤，
    无论是均线还是缠论还是 SMC，你看一下有没有一个买卖点的过滤。
    就是说你都没当买卖点，我肯定不要买啊」

→ 子行业下钻出来的个股，**必须过技术形态确认**才进「可以关注」清单。
  没有买点的直接标 ⛔，不推荐。

════════════════════════════════════════════════════════════════════
三道过滤器（全部复用现有实现，不重写）
  ① 缠论（chan_smc_index.chan_read）
     中枢位置 zpos：above / in / below
     背驰 div：底背驰 / 顶背驰
     阶段 stage：三买区 / 二买区 / 一买区 / 三卖结构 / 中枢下方下跌 …
     买卖点清单 buy_list / sell_list（带 age 新鲜度 + active 是否在点位上）
  ② SMC（chan_smc_index.smc_read）
     结构 struct：多头(HH+HL) / 空头(LH+LL) / 扩张 / 收敛
     破位 bos：上破摆动高 / 下破摆动低 / 区间内
     位置 zone：区间偏上(溢价) / 偏下(折价) / 中部
  ③ 均线（自算）
     close vs MA20 / MA40 / MA60，MA20>MA40 多头排列

════════════════════════════════════════════════════════════════════
判定（三档，只有 ✅ 进推荐清单）
  ✅ 买点成立  ：缠论新鲜买点（三买/二买/底背驰一买）+ SMC 非空头 + 站上 MA20
  ⚠️ 等确认    ：有买点但 SMC 偏空或破 MA20 / 或中枢上方但无新鲜买点
  ❌ 不碰      ：中枢下方 / 顶背驰 / 二卖三卖 / SMC 空头结构且下破摆动低
  ⛔ 无买卖点  ：既无买点也无卖点标记（大佬：没买点肯定不买）

用法：
  python3 strategies/tech_confirm.py 600519.SH 000858.SZ       # 单测
  from tech_confirm import confirm_batch                 # 批量
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import time
import numpy as np

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import socket
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

VER = "技术确认 V1（缠论买卖点 + SMC 结构 + 均线排列）"

# 买点优先级（越靠前越好）
BUY_PRI = {"三买": 0, "二买": 1, "一买": 2}
NEAR_PCT = 3.0          # 距买点 ±3% 内算「就在点位上」
MAX_AGE = 25            # 买点新鲜度（日线根数）


def _f(x, d=float("nan")):
    try:
        v = float(x)
        return v if np.isfinite(v) else d
    except Exception:
        return d


def _p(x, nd=1):
    v = _f(x)
    return "—" if not np.isfinite(v) else "%+.*f%%" % (nd, v * 100)


def log(*a):
    print(*a, flush=True)


# ══════════════════════════════════════════ 单只股票确认
def confirm_one(ts_code, name=""):
    """返回 dict（永不抛异常）

    字段：
      code / name / close / pct
      chan: {stage, zpos, div, buys:[...], sells:[...], fresh_buy}
      smc : {struct, bos, zone, pos}
      ma  : {ma20, ma40, ma60, above20, above40, bull_align}
      verdict: ✅买点成立 / ⚠️等确认 / ❌不碰 / ⛔无买卖点
      why: [理由...]
      buy_tag: 最强买点标签（三买/二买/一买/None）
      buy_px: 买点价位
      age: 买点距今根数
    """
    out = {"code": ts_code, "name": name or ts_code, "ok": False,
           "verdict": "⛔无买卖点", "why": [], "buy_tag": None}
    try:
        import chan_smc_index as C
        import holding_deep as HD
        d = HD.fetch_stock_daily(ts_code)
        if d is None or len(d) < 120:
            out["why"] = ["日线数据不足"]
            return out
        close = float(d["close"].iloc[-1])
        prev = float(d["close"].iloc[-2]) if len(d) > 1 else close
        out["close"] = close
        out["pct"] = close / prev - 1 if prev else 0.0

        # ① 缠论
        r = C.chan_read(C.bars_of(d), ts_code)
        chan = {"ok": bool(r.get("ok"))}
        fresh_buy = None
        if r.get("ok"):
            buys = [b for b in (r.get("buy_list") or []) if b.get("fresh")]
            # 只认 一买/二买/三买 三种正买点
            buys = [b for b in buys if b.get("tag") in BUY_PRI]
            buys.sort(key=lambda b: (BUY_PRI[b["tag"]], b.get("age", 99)))
            fresh_buy = buys[0] if buys else None
            chan.update({
                "stage": r.get("stage") or "—",
                "zpos": r.get("zpos"),
                "div": r.get("div"),
                "zs": r.get("zs"),
                "buys": [{"tag": b["tag"], "px": b["level"], "age": b["age"],
                          "active": b.get("active")} for b in buys[:3]],
                "sells": [{"tag": s["tag"], "px": s["level"], "age": s["age"]}
                          for s in (r.get("sell_list") or [])[:3]],
                "fresh_buy": bool(fresh_buy),
            })
            if fresh_buy:
                out["buy_tag"] = fresh_buy["tag"]
                out["buy_px"] = float(fresh_buy["level"])
                out["age"] = int(fresh_buy["age"])
                out["buy_active"] = bool(fresh_buy.get("active"))
        out["chan"] = chan

        # ② SMC
        smc = {"ok": False}
        try:
            s = C.smc_read(d, 3, ts_code)
            if s.get("ok"):
                smc = {"ok": True, "struct": s.get("struct"), "bos": s.get("bos"),
                       "zone": s.get("zone"), "pos": s.get("pos"),
                       "rng": s.get("rng")}
        except Exception:
            pass
        out["smc"] = smc

        # ③ 均线
        c = d["close"].astype(float)
        ma = {}
        for n in (20, 40, 60):
            if len(c) >= n:
                ma["ma%d" % n] = float(c.tail(n).mean())
        ma["above20"] = bool("ma20" in ma and close >= ma["ma20"])
        ma["above40"] = bool("ma40" in ma and close >= ma["ma40"])
        ma["bull_align"] = bool("ma20" in ma and "ma40" in ma and ma["ma20"] > ma["ma40"])
        out["ma"] = ma

        # ── 判定 ──
        why = []
        zpos = chan.get("zpos")
        div = chan.get("div")
        stage = chan.get("stage") or ""
        st = (smc.get("struct") or "")
        bos = (smc.get("bos") or "")

        # 硬否决
        bad_struct = st.startswith("空头") or "下破摆动低" in bos
        bad_chan = (zpos == "below") or (div == "顶背驰") or ("二卖" in stage) or ("三卖" in stage)

        if bad_chan and bad_struct:
            out["verdict"] = "❌不碰"
            why.append("缠论%s + SMC%s" % (stage or "无阶段", "空头破位"))
        elif bad_chan:
            out["verdict"] = "❌不碰"
            why.append("缠论%s%s" % (stage or "",
                                   "（顶背驰）" if div == "顶背驰" else ""))
        elif bad_struct:
            out["verdict"] = "❌不碰"
            why.append("SMC %s / %s" % (st, bos or "—"))
        elif fresh_buy:
            ok_ma = ma.get("above20", False)
            ok_smc = not st.startswith("空头")
            if ok_ma and ok_smc:
                out["verdict"] = "✅买点成立"
                why.append("缠论%s（%s，%d根K线前%s%.2f）" % (
                    fresh_buy["tag"], stage.split("（")[0] or stage,
                    fresh_buy["age"],
                    "现价就在点位上 " if fresh_buy.get("active") else "",
                    fresh_buy["level"]))
                if ma.get("bull_align"):
                    why.append("MA20>MA40 多头排列")
                why.append("站上 MA20")
                if st.startswith("多头"):
                    why.append("SMC 多头结构")
            else:
                out["verdict"] = "⚠️等确认"
                if not ok_ma:
                    why.append("有%s但破 MA20" % fresh_buy["tag"])
                if not ok_smc:
                    why.append("SMC %s" % st)
        elif chan.get("ok") and zpos == "above":
            out["verdict"] = "⚠️等确认"
            why.append("中枢上方但无新鲜买点（等回踩三买）")
        elif chan.get("ok") and zpos == "in":
            out["verdict"] = "⚠️等确认"
            why.append("中枢内震荡，无买点（等突破或二买）")
        else:
            out["verdict"] = "⛔无买卖点"
            why.append(stage or "无买点标记")
        out["why"] = why
        out["ok"] = True
        return out
    except Exception as e:
        out["why"] = ["计算失败：%s" % str(e)[:60]]
        return out


# ══════════════════════════════════════════ 批量
def confirm_batch(codes, names=None, workers=5):
    """codes=[ts_code...] → {code: confirm_one 结果}（并发，永不抛异常）"""
    from concurrent.futures import ThreadPoolExecutor
    names = names or {}
    codes = list(dict.fromkeys([c for c in codes if c]))
    if not codes:
        return {}
    t0 = time.time()

    def _one(c):
        return c, confirm_one(c, names.get(c, ""))

    out = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as p:
            for c, r in p.map(_one, codes):
                out[c] = r
    except Exception as e:
        log("[tech] 批量失败 %s" % str(e)[:100])
    if os.environ.get("TECH_VERBOSE", "0") == "1":
        n_ok = sum(1 for r in out.values() if r["verdict"].startswith("✅"))
        log("[tech] %d 只 → ✅ %d ｜耗时 %.1fs" % (len(out), n_ok, time.time() - t0))
    return out


# ══════════════════════════════════════════ 渲染
def fmt_one(r, show_code=True):
    """单只一行文字"""
    nm = r.get("name") or r["code"]
    if show_code and nm != r["code"]:
        nm = "%s(%s)" % (nm, r["code"])
    s = "%s %s" % (r["verdict"], nm)
    if r.get("close"):
        s += " ｜%.2f %s" % (r["close"], _p(r.get("pct")))
    if r.get("buy_tag"):
        s += " ｜%s@%.2f(%d根前)" % (r["buy_tag"], r.get("buy_px") or 0, r.get("age") or 0)
    if r.get("why"):
        s += " ｜%s" % "；".join(r["why"][:2])
    return s


def summary(res):
    """{code: r} → (n_ok, n_wait, n_no, n_bad)"""
    n = {"✅": 0, "⚠️": 0, "❌": 0, "⛔": 0}
    for r in res.values():
        for k in n:
            if r["verdict"].startswith(k):
                n[k] += 1
                break
    return n["✅"], n["⚠️"], n["❌"], n["⛔"]


def pick_recommend(res, min_verdict=("✅",)):
    """只留可以关注的（默认只要 ✅）"""
    out = [r for r in res.values()
           if any(r["verdict"].startswith(v) for v in min_verdict)]
    out.sort(key=lambda r: (BUY_PRI.get(r.get("buy_tag") or "", 9),
                            r.get("age") or 99))
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    import holding_deep as HD
    nm = HD.name_map()
    res = confirm_batch(args, nm)
    print("口径：%s" % VER)
    print()
    for c in args:
        r = res.get(c)
        if r:
            print(fmt_one(r, show_code=True))
    a, b, x, y = summary(res)
    print()
    print("汇总：✅%d ⚠️%d ❌%d ⛔%d" % (a, b, x, y))
