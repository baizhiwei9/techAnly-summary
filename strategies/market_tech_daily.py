#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大盘技术拆解 · 日报（纯脚本 0 token）
================================================================
合并内容（2026-10-26 大佬要求把 PCR + 周线/日线中枢都归到这里）：
  ① 成交量 PCR（分指数，5日均，剔除当月合约）—— bin/opt_pcr_idx.py
  ② **五大宽基 · 周线 + 日线 · 缠论中枢与买卖点** —— 突破/跌破中枢、
     二买/二买类/三买/二卖/二卖类/三卖（`zs_block()`）
  ③ 缠论 + SMC 技术拆解（5 宽基）—— bin/chan_smc_index.py
      缠论 = 日线；SMC = 日线 + 周线
      （历史：曾含小时线60m，2026-10-26 大佬要求小时线回 19:40 宽基监控，大盘只剩日线）
  ④ MF-DFA 多分形谱宽 Δα + λ1/λ2（值 + MA5 斜率）—— 2026-11-24 替换原 R_cascade
推送：飞书**私聊**（CHAT_PUSH, env CHAN_FEISHU_CHAT，默认 oc_0e4e3335...）

用法：
  python3 strategies/market_tech_daily.py            # 生成并推送到私聊
  python3 strategies/market_tech_daily.py --no-push  # 只打印
  python3 strategies/market_tech_daily.py --no-push --no-chart --no-panel-sync --email-dry-run
  python3 strategies/market_tech_daily.py --no-email  # 不发告警邮件
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, re, glob, socket, subprocess, traceback
from datetime import datetime

# AGENTS.md 硬规矩：对外请求强制 IPv4（容器 IPv6 不通会静默卡死）
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k)
                                     if r[0] == socket.AF_INET]

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
REPO = os.environ.get("TECHANLY_DIR") or os.path.expanduser("~/techAnly-summary")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CHAT_PUSH = os.environ.get("CHAN_FEISHU_CHAT") or os.environ.get("FEISHU_CHAT_DM") or os.environ.get("FEISHU_CHAT_ID", "")


def pcr_block():
    """成交量 PCR + **持仓量 PCR**（大佬 2026-11-16：主要看持仓量）

    两段都要有、各自标明口径：
      ① 成交量 PCR（opt_pcr_idx，无条件剔当月，5 日均）
      ② 持仓量 PCR（opt_pcr_oi，**只剔到期 dte≤5 的月份**，单日）
    """
    out = []
    try:
        import opt_pcr_idx as PCR
        out.append(PCR.section_text(PCR.anchor_date(None, "evening")).strip())
    except Exception as e:
        out.append("【📊 成交量 PCR】数据暂不可用（%s）" % str(e)[:80])
    try:
        import opt_pcr_oi as PCR_OI
        out.append(PCR_OI.section_text(PCR_OI.anchor_date(None, "evening")).strip())
    except Exception as e:
        out.append("【📊 持仓量 PCR】数据暂不可用（%s）" % str(e)[:80])
    return "\n\n".join(out)


def tech_block():
    try:
        import chan_smc_index as C
        # 2026-10-26 大佬分工：20:15 大盘技术拆解**不含小时级别**，小时线归 19:40 宽基监控
        return C.build_text(include_h1=False).strip()
    except Exception as e:
        import traceback
        return "【技术拆解】生成失败\n```\n%s\n```" % traceback.format_exc()[:400]


def zs_block():
    """五大宽基 · 周线 + 日线 缠论中枢（突破/跌破）+ 买卖点
    2026-10-26 大佬要求：把日线/周线的缠论分析（有没有突破中枢、跌破中枢、
    有没有二买/二卖/三买/三卖）放到**大盘技术拆解**任务里。
    + 「如果要出现一买的机会，并且 MA40 是向上，你也要提示可能有一买机会出现」
    """
    import chan_smc_index as C
    import chan_signal_monitor as CSM
    import y1_watch as Y1
    import pandas as pd
    L = ["【📐 五大宽基 · 周线 + 日线 · 缠论中枢与买卖点】",
         "  口径：%s｜周线=W-FRI，只用已收盘周" % CSM.VER_FULL,
         "  标记：🌙突破中枢/买点（机会）｜⭐跌破中枢/卖点（风险）｜中枢内不着色",
         "  买卖点只认：二买/类二买/三买 · 二卖/类二卖/三卖",
         "  🌙可能一买 = 一买预备（还没成一买，但 MA40 已向上 + 已回踩到前低附近）"]
    pres = []
    for lv, lv_name in (("week", "周线"), ("day", "日线")):
        L.append("  ── %s 级别 ──" % lv_name)
        for name, code in C.IDX:
            try:
                dd = CSM.fetch_daily_fresh(code)
                if dd is None or len(dd) < 80:
                    L.append("    %-8s 数据不足" % name)
                    continue
                close = float(dd["close"].iloc[-1])
                if lv == "week":
                    d = dd.set_index("dt")
                    w = pd.DataFrame({
                        "open": d["open"].resample("W-FRI").first(),
                        "high": d["high"].resample("W-FRI").max(),
                        "low": d["low"].resample("W-FRI").min(),
                        "close": d["close"].resample("W-FRI").last()}).dropna().reset_index()
                    w = w[w["dt"] <= dd["dt"].max()]
                    bars = C.bars_of(w)
                    closes = w["close"].values
                else:
                    bars = C.bars_of(dd)
                    closes = dd["close"].values
                r = CSM._zs_read(bars, lv, close)
                if not r.get("ok"):
                    L.append("    %-8s 无中枢" % name)
                    continue
                zpos = r.get("zpos")
                pos = {"above": "🌙突破中枢", "below": "⭐跌破中枢", "in": "中枢内"}.get(zpos, "-")
                # 买卖点（六个）
                pts = []
                for b in (r.get("buys") or []):
                    if b.get("tag") in CSM.IND_TAGS and b.get("fresh"):
                        pts.append("🌙" + b["tag"])
                for sl in (r.get("sells") or []):
                    if sl.get("tag") in CSM.IND_TAGS and sl.get("fresh"):
                        pts.append("⭐" + sl["tag"])
                # 一买预备：还没成一买 + MA40↑ + 回踩到前低附近
                try:
                    prep = Y1.y1_prepare(C.chan_read(bars, lv), closes, close)
                except Exception:
                    prep = None
                if (prep or {}).get("ok"):
                    pts.append("🌙可能一买(MA40↑)")
                    pres.append((name, lv_name, prep))
                pt = "｜" + " ".join(sorted(set(pts))) if pts else ""
                L.append("    %-8s %9.2f  %s｜中枢 %.2f~%.2f%s" % (
                    name, close, pos, r["zd"], r["zg"], pt))
            except Exception as e:
                L.append("    %-8s 失败 %s" % (name, str(e)[:40]))
    if pres:
        L.append("  ── 🌙 一买预备（可能有一买机会）──")
        for name, lv_name, p in pres:
            L.append("    %-8s %s ｜%s" % (name, lv_name, Y1.tag(p)))
    return "\n".join(L)


def ind_block(own_key=None):
    """两个实盘所属行业 · 周线 + 日线 缠论拆解（带**该实盘真实持仓股票**）

    2026-10-26 大佬要求：早晨分析昨天两个实盘所属行业的日线/周线；
    行业要带上「我实盘里的股票」，不是龙头股、不是别的股票。

    own_key：本份报告属于哪个组合 → 自己的完整列，另一个只列**有信号**的
             （两份早报都发同一聊天，避免 40 行完全重复）
    """
    import wide_ind_report as WIR
    import chan_signal_monitor as CSM
    L = ["【🏭 两个实盘股票所属行业 · 周线 + 日线 缠论拆解】",
         "  看两件事：① 在不在中枢上下（突破/跌破/中枢内）② 有没有买卖点",
         "  口径：%s｜行业=各实盘持仓的申万二级+三级" % CSM.VER_FULL,
         "  标记：🌙突破中枢/买点｜⭐跌破中枢/卖点｜持股=**该实盘自己持仓的股票**（非龙头）"]

    def _line(r):
        stk = "、".join("%s%.1f%%" % (a, b) for a, b in (r.get("stocks") or [])[:3])
        cells, has_sig = [], False
        for lv, lv_name in (("week", "周"), ("day", "日")):
            a = r.get(lv) or {}
            if not a.get("ok"):
                cells.append("%s数据不足" % lv_name)
                continue
            pos = {"above": "🌙突破", "below": "⭐跌破", "in": "中枢内"}.get(a.get("zpos"), "-")
            if a.get("zpos") in ("above", "below"):
                has_sig = True
            pts = []
            for side, tag, _px, _ag in (a.get("points") or []):
                pts.append(("🌙" if side == "buy" else "⭐") + tag)
                has_sig = True
            # 一买预备（可能有一买机会 · MA40↑）—— 2026-10-26 大佬要求行业也要提示
            if (a.get("prep") or {}).get("ok"):
                pts.append("🌙可能一买(MA40↑)")
                has_sig = True
            cells.append("%s%s%s" % (lv_name, pos, ("｜" + " ".join(pts)) if pts else ""))
        return ("    %-14s 仓%4.1f%% ｜%s ｜持股：%s" % (
            r["name"], r.get("weight") or 0, " ｜ ".join(cells), stk or "-")), has_sig

    res = WIR.collect()
    ports = res.get("ports") or []
    # 把自己的排在前面
    ports = sorted(ports, key=lambda p: 0 if p["key"] == own_key else 1)
    for port in ports:
        own = (own_key is None) or (port["key"] == own_key)
        rows = sorted(port["rows"], key=lambda x: -(x.get("weight") or 0))
        if own:
            L.append("  ── %s（%d 个行业，全部）──" % (port["label"], len(rows)))
        else:
            L.append("  ── %s（只列有信号/买卖点的）──" % port["label"])
        shown = 0
        for r in rows:
            line, has_sig = _line(r)
            if own or has_sig:
                L.append(line)
                shown += 1
        if not own and shown == 0:
            L.append("      全部仍在中枢内、无买卖点")
    return "\n".join(L)


# ───────────────────── MF-DFA Δα + λ1/λ2 监控（2026-11-24 替换 R_cascade）───────
# 大佬 2026-11-24 08:00：「那就把那个 R_cascade 那个替换了，用那个分型谱 MFDFA。
#   那个 T 那里边就应该有这个算法，就是两个 lambda，把这个两个 lambda 这个算法加上，
#   就是两个 lambda 的斜率与值，在监控加上。」
#
# 算法唯一实现（本文件**一行算法都不写**）：
#   Δα       : charts/phase_mfdfa.mfdfa_delta_alpha / rolling_delta_alpha（N=250）
#   λ1 / λ2  : charts/rmt_rc.series_for（60 日滚动相关矩阵前两个特征值）+ slope_frame
#   告警判据 : charts/rmt_rc.build_slope_alerts（规则A 方向分叉 / 规则B 斜率变号 + 1.5× 幅度门槛）
MFDFA_TARGETS = [("沪深300", "000300.SH"), ("中证500", "000905.SH"),
                 ("中证1000", "000852.SH"), ("创业板指", "399006.SZ"),
                 ("科创50", "000688.SH")]


def _da_panel_refresh(days=560):
    """刷 λ1/λ2 依赖的全市场日收盘面板（~/.cache/rmt_panel/close_*.csv）。

    面板由 bin/rmt_panel_sync.py 按「日」增量落 CSV；cron 里没有同步任务时
    面板会停在几天前 → λ 序列过期。这里先补一次（幂等，只补缺的日期）。
    失败不阻断整段（面板里已有的旧数据仍可用）。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import rmt_panel_sync as PS
        # keep 与 days 对齐：rmt_rc.series_for(panel_days=560) 要用 560 个交易日
        n = PS.sync(days=days, keep=days, verbose=False)
        print("[mfdfa] 面板同步 +%d 日（keep=%s）" % (n, days))
        return n
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] 面板同步失败（用现有面板继续）: %s" % str(e)[:150])
        return -1


def _typ_of(code):
    return "index_sz" if str(code).upper().endswith(".SZ") else "index_sh"


def mfdfa_lambda_block(panel_sync=True):
    """MF-DFA 多分形谱宽 Δα + λ1/λ2（值 + MA5 斜率）· 五大宽基。

    失败只影响本段，不影响主报告。globals()["_MFDFA_LAMBDA"] 留给邮件用。
    """
    out = []
    da_rows, lam_rows = [], []
    series = {}
    ctx = {"da": da_rows, "lam": lam_rows, "date": None}
    try:
        sys.path.insert(0, REPO)
        from charts import rmt_rc as RRC
    except Exception as e:                                       # noqa: BLE001
        print(traceback.format_exc()[:400])
        return "【📐 MF-DFA】库不可用（%s）" % str(e)[:120]

    # ① 面板同步（λ 依赖）
    if panel_sync:
        _da_panel_refresh(560)

    # ② Δα：五宽基各一条滚动序列（N=250），最新值 + 历史分位 + P90
    for nm, code in MFDFA_TARGETS:
        try:
            df = RRC.da_series_via_lib(_typ_of(code), code)
        except Exception as e:                                   # noqa: BLE001
            print("[WARN] Δα %s: %s" % (nm, str(e)[:120]))
            df = None
        if df is None or not len(df) or df["da"].notna().sum() < 30:
            da_rows.append(dict(name=nm, code=code, da=float("nan"), pct=float("nan"),
                                p90=float("nan"), date="—"))
            continue
        s = df["da"].astype(float).dropna()
        v = float(s.iloc[-1])
        p90 = float(s.quantile(0.9))
        pct = float((s <= v).mean() * 100)
        da_rows.append(dict(name=nm, code=code, da=v, pct=pct, p90=p90,
                            date=str(s.index[-1])))
        ctx["date"] = str(s.index[-1]) if not ctx["date"] else max(ctx["date"], str(s.index[-1]))

    # ③ λ1/λ2 序列（60 日窗口）+ MA5 斜率
    _lam_errs = []
    for nm, _code in MFDFA_TARGETS:
        try:
            full = RRC.series_for(nm, win=60, verbose=False)
        except Exception as e:                                   # noqa: BLE001
            print("[WARN] λ %s: %s" % (nm, str(e)[:120]))
            _lam_errs.append('%s 取序列失败' % nm)
            continue
        if full is None or not len(full):
            _lam_errs.append('%s 序列为空' % nm)
            continue
        series[nm] = full
        try:
            sf = RRC.slope_frame(full).dropna(subset=["s1", "s2"])
        except Exception as e:                                   # noqa: BLE001
            print("[WARN] slope %s: %s" % (nm, str(e)[:120]))
            _lam_errs.append('%s slope_frame 失败' % nm)
            continue
        if not len(sf):
            continue
        r = sf.iloc[-1]
        prev_opp = bool(len(sf) > 1 and sf["s1"].iloc[-2] * sf["s2"].iloc[-2] < 0)
        lam_rows.append(dict(name=nm, date=str(sf.index[-1]),
                             l1=float(r["l1"]), l2=float(r["l2"]),
                             ma1=float(r["ma1"]), ma2=float(r["ma2"]),
                             s1=float(r["s1"]), s2=float(r["s2"]),
                             prev_opp=prev_opp))

    # ④ 告警判据（库）—— 只取各标的**最新交易日**的触发（days=1）
    trig = []
    try:
        _dd, _alerts, trig = RRC.build_slope_alerts(series, ma=RRC.SLOPE_MA,
                                                    days=1, min_amp=1.5)
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] build_slope_alerts: %s" % str(e)[:150])
        _lam_errs.append('build_slope_alerts 失败: %s' % str(e)[:80])

    # ④b 健康上报：λ 段整段拿不到 = 告警链路瞎了 → 连续失败就发告警
    #     （2026-11-27 事故：库删了 slope_frame，这里只打一行 [WARN]，
    #      日报照常推送、λ 表退化成一整行「无 RMT 面板数据」，没人发现）
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import job_health as JH
        _probs = list(_lam_errs)
        if not lam_rows and not _probs:
            _probs.append('五个宽基 λ 行全空')
        if da_rows and all(r['da'] != r['da'] for r in da_rows):
            _probs.append('五个宽基 Δα 全不可用')
        JH.report("market_tech_daily", not _probs,
                  detail='；'.join(_probs),
                  extra={'da_ok': sum(1 for r in da_rows if r['da'] == r['da']),
                         'lam_ok': len(lam_rows), 'date': ctx.get('date')})
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] job_health 上报失败: %s" % str(e)[:120])

    ctx["trig"] = trig
    ctx["series"] = series

    # ── 表格 A：Δα
    #    ⚠️ 接缝：charts/phase_mfdfa.py 一旦是坏的（远端目前只有 38 字节占位符），
    #       da_series_via_lib 返回 None → 这里是**整段跳过**，只留一行说明，
    #       不显示「数据不足」的空表（免得大佬以为算法算不出东西）。
    _da_ok = [r for r in da_rows if r["da"] == r["da"]]
    if _da_ok:
        out.append("**📐 MF-DFA 多分形谱宽 Δα**（N=250 交易日 · scales=[20,30,50,80,125] · q=[-4..4]）")
        out.append("| 宽基 | Δα | 历史分位 | P90 阈值 | 状态 |")
        out.append("|---|---|---|---|---|")
        for r in da_rows:
            if r["da"] != r["da"]:
                out.append("| %s | — | — | — | 数据不足 |" % r["name"])
                continue
            hit = r["da"] > r["p90"]
            out.append("| %s | **%.3f** | %.0f%% | %.3f | %s |"
                       % (r["name"], r["da"], r["pct"], r["p90"],
                          "🔴 破P90" if hit else "🟢 正常"))
        out.append("> Δα 越大 = 多分形性越强（波动结构越散、越难用单一 Hurst 刻画）；"
                   "**Δα > P90 即触发告警邮件**。")
        out.append("")
    else:
        out.append("**📐 MF-DFA 多分形谱宽 Δα**：⚠️ 暂不可用"
                   "（`charts/phase_mfdfa.py` 目前是占位符，等库原件）→ 本段跳过，"
                   "λ1/λ2 不受影响。")
        out.append("")

    # ── 表格 B：λ1/λ2
    rule_map = {}
    for t in trig:
        rule_map.setdefault(t["target"], []).append(t["rule"])
    out.append("**🧬 λ1 / λ2**（60 日滚动相关矩阵前两个特征值 · 斜率 = MA5.diff()）")
    out.append("| 宽基 | λ1 | λ1 斜率 | λ2 | λ2 斜率 | 判读 |")
    out.append("|---|---|---|---|---|---|")

    def _arrow(v):
        return "↑" if v > 0 else ("↓" if v < 0 else "→")

    for r in lam_rows:
        rs = rule_map.get(r["name"]) or []
        if "A" in rs:
            note = "⚠️ 方向分叉(A)"
        elif rs:
            note = "⚠️ 斜率反转(" + "/".join(rs) + ")"
        elif r["s1"] * r["s2"] < 0:
            # 今天也分叉但没被库判为「首次」→ 要么昨天就分叉，要么幅度不到门槛
            note = "分叉持续" if r.get("prev_opp") else "分叉（幅度未达门槛）"
        else:
            note = "同向"
        out.append("| %s | %.3f | %+.4f %s | %.3f | %+.4f %s | %s |"
                   % (r["name"], r["l1"], r["s1"], _arrow(r["s1"]),
                      r["l2"], r["s2"], _arrow(r["s2"]), note))
    if not lam_rows:
        out.append("| — | — | — | — | — | 无 RMT 面板数据 |")
    out.append("> λ1/λ2 = 抱团集中度；λ1 斜率↑ 且 λ2 斜率↓（或反之）= 结构分叉，"
               "规则 A/B 触发时随本邮件发告警（与 20:45 RMT 任务共用同一份幂等状态，不重复发）。")
    if ctx.get("date"):
        out.append("")
        out.append("> 数据截至 **%s**" % ctx["date"])
    ctx["txt"] = "\n".join(out)
    globals()["_MFDFA_LAMBDA"] = ctx
    return ctx["txt"]


def gsr_block():
    """股债比值 R（近一年锚）· 文字 + 出图 + 告警邮件。

    2026-11-04 大佬：「还是用这个近一年的吧。布林线触及上下轨要发邮件提醒；
    股债线出现交叉（股票上穿/下穿债券）也要发邮件。合并到晚上那个大盘的技术拆解里边，
    把这个图片也带上，都是用程序来生成，不要消耗 token」
    → 全部逻辑在 bin/gsr_alert.py（纯脚本 0 token），这里只调用并取文字/图。
    """
    try:
        import gsr_alert as GA
    except Exception as e:
        print("[WARN] gsr_alert 不可用:", str(e)[:120])
        return None
    try:
        df = GA.compute()
        sig, info = GA.detect(df)
        png = GA.make_chart(df, GA.DEF_PNG, months=3)
        md = GA.report_md(df, sig, info)
        # 告警邮件（内置 7 天去重，重复不会天天发）
        sent = False
        if sig:
            fresh = GA.dedup(sig, info['date'])
            if fresh:
                print("[gsr-email] 新告警 %s" % [k for k, _, _ in fresh])
                sent = GA.send_email(fresh, info, png)
            else:
                print("[gsr-email] 信号已在去重窗口内，跳过")
        globals()['_GSR'] = {'info': info, 'sig': sig, 'png': png, 'md': md, 'email_sent': sent}
        return {'txt': md, 'png': png, 'info': info, 'sig': sig}
    except Exception as e:
        print("[WARN] gsr_block:", str(e)[:180])
        return None


def eyby_block():
    """股债利差 EY−BY（底部区间下界 5.5pct）· 文字 + 出图 + 告警邮件。

    2026-11-05 大佬：“5.5P 以下就要报警发邮件”。
    全部逻辑在 bin/eyby_alert.py（纯脚本 0 token），这里只调用并取文字/图。
    """
    try:
        import eyby_alert as EA
    except Exception as e:
        print("[WARN] eyby_alert 不可用:", str(e)[:120])
        return None
    try:
        df = EA.compute()
        sig, info = EA.detect(df)
        png = EA.make_chart(df, EA.DEF_PNG)
        md = EA.report_md(info, sig)
        sent = False
        if sig:
            fresh = EA.dedup(sig, info['date'])
            if fresh:
                print("[eyby-email] 新告警 %s" % [k for k, _, _ in fresh])
                sent = EA.send_email(fresh, info, png)
            else:
                print("[eyby-email] 信号已在去重窗口内，跳过")
        globals()['_EYBY'] = {'info': info, 'sig': sig, 'png': png,
                             'md': md, 'email_sent': sent}
        return {'txt': md, 'png': png, 'info': info, 'sig': sig}
    except Exception as e:
        print("[WARN] eyby_block:", str(e)[:180])
        return None


def etf_flow_block():
    """五大宽基 ETF 净申赎监控 · 文字 + 触发时统一邮件。

    2026-11-13 大佬：「就在大盘技术拆解这块，加一个这几个宽基指数的 ETF 的
    流入流出的监控…数据还是用那个 DataHub 啊，不要自己写。
    如果连续 3 天流入流出同向，并且净流入每天大于 100 亿，并且持续增大，
    那就发提醒邮件。流出也是，如果大于 100 亿，也要发邮件提醒。
    是 5 个宽基指数都检查完了再统一发邮件。」

    全部逻辑在 bin/wide_etf_alert.py（纯脚本 0 token），
    数据层是 DataHub `etf_net_flow`（本文件不做任何抓取）。
    """
    try:
        import wide_etf_alert as A
    except Exception as e:
        print("[WARN] wide_etf_alert 不可用:", str(e)[:120])
        return None
    try:
        r = A.run(send=True)          # 内部：5 个全查完 → 有触发才统一发一封（带 7 天去重）
        globals()['_ETF_FLOW'] = r
        return {'txt': r.get('block') or '', 'fired': r.get('fired') or [],
                'mail_sent': r.get('mail_sent')}
    except Exception as e:
        print("[WARN] etf_flow_block:", str(e)[:180])
        return None


def _mfdfa_state_path():
    return os.environ.get("RMT_ALERT_STATE",
                          os.path.join(WS, ".cache", "rmt_alert_state.json"))


def _mfdfa_load_state():
    fp = _mfdfa_state_path()
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                            # noqa: BLE001
        return {}


def _mfdfa_save_state(st):
    fp = _mfdfa_state_path()
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, fp)


def _mfdfa_make_chart(only=None, out_name="mfdfa_alert_chart.png"):
    """画 MF-DFA 多分形谱图（走势 + Δα + λ1/λ2 三栏）：各标的出一张 → 2 列网格合并。

    only    : 需要画的标的名列表（None = 五个宽基全画）
    out_name: 合并图文件名（日报用 mfdfa_panel.png；告警邮件用 mfdfa_alert_chart.png）
    返回 png 路径或 None。
    """
    info = globals().get("_MFDFA_LAMBDA") or {}
    da_rows = info.get("da") or []
    lam_rows = info.get("lam") or []
    series = info.get("series") or {}
    names = only or [r["name"] for r in lam_rows] or [r["name"] for r in da_rows]
    names = [n for n in names if n]
    if not names:
        return None
    try:
        sys.path.insert(0, REPO)
        from charts import rmt_rc as RRC
        from charts import mfdfa_rc as MRC
    except Exception as e:                                       # noqa: BLE001
        print("[mfdfa-chart] 库不可用：%s" % str(e)[:120])
        return None

    os.makedirs(os.path.join(WS, ".cache"), exist_ok=True)
    da_map = {r["name"]: r for r in da_rows}
    lam_map = {r["name"]: r for r in lam_rows}
    pngs = []
    for nm in names[:5]:
        try:
            full = series.get(nm)
            if full is None:
                full = RRC.series_for(nm, win=60, verbose=False)
            sf = RRC.slope_frame(full, ma=RRC.SLOPE_MA)
            # 自身收盘（宽度 × 长度对齐）
            close = RRC.own_close_via_lib("index_sh" if nm != "创业板指" else "index_sz",
                                          {"沪深300": "000300", "中证500": "000905",
                                           "中证1000": "000852", "创业板指": "399006",
                                           "科创50": "000688"}.get(nm))
            da = RRC.da_series_via_lib("index_sh" if nm != "创业板指" else "index_sz",
                                       {"沪深300": "000300", "中证500": "000905",
                                        "中证1000": "000852", "创业板指": "399006",
                                        "科创50": "000688"}.get(nm))
            if close is None or not len(close) or da is None or not len(da):
                print("[mfdfa-chart] %s 缺收盘或 Δα，跳过" % nm)
                continue
            da_s = da["da"] if hasattr(da, "columns") else da
            p = os.path.join(WS, ".cache", "mfdfa_alert_%s.png" % nm)
            MRC.plot_from_series(nm, close, da_s, sf, p, lookback=250,
                                 da_p90=(da_map.get(nm) or {}).get("p90"))
            pngs.append(p)
        except Exception as e:                                   # noqa: BLE001
            print("[mfdfa-chart] %s 出图失败：%s" % (nm, str(e)[:130]))

    if not pngs:
        return None
    if len(pngs) == 1:
        return pngs[0]
    # 多张 → **2 列网格**拼成一张。
    #   2026-11-24：原来纵向叠 5 张 = 1820×7560（邮件里根本看不清），
    #   改成 2 列网格后 3 行内收，单张高度可控、字也不缩。
    try:
        from PIL import Image
        ims = [Image.open(x).convert("RGB") for x in pngs]
        ncol = 2 if len(ims) > 1 else 1
        # 统一单格宽度（取最宽的），高度按比例缩
        cw = max(i.width for i in ims)
        cells = [i.resize((cw, int(i.height * cw / i.width))) for i in ims]
        ch = max(i.height for i in cells)
        nrow = (len(cells) + ncol - 1) // ncol
        canvas = Image.new("RGB", (cw * ncol, ch * nrow), "white")
        for k, im in enumerate(cells):
            r_, c_ = divmod(k, ncol)
            canvas.paste(im, (c_ * cw, r_ * ch))
        out = os.path.join(WS, ".cache", out_name)
        canvas.save(out, "PNG")
        print("[mfdfa-chart] %d 张 → %d×%d 网格 %s"
              % (len(cells), nrow, ncol, canvas.size))
        return out
    except Exception as e:                                       # noqa: BLE001
        print("[mfdfa-chart] 拼接失败（用第一张）：%s" % str(e)[:120])
        return pngs[0]


def mfdfa_email_alerts(dry_run=False):
    """MF-DFA Δα + λ1/λ2 斜率告警邮件（任一触发 → **合并成一封**）。

    幂等口径与 20:45 的 bin/rmt_rc_alert.py **共用同一个状态文件**
    `.cache/rmt_alert_state.json`，key 一律 `标的|规则|日期`：
      DA90         ← 本文件（Δα > 该标的历史 90 分位）
      S:A/S:B1/S:B2 ← 20:45 的 rmt_rc_alert.py 与 本文件 都写同一批 key
    → 同一事件只发一次，两个任务谁先跑谁发，另一个自动跳过。

    2026-11-24 决策：λ 斜率**不再单独发一封**，只在事件新出现时并进这一封；
    原来 R_cascade 的 R>1.2 规则随之废弃（R_cascade 已被 MF-DFA 取代）。
    """
    info = globals().get("_MFDFA_LAMBDA") or {}
    da_rows = info.get("da") or []
    lam_rows = info.get("lam") or []
    trig = info.get("trig") or []
    if not da_rows and not lam_rows:
        print("[mfdfa-email] 无数据，不发邮件")
        return False

    state = _mfdfa_load_state()
    new_state = dict(state)
    lines, tags, fired = [], [], []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 1) Δα > P90
    da_hit = []
    for r in da_rows:
        if r["da"] != r["da"] or r["p90"] != r["p90"]:
            continue
        if r["da"] <= r["p90"]:
            continue
        k = "%s|DA90|%s" % (r["name"], r["date"])
        da_hit.append((k, r))
    fresh_da = [(k, r) for k, r in da_hit if k not in state]
    if fresh_da:
        lines.append("## 一、MF-DFA Δα 破 90 分位\n")
        for k, r in fresh_da:
            lines.append("- 🔴 **%s** Δα=**%.3f** ｜ 历史分位 %.0f%% ｜ P90=%.3f"
                         % (r["name"], r["da"], r["pct"], r["p90"]))
            new_state[k] = now
            fired.append((k, "da"))
        tags.append("Δα破P90×%d" % len(fresh_da))
    print("[mfdfa-email] Δα：%d 个破P90，其中新的 %d 个" % (len(da_hit), len(fresh_da)))

    # ── 2) λ1/λ2 斜率（判据全部来自库 build_slope_alerts）
    fresh_s = []
    for t in trig:
        k = "%s|%s|%s" % (t["target"], "S:" + t["rule"], t["date"])
        if k not in state:
            fresh_s.append((k, t))
    if fresh_s:
        lines.append("## 二、λ1/λ2 MA5 斜率\n")
        for k, t in fresh_s:
            lines.append("- ⚠️ " + t["text"])
            new_state[k] = now
            fired.append((k, "slope"))
        tags.append("λ斜率×%d" % len(fresh_s))
    print("[mfdfa-email] λ 斜率：共 %d 条，其中新的 %d 条" % (len(trig), len(fresh_s)))

    if not fresh_da and not fresh_s:
        print("[mfdfa-email] 无新触发，不发邮件（状态共 %d 条）" % len(state))
        return False

    # 附上监控表原文，邮件里能一眼看全
    body = ("# MF-DFA / λ1·λ2 告警 · %s\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M")
            + "\n".join(lines)
            + "\n\n---\n\n" + info.get("txt", "")
            + "\n\n> 口径：Δα = charts/phase_mfdfa.rolling_delta_alpha(N=250)；"
              "λ1/λ2 = 60 日滚动相关矩阵前两个特征值（charts/rmt_rc）；"
              "斜率 = MA5.diff()，幅度门槛 |slope| > 1.5×近60日滚动中位数。"
              "\n\n**📎 监控图见附件**（走势 + Δα + λ1/λ2 三栏，红色竖虚线 = Δα 破 P90 的位置）")

    subj = "[MF-DFA告警] %s · %s" % (info.get("date") or "—", " / ".join(tags))
    if dry_run:
        print("\n" + "=" * 60 + "\n[DRY-RUN] %s\n" % subj + "=" * 60)
        print(body)
        print("=" * 60)
        return False

    fp = os.path.join(WS, ".cache", "mfdfa_alert_body.md")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(body)

    # 出图（只有触发的标的；大佬 2026-11-24："邮件里带上这个图，要不然我都看不清"）
    _fire_names = []
    for k, kind in fired:
        nm = k.split("|")[0]
        if nm not in _fire_names:
            _fire_names.append(nm)
    chart = _mfdfa_make_chart(only=_fire_names)
    if chart:
        print("[mfdfa-email] 图 → %s（%.0f KB）"
              % (chart, os.path.getsize(chart) / 1024.0))
    else:
        print("[mfdfa-email] ⚠️ 出图失败，本封邮件无附件")

    cmd = [sys.executable, os.path.join(WS, "scripts", "send_email.py"),
           "--subject", subj, "--body-file", fp]
    if chart:
        cmd += ["--attach", chart]
    ok = False
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        print((r.stdout or "")[-500:])
        ok = r.returncode == 0
        if not ok:
            print("[mfdfa-email] 发送失败：%s" % (r.stderr or "")[-400:])
    except Exception as e:                                       # noqa: BLE001
        print("[mfdfa-email] 发送异常：%s" % str(e)[:150])
    if ok:
        _mfdfa_save_state(new_state)
        print("[mfdfa-email] 已发送：%s（状态 %d 条）" % (subj, len(new_state)))
    return ok


def feishu_push(text, title):
    import urllib.request
    c = json.load(open(os.path.join(os.path.dirname(WS), "openclaw.json"), encoding="utf-8"))["channels"]["feishu"]
    b = json.dumps({"app_id": c["appId"], "app_secret": c["appSecret"]}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=b, headers={"Content-Type": "application/json"})
    tok = json.loads(urllib.request.urlopen(req, timeout=20).read())["tenant_access_token"]
    card = {"config": {"wide_screen_mode": True},
            "header": {"template": "blue",
                       "title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": "📎 来自 OpenClaw.Deepseek"},
                         {"tag": "markdown", "content": text}]}
    body = json.dumps({"receive_id": CHAT_PUSH, "msg_type": "interactive",
                       "content": json.dumps(card)}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=body, headers={"Authorization": "Bearer " + tok,
                            "Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print("feishu push:", r.get("code"), r.get("msg"))
    return r.get("code") == 0


def make_charts():
    """**日线合成一张图**（2026-10-26 大佬口径：大盘报告不含小时级别）

    历史：2026-10-24 曾定稿「小时线 1 张 + 日线 1 张」；
    2026-10-26 改为「小时的还是监控吧」→ 小时线回到 19:40 宽基监控，
    本报告只出**日线 1 张**（有信号的只拼有信号的，没信号拼全部 5 个当走势参考）。
    返回 [(标签, png路径, 级别)]；失败不影响文字推送。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    outs = []
    try:
        import chan_smc_index as C
    except Exception as e:
        print("[WARN] 模块不可用:", str(e)[:120])
        return outs
    try:
        import chan_panorama as PANO
    except Exception as e:
        print("[WARN] 全景图模块不可用:", str(e)[:120])
        return outs

    # ⚠️ 2026-11-27 大佬：「这个第一张图是啥意思？我没必要，把它删了」
    #    panorama_day = 5 指数纵向拼的「日线全景图」，与后面的
    #    wide_day（日线中枢图）、chan_stack（缠论五指数图）内容重复，
    #    信息量最少却排第一 → **默认不出**；要临时要回来用 --with-panorama。
    if "--with-panorama" not in sys.argv:
        print("[chart] panorama 已按 2026-11-27 口径跳过（--with-panorama 可恢复）")
        return outs

    for lv, tag_lv in (("day", "日线"),):
        names, sigs = [], []
        for name, _code in C.IDX:
            sig = fresh_signal(name, lv)
            if sig:
                names.append(name)
                sigs.append("%s %s" % (name, sig))
        # 没有信号 → 也出图（全部 5 个指数，当走势参考）
        no_sig = not names
        if no_sig:
            names = [x[0] for x in C.IDX]
            extra = "　｜　无买卖点（走势参考）"
            lbl = "%s · 无买卖点" % tag_lv
        else:
            extra = "　｜　" + "、".join(sigs)
            lbl = "%s · %s" % (tag_lv, "、".join(sigs))
        try:
            p = PANO.draw_panorama(lv, n=90, names=names, title_extra=extra)
            # 2026-11-27 大佬：「也不告诉我是哪个指数」→ 标签里写全指数名，
            # 单指数时直接「日线 · 创业板指」，多指数「日线 · 5指数」。
            if len(names) == 1:
                lbl = "%s · %s" % (tag_lv, names[0])
            elif names:
                lbl = "%s · %d指数" % (tag_lv, len(names))
            print("[chart]", lbl, p)
            outs.append((lbl, p, lv))
        except Exception as e:
            print("[WARN] 拼图失败 %s: %s" % (tag_lv, str(e)[:150]))
    if not outs:
        print("[chart] 日线无图")
    return outs


def stage_aligned_sig(ck, key, P):
    """阶段方向仲裁后的买卖点签名。方向与缠论阶段相反 → 返回 ""（只是历史路标）。

    ⚠️ 2026-11-27：创业板指阶段=⭐中枢下方下跌(趋势破坏)、现价已在「一买 2155.01」
      上方 13.4%（早走远），point_sig 只按新鲜度(12根)过滤仍返回一买 →
       ① 出了只含 1 个子图的全景图 ② 该指数被从「没买卖点」清单里踢掉。
       统一在这里仲裁，fresh_signal 和 build_no_signal_note 都走它，口径只此一处。
    """
    try:
        import chan_signal_monitor as M
        sig, tag, px, age, is_buy = M.point_sig(ck, key, P)
    except Exception:
        return ""
    if not sig:
        return ""
    try:
        import chan_smc_index as _C
        _dir = _C._stage_dir(ck)
    except Exception:
        _dir = None
    if _dir is not None and (_dir != bool(is_buy)):
        return ""
    return sig


def fresh_signal(name, level):
    """该指数该级别当前的买卖点签名（如 "一买@6440.29"）；无则 ""。"""
    try:
        import chan_smc_index as C
        import chan_signal_monitor as M
        if level == "h1":
            df = M.fetch_60_fresh(C.IDX_SH[name])
            key = "小时"
        else:
            df = M.fetch_daily_fresh(dict((x[0], x[1]) for x in C.IDX)[name])
            key = "日"
        if df is None or len(df) < 60:
            return ""
        ck = C.chan_read(C.bars_of(df), "h1" if level == "h1" else "day")
        if not ck.get("ok"):
            return ""
        P = float(df["close"].iloc[-1])
        return stage_aligned_sig(ck, key, P)
    except Exception as e:
        print("[WARN] fresh_signal %s %s: %s" % (name, level, str(e)[:100]))
        return ""


def build_no_signal_note():
    """生成「没机会的级别差多少」文字说明（2026-10-24 用户要求：
    没机会的不发图，但要用文字说清楚差多少）

    2026-10-26 大佬分工：大盘报告**只留日线**，rows_h1 相关全删。
    """
    try:
        import chan_smc_index as C
        import chan_signal_monitor as M
    except Exception:
        return ""
    rows_day = []
    for name, code in C.IDX:
        try:
            df = M.fetch_daily_fresh(code)
            key = "日"
            if df is None or len(df) < 60:
                continue
            ck = C.chan_read(C.bars_of(df), "day")
            if not ck.get("ok"):
                continue
            P = float(df["close"].iloc[-1])
            # ⚠️ 2026-11-27：改用 stage_aligned_sig（方向与阶段一致才算有信号），
            #   否则创业板指被判「有买点」→ 从本清单消失（卡片上只剩 4 个指数）。
            if stage_aligned_sig(ck, key, P):
                continue          # 有信号的已单独出图，这里只列没信号的
            zs = ck.get("zs")
            if not zs:
                continue
            zg, zd = zs[1], zs[0]
            dist_zg = (zg - P) / P * 100       # 到中枢上沿（正=还要涨）
            if dist_zg > 0:
                hint = "距中枢上沿 ZG %.2f 还差 **+%.2f%%**" % (zg, dist_zg)
            else:
                hint = "已在上沿上方 **%+.2f%%**" % dist_zg
            # 一买预备：可能有一买机会（MA40 向上）→ 不是「还早」，要提
            prep_txt = ""
            try:
                import y1_watch as _Y1
                pr = _Y1.y1_prepare(ck, df["close"].values, P)
                if (pr or {}).get("ok"):
                    prep_txt = " ｜🌙可能一买(MA40↑)：前低 %.2f，距 %+.1f%%" % (
                        pr.get("prev_low") or 0, pr.get("dist") or 0)
            except Exception:
                pass
            _stg = (ck.get("stage") or "").split("（")[0].strip()   # 取括号前的短名，避免截断出半个括号
            rows_day.append("%s %.2f ｜ %s ｜ 到买点还早 ｜ %s%s"
                            % (name, P, _stg, hint, prep_txt))
        except Exception:
            continue
    L = []
    if rows_day:
        L += ["**⏳ 日线（无买卖点）**"] + ["- " + r for r in rows_day]
    return "\n".join(L)


def trim_index_body(body):
    """指数小节**只保留「日线近40日」ASCII 走势**。

    2026-11-14 23:13 大佬：「其次这个大盘技术拆解，第四个栏目，就是那个买点分析全不要了，
    只留那个大盘近 40 日那个价格的那个 ascii 码变动就行了。」

    → 第四栏目（逐指数明细）里，每个指数**只留 `日线近40日:` 这一行**；
      近10日放大行、数据区间、ASCII 阶梯示意图、缠论分型版结论、SMC 全部不再进卡片
      （这些内容图上已有笔/中枢/买卖点，SMC 仍在图文报告里可查）。
    """
    keep = []
    for ln in body.splitlines():
        st = ln.strip()
        if st.startswith("日线近40日"):
            keep.append(st)
    return "\n".join(keep)


def build_card_text(txt, d):
    """把纯文本日报转成 QQ markdown 正文。
    2026-11-14 大佬：第四个栏目只留「日线近40日」ASCII，买点分析全不要。"""
    out = []
    out.append("**级别**：日线")
    out.append("**版本**：缠论·分型版（包含处理→顶底分型→笔→中枢ZG/ZD→背驰）")
    out.append("")
    in_ladder = False
    in_idx = False          # 是否处于「逐指数明细」段落内
    for ln in txt.splitlines():
        s = ln.rstrip()
        st = s.strip()
        if not st:
            if out and out[-1] != "":
                out.append("")
            continue
        if st.startswith("📋 口径说明"):
            continue          # 上面已经写了级别+版本
        if st.startswith("示意图"):
            in_ladder = True
            continue
        if in_ladder:
            if "┤" in ln:
                continue
            in_ladder = False
        if "┤" in ln:
            continue          # 其他 ASCII 图也丢掉
        if st == "─" * 30:
            out.append("***")
            continue
        m = re.match(r"══════ 【([^】]+)】收 ([\d.]+) ══════", st)
        if m:
            in_idx = True
            out.append("")
            out.append("### 📐 %s　收 %s" % m.groups())
            continue
        if st == "─" * 30:
            in_idx = False
            out.append("***")
            continue
        if st.startswith("【📊 成交量 PCR"):
            out.append("### 📊 一、成交量 PCR")
            continue
        if st.startswith("🎯 一、有机会的指数"):
            out.append("### 🎯 二、有机会的指数")
            continue
        if st.startswith("⚠️ 二、有风险的指数"):
            out.append("### ⚠️ 三、有风险的指数")
            continue
        # ⚠️ 2026-11-14 大佬：第四栏目（逐指数）**只留日线近40日 ASCII**，
        #    分型版结论 / SMC / 阶梯示意图 / 数据区间 全不要。
        if in_idx:
            if st.startswith("日线近40日"):
                out.append(st)
            continue
        if st.startswith(("日线近10日", "数据：", "示意图", "【分型版", "SMC", "买点确认", "一买预备")):
            continue
        if "┤" in ln:
            continue
        if st.startswith("──"):
            continue
        out.append(st)
    return "\n".join(out)


def push_qq_md(d, txt=None, charts=None):
    """QQ 通道：发 **Markdown 卡片消息**（msg_type=2，图片内嵌 qqbot-media://）

    2026-10-24 用户要求：「你还是换成交互式卡片形式的消息吧。这个交互式卡片，我记着可以配图的」
    → QQ 侧等价的「卡片」就是 Markdown 消息，一条消息里既有排版文字又有图。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import qq_md_card as QQ
    except Exception as e:
        print("[WARN] qq_md_card 不可用:", str(e)[:150])
        return False
    if txt is None:
        cands = sorted(glob.glob(os.path.join(WS, ".cache/market_tech_*.txt")),
                       key=os.path.getmtime, reverse=True)
        txt = open(cands[0], encoding="utf-8").read() if cands else ""
    body = build_card_text(txt, d)
    # 附图：日线合图（2026-10-26 起不再有小时线图）
    imgs = []
    for item in (charts or []):
        path = item[1] if len(item) > 1 else None
        if path and os.path.exists(path):
            # 带上中文标签当图片说明（2026-11-27 大佬：不告诉我是哪个指数）
            _lbl = item[0] if item else ''
            imgs.append((path, _lbl) if _lbl else path)
    # 加上「没买卖点的级别差多少」
    ns = build_no_signal_note()
    if ns:
        body += "\n\n***\n**📉 没买卖点的级别 · 还差多少**\n\n" + ns
    _ni = sum(1 for it in (charts or []) if len(it) > 1 and os.path.exists(it[1]))
    body += ("\n\n> 🌙 = 买点（越多越强）　⭐ = 卖点（越多越重）\n"
             "> 纯脚本生成 · 0 token%s" % ("　｜　配图 %d 张" % _ni if _ni else ""))
    return QQ.send_md("🧭 大盘技术拆解 · %s" % d, body, imgs=imgs)


def _md_text_to_blocks(text):
    """Markdown 文本 → 卡片元素列；连续 `|` 行转 column_set，其余保持 md。

    飞书卡片 markdown 元素不支持管道表格（memory/2026-11-14.md 教训）。
    """
    out, chunk, i = [], [], 0
    lines = (text or "").splitlines()
    n = len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip().startswith("|"):
            chunk.append(ln)
            i += 1
            continue
        tbl = []
        while i < n and lines[i].strip().startswith("|"):
            tbl.append(lines[i].strip())
            i += 1
        if chunk:
            out.append({"type": "md", "text": "\n".join(chunk).strip()})
            chunk = []
        out.extend(_md_table_to_cols(tbl))
    if chunk:
        out.append({"type": "md", "text": "\n".join(chunk).strip()})
    return [b for b in out if b.get("text") != "" or b.get("tag")]


def _md_table_to_cols(tbl_lines):
    """把管道 Markdown 表格行 → 飞书卡片的 column_set 行。

    输入：["| 宽基 | Δα | ...", "|---|---|...", "| 沪深300 | 0.277 | ..."]
    输出：[{"tag":"column_set", ...} × 行数]（表头用 lark_md **加粗**）
    为什么：飞书卡片 markdown 不支持管道表格（memory/2026-11-14.md），
    直接塞会渲染成 `|---|---|` 乱码。
    """
    rows = []
    for ln in tbl_lines:
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue                      # 分隔行 |---| 丢掉
        rows.append(cells)
    if not rows:
        return []
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    # 列宽：首列稍宽，其余等分
    weights = [max(2, int(10 / ncol) + 1)] + [max(2, int(10 / ncol))] * (ncol - 1)
    aligns = ["left"] + ["left"] * (ncol - 1)
    out = []
    for k, r in enumerate(rows):
        cells = [(c if k else ("**%s**" % c if c else "")) for c in r]
        out.append({"tag": "column_set", "flex_mode": "none",
                    "horizontal_spacing": "small",
                    "columns": [
                        {"tag": "column", "width": "weighted", "weight": w,
                         "vertical_align": "center",
                         "elements": [{"tag": "div", "text": {
                             "tag": "lark_md", "content": str(t), "text_align": a}}]}
                        for t, w, a in zip(cells, weights, aligns)]})
    return out


def push_combined(txt, title, charts, d):
    """把「文字报告 + 配图」合成**一条**飞书交互式卡片。

    结构：
      📎 署名（卡片第一行）
      ── 一、成交量 PCR（表格文字）
      ── 二、结论速览
      ── 三、各指数明细（每个指数：结论文字 → 若有信号，紧跟它的 K线图）
      ── 附注

    charts: [(标签, 路径, 级别)]，标签形如 "沪深300·日线·一买@4115.07"
    """
    try:
        import feishu_card as FC
    except Exception as e:
        print("[WARN] feishu_card 不可用:", str(e)[:120])
        return False

    # 图表按**级别**分桶：{"day": [(路径, 标签)], "week": [...], "rc": [...]}
    # 2026-10-26 大佬分工：已无 h1 图（小时线回 19:40 宽基监控）
    by_level = {}
    for item in charts or []:
        lbl, path = item[0], item[1]
        lv = item[2] if len(item) > 2 else ("day" if "日线" in lbl else "h1")
        by_level.setdefault(lv, []).append((path, lbl))

    blocks = []
    # ── 正文：把纯文本报告按段落切成 markdown，并在每个指数明细里插入它的图
    lines = txt.splitlines()
    buf = []

    def flush():
        if not buf:
            return
        blocks.extend(_md_text_to_blocks("\n".join(buf).strip()))
        buf.clear()

    idx_hdr = re.compile(r"^══════ 【([^】]+)】收 ([\d.]+) ══════$")
    cur_sec = None
    for ln in lines:
        s = ln.rstrip()
        if not s.strip():
            buf.append("")
            continue
        if s.startswith("【📊 成交量 PCR"):
            flush()
            buf.append("**" + s.strip("【】") + "**")
            continue
        if s.strip() == "─" * 30:
            flush()
            blocks.append({"type": "hr"})
            continue
        m = idx_hdr.match(s.strip())
        if m:
            # 进入某个指数的明细：先把前面的文字吐出去，再把这个指数的图插进来
            flush()
            nm, close = m.groups()
            buf.append("**📐 %s　收 %s**" % (nm, close))
            continue
        # 指数小节结束（遇到下一个指数或末尾）时插图：这里用「行内标记」延迟处理
        buf.append(s)

    flush()

    # ⚠️ 上面按行累积会把所有段落糊在一起；改成更稳的做法：
    #    重新按「指数块」切分 txt，块内文字 + 该块的图 作为一组
    blocks = []
    parts = re.split(r"(══════ 【[^】]+】收 [\d.]+ ══════)", txt)
    head = parts[0]
    # 头部（PCR + 口径 + 榜单）
    for seg in re.split(r"─{30}", head):
        t = seg.strip()
        if not t:
            continue
        t = t.replace("【📊 成交量 PCR · 分指数 · 5日均 · 剔除当月合约】",
                      "**📊 一、成交量 PCR**（分指数 · 5日均 · 剔除当月合约）")
        t = t.replace("🎯 一、有机会的指数【分型版·缠论结构】", "**🎯 二、有机会的指数**")
        t = t.replace("⚠️ 二、有风险的指数【分型版·缠论结构】", "**⚠️ 三、有风险的指数**")
        blocks.extend(_md_text_to_blocks(t))
        blocks.append({"type": "hr"})

    # 各指数块
    for i in range(1, len(parts), 2):
        hdr = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        m = re.match(r"══════ 【([^】]+)】收 ([\d.]+) ══════", hdr)
        if not m:
            continue
        nm, close = m.groups()
        blocks.append({"type": "md", "text": "**📐 四.%d %s　收 %s**"
                                             % ((i + 1) // 2, nm, close)})
        # 该指数的文字：**只留「日线近40日」ASCII 走势**（2026-11-14 大佬要求，
        # 买点分析/示意图/SMC 等全不要；图上已有笔/中枢/买卖点）
        body_txt = trim_index_body(body)
        if body_txt:
            blocks.extend(_md_text_to_blocks(body_txt))
        blocks.append({"type": "hr"})

    # ── 走势图：周线中枢 + 日线 + R_cascade（2026-10-26 起无小时线图）
    if by_level:
        blocks.append({"type": "md", "text": "**🖼 五、走势图**"})
        # 2026-11-17 大佬：单独那张 R_cascade 图不要了 ——
        # 「五大宽基缠论」图上方已经是 R_cascade + VIX/偏度分位的双轴混图，重复了。
        for lv, lv_txt in (("week", "周线中枢"), ("day", "日线"),
                           ("chan5", "五大宽基缠论买卖点"),
                           ("mfdfa", "五大宽基 MF-DFA 多分形谱（Δα + λ1/λ2）"),
                           ("topo", "五大宽基拓扑结构总览"),
                           ("gsr", "股债比值 R"), ("eyby", "股债利差 EY−BY")):
            if lv not in by_level:
                continue
            for path, lbl in by_level[lv]:
                blocks.append({"type": "md", "text": "**%s**" % lbl})
                blocks.append({"type": "_img", "path": path, "alt": lbl})
        blocks.append({"type": "hr"})
    # MF-DFA Δα / λ1·λ2 文字表（2026-11-24 替换原 R_cascade 表）
    # ⚠️ 这段在 txt 里挂在最后一个指数块尾部，会被 trim_index_body 丢掉
    #    （那里只留「日线近40日」一行）→ 必须在这里单独补进卡片。
    #    表格走 _md_text_to_blocks 转 column_set（卡片不支持管道表格）。
    _ml = globals().get("_MFDFA_LAMBDA") or {}
    if _ml.get("txt"):
        blocks.append({"type": "hr"})
        blocks.extend(_md_text_to_blocks(_ml["txt"]))

    # 附注 + 没机会的级别差多少（文字说明）
    ns = build_no_signal_note()
    if ns:
        blocks.append({"type": "md", "text": "**📉 没买卖点的级别 · 还差多少**"})
        blocks.extend(_md_text_to_blocks(ns))
        blocks.append({"type": "hr"})
    blocks.append({"type": "md",
                   "text": "> 级别：日线 + 周线中枢　｜　版本：缠论·分型版\n"
                           "> 🌙 = 买点（越多越强）　⭐ = 卖点（越多越重）\n"
                           "> **缠论归缠论、拓扑归拓扑**：缠论图看笔/中枢/买卖点；"
                           "拓扑图先看顶上四象限（聚合度×动量）定机会/风险，"
                           "再逐行看阵营块图、聚合度刻度尺、联动网络（里奇曲率）、阵营内/间相关走势、"
                           "Betti 分组、里奇流（滚动平均曲率：↑聚合 / ↓撕裂）\n"
                           "> 拓扑图时间窗口默认 60 个交易日（可用 --days 20/40/60/120 切换）\n"
                           "> 纯脚本生成 · 0 token"})

    # 先把 _img 占位转成真实 img_key
    try:
        tok = FC.token()
    except Exception as e:
        print("[WARN] 取 token 失败:", str(e)[:120])
        return False
    final_blocks = []
    n_img = 0
    for b in blocks:
        if b.get("type") == "_img":
            key = FC.upload_image(tok, b["path"])
            if key:
                n_img += 1
                final_blocks.append({"type": "img", "key": key, "alt": b.get("alt", "")})
            continue
        final_blocks.append(b)

    print("[card] 文字块 %d ｜ 图片 %d 张" % (len(final_blocks), n_img), flush=True)
    return FC.send_card(title, final_blocks, imgs=None, sign="📎 来自 OpenClaw.Deepseek")


def push_charts(charts, d):
    """图片推送：QQ 私聊 + 飞书私聊
    顺序：文字卡片已经先发过了（main 里），这里逐张补对应的图。
    charts: [(标签, 路径, 级别关键词)]
    """
    if not charts:
        return False
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import push_image as PI
    except Exception as e:
        print("[WARN] push_image 不可用:", str(e)[:120])
        return False
    ok = False
    n = len(charts)
    for i, item in enumerate(charts, 1):
        tag, p = item[0], item[1]
        try:
            txt = "📐 %s · %s · 缠论·分型版 （%d/%d）" % (d, tag, i, n)
            r = PI.push(p, text=txt)
            print("[push-img]", tag, r)
            ok = r or ok
        except Exception as e:
            print("[WARN] 推图失败 %s: %s" % (tag, str(e)[:150]))
    return ok


def main():
    _t_start = __import__("time").time()
    # 非交易日跳过（cron 只按工作日跑，遇节假日休市仍需拦截）；--force 可强制跑
    if "--force" not in sys.argv:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import trade_day
            if not trade_day.is_open()[0]:
                print("非交易日，跳过")
                return 0
        except Exception as e:
            print("[WARN] trade_day:", str(e)[:80])
    d = datetime.now().strftime("%Y-%m-%d")
    # 标题明写「级别 + 版本」（2026-10-23 用户要求：发出去要一眼看出是什么级别、哪个版本）
    title = "🧭 大盘技术拆解 · %s · PCR + 五大宽基 周线/日线中枢 + 日线缠论 · 分型版" % d
    txt = "%s\n\n%s\n\n%s\n\n%s\n\n%s\n\n%s\n\n%s" % (
        pcr_block(), "─" * 30, zs_block(), "─" * 30,
        tech_block(), "─" * 30, mfdfa_lambda_block(panel_sync=("--no-panel-sync" not in sys.argv)))
    # ── 股债比值 R（近一年锚）2026-11-04 大佬要求并入本报告 ──
    gsr = None
    try:
        gsr = gsr_block()
        if gsr:
            txt += "\n\n%s\n\n%s" % ("─" * 30, gsr["txt"])
    except Exception as e:
        print("[WARN] 股债模块:", str(e)[:150])
    # ── 股债利差 EY−BY（5.5pct 底线告警）2026-11-05 大佬要求 ──
    try:
        eb = eyby_block()
        if eb:
            txt += "\n\n%s\n\n%s" % ("─" * 30, eb["txt"])
    except Exception as e:
        print("[WARN] 股债利差模块:", str(e)[:150])
    # ── 五大宽基 ETF 净申赎监控（2026-11-13 大佬要求）──
    try:
        eF = etf_flow_block()
        if eF and eF.get("txt"):
            txt += "\n\n%s\n\n%s" % ("─" * 30, eF["txt"])
    except Exception as e:
        print("[WARN] ETF净申赎模块:", str(e)[:150])
    print(txt)
    p = os.path.join(WS, ".cache/market_tech_%s.txt" % datetime.now().strftime("%Y%m%d_%H%M"))
    open(p, "w", encoding="utf-8").write(txt)
    print("\n[saved]", p)
    # ── 出图：只给「出现买卖点」的（指数 × 级别）出一张（2026-10-23 用户口径）──
    charts = []
    if "--no-chart" not in sys.argv:
        try:
            charts = make_charts()
        except Exception as e:
            print("[WARN] make_charts:", str(e)[:150])
        # 五大宽基 周线/日线 中枢图（2026-10-26 大佬分工：宽基图归大盘任务）
        try:
            import wide_ind_charts as WC
            import wide_ind_report as WIR
            wres = WIR.collect()
            for c in WC.build(wres, kinds=("wide",)):
                lvtag = "week" if c["lv"] == "week" else "day"
                charts.append(("五大宽基 · %s中枢" % ("周线" if lvtag == "week" else "日线"),
                               c["path"], lvtag))
                print("[chart] wide_zs", c["lv"], c["path"], c["n"], "子图")
        except Exception as e:
            print("[WARN] 宽基中枢图:", str(e)[:150])
        # 五大宽基 · 缠论图 与 拓扑图 分开出（2026-11-14 大佬：
        # 「缠论的是缠论的，拓扑图是拓扑图；两个图都合上去，而且要写清看多还是看空」）
        import subprocess as _sp
        _dd = d.replace("-", "")
        # ⚠️ 2026-11-19 修复（扣子掉线事故）：子进程原来只传 MPLBACKEND，缺
        #   DATA_HUB_PATH → dh_bridge 判定"不可用"降级到 tushare 代理，实测 5 个
        #   宽基取数要 256 秒（科创50 一个就 180 秒），把出图拖到 27 分钟都没跑完。
        #   同时固定 MPLCONFIGDIR，避免每个子进程重建字体缓存。
        _chart_env = dict(os.environ, MPLBACKEND="Agg",
                          DATA_HUB_PATH=os.path.join(WS, "optstack"),
                          MPLCONFIGDIR=os.environ.get(
                              "MPLCONFIGDIR",
                              os.path.join(os.path.expanduser("~"), ".config", "matplotlib")))
        # ① 缠论 · 五大宽基日线对比（左笔/中枢/买卖点）
        try:
            _cd = os.path.join(WS, ".cache", "charts")
            os.makedirs(_cd, exist_ok=True)
            # ⚠️ 2026-11-14 23:13 大佬：并排 5 列会被压成 11:1 的窄条、字和图重叠。
            #    → 改成 --stack「纵向堆叠」，一指数一行、画布不压缩（1935×3621）。
            _cp = os.path.join(_cd, "chan_stack_%s.png" % _dd)
            # 2026-11-16 终稿：这张图 = 缠论+R_cascade 双轴（上） + MACD（下），
            # 不再画 RMT 层，所以这里也不用再同步 ~/.cache/rmt_panel。
            if not os.path.exists(_cp):
                _r = _sp.run([sys.executable, os.path.join(REPO, "charts", "chan_fractal.py"),
                              "--stack", "沪深300,中证500,中证1000,创业板指,科创50",
                              "--stack-n", "160", "--out", _cp],
                             capture_output=True, text=True,
                             timeout=max(120, min(900, 2400 - (__import__("time").time() - _t_start))),
                             cwd=REPO, env=_chart_env)
                print("[chan5] rc=%s %s" % (_r.returncode, (os.path.basename(_cp))))
            if os.path.exists(_cp):
                charts.append(("五大宽基 · 缠论（原图）＋ 下方 VIX/偏度分位/R_cascade 双轴",
                               _cp, "chan5"))
                print("[chart] chan5", _cp)
        except Exception as e:
            print("[WARN] 缠论五指数图:", str(e)[:150])
        # ①b MF-DFA 多分形谱图（2026-11-24 大佬：「换成 MFDFA 那个多分形谱那张图，
        #     或者直接加在那里边的子图」→ 接进日报，替换原来 R_cascade 的图位）
        #     三栏：走势 / Δα 多分形谱宽 / λ1·λ2 · 五个宽基 → 2 列网格合成一张
        try:
            _mf = os.path.join(WS, ".cache", "mfdfa_panel_%s.png" % _dd)
            if not os.path.exists(_mf):
                _mf = _mfdfa_make_chart(out_name="mfdfa_panel_%s.png" % _dd) or _mf
            if _mf and os.path.exists(_mf):
                charts.append(("五大宽基 · MF-DFA 多分形谱（走势/Δα/λ1·λ2）", _mf, "mfdfa"))
                print("[chart] mfdfa", _mf)
            else:
                print("[mfdfa-chart] 未生成图（Δα 序列不可用）")
        except Exception as e:
            print("[WARN] MF-DFA 图谱:", str(e)[:150])
        # ② 拓扑结构总览（2026-11-16 大佬要求：换成新版五面板合成图）
        #    四象限（聚合度×动量）+ 每指数一行：UMAP 结构位置 / 阵营块图 /
        #    聚合度刻度尺 / 联动网络（里奇曲率）/ 阵营内-间相关走势 / Betti 分组 / 里奇流
        # ⚠️ 2026-11-24 事故：这一步（面板刷新 + 出图）今天卡到 cron 的 2400s 超时，
        #    整个日报被 kill → **飞书和 QQ 一条都没发出去**（连文字都没有）。
        #    对策：① 刷新面板限时 300s（在 five_index_topo 里）；
        #          ② 每个出图子进程的超时都**按剩余时间预算动态收敛**；
        #          ③ 总时长超预算就**直接跳过该图**先推送，图可以明天补，报告不能丢。
        _elapsed = __import__("time").time() - _t_start
        if _elapsed > 2700:
            print("[topo5] 已跑 %ds，超总预算 2700s → 跳过拓扑图，直接推送" % _elapsed, flush=True)
            raise RuntimeError("skip-topo-deadline")
        try:
            _tp = os.path.join(WS, ".cache", "five_topo_%s.png" % _dd)
            # 先增量刷新全市场逐日收盘面板（否则拓扑图会停在上次刷新的日期）
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import five_index_topo as FIT
                # ⚠️ 2026-11-24：刷新面板必须限时（默认 300s），否则 tushare 代理抽风
                #   会把整个日报拖到 cron 2400s 超时被杀 → 文字和图全丢。
                _added = FIT._refresh_panel(60, budget_s=420)
                print("[topo5] 面板刷新 +%d 个交易日" % _added)
            except Exception as _e:
                print("[WARN] 拓扑面板刷新: %s" % str(_e)[:120])
            if not os.path.exists(_tp):
                # 出图前再查一次预算（面板刷新可能已经吃掉 300s）
                _el2 = __import__("time").time() - _t_start
                if _el2 > 2700:
                    print("[topo5] 已跑 %ds，跳过出图直接推送" % _el2, flush=True)
                    raise RuntimeError("skip-topo-deadline")
                _r = _sp.run([sys.executable, os.path.join(REPO, "charts", "five_topo.py"),
                              "--panel", os.environ.get("TOPO_PANEL_DIR") or os.path.expanduser("~/.cache/topo5"),
                              "--days", "60", "--out", _tp],
                             capture_output=True, text=True,
                             timeout=max(60, min(1500, 2700 - _el2)),
                             cwd=REPO, env=_chart_env)
                for _ln in (_r.stdout or "").splitlines()[-3:]:
                    print("[topo]", _ln)
            if os.path.exists(_tp):
                charts.append(("五大宽基 · 拓扑结构总览（四象限＋阵营块图/刻度尺/联动网络（里奇曲率）/相关走势/Betti分组/里奇流）",
                               _tp, "topo"))
                print("[chart] five-topo", _tp)
            else:
                # 拓扑失败 → 退回旧的合并图，至少日报有图
                _old = os.path.join(WS, ".cache", "five_index_topo_%s.png" % _dd)
                if not os.path.exists(_old):
                    _sp.run([sys.executable, os.path.join(WS, "bin", "five_index_topo.py"),
                             "--days", "60", "--out", _old],
                            capture_output=True, text=True, timeout=300,
                            env=_chart_env)
                if os.path.exists(_old):
                    charts.append(("五大宽基 · 缠论+拓扑合并（拓扑图生成失败，退回旧图）", _old, "topo"))
                    print("[chart] five-topo-fallback", _old)
        except Exception as e:
            print("[WARN] 拓扑结构图:", str(e)[:150])
        # 股债比值 R 图（2026-11-04 新增）
        try:
            _g = globals().get('_GSR') or {}
            if _g.get('png') and os.path.exists(_g['png']):
                charts.append(("股债比值 R（近一年锚）· 布林带", _g['png'], "gsr"))
                print("[chart] gsr", _g['png'])
        except Exception as e:
            print("[WARN] 追加股债图失败:", str(e)[:120])
        # 股债利差 EY−BY 图（2026-11-05 新增）
        try:
            _e = globals().get('_EYBY') or {}
            if _e.get('png') and os.path.exists(_e['png']):
                charts.append(("股债利差 EY−BY（底部区间下界 5.5pct）", _e['png'], "eyby"))
                print("[chart] eyby", _e['png'])
        except Exception as e:
            print("[WARN] 追加股债利差图失败:", str(e)[:120])
        # 【2026-11-24 更新】R_cascade 已被 MF-DFA Δα + λ1/λ2 取代：
        #   文字表 = _MFDFA_LAMBDA['txt']（在 push_combined 里渲染）；
        #   告警邮件 = mfdfa_email_alerts()（合并一封，与 20:45 RMT 任务共用幂等状态）。
        #   原来「R_cascade 单独图」自 2026-11-17 起就不再推（与宽基图重复），保持不推。

    if "--no-push" not in sys.argv:
        # ⚠️ 2026-10-23 用户要求：**文字 + 配图合成一条卡片**，不要分开发一堆图。
        # 失败才回退「纯文字卡片 + 逐张图片」（保证至少文字能到）。
        ok = False
        try:
            ok = push_combined(txt, title, charts, d)
        except Exception as e:
            print("[WARN] 图文合成卡片失败:", str(e)[:200])
        # QQ 不支持交互卡片 → 发一份**自包含 Markdown 文件**（图文都嵌在里面）
        try:
            ok_qq = push_qq_md(d, txt, charts)
            print("[qq-md-card]", ok_qq)
        except Exception as e:
            print("[WARN] QQ Markdown 失败:", str(e)[:180])
        if not ok:
            print("[fallback] 退回：纯文字卡片 + 逐张图片")
            try:
                feishu_push(txt, title)
                print("[push] 文字卡片 done")
                if charts:
                    push_charts(charts, d)
            except Exception as e:
                print("[ERR] push:", str(e)[:150])
                return 1
    else:
        print("[no-push] 已跳过推送；图片：")
        for c in charts:
            print("  ", c)
    # ── MF-DFA Δα / λ1·λ2 告警邮件（2026-11-24 替换 R_cascade 的口径）──
    #    任一触发 → **合并成一封**；幂等状态与 20:45 bin/rmt_rc_alert.py 共用，
    #    同一事件只发一次。--no-email 跳过；--email-dry-run 只打印不发送。
    if "--no-email" not in sys.argv:
        try:
            mfdfa_email_alerts(dry_run=("--email-dry-run" in sys.argv))
        except Exception as e:
            print("[WARN] mfdfa 邮件:", str(e)[:150])
    return 0


if __name__ == "__main__":
    sys.exit(main())
