#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""👑 三级子行业龙头股识别（纯脚本 0 token）

════════════════════════════════════════════════════════════════════
大佬 2026-11-07 16:52 原话
  「你下挖到三级行业之后，你要告诉我龙头股了。三级行业必须是有上涨的时候，
    经过资金的聚集，对吧？然后你告诉我龙头股，然后技术形态还确认」

→ 三步：
  ① 三级子行业门槛：**在上涨 + 资金聚集** 才认（否则直接标「无资金聚集·不给龙头」）
  ② 过门槛的才评龙头：综合分排序，前 3 名标龙头
  ③ 龙头股再走技术确认（缠论买卖点 + SMC + 均线）

════════════════════════════════════════════════════════════════════
龙头综合分（大佬口述「资金聚集」，成交额占比权重最高）
  score = 0.40×成交额占行业比 + 0.30×流通市值占行业比
        + 0.20×20日动量分 + 0.10×近5日主力净流入占行业比
  各分项先做行业内 min-max 归一化到 [0,1]，避免量纲打架。

为什么成交额占比权重最高：成交额占比就是「资金聚集度」的直接度量——
行业里谁吸走了最多的钱，谁就是头。市值是行业地位，动量是弹性，
主力净流入是聪明钱，依次降权。
"""
import numpy as np

# 权重（改这里就能调口径）
# ⚠️ 2026-11-07 22:43 大佬确认改权重
#   原: 成交额40 + 市值30 + 动量20 + 资金10
#   → 招行成交额占 36.7% 自动排第一，但它资金 -3.29亿（在派发）、技术面顶背驰
#   **成交额大 ≠ 该买，可能是在出货**
#   新: 资金流入优先，成交额降权
# ⚠️ 2026-11-08 01:38 大佬「可以」→ 定稿权重：资金35 + 成交额25 + 市值20 + 动量20
W_AMT = 0.25       # 成交额占行业比（降权：量大可能是派发）
W_MV = 0.20        # 流通市值占行业比（行业地位）
W_MOM = 0.20       # 20 日动量 = 弹性
W_FLOW = 0.35      # 近5日主力净流入占行业比 = **真正的资金聚集（权重最高）**

TOP_N = 3          # 龙头取前几名（只有这几位标 👑）
TECH_ALL = True    # 对所有候选跑技术确认（不只前 TOP_N 名）
CAND_N = 12        # 候选池基础大小（按综合分前 N 名）
CAND_MAX = 20      # 候选池上限（前 N 名之外，「资金流入 + 上涨」的追加到这里为止）

# 三级子行业「上涨 + 资金聚集」门槛
L3_MIN_MOM20 = 0.0     # 20 日动量 > 0（%）
L3_TODAY_UP = True     # 或者今日上涨（二者取或，见 qualify_l3）
L3_MIN_FLOW = 0.0      # 近5日主力净流入 > 0（万元）
# ⚠️ 2026-11-08 01:38 大佬确认新增「修复中」通道
#   原门槛「20日>0 或 今日>0」会漏掉「跌过之后资金先进入、5日已转正」的早期修复
#   例：乳品 20日 -3.8% / 今日 -2.6% / 5日 +3.3% / 资金 +3.67亿
#   → 这类给 🟡可观察，**不给 ⭐可买**
L3_REPAIR_MOM20 = -5.0  # 修复通道：20 日 > -5%（跌得不深）


def _norm(d):
    """dict/list → min-max 归一化到 [0,1]；全相同返回 0.5"""
    vals = list(d.values()) if isinstance(d, dict) else list(d)
    if not vals:
        return {}
    lo, hi = min(vals), max(vals)
    keys = list(d.keys()) if isinstance(d, dict) else list(range(len(vals)))
    if hi - lo < 1e-12:
        return {k: 0.5 for k in keys}
    return {k: (v - lo) / (hi - lo) for k, v in zip(keys, vals)}


def qualify_tier(mom20, today, flow_5d, mom5=None):
    """三级子行业分层：'buy'（可买）/ 'repair'（修复中·只观察）/ 'none'

      · buy    ：上涨（20日>0 或 今日>0）且 资金聚集（近5日净流入>0）
      · repair ：**修复中** —— 5日>0 且 资金>0 且 20日 > -5%
                 （跌过之后资金先进入、短周期已转正，但中期还没修复完）
                 → 只标 🟡可观察，**不给 ⭐可买**
      · none   ：其余
    """
    up = (mom20 is not None and mom20 > L3_MIN_MOM20) or \
         (L3_TODAY_UP and today is not None and today > 0)
    money = flow_5d is not None and flow_5d > L3_MIN_FLOW
    if up and money:
        parts = []
        if mom20 is not None and mom20 > 0:
            parts.append("20日 %+.1f%%" % mom20)
        if today is not None and today > 0:
            parts.append("今日 %+.1f%%" % today)
        return "buy", "上涨（%s）+ 资金聚集（近5日 %+.2f亿）" % (
            "、".join(parts) or "—", (flow_5d or 0) / 1e4)
    if (money and mom5 is not None and mom5 > 0 and mom20 is not None
            and mom20 > L3_REPAIR_MOM20):
        return "repair", "修复中（20日 %+.1f%% ｜ 5日 %+.1f%% ｜ 近5日 %+.2f亿）" % (
            mom20, mom5, (flow_5d or 0) / 1e4)
    if not up:
        return "none", "无上涨（20日 %s / 今日 %s）" % (
            "—" if mom20 is None else "%+.1f%%" % mom20,
            "—" if today is None else "%+.1f%%" % today)
    return "none", "无资金聚集（近5日 %s）" % (
        "—" if flow_5d is None else "%+.2f亿" % (flow_5d / 1e4))


def is_repair(mom20, today, flow_5d, mom5=None):
    return qualify_tier(mom20, today, flow_5d, mom5)[0] == "repair"


def qualify_l3(mom20, today, flow_5d):
    """三级子行业是否「在上涨 + 资金聚集」（严格可买档；兼容旧调用）

    返回 (bool, 理由)
      · 上涨：20日动量>0 **或** 今日上涨（不限死，因为有的刚启动）
      · 资金聚集：近5日主力净流入 > 0
      两个都要满足才算。修复中（repair）在这里算 False。
    """
    up = (mom20 is not None and mom20 > L3_MIN_MOM20) or \
         (L3_TODAY_UP and today is not None and today > 0)
    money = flow_5d is not None and flow_5d > L3_MIN_FLOW
    if up and money:
        parts = []
        if mom20 is not None and mom20 > 0:
            parts.append("20日 %+.1f%%" % mom20)
        if today is not None and today > 0:
            parts.append("今日 %+.1f%%" % today)
        return True, "上涨（%s）+ 资金聚集（近5日 %+.2f亿）" % (
            "、".join(parts) or "—", (flow_5d or 0) / 1e4)
    if not up:
        return False, "无上涨（20日 %s / 今日 %s）" % (
            "—" if mom20 is None else "%+.1f%%" % mom20,
            "—" if today is None else "%+.1f%%" % today)
    return False, "无资金聚集（近5日 %s）" % (
        "—" if flow_5d is None else "%+.2f亿" % (flow_5d / 1e4))


def pick_leaders(stocks, top_n=TOP_N):
    """龙头识别

    stocks: [{code, name, amount, circ_mv, mom20, flow_5d}, ...]
            同一三级子行业的全部成分股
    返回 [ {..., score, rank, parts:{...}} ]  按 score 降序

    ⚠️ 缺项的股票（如全市场面板里没有成交额）按该项 0 分处理，
       但至少要有一项有效，否则跳过。
    """
    rows = []
    for s in stocks:
        amt = s.get("amount")
        mv = s.get("circ_mv")
        mom = s.get("mom20")
        fl = s.get("flow_5d")
        if all(v is None for v in (amt, mv, mom, fl)):
            continue
        rows.append(s)
    if not rows:
        return []

    # 各项行业占比（成交额/市值/资金流用占比，动量直接归一化）
    tot_amt = sum((r.get("amount") or 0) for r in rows) or 1.0
    tot_mv = sum((r.get("circ_mv") or 0) for r in rows) or 1.0
    tot_fl = sum(abs(r.get("flow_5d") or 0) for r in rows) or 1.0

    idx = list(range(len(rows)))
    s_amt = _norm({i: (rows[i].get("amount") or 0) / tot_amt for i in idx})
    s_mv = _norm({i: (rows[i].get("circ_mv") or 0) / tot_mv for i in idx})
    s_mom = _norm({i: (rows[i].get("mom20") or 0) for i in idx})
    s_fl = _norm({i: (rows[i].get("flow_5d") or 0) / tot_fl for i in idx})
    # ⚠️ 资金净流出要**重罚**：归一化后 min-max 给 0 分仍可能靠成交额/市值爬上来，
    #   这里直接把负流入的分项压到 0 以下（用负值），保证资金流出的一定排后面
    for i in idx:
        if (rows[i].get("flow_5d") or 0) < 0:
            s_fl[i] = -1.0
    # 动量同理：20日跌的也压到 0 以下
    for i in idx:
        if (rows[i].get("mom20") or 0) < 0:
            s_mom[i] = min(s_mom.get(i, 0), 0.0) - 0.5

    out = []
    for i, r in enumerate(rows):
        score = (W_AMT * s_amt.get(i, 0) + W_MV * s_mv.get(i, 0)
                 + W_MOM * s_mom.get(i, 0) + W_FLOW * s_fl.get(i, 0))
        o = dict(r)
        o["score"] = round(float(score), 4)
        o["parts"] = {
            "amt_share": round((r.get("amount") or 0) / tot_amt * 100, 1),
            "mv_share": round((r.get("circ_mv") or 0) / tot_mv * 100, 1),
            "flow_share": round((r.get("flow_5d") or 0) / tot_fl * 100, 1),
            "s_amt": round(s_amt.get(i, 0), 3),
            "s_mv": round(s_mv.get(i, 0), 3),
            "s_mom": round(s_mom.get(i, 0), 3),
            "s_flow": round(s_fl.get(i, 0), 3),
        }
        out.append(o)
    out.sort(key=lambda x: -x["score"])
    for k, o in enumerate(out):
        o["rank"] = k + 1
        o["is_leader"] = k < top_n
    return out


def pick_candidates(leaders, base_n=CAND_N, max_n=CAND_MAX):
    """从 pick_leaders 结果里取「要跑技术确认」的候选池

    大佬 2026-11-08 01:38 确认：**所有龙头都跑技术确认**（不只前 3 名），
    且「有资金流入 + 上涨」的不应该被综合分名次挡掉。

    规则：
      ① 综合分前 base_n 名全收
      ② 其余凡「近5日净流入 > 0 且（今日上涨 或 20日动量 > 0）」的追加
      ③ 总数封顶 max_n（防止技术确认跑太久）
    leaders: pick_leaders 的返回值（已按 score 降序）
    """
    if not leaders:
        return []
    keep = list(leaders[:base_n])
    seen = {id(x) for x in keep}
    for x in leaders[base_n:]:
        if len(keep) >= max_n:
            break
        if id(x) in seen:
            continue
        fl = x.get("flow_5d") or 0
        up = (x.get("today") or 0) > 0 or (x.get("mom20") or 0) > 0
        if fl > 0 and up:
            keep.append(x)
            seen.add(id(x))
    return keep


def fmt_leader(o, show_rank=True):
    """单只龙头一行"""
    s = ""
    if show_rank:
        s += "%d. " % o["rank"]
    s += "%s" % (o.get("name") or o.get("code"))
    s += " ｜综合分 %.2f" % o["score"]
    p = o.get("parts") or {}
    s += " ｜成交额占 %s%%" % p.get("amt_share", "—")
    if o.get("circ_mv"):
        s += " ｜流通市值 %s%%" % p.get("mv_share", "—")
    if o.get("mom20") is not None:
        s += " ｜20日 %+.1f%%" % o["mom20"]
    if o.get("flow_5d") is not None:
        s += " ｜近5日 %+.2f亿" % (o["flow_5d"] / 1e4)
    return s


if __name__ == "__main__":
    # 自测
    demo = [
        {"code": "A", "name": "甲", "amount": 50e4, "circ_mv": 800e4, "mom20": 25.0, "flow_5d": 12e4},
        {"code": "B", "name": "乙", "amount": 30e4, "circ_mv": 1200e4, "mom20": 10.0, "flow_5d": 5e4},
        {"code": "C", "name": "丙", "amount": 15e4, "circ_mv": 300e4, "mom20": -5.0, "flow_5d": -3e4},
        {"code": "D", "name": "丁", "amount": 5e4, "circ_mv": 100e4, "mom20": 3.0, "flow_5d": 1e4},
    ]
    for o in pick_leaders(demo):
        print(fmt_leader(o), "【龙头】" if o["is_leader"] else "")
    print()
    for m, t, f in [(5.0, 1.0, 10e4), (5.0, 1.0, -10e4), (-5.0, -1.0, 10e4)]:
        print(qualify_l3(m, t, f))
    print()
    # 修复中通道自测（乳品：20日 -3.8% / 5日 +3.3% / 资金 +3.67亿）
    for m, d, f in [(-3.8, -2.6, 3.67e4), (-7.0, -2.6, 3.67e4), (-3.8, -2.6, -3e4)]:
        print(qualify_tier(m, d, f, mom5=3.3))
