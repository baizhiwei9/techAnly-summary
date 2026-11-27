#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基缠论 · 小时线买卖点监控（★只有买卖点才推 + 自动附图）→ QQ私聊 + 飞书私聊
======================================================================
标的：沪深300 / 中证500 / 中证1000 / 创业板指 / 科创50

监控内容（**只在出现买卖点时推送**，不心跳、不推中枢突破）：
  **缠论买卖点**：一买 / 二买 / 三买（🌙机会）· 一卖 / 二卖 / 三卖（⭐风险）
  ⚠️ 2026-10-23 用户最终口径：
     - 「就出现几买了 → 把那张图发给我」
     - **中枢突破/跌破不推**（用户明确：突破中枢、跌破中枢就不用发）
     - 认的是 chan_read 的 buy_list/sell_list（真买卖点），不是 stage 里的「XX区」
     - 同一个点（签名如 "一买@6440.29" 不变）不重复推
  🖼 每次推送会**自动附上该指数的小时线K线图**（程序生成，0 token）

📌 级别 + 版本（2026-10-23 用户要求：报告里必须说明白）
  级别：**小时线 60m**（只这一个级别，不算日线）
  版本：**缠论·分型版**（包含处理→顶底分型→笔→中枢ZG/ZD→背驰）——只这一个版本，
        不用「均线版/趋势浪子版」（MA10/20/40/80/160 + MACD(10,20,5)）。
  抬头三行常量在 LV / VER / VER_FULL，改口径只改这里。

推送渠道（2026-10-23 用户要求：QQ 私聊 + 飞书私聊 **都发**）：
  - QQ 私聊：`openclaw message send --channel qqbot --target <QQ_TARGET>`
  - 飞书私聊：chan_smc_index.feishu_push（默认 chat = <chat_id>）
  - 邮件：默认**不发**（加 --mail 才发，保留旧能力）

⚠️ 数据新鲜度（2026-10-23 修复）：小时K线缓存加 TTL（默认 10 分钟），
   否则当天 09:35 写下的缓存会被后续每小时调用一直复用，价格停在早上那根。
   实测事故：缓存末根 10:30 3988.27，实际收盘 4105.33（方向判断完全相反）。

状态文件：memory/chan_signal_state.json
  "沪深300|小时": "三卖"/null         ← 买卖点区
  "沪深300|中枢": "in"/"above"/"below"  ← 中枢位置（新增）

用法：
  python3 strategies/chan_signal_monitor.py            # 检测 + 有变化就推 QQ+飞书
  python3 strategies/chan_signal_monitor.py --dry      # 只打印，不发
  python3 strategies/chan_signal_monitor.py --test     # 强制推送全部标的当前状态
  python3 strategies/chan_signal_monitor.py --qq-only  # 只发 QQ（调试用）
  python3 strategies/chan_signal_monitor.py --feishu-only
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, socket, time, argparse
from datetime import datetime

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import chan_smc_index as C   # 复用缠论口径 + 数据获取

STATE = os.path.join(WS, "memory/chan_signal_state.json")
QQMAIL_CFG = os.path.join(WS, "skills/qqmail-assistant/config.json")
QQ_TARGET = os.environ.get("CHAN_QQ_TARGET") or os.environ.get("QQ_DEFAULT_TARGET", "")
CACHE_TTL_MIN = int(os.environ.get("CHAN_CACHE_TTL_MIN", "10"))
# 级别 + 版本（2026-10-23 用户要求：推送里必须写明白是哪个级别、哪个缠论版本）
LV_H1 = "小时线60m"       # 显示用级别标签
# ⚠️ 2026-11-08 大佬定稿：小时线只认三买/三卖（一买二买只在日线有意义）
H1_ALLOWED_BUY = {"三买"}
H1_ALLOWED_SELL = {"三卖"}
LV_DAY = "日线"
KEY_H1 = "小时"           # ⚠️ 状态文件键名专用，**不要跟着显示标签改**（改了会导致历史状态失配→每次重发）
KEY_DAY = "日"
# 兼容旧引用
LV = LV_H1
KEY_LV = KEY_H1
VER = "分型版"
VER_FULL = "缠论·分型版（包含处理→顶底分型→笔→中枢→背驰）"


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def signals_of(ck):
    """从 chan_read 结果提取买卖点信号集合

    ⚠️ 2026-11-08 大佬定稿：**小时线只认三买/三卖**
       原话：「一买二买它都是在那个中枢以下的。其实我不需要，
              你只有三日线的一买二买还有意义，小时级别的一买二买都没有意义，只有三买有意义」
       本脚本只跑小时线级别 → 因此只保留 三买 / 三卖。
       （日线的一买二买由 chan_smc_index.build_text 负责，那边不受影响）
    """
    if not ck.get("ok"):
        return set()
    out = set(t for t, _p in ck.get("buys", []) if t in H1_ALLOWED_BUY)
    out |= set(t for t, _p in ck.get("sells", []) if t in H1_ALLOWED_SELL)
    return out


def fetch_60_fresh(sh_code):
    """小时K线 —— 带 TTL 的缓存（防当天早上缓存被一整天复用）"""
    try:
        import minute_hub as mh
        df = mh.get_kline(sh_code, "60", n=400, kind="index",
                          cache=True, cache_ttl_min=CACHE_TTL_MIN)
        if df is not None and len(df) > 40:
            return df.rename(columns={"day": "dt"})[["dt", "open", "high", "low", "close"]]
    except Exception as e:
        print("[WARN] 60min %s: %s" % (sh_code, str(e)[:80]), flush=True)
    return None


def fetch_daily_fresh(ts_code, ttl_min=None):
    """日线 —— 带 TTL 缓存（日线盘中不变化，没必要每小时都打 tushare）
    ⚠️ 收盘后必须刷新：TTL 默认 30 分钟，等于每小时跑都会重新取一次。
    """
    import time as _t
    cache = os.path.join(WS, ".cache/chan_daily", ts_code + ".csv")
    ttl = CACHE_TTL_MIN * 3 if ttl_min is None else ttl_min
    if os.path.exists(cache):
        try:
            age_min = (_t.time() - os.path.getmtime(cache)) / 60.0
            if age_min <= ttl:
                import pandas as pd
                df = pd.read_csv(cache, parse_dates=["dt"])
                if len(df) > 100:
                    return df
        except Exception:
            pass
    return C.fetch_daily(ts_code)


def scan_levels(levels=("h1", "day")):
    """按级别扫全部指数。

    ⚠️ 2026-10-23 用户要求：**小时线和日线都要监控**，两个级别各自出现买卖点都提示。

    levels: ("h1",) 只小时线｜("day",) 只日线｜("h1","day") 两者（默认）

    返回 rows: [(指数, 显示级别, 状态键级别, stage, 现价, chan_read结果)]
    """
    rows = []
    for name, code in C.IDX:
        if not C.IDX_SH.get(name):
            continue
        # ── 小时线60m ──
        if "h1" in levels:
            h6 = fetch_60_fresh(C.IDX_SH[name])
            if h6 is None or len(h6) <= 40:
                rows.append((name, LV_H1, KEY_H1, "数据不足", float("nan"), {"ok": False}))
            else:
                P = float(h6["close"].iloc[-1])
                ck = C.chan_read(C.bars_of(h6), "h1")
                rows.append((name, LV_H1, KEY_H1,
                             ck.get("stage", "") if ck.get("ok") else "数据不足", P, ck))
        # ── 日线 ──
        if "day" in levels:
            dd = fetch_daily_fresh(code)
            if dd is None or len(dd) < 60:
                rows.append((name, LV_DAY, KEY_DAY, "数据不足", float("nan"), {"ok": False}))
            else:
                P = float(dd["close"].iloc[-1])
                ck = C.chan_read(C.bars_of(dd), "day")
                rows.append((name, LV_DAY, KEY_DAY,
                             ck.get("stage", "") if ck.get("ok") else "数据不足", P, ck))
    return rows


# ⚠️ 2026-10-23 用户最终口径：**只有出现买卖点才推**，中枢突破/跌破不推。
#    而且认的是 chan_read 的 buy_list/sell_list（真买卖点），不是 stage 里的「XX区」。
WATCH = ("一买", "二买", "三买", "一卖", "二卖", "三卖")
BUY_TAGS = ("一买", "二买", "三买")
SELL_TAGS = ("一卖", "二卖", "三卖")

# 中枢位置的中文描述（仅用于正文展示，不再触发推送）
ZPOS_TXT = {"in": "中枢内", "above": "中枢上方", "below": "中枢下方"}

# ─────────── 中枢监控（2026-10-26 用户要求：并入本任务，不单开任务） ───────────
#  五大宽基：日线 + 周线 中枢突破/跌破   ← 之前 10-23 说过"不用发"，10-26 又要求加回来
#  每个实盘：其持仓所属行业（申万二级+三级） 日线 + 周线 中枢 + 六个买卖点（不做小时线）
#  口径：已跌破/已突破 → **只在报告里标星/标月**，不发邮件；
#        当次"正在发生"的转折（位置刚变 / 刚出现新买卖点）→ **发邮件** + 报告最前面
ZS_MARK = {"above": "🌙突破中枢", "below": "⭐跌破中枢", "in": "中枢内"}
KEY_ZS_DAY = "日"        # 状态键 "<名称>|中枢日"
KEY_ZS_WEEK = "周"       # 状态键 "<名称>|中枢周"
IND_TAGS = ("二买", "类二买", "三买", "二卖", "类二卖", "三卖")

# 监控专用新鲜度（比日报更严：只推「刚出现、还能下手」的点）
MON_MAX_AGE = {"小时": 30, "日": 12}
BREAK_PCT = 1.5      # 买点被跌破 / 卖点被涨破 超过这个百分比 → 该点失效，不再提示


def point_sig(ck, key_lv="小时", price=None):
    """取当前「最新鲜、且仍有效」的一个买卖点作为状态签名，如 "一买@6440.29"。

    过滤三关（2026-10-23 加，防刷屏误报）：
      ① 新鲜度：小时线 ≤30 根、日线 ≤12 根（日线 25 根太老，行情早走完了）
      ② 有效性：买点被跌破 1.5% 以上 = 那个低点已被打穿，买点作废；
                卖点被涨破 1.5% 以上 = 高点已被突破，卖点作废
      ③ 取最新：多个点时只取 age 最小的那个

    返回 (签名, tag, 价格, age, 是买点?)
    """
    if not ck.get("ok"):
        return None, None, None, None, None
    maxage = MON_MAX_AGE.get(key_lv, 30)
    P = price if price is not None else ck.get("cur")
    cands = []
    for b in (ck.get("buy_list") or []):
        if not b.get("fresh") or b["tag"] not in BUY_TAGS:
            continue
        if b["age"] > maxage:
            continue
        # 买点被跌破 → 作废
        if P and b["level"] and P < b["level"] * (1 - BREAK_PCT / 100.0):
            continue
        cands.append((b["age"], b["tag"], b["level"], True))
    for sl in (ck.get("sell_list") or []):
        if not sl.get("fresh") or sl["tag"] not in SELL_TAGS:
            continue
        if sl["age"] > maxage:
            continue
        # 卖点被涨破 → 作废
        if P and sl["level"] and P > sl["level"] * (1 + BREAK_PCT / 100.0):
            continue
        cands.append((sl["age"], sl["tag"], sl["level"], False))
    if not cands:
        return None, None, None, None, None
    cands.sort(key=lambda x: x[0])          # age 小的 = 最新的
    age, tag, px, is_buy = cands[0]
    return "%s@%.2f" % (tag, px), tag, px, age, is_buy


def zs_event(prev, now):
    """中枢位置变化 → (emoji, 标题, 说明) 或 None（不推送）"""
    if now is None or prev is None:
        return None          # 首次运行只做基线，不报警
    if prev == now:
        return None
    if now == "above":
        return ("✅", "突破中枢", "上破中枢上沿 ZG")
    if now == "below":
        return ("⚠️", "跌破中枢", "下破中枢下沿 ZD")
    if now == "in" and prev == "above":
        return ("↪", "跌回中枢内", "重回中枢区间（防假突破）")
    if now == "in" and prev == "below":
        return ("↪", "拉回中枢内", "重回中枢区间（防假跌破）")
    return ("🔁", "中枢位置改变", "%s → %s" % (ZPOS_TXT.get(prev, prev), ZPOS_TXT.get(now, now)))


def diff_new(rows, state, prev_zmap=None):
    """对比状态，返回 (hits, cur)

    ⚠️ 2026-10-23 用户口径：
       - **小时线 + 日线两个级别都监控**，各自出现买卖点都提示
       - 只有买卖点变化才推；中枢突破/跌破不推
       - 认 chan_read 的 buy_list/sell_list（真买卖点），不是「二买区」这种区域
       - 状态键 = "<指数>|<级别>"，如 "中证500|小时" / "中证500|日"

    hits: [(指数, 显示级别, kind, key, P, ck, 状态键)]
    """
    cur, hits = {}, []
    for name, lv, key_lv, stage, P, ck in rows:
        st_key = "%s|%s" % (name, key_lv)
        sig, ptag, ppx, age, is_buy = point_sig(ck, key_lv, P)
        cur[st_key] = sig
        # 中枢位置只记录，不推送
        cur["%s|中枢%s" % (name, key_lv)] = ck.get("zpos") if ck.get("ok") else None
        if sig and sig != state.get(st_key):
            hits.append((name, lv, "买点" if is_buy else "卖点", sig, P, ck, st_key))
    return hits, cur


# ─────────── 中枢/行业 监控（五大宽基 日线+周线；每个实盘 日线+周线） ───────────
def _zs_read(bars, lv, price, closes=None):
    """读一个级别的中枢位置 + 六个买卖点（行业用；宽基只取中枢位置）

    closes 给了就顺带算「一买预备 · 可能有一买机会」（2026-10-26 大佬要求：
    「如果要出现一买的机会，并且 MA40 是向上，你也要提示可能有一买机会出现」）
    """
    if not bars or len(bars) < 25:
        return {"ok": False}
    r = C.chan_read(bars, lv)
    if not r.get("ok"):
        return {"ok": False}
    zs = r.get("zs")
    zg = round(float(zs[1]), 2) if zs else None
    zd = round(float(zs[0]), 2) if zs else None
    zpos = None
    if zs:
        zpos = "above" if price > zg else ("below" if price < zd else "in")
    prep = None
    if closes is not None:
        try:
            import y1_watch as _Y1
            prep = _Y1.y1_prepare(r, closes, price)
        except Exception as e:
            print("[WARN] prep %s: %s" % (lv, str(e)[:60]), flush=True)
    return {"ok": True, "zpos": zpos, "zg": zg, "zd": zd,
            "buys": r.get("buy_list") or [], "sells": r.get("sell_list") or [],
            "div": r.get("div"), "stage": r.get("stage"), "prep": prep}


def scan_zs_all(portfolios=None, l3_min_w=8.0):
    """扫「五大宽基 日/周 + 每个实盘行业 日/周」。

    返回 rows: [dict(kind='wide'|'ind', name, pf, level('day'|'week'), label, lv_name,
                     weight, ind_kind, ok, zpos, zg, zd, buys, sells, close)]
    """
    import weekly_zs as WZS
    import xq_ind_monitor as XIM
    import xq_portfolio as PF
    import pandas as pd

    out = []
    # ── ① 五大宽基：日线 + 周线 中枢 ──
    for name, code in C.IDX:
        try:
            dd = fetch_daily_fresh(code)
            if dd is None or len(dd) < 80:
                continue
            close = float(dd["close"].iloc[-1])
            d = dd.set_index("dt")
            w = pd.DataFrame({
                "open": d["open"].resample("W-FRI").first(),
                "high": d["high"].resample("W-FRI").max(),
                "low": d["low"].resample("W-FRI").min(),
                "close": d["close"].resample("W-FRI").last()}).dropna().reset_index()
            done = w[w["dt"] <= dd["dt"].max()]
            for lv, lv_name, bars, cls in (("day", LV_DAY, C.bars_of(dd), dd["close"].values),
                                          ("week", "周线", C.bars_of(done), done["close"].values)):
                a = _zs_read(bars, lv, close, closes=cls)
                out.append({"kind": "wide", "name": name, "pf": "", "level": lv,
                            "lv_name": lv_name, "close": round(close, 2),
                            "weight": None, "ind_kind": "", **a})
        except Exception as e:
            print("[WARN] 宽基 %s: %s" % (name, str(e)[:80]), flush=True)

    # ── ② 每个实盘：其持仓行业 日线 + 周线（含六个买卖点） ──
    profs = [PF.PROFILES[portfolios]] if isinstance(portfolios, str) and portfolios in PF.PROFILES \
        else PF.all_profiles()
    for prof in profs:
        try:
            inds = WZS.holdings_industries(os.path.join(WS, prof["snap"]))
        except Exception as e:
            print("[WARN] 持仓 %s: %s" % (prof["label"], str(e)[:80]), flush=True)
            continue
        l2m = WZS.sw_l2_map()
        rev2 = {v: k for k, v in l2m.items()}
        l3agg = {}
        targets = []
        for ind, v in sorted(inds.items(), key=lambda x: -x[1]["w"]):
            code = rev2.get(ind)
            if code:
                # 2026-10-26：带上相关股票（邮件里要说清楚「电池」是哪只票）
                stk = [(a, round(b, 2)) for a, b, _l2, _l3 in
                       sorted(v["stocks"], key=lambda z: -z[1])[:6]]
                targets.append((ind, code, "ind", v["w"], stk))
            for l3n, l3w in (v.get("l3") or {}).items():
                o = l3agg.setdefault(l3n, {"w": 0.0, "stocks": []})
                o["w"] += l3w
                for a, b, _l2, l3 in v["stocks"]:
                    if l3 == l3n:
                        o["stocks"].append((a, b))
        rev3 = {v: k for k, v in WZS.sw_l3_map().items()}
        for l3n, v in sorted(l3agg.items(), key=lambda x: -x[1]["w"]):
            if v["w"] >= l3_min_w and rev3.get(l3n):
                stk = [(a, round(b, 2)) for a, b in
                       sorted(v.get("stocks") or [], key=lambda z: -z[1])[:6]]
                targets.append((l3n, rev3[l3n], "ind3", v["w"], stk))
        for nm, code, ik, w, stk in targets:
            try:
                df = WZS.fetch_sw_daily(code)
                if df is None or len(df) < 80:
                    continue
                close = float(df["close"].iloc[-1])
                d = df.set_index("dt")
                wdf = pd.DataFrame({
                    "open": d["open"].resample("W-FRI").first(),
                    "high": d["high"].resample("W-FRI").max(),
                    "low": d["low"].resample("W-FRI").min(),
                    "close": d["close"].resample("W-FRI").last()}).dropna().reset_index()
                done = wdf[wdf["dt"] <= df["dt"].max()]
                for lv, lv_name, bars, cls in (("day", LV_DAY, C.bars_of(df), df["close"].values),
                                              ("week", "周线", C.bars_of(done), done["close"].values)):
                    a = _zs_read(bars, lv, close, closes=cls)
                    if not a.get("ok"):
                        continue
                    # 类二买/类二卖（本机口径，复用 xq_ind_monitor）
                    lb, ls = XIM.lei_points(bars, close)
                    raw_b = list(a["buys"]) + [{"tag": t, "level": px, "age": ag,
                                                "fresh": ag <= 12} for t, px, ag in lb]
                    raw_s = list(a["sells"]) + [{"tag": t, "level": px, "age": ag,
                                                 "fresh": ag <= 12} for t, px, ag in ls]
                    # 二买优先于类二买、二卖优先于类二卖（统一口径，避免同一方向重复标）
                    bu, se = XIM.merge_points(raw_b, raw_s)
                    a["buys"] = [{"tag": t, "level": px, "age": ag, "fresh": True}
                                 for t, px, ag in bu]
                    a["sells"] = [{"tag": t, "level": px, "age": ag, "fresh": True}
                                  for t, px, ag in se]
                    out.append({"kind": "ind", "name": nm, "pf": prof["label"],
                                "level": lv, "lv_name": lv_name, "close": round(close, 2),
                                "weight": round(w, 2), "ind_kind": ik,
                                "stocks": stk, **a})
            except Exception as e:
                print("[WARN] 行业 %s: %s" % (nm, str(e)[:80]), flush=True)
            time.sleep(0.3)
    return out


def zs_signals(state, zs_rows, only_turn=True):
    """从中枢扫描结果里取信号。

    only_turn=True  → 只返回「正在发生」的转折（位置刚变 / 刚出现新买卖点）→ 发邮件
    only_turn=False → 返回全部「已跌破/已突破/有买卖点」的现状 → 只用于报告标记，不发邮件
    返回 (turns, marks, cur_state)
      turns : [(kind,name,pf,level_name,type,data)]
      marks : 同结构，状态类（已跌破/已突破/已有买卖点）
    """
    turns, marks, cur = [], [], dict(state)
    baselined = [0]
    for r in zs_rows:
        if not r.get("ok"):
            continue
        base = "%s|%s" % (r["name"], r["level"])
        pfk = ("%s@" % r["pf"]) if r.get("pf") else ""
        # ── 中枢位置 ──
        kz = "%s中枢%s|%s" % (pfk, r["level"], r["name"])
        prev = state.get(kz)
        now = r.get("zpos")
        if now:
            if prev and prev != now:
                turns.append(("zs", r, prev, now))
            elif prev is None:
                baselined[0] += 1        # 首次：只建基线，不当作转折
            if now in ("above", "below"):
                marks.append(("zs", r, None, now))
            cur[kz] = now
        # ── 六个买卖点（只实盘行业有） ──
        if r["kind"] == "ind":
            for side, sign in (("buys", "buy"), ("sells", "sell")):
                kb = "%s点%s%s|%s" % (pfk, r["level"], side, r["name"])
                first = kb not in state
                olds = set(state.get(kb) or [])
                news = set()
                for p in (r.get(side) or []):
                    if p.get("tag") not in IND_TAGS or not p.get("fresh"):
                        continue
                    sig = "%s@%.2f" % (p["tag"], p["level"])
                    news.add(sig)
                    if first:
                        baselined[0] += 1     # 首次：历史已有的点只进基线/报告，不发邮件
                    elif sig not in olds:
                        turns.append((sign, r, None, sig))
                    marks.append((sign, r, None, sig))
                if news or olds:
                    cur[kb] = sorted(news)
    if baselined[0]:
        print("[基线] 首次建立 %d 条状态（历史既有位置/买卖点不发邮件，之后只报变化）"
              % baselined[0], flush=True)
    return turns, marks, cur


def _zs_line(ck):
    zs = ck.get("zs")
    if not zs:
        return None
    P = ck.get("cur")
    s = "  中枢 %.2f ~ %.2f" % zs
    if P:
        s += " ｜ 现价距上沿 %+.2f%%" % ((zs[1] - P) / P * 100)
    return s


def _head(title):
    """统一抬头：把「级别 + 版本」写死在最前面（2026-10-23 用户要求：必须说明白）"""
    return ["%s" % title,
            "级别：%s ｜ 版本：%s" % (LV, VER_FULL),
            "（只看这一个版本，不含均线版/趋势浪子版）", ""]


def fmt_hits(hits, head=True):
    """只讲买卖点；**小时线和日线分开列**（2026-10-23 用户要求两个级别都提示）"""
    L = _head("缠论买卖点 · %s" % datetime.now().strftime("%m-%d %H:%M")) if head else \
        ["━━ 🌙/⭐ 宽基买卖点（小时线60m + 日线）━━", ""]
    # 按级别分组：小时线在前，日线在后
    for lv in (LV_H1, LV_DAY):
        sub = [h for h in hits if h[1] == lv]
        if not sub:
            continue
        L.append("━━ %s ━━" % lv)
        L.append("")
        for name, _lv, kind, key, P, ck, _k in sub:
            icon = "🌙机会" if kind == "买点" else "⭐风险"
            L.append("【%s】%s %s" % (name, icon, key.split("@")[0]))
            L.append("  现价 %.2f ｜ 阶段 %s" % (P, ck.get("stage", "")))
            zl = _zs_line(ck)
            if zl:
                L.append(zl)
            if ck.get("div"):
                L.append("  背驰 %s" % ck["div"])
            L.append("")
    L.append("（下面附对应指数的K线图，图与级别一一对应）")
    return "\n".join(L)


def _one_mark(side, r, val):
    """一行标记：已跌破/已突破/买卖点"""
    if side == "zs":
        icon = ZS_MARK.get(val, "中枢内")
    elif side == "buy":
        icon = "🌙" + val.split("@")[0]
    else:
        icon = "⭐" + val.split("@")[0]
    w = ("仓%.1f%%" % r["weight"]) if r.get("weight") is not None else ""
    lhs = ("%s %s" % (r["name"], w)).strip()
    if r["kind"] == "ind":
        lhs = "%s·%s" % (r["pf"], lhs)
    return "  %-22s %s ｜%s ｜ %.2f" % (lhs, icon, r["lv_name"], r.get("close") or 0)


def _pts_of(r):
    """该级别下新鲜且属于用户指定六个的买卖点 → ['🌙二买', '⭐二卖']

    + 一买预备（可能有一买机会，MA40 向上）→ '🌙可能一买(MA40↑)'
      2026-10-26 大佬：「如果要出现一买的机会，并且 MA40 是向上，你也要提示
      可能有一买机会出现」
    """
    out = []
    for p in (r.get("buys") or []):
        if p.get("tag") in IND_TAGS and p.get("fresh"):
            out.append("🌙" + p["tag"])
    for p in (r.get("sells") or []):
        if p.get("tag") in IND_TAGS and p.get("fresh"):
            out.append("⭐" + p["tag"])
    if (r.get("prep") or {}).get("ok"):
        out.append("🌙可能一买(MA40↑)")
    return sorted(set(out))


def _cell(r):
    """单元格文本。
    宽基（日/周）：**只标中枢位置**（买卖点走「三、小时线60m」那一块，避免重复刷屏）
    实盘行业（日/周）：中枢位置 + 六个买卖点
    """
    s = ZS_MARK.get(r.get("zpos"), "-")
    pts = _pts_of(r)
    if r.get("kind") != "ind":
        # 宽基只标中枢位置；唯一例外：一买预备（大佬要的「可能有一买机会」）也标出来
        if (r.get("prep") or {}).get("ok"):
            return s + "｜🌙可能一买(MA40↑)"
        return s
    return (s + "｜" + " ".join(pts)) if pts else s


def _dw(s):
    """显示宽度（CJK 算 2）"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in str(s))


def _pad(s, w):
    s = str(s)
    return s + " " * max(0, w - _dw(s))


def _tbl(headers, widths, rows, ind="  "):
    """纯文本等宽表格"""
    out = [ind + " ".join(_pad(h, w) for h, w in zip(headers, widths)).rstrip()]
    out.append(ind + " ".join("-" * w for w in widths))
    for r in rows:
        out.append(ind + " ".join(_pad(c, w) for c, w in zip(r, widths)).rstrip())
    return out


def fmt_zs_front(zs_rows, turns, title=None, h1_rows=None):
    """「宽基行业监控」报告段（2026-10-26 定稿结构；**三列表格版**）

    顺序（用户明确要求）：
      ① 🔔 本次新发生 · 转折点（只有这部分发邮件）
      ② 一、五大宽基指数 —— **三列表：指数 ｜ 周线级别 ｜ 日线级别**
      ③ 二、实盘所属行业 —— 每个实盘一张三列表：行业(仓位) ｜ 周线级别 ｜ 日线级别
      ④ 三、小时线60m —— 单独一块（宽基买卖点）
    """
    L = [title or "【📐 宽基行业监控 · %s】" % datetime.now().strftime("%m-%d %H:%M"),
         "口径：%s｜周线=W-FRI，只用已收盘周" % VER_FULL,
         "标记：⭐跌破中枢/卖点（风险）｜🌙突破中枢/买点（机会）"
         "｜**只有第①段（转折点）会发邮件**，已跌破/已突破只做标记",
         ""]

    # ① 转折（排序：先周线后日线，再按 宽基→行业）
    def _tkey(t):
        side, r = t[0], t[1]
        return (0 if r.get("level") == "week" else 1,
                0 if r.get("kind") == "wide" else 1,
                r.get("pf") or "", r.get("name") or "")
    turns = sorted(turns or [], key=_tkey)
    L.append("━━ 🔔 本次新发生 · 转折点（会发邮件）━━")
    if turns:
        for side, r, prev, now in turns:
            who = ("%s·%s" % (r["pf"], r["name"])) if r.get("pf") else r["name"]
            if side == "zs":
                L.append("  %s ｜%s：%s → %s" % (
                    r.get("lv_name", ""), who,
                    ZS_MARK.get(prev, prev), ZS_MARK.get(now, now)))
            else:
                L.append("  %s ｜%s：%s 新出现" % (
                    r.get("lv_name", ""), who, now))
    else:
        L.append("  无（所有标的位置未变、无新买卖点）")
    L.append("")

    wide = [r for r in zs_rows if r["kind"] == "wide" and r.get("ok")]
    inds = [r for r in zs_rows if r["kind"] == "ind" and r.get("ok")]

    def _get(kind_rows, nm, lv):
        return next((x for x in kind_rows if x["name"] == nm and x["level"] == lv), None)

    def _c(r):
        return _cell(r) if r else "数据不足"

    # ② 五大宽基：三列表（指数 ｜ 周线级别 ｜ 日线级别）
    L.append("━━ 一、五大宽基指数 ━━")
    rows = []
    for nm in [x[0] for x in C.IDX]:
        rows.append((nm, _c(_get(wide, nm, "week")), _c(_get(wide, nm, "day"))))
    L += _tbl(("指数", "周线级别", "日线级别"), (10, 18, 18), rows)
    L.append("")

    # ③ 实盘行业：每个实盘一张三列表
    pfs = []
    for r in inds:
        if r.get("pf") not in pfs:
            pfs.append(r.get("pf"))
    if not inds:
        L.append("━━ 二、实盘所属行业 ━━")
        L.append("  （未识别到持仓行业）")
        L.append("")
    for pf in pfs:
        sub_w = sorted([r for r in inds if r.get("pf") == pf and r["level"] == "week"],
                       key=lambda x: -(x.get("weight") or 0))
        sub_d = {r["name"]: r for r in inds if r.get("pf") == pf and r["level"] == "day"}
        L.append("━━ 二、实盘所属行业 · %s（%d 个）━━" % (pf, len(sub_w)))
        rows = []
        for r in sub_w:
            rows.append(("%s(%.1f%%)" % (r["name"], r.get("weight") or 0),
                         _c(r), _c(sub_d.get(r["name"]))))
        if rows:
            L += _tbl(("行业(仓位)", "周线级别", "日线级别"), (22, 20, 20), rows)
        else:
            L.append("  未识别到行业")
        L.append("")

    # ④ 小时线60m：单独一块（宽基买卖点）
    L.append("━━ 三、小时线60m · 五大宽基（买卖点）━━")
    if h1_rows:
        for it in h1_rows:
            try:
                nm, _lv, _k, _stage, P, ck = it
            except Exception:
                continue
            if not isinstance(ck, dict) or not ck.get("ok"):
                L.append("  %-8s 数据不足" % nm)
                continue
            zpos = ZPOS_TXT.get(ck.get("zpos"), "")
            sig, _t, _p, _a, is_buy = point_sig(ck, KEY_H1, P)
            pt = ""
            if sig:
                pt = "｜%s%s" % ("🌙" if is_buy else "⭐", sig.split("@")[0])
            L.append("  %-8s %9.2f  %s%s" % (nm, P, zpos, pt))
    else:
        L.append("  （本次未取小时线数据）")
    return "\n".join(L)


def fmt_turn_mail(turns):
    """转折点邮件正文（只在有新转折时发）"""
    L = ["🔔 宽基行业监控 · 转折预警 · %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
         "级别：日线 + 周线（周线=W-FRI，只用已收盘周）｜版本：%s" % VER_FULL,
         "本次新发生 %d 条（已跌破/已突破的历史状态不发邮件，只进报告标记）" % len(turns),
         ""]
    for side, r, prev, now in turns:
        who = ("%s · %s" % (r["pf"], r["name"])) if r["kind"] == "ind" else ("宽基 " + r["name"])
        # 2026-10-26 大佬要求：邮件里行业要带相关股票（不然不知道「电池」是哪只票）
        stk = ""
        st = r.get("stocks") or []
        if st:
            stk = "\n    持股：%s" % "、".join("%s%.1f%%" % (a, b) for a, b in st[:5])
        if side == "zs":
            L.append("【%s】%s：%s → %s ｜ 现价 %.2f ｜ 中枢 %s~%s%s" % (
                r["lv_name"], who, ZS_MARK.get(prev, prev), ZS_MARK.get(now, now),
                r.get("close") or 0,
                ("%.2f" % r["zd"]) if r.get("zd") else "-",
                ("%.2f" % r["zg"]) if r.get("zg") else "-", stk))
        elif side == "buy":
            L.append("【%s】%s：🌙 出现 %s ｜ 现价 %.2f%s" % (
                r["lv_name"], who, now, r.get("close") or 0, stk))
        else:
            L.append("【%s】%s：⭐ 出现 %s ｜ 现价 %.2f%s" % (
                r["lv_name"], who, now, r.get("close") or 0, stk))
    L.append("")
    L.append("口径：缠论·分型版｜日线 + 周线（周线=W-FRI 只用已收盘周）")
    L.append("买卖点只认 二买/类二买/三买 · 二卖/类二卖/三卖（类二=抬高低点/压低高点）")
    return "\n".join(L)


# ─────────── 报告用：中枢段（带缓存，早报/晚报共用） ───────────
ZS_CACHE = os.path.join(WS, ".cache/chan_zs_scan")
os.makedirs(ZS_CACHE, exist_ok=True)
_ZS_DONE = os.path.join(WS, ".cache/chan_zs_scan/.last_scan")


def _zs_should_scan(force=False):
    """中枢/行业扫描的节流：日线/周线日内在收盘前不会变，每小时都扫是白打 tushare。

    规则（每个交易日最多扫 2 次）：
      · 当天还没扫过          → 扫（早盘那次建基线，有转折就发邮件）
      · 已过收盘 15:00 且当天只在盘中扫过 → **再扫一次**（收盘口径，这是最关键的一次）
      · 其它（盘中重复跑）    → 跳过
    """
    if force:
        return True
    today = datetime.now().strftime("%Y%m%d")
    now_hm = datetime.now().strftime("%H%M")
    try:
        if os.path.exists(_ZS_DONE):
            st = json.load(open(_ZS_DONE, encoding="utf-8"))
            if st.get("date") == today:
                if now_hm >= "1500" and not st.get("post_close"):
                    return True          # 收盘后补扫一次
                return False
    except Exception:
        pass
    return True


def _zs_mark_done():
    try:
        today = datetime.now().strftime("%Y%m%d")
        post = datetime.now().strftime("%H%M") >= "1500"
        old = {}
        if os.path.exists(_ZS_DONE):
            try:
                old = json.load(open(_ZS_DONE, encoding="utf-8"))
            except Exception:
                old = {}
        json.dump({"date": today,
                   "post_close": bool(old.get("post_close") if old.get("date") == today else False) or post,
                   "ts": int(time.time())},
                  open(_ZS_DONE, "w", encoding="utf-8"))
    except Exception:
        pass


def scan_cached(pfkey=None, ttl_min=180):
    """带缓存的扫描。pfkey=None → 只扫五大宽基；pfkey='moergen' → 宽基 + 该组合行业"""
    import datetime as _dt
    p = os.path.join(ZS_CACHE, "%s_%s.json" % (pfkey or "wide",
                                              _dt.date.today().strftime("%Y%m%d")))
    if os.path.exists(p):
        try:
            if time.time() - os.path.getmtime(p) < ttl_min * 60:
                d = json.load(open(p, encoding="utf-8"))
                if d.get("rows"):
                    return d["rows"]
        except Exception:
            pass
    rows = scan_zs_all(portfolios=pfkey) if pfkey else _scan_wide_only()
    try:
        json.dump({"rows": rows}, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, default=str)
    except Exception:
        pass
    return rows


def _scan_wide_only():
    """只扫五大宽基（不碰组合持仓，快）"""
    import pandas as pd
    out = []
    for name, code in C.IDX:
        try:
            dd = fetch_daily_fresh(code)
            if dd is None or len(dd) < 80:
                continue
            close = float(dd["close"].iloc[-1])
            d = dd.set_index("dt")
            w = pd.DataFrame({
                "open": d["open"].resample("W-FRI").first(),
                "high": d["high"].resample("W-FRI").max(),
                "low": d["low"].resample("W-FRI").min(),
                "close": d["close"].resample("W-FRI").last()}).dropna().reset_index()
            done = w[w["dt"] <= dd["dt"].max()]
            for lv, lv_name, bars, cls in (("day", LV_DAY, C.bars_of(dd), dd["close"].values),
                                          ("week", "周线", C.bars_of(done), done["close"].values)):
                a = _zs_read(bars, lv, close, closes=cls)
                out.append({"kind": "wide", "name": name, "pf": "", "level": lv,
                            "lv_name": lv_name, "close": round(close, 2),
                            "weight": None, "ind_kind": "", **a})
        except Exception as e:
            print("[WARN] 宽基 %s: %s" % (name, str(e)[:80]), flush=True)
    return out


def _dedup_rows(rows):
    """去重（宽基缓存 + 组合缓存会重叠；按 kind|pf|name|level 去重，保留先出现的）"""
    seen, out = set(), []
    for r in rows:
        k = "%s|%s|%s|%s" % (r.get("kind"), r.get("pf"), r.get("name"), r.get("level"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


# ─────────── 转折日志（谁发现转折 → 写这里；报告从这里读，避免重复判状态） ───────────
TURN_LOG = os.path.join(WS, ".cache/chan_zs_turns.jsonl")


def save_turns(turns):
    """把本次发现的转折追加到日志（报告里的「本次新发生」从这里读）"""
    if not turns:
        return
    try:
        with open(TURN_LOG, "a", encoding="utf-8") as f:
            for side, r, prev, now in turns:
                f.write(json.dumps({
                    "ts": int(time.time()),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "side": side, "kind": r.get("kind"), "pf": r.get("pf"),
                    "name": r.get("name"), "level": r.get("level"),
                    "lv_name": r.get("lv_name"), "weight": r.get("weight"),
                    "close": r.get("close"), "zd": r.get("zd"), "zg": r.get("zg"),
                    "stocks": r.get("stocks") or [],
                    "prev": prev, "now": now,
                }, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[WARN] 转折日志写入失败 %s" % str(e)[:80], flush=True)


def load_turns_today():
    """读回今天的转折（去重、保持顺序）"""
    today = datetime.now().strftime("%Y-%m-%d")
    out, seen = [], set()
    if not os.path.exists(TURN_LOG):
        return out
    try:
        for ln in open(TURN_LOG, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if d.get("date") != today:
                continue
            k = (d.get("side"), d.get("pf"), d.get("name"), d.get("level"), d.get("now"))
            if k in seen:
                continue
            seen.add(k)
            fake_r = {"kind": d.get("kind"), "pf": d.get("pf"), "name": d.get("name"),
                      "level": d.get("level"), "lv_name": d.get("lv_name"),
                      "weight": d.get("weight"), "close": d.get("close"),
                      "zd": d.get("zd"), "zg": d.get("zg"),
                      "stocks": d.get("stocks") or []}
            out.append((d.get("side"), fake_r, d.get("prev"), d.get("now")))
    except Exception:
        pass
    return out


def _load_h1_cache():
    """读当日小时线缓存（给报告的「三、小时线60m」块）"""
    try:
        p = os.path.join(ZS_CACHE, "h1_%s.json" % datetime.now().strftime("%Y%m%d"))
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8")).get("rows") or []
    except Exception:
        pass
    return []


def report_section(pfkey, pf_label=None, max_ind=14):
    """早晚报内嵌段 = 「宽基行业监控」，放在报告最前面（PCR 之后）。

    · 只做**标记**（⭐跌破/卖点、🌙突破/买点），不消费状态、不发邮件
      → 状态推进由系统 crontab 的小时任务负责（它是唯一发邮件的地方）
    · 段内第①小节 = **当日已发生的转折**（从 turn log 读，小时任务发现时写入），
      这样 07:50/19:50 的报告里也能看到今天有没有真转折
    · 结构：① 转折 ② 五大宽基（先周后日）③ 实盘行业（先周后日）④ 小时线60m
    """
    rows = scan_cached(pfkey)
    import xq_portfolio as PF
    lab = pf_label or PF.PROFILES.get(pfkey, {}).get("label", "")
    title = "【📐 宽基行业监控 · 五大宽基指数 + %s 所属行业】" % lab
    turns = [t for t in load_turns_today() if t[1]["kind"] == "wide" or t[1].get("pf") == lab]
    # 小时线一块用当日缓存（报告里只做展示，不参与转折判定）
    return fmt_zs_front(rows, turns, title=title, h1_rows=_load_h1_cache())


def _h1_for_report():
    """小帮手：给报告缓存一份小时线现状（5 个宽基）"""
    out = []
    for name, _code in C.IDX:
        h6 = fetch_60_fresh(C.IDX_SH.get(name, ""))
        if h6 is None or len(h6) <= 40:
            out.append((name, LV_H1, KEY_H1, "数据不足", float("nan"), {"ok": False}))
            continue
        P = float(h6["close"].iloc[-1])
        ck = C.chan_read(C.bars_of(h6), "h1")
        out.append((name, LV_H1, KEY_H1,
                    ck.get("stage", "") if ck.get("ok") else "数据不足", P, ck))
    return out


def fmt_status(rows):
    L = _head("缠论当前状态 · %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    for lv in (LV_H1, LV_DAY):
        sub = [r for r in rows if r[1] == lv]
        if not sub:
            continue
        L.append("━━ %s ━━" % lv)
        L.append("")
        for name, _lv, _k, stage, P, ck in sub:
            if not ck.get("ok"):
                L.append("【%s】数据不足" % name)
                L.append("")
                continue
            zp = ck.get("zpos")
            pos = (" ｜ " + ZPOS_TXT.get(zp, "")) if zp else ""
            sig, ptag, _px, _age, _b = point_sig(ck, _k, P)
            pt = (" ｜ 买卖点 %s" % sig) if sig else " ｜ 无买卖点"
            L.append("【%s】现价 %.2f ｜ %s%s%s" % (name, P, stage, pos, pt))
            zl = _zs_line(ck)
            if zl:
                L.append(zl)
            if ck.get("div"):
                L.append("  背驰 %s" % ck["div"])
            L.append("")
    return "\n".join(L)


# ─────────── 发送 ───────────
def send_qq(text):
    """QQ 私聊（走 OpenClaw 路由，不直连 provider）"""
    try:                              # 统一通知（data_hub.notify）优先
        import _notify
        _r = _notify.send_card(((text or "").strip().splitlines() or [""])[0][:60], text)
        if _r.get("ok"):
            return True
        print("[notify] 统一库未就绪，回落原通道：%s" % _r.get("reason"))
    except Exception as _exc:
        print("[notify] 统一库异常，回落原通道：%s" % _exc)
    import subprocess
    try:
        p = subprocess.run(["openclaw", "message", "send", "--channel", "qqbot",
                            "--target", QQ_TARGET, "--message", text],
                           capture_output=True, text=True, timeout=120)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        if p.returncode == 0:
            print("qq sent ->", QQ_TARGET[-12:], (out.splitlines() or [""])[-1][:80])
            return True
        print("[WARN] qq failed rc=%d %s" % (p.returncode, out[-150:]))
    except Exception as e:
        print("[WARN] qq exc:", str(e)[:120])
    return False


def send_feishu(subject, body):
    try:
        ok = C.feishu_push(body, title=subject)
        print("feishu sent ->", ok)
        return bool(ok)
    except Exception as e:
        print("[WARN] feishu failed:", str(e)[:120])
    return False


def send_mail(subject, body, to=None):
    """发邮件（可选，加 --mail 才走）：scripts/send_email.py → 回退 qqmail SMTP"""
    if not to:                        # 指定收件人时仍走原通道，避免忽略 to
        try:                          # 统一通知（data_hub.notify）优先
            import _notify
            _r = _notify.send_email(subject, body)
            if _r.get("ok"):
                return True
            print("[notify] 统一库未就绪，回落原通道：%s" % _r.get("reason"))
        except Exception as _exc:
            print("[notify] 统一库异常，回落原通道：%s" % _exc)
    import subprocess
    sender = os.path.join(WS, "scripts/send_email.py")
    if os.path.exists(sender):
        cmd = [sys.executable, sender, "--subject", subject, "--body", body, "--plain"]
        if to:
            cmd += ["--to", to]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 and "sent ->" in out:
            return True
        raise RuntimeError(out.strip()[-150:] or "send_email.py failed")
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.header import Header
    _mc = __import__('ocsecrets').mail_config()
    email_addr, code = _mc["user"], _mc["pwd"]
    to = to or email_addr
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = email_addr
    msg["To"] = to
    msg["Subject"] = Header(subject, "utf-8")
    s = smtplib.SMTP_SSL(_mc["host"], int(_mc["port"]), context=ssl.create_default_context(), timeout=30)
    s.login(email_addr, code)
    s.sendmail(email_addr, [to], msg.as_string())
    s.quit()
    return True


def deliver(subject, body, to=None, qq_only=False, feishu_only=False, mail=False):
    """QQ 私聊 + 飞书私聊 **都发**（2026-10-23 用户要求）；邮件默认不发"""
    ok_any = False
    if not feishu_only:
        ok_any = send_qq(body) or ok_any
    if not qq_only:
        ok_any = send_feishu(subject, body) or ok_any
    if mail:
        try:
            send_mail(subject, body, to)
            print("mail sent ->", to or "self")
        except Exception as e:
            print("[WARN] mail failed:", str(e)[:120])
    return ok_any


def make_charts(hits):
    """给每个触发的（指数 × 级别）各出一张缠论图（2026-10-23：小时线/日线都出）
    返回 [(指数, 级别, png路径)]；出图失败不影响文字推送。
    """
    seen = []
    for h in hits:
        key = (h[0], h[1])       # (指数, 显示级别)
        if key not in seen:
            seen.append(key)
    outs = []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import chan_chart as CH
    except Exception as e:
        print("[WARN] 出图模块不可用:", str(e)[:120])
        return outs
    for nm, lv in seen:
        level = "h1" if lv == LV_H1 else "day"
        try:
            p = CH.draw(nm, level, n=100)
            print("[chart]", p)
            outs.append((nm, lv, p))
        except Exception as e:
            print("[WARN] 出图失败 %s·%s: %s" % (nm, lv, str(e)[:150]))
    return outs


def push_charts(charts, qq_only=False, feishu_only=False):
    """图片推送（QQ 私聊 + 飞书私聊）—— 复用 bin/push_image.py 的受信目录逻辑"""
    if not charts:
        return False
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import push_image as PI
    except Exception as e:
        print("[WARN] push_image 不可用:", str(e)[:120])
        return False
    ok = False
    for nm, lv, p in charts:
        try:
            r = PI.push(p, text="%s · %s · %s" % (nm, lv, VER_FULL),
                        qq=not feishu_only, fs=not qq_only)
            ok = r or ok
        except Exception as e:
            print("[WARN] 图片推送失败 %s: %s" % (nm, str(e)[:150]))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--to", default=None, help="邮件收件人（需同时加 --mail）")
    ap.add_argument("--mail", action="store_true", help="额外发邮件（默认不发）")
    ap.add_argument("--no-zs", action="store_true", help="不扫中枢/行业（只做原来的买卖点监控）")
    ap.add_argument("--zs-only", action="store_true", help="只扫中枢/行业（不出图、不推买卖点）")
    ap.add_argument("--no-mail-turn", action="store_true", help="转折点也不发邮件")
    ap.add_argument("--qq-only", action="store_true")
    ap.add_argument("--feishu-only", action="store_true")
    ap.add_argument("--no-chart", action="store_true", help="只发文字，不出图")
    a = ap.parse_args()

    try:
        import trade_day
        if not a.test and not trade_day.is_open()[0]:
            print("非交易日，跳过")
            return 0
    except Exception:
        pass

    state = load_state()
    rows = scan_levels(("h1", "day"))      # 小时线 + 日线 都扫

    # ── 2026-10-26：中枢突破/跌破（五大宽基 日/周）+ 实盘行业（日/周 + 六个买卖点）──
    #   转折点 → 发邮件；已跌破/已突破 → 只在报告里标星/标月，不发邮件
    #   ⚠️ 只在「收盘后那次（≥15:00）」或「当天还没扫过」时扫：
    #      日线/周线日内在收盘前不会变，每小时都扫等于白打 tushare，还会拖慢小时任务。
    zs_rows, turns, cur_zs = [], [], {}
    if not a.no_zs:
        if _zs_should_scan():
            try:
                zs_rows = scan_zs_all()
                turns, _marks, cur_zs = zs_signals(state, zs_rows)
                _zs_mark_done()
                # 顺手缓存小时线现状（给早晚报「三、小时线60m」那一块用）
                try:
                    h1r = _h1_for_report()
                    json.dump({"rows": h1r},
                              open(os.path.join(ZS_CACHE, "h1_%s.json"
                                                % datetime.now().strftime("%Y%m%d")),
                                   "w", encoding="utf-8"),
                              ensure_ascii=False, default=str)
                except Exception as _e:
                    print("[WARN] 小时线缓存失败 %s" % str(_e)[:80])
            except Exception as e:
                print("[WARN] 中枢扫描失败: %s" % str(e)[:150])
        if not zs_rows:
            # 本次跳过扫描（今天已扫过）→ 用当日缓存出「现状标记」，但**不判转折、不发邮件**
            zs_rows = scan_cached(None) + scan_cached("moergen") + scan_cached("yali")
            zs_rows = _dedup_rows(zs_rows)

    if a.test or a.dry:
        body = fmt_status(rows)
        if zs_rows:
            body = fmt_zs_front(zs_rows, turns) + "\n" + body
        print(body)
        if a.dry:
            return 0
        ch = deliver("【📐 宽基行业监控】测试状态 · %s" % datetime.now().strftime("%m-%d %H:%M"), body, a.to,
                     qq_only=a.qq_only, feishu_only=a.feishu_only, mail=a.mail)
        return 0 if ch else 1

    hits, cur = diff_new(rows, state)
    cur.update(cur_zs)

    # 只扫中枢/行业（用于单独验证或手动跑）
    if a.zs_only:
        print(fmt_zs_front(zs_rows, turns))
        save_state(cur)
        return 0

    # 转折点邮件（即使买卖点没变化，中枢转折也要发）
    if turns and not a.no_mail_turn and not a.zs_only:
        try:
            save_turns(turns)
            subj = "🔔 宽基行业监控 · 转折 %d 条 · %s" % (len(turns), datetime.now().strftime("%m-%d %H:%M"))
            send_mail(subj, fmt_turn_mail(turns), a.to)
            print("mail sent -> 转折 %d 条" % len(turns))
        except Exception as e:
            print("[WARN] 转折邮件失败:", str(e)[:120])
    elif turns:
        save_turns(turns)      # 不发邮件时也记日志（报告里要显示）

    if not hits:
        if zs_rows:
            print(fmt_zs_front(zs_rows, turns))
        print("无信号变化")
        save_state(cur)
        return 0

    body = fmt_hits(hits, head=not zs_rows)
    if zs_rows:
        body = fmt_zs_front(zs_rows, turns) + "\n" + body
    print(body)
    ch = deliver("【📐 宽基行业监控】买卖点 %d 个 · %s" % (len(hits), datetime.now().strftime("%m-%d %H:%M")),
                 body, a.to, qq_only=a.qq_only, feishu_only=a.feishu_only, mail=a.mail)
    # ── 文字之后补发图（2026-10-23 用户要求：信号要配当时的K线图）──
    if ch and not getattr(a, "no_chart", False):
        charts = make_charts(hits)
        if charts:
            push_charts(charts, qq_only=a.qq_only, feishu_only=a.feishu_only)
    if ch:
        save_state(cur)
    else:
        print("[ERR] 所有通道发送失败，状态未更新（下次会重试）")
    return 0 if ch else 1


if __name__ == "__main__":
    sys.exit(main())
