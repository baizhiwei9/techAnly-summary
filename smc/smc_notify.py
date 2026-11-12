#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 增量通知引擎
================================================================
把模拟交易库里「未通知」的事件（开仓/平仓/减半/加回）聚合成一条增量消息。

⚠️ 用户 2026-10-07 强调：**平仓消息必须给出平仓逻辑/原因**，因为程序跑的，
   人不知道决策依据。因此每条消息都带：入场时间价格 / 离场时间价格 /
   离场原因（结构信号 或 止损）/ 盈亏金额与百分比 / 持仓时长。

发送目标：
  1) **多尺度交易群** → 飞书群 webhook（主）
  2) Coze 群（备用，文件回传）

用法：
  python3 smc_notify.py                 # 发未读增量事件
  python3 smc_notify.py --dry           # 只打印
  python3 smc_notify.py --digest        # 全标的持仓/权益快照
  python3 smc_notify.py --coze-only     # 只发 Coze 群
  python3 smc_notify.py --no-coze       # 只发飞书
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smc_config import COZE, INSTRUMENTS, TRADE_TF, REPORT_DIR, FEISHU_WEBHOOK, FEISHU_APP_CHAT, level_of
import smc_db

SIGN = "📎 来自 openclaw.glm · SMC 多尺度模拟交易引擎"
BRIDGE = COZE["bridge"]
WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── FVG / OB 播报开关（2026-11-08 新增）──────────────────────
# ⚠️ 这两类只是「机会 / 风险提示」，**不参与任何入场判定**。
ZONES_ON = os.environ.get("SMC_ZONES", "1") != "0"
ZONES_NEAR_PCT = float(os.environ.get("SMC_ZONES_NEAR", "2.0"))   # 只报这个距离内的
ZONES_MAX_IT = int(os.environ.get("SMC_ZONES_MAX", "6"))          # 最多几个标的
TEXT_CAP = 4500                                                    # 纯文本上限
_ZCACHE = {}


# ───────────────────── 通道 ─────────────────────
def _plain_card(text, title=None):
    """把纯文本包装成一张交互式卡片（标题+markdown+署名）。"""
    import smc_feishu
    ttl = title or "⚡ SMC 多尺度模拟交易"
    return smc_feishu.build_card(ttl, text, sign=SIGN)


def send_feishu(text, title=None):
    """发飞书群（多尺度交易群）。

    优先：飞书应用（机器人）直发到 FEISHU_APP_CHAT 群（**interactive 卡片**）；
    未配置时：回退到群自定义机器人 webhook（也发 card）。
    """
    import urllib.request
    card = _plain_card(text, title)
    # ① 应用（机器人）通道：应用被拉进哪个群就能发哪个群
    if FEISHU_APP_CHAT:
        try:
            import smc_feishu
            r = smc_feishu.send_card(FEISHU_APP_CHAT, card)
            ok = r.get("code") == 0
            return ("feishu-app-多尺度群", 0 if ok else -1, json.dumps(r, ensure_ascii=False)[:160])
        except Exception as e:
            return ("feishu-app-多尺度群", -1, str(e)[:160])
    # ② 群自定义机器人 webhook（兜底，同样发交互式卡片）
    body = json.dumps({"msg_type": "interactive", "card": card}, ensure_ascii=False).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            FEISHU_WEBHOOK, data=body, headers={"Content-Type": "application/json"}), timeout=15)
        return ("feishu-webhook-群", 0, r.read().decode()[:160])
    except Exception as e:
        return ("feishu-webhook-群", -1, str(e)[:160])


def _coze_send(text, fname, caption=None):
    """把文本落成文件 + caption，推到 Coze 群。"""
    p = os.path.join(REPORT_DIR, fname)
    with open(p, "w") as f:
        f.write(text)
    rel = os.path.relpath(p, WS)
    cmd = [BRIDGE, "send", "file", rel,
           "--agent-id", COZE["agent_id"], "--session-id", COZE["session_id"],
           "--name", fname, "--caption", (caption or text[:180])]
    try:
        r = subprocess.run(cmd, cwd=WS, capture_output=True, text=True, timeout=90)
        return ("coze-群", r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()[:300])
    except Exception as e:
        return ("coze-群", -1, str(e)[:200])


# ───────────────────── 事件格式化 ─────────────────────
def _fmt_ev2(r):
    """带 extra（entry_dt/entry_px）的富格式：平仓时同时显示开仓信息与持仓时长。"""
    (eid, code, name, cls, kind, tf, etype, dt, px, units, reason, pnl, pnl_pct, extra) = r
    d = {}
    try:
        d = json.loads(extra) if extra else {}
    except Exception:
        d = {}
    icon = {"开仓": "🟢", "平仓": "🔴", "减半(XCH)": "🟡", "加回满仓": "🔵"}.get(etype, "⚪")
    head = f"{icon}【{cls}】{name}（{code}）· {tf} · {etype}"
    body = [head, f"  时间：{dt}"]
    if px is not None:
        body.append(f"  成交价：{px:.4g}" + (f"   数量：{units:.2f}" if units else ""))
    if reason:
        body.append(f"  逻辑：{reason}")
    edt, epx = d.get("entry_dt"), d.get("entry_px")
    if etype in ("平仓", "减半(XCH)", "加回满仓") and edt:
        hold = ""
        try:
            t0 = datetime.strptime(str(edt)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
            t1 = datetime.strptime(str(dt)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
            mins = int((t1 - t0).total_seconds() // 60)
            hold = f"（持仓 {mins // 1440}天{mins % 1440 // 60}小时）" if mins >= 1440 else f"（持仓 {mins // 60}小时{mins % 60}分）"
        except Exception:
            pass
        if epx is not None:
            body.append(f"  建仓：{edt} @ {float(epx):.4g} {hold}")
    if pnl is not None:
        body.append(f"  盈亏：{pnl:+,.0f} 元（{pnl_pct:+.2f}%）")
    return "\n".join(body)


def _fmt_line(r):
    """把一个事件压缩成 1~2 行（合并消息用）。"""
    (eid, code, name, cls, kind, tf, etype, dt, px, units, reason, pnl, pnl_pct, extra) = r
    d = {}
    try:
        d = json.loads(extra) if extra else {}
    except Exception:
        d = {}
    ts = str(dt)[:16].replace("T", " ")
    px_s = f"{px:.4g}" if px is not None else "-"
    if etype == "开仓":
        s = f"   时间 {ts} · 成交 {px_s}"
        if units:
            s += f" · 数量 {units:.2f}"
        if reason:
            s += f"\n   逻辑：{reason}"
        return s
    if etype == "平仓":
        s = f"   时间 {ts} · 成交 {px_s}"
        if reason:
            s += f"\n   逻辑：{reason}"
        edt, epx = d.get("entry_dt"), d.get("entry_px")
        if edt and epx is not None:
            s += f"\n   建仓：{str(edt)[:16].replace('T', ' ')} @ {float(epx):.4g} {_hold(edt, dt)}"
        elif edt:
            s += f"\n   建仓：{str(edt)[:16]}"
        if pnl is not None:
            s += f"\n   盈亏：{pnl:+,.0f} 元（{pnl_pct:+.2f}%）"
        return s
    # 减半 / 加回满仓
    s = f"   时间 {ts} · 成交 {px_s}（{etype}）"
    if reason:
        s += f"\n   逻辑：{reason}"
    if pnl is not None:
        s += f"\n   盈亏：{pnl:+,.0f} 元（{pnl_pct:+.2f}%）"
    return s


def _hold(edt, dt):
    try:
        t0 = datetime.strptime(str(edt)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(str(dt)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        m = int((t1 - t0).total_seconds() // 60)
        return f"（持仓 {m // 1440}天{m % 1440 // 60}小时）" if m >= 1440 else f"（持仓 {m // 60}小时{m % 60}分）"
    except Exception:
        return ""


def build_incremental():
    """★ 一轮只发**一条**合并消息（用户 2026-10-08 明确要求）。

    · 本轮所有动作合并成一条，按时间顺序排列，开头给「净方向」；
    · 同一标的本轮若**先开后平**（净变动≈0）→ 单独标注「无需跟单」，不制造
      「一条做多、一条平仓」自相矛盾的噪音。"""
    c = smc_db.conn()
    rows = smc_db.unread_events_full(c)
    if not rows:
        c.close()
        return None, []
    rows = sorted(rows, key=lambda r: str(r[7]))
    by = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    now = f"{datetime.now():%Y-%m-%d %H:%M}"
    picks = []
    pick_items = []
    for code, rs in by.items():
        # ① 同一根 K 线内「又开又平」→ 两边都不发（净变动 0，无法跟单）
        open_dts = {str(r[7]) for r in rs if r[6] == "开仓"}
        close_dts = {str(r[7]) for r in rs if r[6] == "平仓"}
        same = open_dts & close_dts
        rs2 = [r for r in rs if str(r[7]) not in same]
        if not rs2:
            continue
        # ② 其他情况只保留本标的**最后一次**动作（开仓或平仓，绝不同时出现）
        picks.append((code, rs2[-1]))
        it = next((x for x in INSTRUMENTS if x["code"] == code), None)
        if it:
            pick_items.append((code, it["name"], it["kind"], level_of(it)))
    L = [f"⚡ SMC 模拟交易 · {now} · 本轮 {len(picks)} 个标的动作"]
    L.append("(只发每个标的的**最后一次**交易动作；开仓=做多、平仓=离场，不会又开又平)")
    for code, r in picks:
        nm, cls, tf0, etype = r[2], r[3], r[5], r[6]
        icon = {"开仓": "🟢", "平仓": "🔴", "减半(XCH)": "🟡", "加回满仓": "🔵"}.get(etype, "⚪")
        act = {"开仓": "做多（看多）", "平仓": "离场（看空/离场）"}.get(etype, etype)
        L.append(f"\n{icon}【{cls}】{nm}（{code}）· {tf0} · {act}")
        L.append("  " + _fmt_line(r).strip().replace("\n", "\n  "))
    # 当前净持仓（看多清单 / 空仓清单）
    holds, flats = [], []
    tot = 0.0
    for it in INSTRUMENTS:
        tf = level_of(it)
        row = c.execute("SELECT state,entry_px,units,last_close FROM state WHERE code=? AND tf=?",
                        (it["code"], tf)).fetchone()
        if not row:
            continue
        st, epx, units, lc = row
        eq = c.execute("SELECT equity FROM equity WHERE code=? AND tf=? ORDER BY dt DESC LIMIT 1",
                       (it["code"], tf)).fetchone()
        tot += (eq[0] if eq else 0)
        if units and float(units) > 0:
            ret = (lc / epx - 1) * 100 if epx else 0
            holds.append(f"  🟩 {it['name']}（{it['code']}）{units:.2f} @ {epx:.4g} · 现价 {lc:.4g} · {ret:+.2f}%")
        else:
            flats.append(it["name"])
    L.append(f"\n—— 动作后状态 ——")
    L.append(f"【看多持仓 {len(holds)}】")
    L += holds or ["  （无）"]
    L.append(f"【空仓观望 {len(flats)}】" + ("（" + "、".join(flats) + "）" if flats else ""))
    L.append(f"合计权益 {tot:,.0f}（初始 15×50万）")
    # ── FVG / OB（只做机会/风险提示，不影响上面的交易动作判定）──
    if ZONES_ON:
        try:
            L.append("")
            L += zones_block(items=(pick_items if picks else None),
                             max_items=max(6, len(pick_items)),
                             header=f"📐🧱 FVG/OB 结构提示（现价 {ZONES_NEAR_PCT:g}% 内，仅供观察，不参与入场）")
            L.append("")
            L += signals_block(items=(pick_items if picks else None),
                               header="🎯 买卖点确认（不破 + 方向确认；仅供观察，不参与入场）")
        except Exception as e:
            L.append(f"（FVG/OB 计算跳过: {str(e)[:60]}）")
    ids = [r[0] for r in rows]
    c.close()
    return "\n".join(L), ids


def snapshot():
    """全标的看板：持仓（含止损位）/ 空仓待机原因 / 合计权益。"""
    c = smc_db.conn()
    lines = [f"📊 SMC 多尺度模拟交易 · 状态看板 · {datetime.now():%Y-%m-%d %H:%M}"]
    tot = 0.0
    holds, flats = [], []
    for it in INSTRUMENTS:
        tf = level_of(it)
        r = c.execute("SELECT state,entry_dt,entry_px,units,cash,last_dt,last_close FROM state WHERE code=? AND tf=?",
                      (it["code"], tf)).fetchone()
        if not r:
            continue
        state, edt, epx, units, cash, ldt, lc = r
        eq = c.execute("SELECT equity FROM equity WHERE code=? AND tf=? ORDER BY dt DESC LIMIT 1",
                       (it["code"], tf)).fetchone()
        eq = eq[0] if eq else 0
        tot += eq
        if units and float(units) > 0:
            ret = (lc / epx - 1) * 100 if epx else 0
            line = f"  🟩 {it['name']}（{it['code']}）[{tf}] 持仓 {units:.2f} @ {epx:.4g} | 现价 {lc:.4g} | 浮盈 {ret:+.2f}%"
            # 止损位（仅期货）
            ss = smc_db.get_stop_state(c, it["code"], tf)
            if ss:
                sp, sk, dd, pk, ep0, lc0 = ss
                line += f"\n       🛡 止损 {sp:.4g}（{sk}，距现价 {dd:+.2f}%）| 持仓期最高 {pk:.4g}"
            else:
                line += "\n       ⚙️ ETF/股票：不加价格止损，靠结构信号离场（MSS 清仓 / XCH 减半）"
            holds.append(line)
        else:
            flats.append(it["name"])
    lines.append(f"\n【持仓中 {len(holds)}】")
    lines += holds or ["  （无）"]
    # 空仓不再逐个推送（用户 2026-10-07 要求），只报数量
    lines.append(f"\n【空仓 {len(flats)} 个】" + ("（" + "、".join(flats) + "）" if flats else ""))
    lines.append(f"\n合计权益 {tot:,.0f}（初始 {len(INSTRUMENTS)}×50万）· 数据截至 {datetime.now():%Y-%m-%d}")
    # ── FVG / OB 结构提示（只做机会/风险提示，不参与入场判定）──
    if ZONES_ON:
        try:
            lines.append("")
            lines += zones_block(header=f"📐🧱 FVG/OB 结构提示（现价 {ZONES_NEAR_PCT:g}% 内，仅供观察，不参与入场）")
            lines.append("")
            lines += signals_block(header="🎯 买卖点确认（不破 + 方向确认；仅供观察，不参与入场）")
        except Exception as e:
            lines.append(f"（FVG/OB 计算跳过: {str(e)[:60]}）")
    lines.append("\n级别：股票/ETF=日线（无保证金，50万实盘买入）· 期货=小时(h1)（保证金交易，1手名义≈5万，最多10手）｜仅做多 · 期货带止损（2×ATR14 跟踪 + 5% 回撤）")
    c.close()
    return "\n".join(lines)


BRIEF_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "brief_state.json")


def _load_prev():
    try:
        return json.load(open(BRIEF_STATE, encoding="utf-8"))
    except Exception:
        return {}


def _save_prev(snap):
    try:
        os.makedirs(os.path.dirname(BRIEF_STATE), exist_ok=True)
        json.dump(snap, open(BRIEF_STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("save brief state fail:", e)


def _pos_snapshot():
    """当前各标的持仓快照 {key: {...}}（key=code/tf）。"""
    c = smc_db.conn()
    snap = {}
    for it in INSTRUMENTS:
        tf = level_of(it)
        r = c.execute("SELECT state,entry_dt,entry_px,units,last_close FROM state WHERE code=? AND tf=?",
                      (it["code"], tf)).fetchone()
        if not r:
            continue
        state, edt, epx, units, lc = r
        k = f'{it["code"]}/{tf}'
        snap[k] = dict(code=it["code"], name=it["name"], cls=it["cls"], kind=it["kind"], tf=tf,
                       state=state, entry_dt=edt, entry_px=epx, units=float(units or 0),
                       last_close=lc)
    c.close()
    return snap


def open_brief(tag="day", dry=False):
    """开盘播报：持仓变化（对比上一次播报）+ 当前持仓列表 + 空仓数量。

    tag: day（日盘 09:00 开盘）| night（夜盘 21:00 开盘）
    ⚠️ 用户 2026-10-11 要求：**就算没有交易、交易没成功，开盘也要有持仓变化/持仓通知/持仓列表**。
    """
    now = datetime.now()
    if tag == "night":
        head = f"🌙 SMC 夜盘开盘持仓播报 · {now:%Y-%m-%d %H:%M}"
    else:
        head = f"🌅 SMC 日盘开盘持仓播报 · {now:%Y-%m-%d %H:%M}"
    cur = _pos_snapshot()
    prev = _load_prev()

    holds_new, holds_closed, holds_chg = [], [], []
    if prev:
        for k, v in cur.items():
            p = prev.get(k)
            if v["units"] > 0 and (not p or p.get("units", 0) <= 0):
                holds_new.append(v)
            elif v["units"] <= 0 and p and p.get("units", 0) > 0:
                holds_closed.append(p)
            elif v["units"] > 0 and p and p.get("units", 0) > 0:
                if abs(float(v["units"]) - float(p.get("units", 0))) > 1e-6:
                    holds_chg.append((p, v))

    L = [head]
    if not prev:
        L.append("（首次播报，暂无对比基线）")
    elif not (holds_new or holds_closed or holds_chg):
        L.append("🔁 与上次播报相比：**持仓无变化**")
    else:
        L.append("🔁 与上次播报相比：")
        for v in holds_new:
            L.append(f"  ▲ 新开仓 {v['name']}（{v['code']}）{v['units']:.2f} @ {v['entry_px']:.4g}")
        for p in holds_closed:
            L.append(f"  ▼ 已平仓 {p['name']}（{p['code']}）")
        for p, v in holds_chg:
            d = v["units"] - float(p.get("units", 0))
            L.append(f"  ◆ 仓位变动 {v['name']}（{v['code']}）{d:+.2f} → {v['units']:.2f}")

    holds, flats, tot = [], [], 0.0
    c = smc_db.conn()
    for it in INSTRUMENTS:
        tf = level_of(it)
        r = c.execute("SELECT state,entry_dt,entry_px,units,cash,last_dt,last_close FROM state WHERE code=? AND tf=?",
                      (it["code"], tf)).fetchone()
        if not r:
            continue
        state, edt, epx, units, cash, ldt, lc = r
        eq = c.execute("SELECT equity FROM equity WHERE code=? AND tf=? ORDER BY dt DESC LIMIT 1",
                       (it["code"], tf)).fetchone()
        tot += (eq[0] if eq else 0)
        if units and float(units) > 0:
            ret = (lc / epx - 1) * 100 if epx else 0
            line = f"  🟩 {it['name']}（{it['code']}）[{tf}] {float(units):.2f} @ {epx:.4g} | 现价 {lc:.4g} | 浮盈 {ret:+.2f}%"
            ss = smc_db.get_stop_state(c, it["code"], tf)
            if ss:
                # get_stop_state 返回 (stop_price, stop_kind, dist_pct, peak, entry_px, last_close)
                line += f"\n       🛡 止损 {ss[0]:.4g}（{ss[1]}，距现价 {ss[2]:+.2f}%）"
            holds.append(line)
        else:
            flats.append(it["name"])
    c.close()

    L.append(f"\n📋 当前持仓列表（{len(holds)} 个）")
    L += holds or ["  （无持仓，全部空仓观望）"]
    if holds:
        L.append(f"  合计权益 {tot:,.0f}（初始 {len(INSTRUMENTS)}×50万）")
    L.append(f"\n【空仓 {len(flats)} 个】" + ("（" + "、".join(flats) + "）" if flats else ""))
    # ── FVG / OB 结构提示（只做机会/风险提示，不参与入场判定）──
    if ZONES_ON:
        try:
            L.append("")
            L += zones_block(header=f"📐🧱 FVG/OB 结构提示（现价 {ZONES_NEAR_PCT:g}% 内，仅供观察，不参与入场）")
            L.append("")
            L += signals_block(header="🎯 买卖点确认（不破 + 方向确认；仅供观察，不参与入场）")
        except Exception as e:
            L.append(f"（FVG/OB 计算跳过: {str(e)[:60]}）")
    L.append("\n级别：股票/ETF=日线 · 期货=小时(h1) · 仅做多 · 期货带止损（2×ATR14 跟踪 + 5% 回撤）")

    if not dry:
        _save_prev(cur)
    return "\n".join(L)


def close_log(n=15):
    """最近的平仓记录及理由（人看决策依据）。"""
    c = smc_db.conn()
    rows = smc_db.close_log(c, n)
    lines = [f"📕 SMC 平仓记录（最近 {len(rows)} 笔）· {datetime.now():%Y-%m-%d}"]
    for name, code, kind, tf, dt, px, reason, pnl, pnl_pct in rows:
        tag = "期货" if kind == "fut" else "ETF"
        lines.append(f"  🔴 {name}（{code}）{tag} {dt} @ {px:.4g} | {reason} | 盈亏 {pnl:+,.0f}（{pnl_pct:+.2f}%）")
    c.close()
    return "\n".join(lines)


# ───────────────── FVG / OB 区块 ─────────────────
def _load_ltf(code, kind, tf):
    """读**磁盘缓存**（不联网）拿到该标的交易级别的 K 线。"""
    import pandas as pd
    from smc_config import CACHE as _C, base_freq
    fn = os.path.join(_C, f"{code}_{base_freq(tf)}.csv")
    if not os.path.exists(fn):
        return None
    try:
        d = pd.read_csv(fn)
        d["dt"] = pd.to_datetime(d["dt"])
    except Exception:
        return None
    if d is None or len(d) < 30:
        return None
    d = d.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    # 丢掉「正在形成」的最后一根（与 smc_data._drop_inprogress 同口径），避免拿半根 K 算缺口
    try:
        if pd.Timestamp(d["dt"].iloc[-1]) > pd.Timestamp(datetime.now()) + pd.Timedelta(seconds=5):
            d = d.iloc[:-1].reset_index(drop=True)
    except Exception:
        pass
    return d


def _zones_one(code, kind, tf):
    """算单个标的的 FVG / OB + 买卖点确认（只用缓存数据；失败返回 None，绝不拖垮播报）。"""
    key = (code, tf)
    if key in _ZCACHE:
        return _ZCACHE[key]
    out = None
    try:
        import smc_core
        d = _load_ltf(code, kind, tf)
        if d is not None:
            fr = smc_core.Frame(d, 1)
            out = fr.zones()                       # <= 最后一根，无 look-ahead
            out["signals"] = fr.signals()          # 🎯 不破 + 方向确认
    except Exception as e:
        print(f"zones {code} 计算失败: {str(e)[:80]}", flush=True)
        out = None
    _ZCACHE[key] = out
    return out


def zones_block(items=None, max_items=None, near=None, force_all=False, header=None,
                signal_only=False, win=None, need=None):
    """把各标的「临近现价」的 FVG / OB 输出成若干行（供快照/开盘播报/增量消息用）。

    items: [(code, name, kind, tf), ...]；默认全标的。
    仅列「有 zone 距现价 <= near%」的标的，按最近距离升序取前 max_items 个。
    signal_only=True → 只报「买点确认 / 压制有效 / 破位」的标的（没有就不列）。
    """
    if not ZONES_ON:
        return []
    max_items = max_items or ZONES_MAX_IT
    near = ZONES_NEAR_PCT if near is None else near
    if items is None:
        items = [(it["code"], it["name"], it["kind"], level_of(it)) for it in INSTRUMENTS]
    HOT = {"buy", "reject", "broken", "breakout"}
    cand = []
    for code, name, kind, tf in items:
        z = _zones_one(code, kind, tf)
        if not z:
            continue
        sigs = z.get("signals") or []
        if signal_only:
            if not any(s.get("state") in HOT for s in sigs):
                continue
        # ⚠️ 播报只列**近端**（fresh）区间，避免长期未破的几十个区间刷屏。
        #    长期未破（sustained）留在数据里，供「买卖点确认」判断使用，不逐条罗列。
        _fv = [x for x in (z.get("fvgs") or []) if x.get("fresh", True)]
        _ob = [x for x in (z.get("obs") or []) if x.get("fresh", True)]
        zones = _fv[:3] + _ob[:3]
        ds = [abs(x.get("dist_pct")) for x in zones if x.get("dist_pct") is not None]
        best = min(ds) if ds else 99.0
        if (not force_all) and (not signal_only) and best > near:
            continue
        cand.append((best, code, name, tf, z, len(zones)))
    if not cand:
        if signal_only:
            return []
        one = (items is not None and len(items) <= 2)
        msg = ("📐🧱 FVG/OB：该标的现价附近无有效缺口/订单块" if one
               else "📐🧱 FVG/OB：各标的现价附近均无有效缺口/订单块")
        return ([header] if header else []) + [msg]
    cand.sort(key=lambda x: x[0])
    n_all = len(cand)
    cand = cand[:max_items]
    L = [header] if header else []
    for best, code, name, tf, z, _n in cand:
        px = z["close"]
        at = z.get("atr")
        at_s = ""
        if at:
            at_s = (f" · ATR {at:.1f}" if px >= 1000 else (f" · ATR {at:.2f}" if px >= 100 else f" · ATR {at:.3f}"))
        L.append(f"  ▪️ {name}（{code}）[{tf}] 现价 {smc_core_px(px)}{at_s}")
        lines, _s = _zones_text(z.get("fvgs"), z.get("obs"), px=px)
        L += ["     " + x for x in lines]
        if signal_only:          # 只有「只报确认」模式才在这里追一行，否则交给 signals_block
            sigs = [s for s in (z.get("signals") or []) if s.get("state") in HOT][:2]
            if sigs:
                import smc_core
                L.append("     🎯 " + "｜".join(smc_core.confirm_text(s, px=px) for s in sigs))
    if n_all > len(cand):
        L.append(f"  （另有 {n_all - len(cand)} 个标的也有缺口/订单块）")
    return L


def signals_block(items=None, max_items=None, header=None):
    """🎯 买卖点确认区：只列「不破 + 方向已确认」的标的（大佬 2026-11-08 口径）。

    看涨区：未破 + 已回踩 + 最近 3 根至少 2 条上攻迹象 → ✅ 买点确认
    看跌区：未破 + 已回抽 + 反向确认 → 🔻 压制有效
    没有确认的就一句话带过，不制造噪音。
    """
    if not ZONES_ON:
        return []
    max_items = max_items or ZONES_MAX_IT
    if items is None:
        items = [(it["code"], it["name"], it["kind"], level_of(it)) for it in INSTRUMENTS]
    import smc_core
    HOT = {"buy": 0, "reject": 1, "broken": 2, "breakout": 3}
    hit, soft = [], []
    for code, name, kind, tf in items:
        z = _zones_one(code, kind, tf)
        if not z:
            continue
        px = z["close"]
        sigs = z.get("signals") or []
        hot = [s for s in sigs if s.get("state") in HOT]
        if hot:
            hot.sort(key=lambda s: (HOT[s["state"]], -s["score"], s["age"]))
            hit.append((f"  ▪️ {name}（{code}）[{tf}] 现价 {smc_core_px(px)}",
                        [smc_core.confirm_text(s, px=px) for s in hot[:2]]))
        else:
            wa = [s for s in sigs if s.get("state") == "wait_up"]
            if wa:
                soft.append(f"{name}({wa[0]['score']}/4)")
    L = [header] if header else []
    if hit:
        for head, txt in hit[:max_items]:
            L.append(head)
            L += ["     🎯 " + t for t in txt]
        if len(hit) > max_items:
            L.append(f"  （另有 {len(hit) - max_items} 个标的也有确认信号）")
    else:
        s = "📐🧱 买卖点：当前无「不破+方向确认」的标的"
        if soft:
            s += f"（已回踩待上攻 {len(soft)} 个：" + "、".join(soft[:6]) + "）"
        L.append(s)
    return L


def smc_core_px(v):
    """价格显示（与 smc_core._fmt_px 同口径，单独包一层避免循环依赖）。"""
    import smc_core
    return smc_core._fmt_px(v)


def _zones_text(fvgs, obs, px=None):
    import smc_core
    return smc_core.zones_text(fvgs, obs, px=px)


def _cap(text, limit=TEXT_CAP):
    """纯文本截断（卡片 50KB / 文本 4500 字符 两个上限取保守值）。"""
    if text is None or len(text) <= limit:
        return text
    return text[:limit - 30].rstrip() + "\n…（内容过长，已截断）"


def dispatch(text, tag, use_coze=True, use_feishu=True):
    text = _cap(text)
    res = []
    fname = f"smc_{tag}_{datetime.now():%Y%m%d_%H%M}.txt"
    if use_feishu:
        res.append(send_feishu(text))       # 多尺度交易群（主）
    if use_coze:
        res.append(_coze_send(text, fname))  # Coze 群（备用）
    return res


def flush(dry=False, use_coze=True, use_feishu=True):
    """一轮只发一条合并消息（用户 2026-10-08 要求）。"""
    text, ids = build_incremental()
    if not text:
        print("无新增量事件")
        return
    print(text)
    if dry:
        return
    for r in dispatch(text, "merged", use_coze, use_feishu):
        print(r)
    c = smc_db.conn()
    smc_db.mark_notified(c, ids)
    c.close()
    print(f"已标记 {len(ids)} 条事件为已通知")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--closelog", action="store_true", help="列出最近平仓记录及理由")
    ap.add_argument("--brief", choices=["day", "night"], help="开盘播报（持仓变化+持仓列表）")
    ap.add_argument("--no-coze", action="store_true", help="只发飞书")
    ap.add_argument("--coze-only", action="store_true", help="只发 Coze 群")
    a = ap.parse_args()
    uc = not a.no_coze
    uf = not a.coze_only
    if a.closelog:
        t = close_log()
        print(t)
        if not a.dry:
            for r in dispatch(t, "closelog", uc, uf):
                print(r)
    elif a.digest:
        t = snapshot()
        print(t)
        if not a.dry:
            for r in dispatch(t, "snapshot", uc, uf):
                print(r)
    elif a.brief:
        t = open_brief(a.brief, dry=a.dry)
        print(t)
        if not a.dry:
            for r in dispatch(t, "brief_" + a.brief, uc, uf):
                print(r)
    else:
        flush(dry=a.dry, use_coze=uc, use_feishu=uf)
