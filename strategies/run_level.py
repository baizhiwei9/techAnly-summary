#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按级别跑四套技术口径：m15 / h1 / day / week
=====================================================================
一条命令，指定标的与级别，跑 ①分型版 ②均线版 ③SMC ④VWAP 六轨。
公式不变，只切换取数周期；周线由日线合成。

  python3 strategies/run_level.py --target 中证500 --level m15
  python3 strategies/run_level.py --target 600519 --level week --strategy all
  python3 strategies/run_level.py --target 000905.SH --level day --strategy fractal
  python3 strategies/run_level.py --target 中证500 --level all      # 四个级别各跑一遍

小波策略不在此入口：见 wavelet/（日线/周线口径）。
"""
from __future__ import annotations

import argparse
import json
import sys

import levels as LV
import tech_summary as TS
import smc_core as S


def _fmt_dt(x):
    return str(x)[:16]


# ─────────────────────────── ① 缠论·分型版 ───────────────────────────
def run_fractal(df, level, tg):
    fx = TS.chan_fx(df, level)
    L = ["① 缠论·分型版"]
    if not fx or not fx.get("ok"):
        L.append("  数据不足，无法构成笔/中枢")
        return L, fx

    zs = fx.get("zs")
    zpos_txt = {"above": "中枢上方", "below": "中枢下方", "in": "中枢内"}.get(fx.get("zpos"), "无中枢")
    L.append("  现价 %.2f ｜ 方向 %s ｜ 中枢 %s ｜ %s ｜ 阶段 %s" % (
        fx["cur"], fx.get("dir", "—"),
        ("%.2f~%.2f" % (zs[0], zs[1])) if zs else "—",
        zpos_txt, fx.get("stage", "—")))
    if fx.get("div"):
        L.append("  背驰：%s" % fx["div"])

    # 这里输出的是「结构侦测」的原始买卖点；各级别的交易闸门不同（见下）
    rule = {
        "m15": "15分钟：结构参考，未套日线闸门（不做交易确认）",
        "h1": "小时线交易口径：只认三买；一买须 MA10>MA20，二买不认",
        "day": "日线交易口径：一/二/三买按 年龄 + 距买点 + 结构未破 闸门过滤",
        "week": "周线交易口径：只认一买/二买（周线闸门）",
    }.get(level)
    if rule:
        L.append("  ⚠ %s" % rule)

    def _tag(x):
        st = "新鲜" if x.get("fresh") else ("延续·结构未破" if x.get("sustained") else "已失效")
        if x.get("active"):
            st += "·就在点位上"
        return "  %s %.2f（%d根前·%s）" % (x["tag"], x["level"], x.get("age", 0), st)

    buys = [b for b in fx.get("buy_list", []) if b.get("fresh") or b.get("sustained")]
    sells = [s for s in fx.get("sell_list", []) if s.get("fresh") or s.get("sustained")]
    L.append("  买点：" + ("、".join("%s %.2f" % (b["tag"], b["level"]) for b in buys) if buys else "无"))
    for b in buys[:4]:
        L.append(_tag(b))
    L.append("  卖点：" + ("、".join("%s %.2f" % (s["tag"], s["level"]) for s in sells) if sells else "无"))
    for s in sells[:4]:
        L.append(_tag(s))
    if zs:
        L.append("  距上沿 ZG %+.2f%% ｜ 距下沿 ZD %+.2f%%" % (fx.get("to_zg", 0), fx.get("to_zd", 0)))
    return L, fx


# ─────────────────────────── ② 缠论·均线版 ───────────────────────────
def run_ma(df, level, tg):
    r = TS.ma_layer(df, "week" if level == "week" else "day")
    L = ["② 缠论·均线版"]
    if not r:
        L.append("  数据不足（MA160 需要 ≥170 根）")
        return L, r
    L.append("  现价 %.2f ｜ %s" % (r["close"], TS.M.tradeable(r)))
    al = r.get("align") or {}
    mo = r.get("mode") or {}
    loc = r.get("loc") or {}
    L.append("  排列 %s（%s）｜ 走势 %s ｜ 当前主走 %s%s" % (
        al.get("state", "—"), al.get("desc", "—"), mo.get("mode", "—"),
        loc.get("label", "—"), ("·" + str(loc.get("dir"))) if loc.get("dir") else ""))
    bc = r.get("beichi")
    if bc:
        L.append("  背驰：%s" % (bc.get("type") or bc.get("dir") or bc))
    sigs = r.get("sigs") or []
    for s in sigs[:5]:
        L.append("  %s：%s（%s，%s根前）" % (
            s.get("type"), s.get("kind"), s.get("src"), s.get("bars_ago")))
    if not sigs:
        L.append("  信号：当前级别无新鲜穿越 / 无长期排列")
    return L, r


# ─────────────────────────── ③ SMC ───────────────────────────
def run_smc(df, level, tg):
    k = LV.SMC_K[level]
    r = TS.smc_layer(df, k)
    L = ["③ SMC（结构突破 + FVG/OB）"]
    L.append("  FVG %d 个 ｜ OB %d 个 ｜ 摆动点 %d/%d ｜ k=%d" % (
        len(r.get("fvgs") or []), len(r.get("obs") or []),
        len(r.get("hi") or []), len(r.get("lo") or []), k))
    px = float(df["close"].iloc[-1])
    sigs = r.get("sigs") or []
    if sigs:
        for s in sigs[:4]:
            L.append("  " + S.confirm_text(s, px))
    else:
        L.append("  当前无确认信号（结构未突破 / 未回踩）")
    return L, r


# ─────────────────────────── ④ VWAP 六轨 ───────────────────────────
def run_vwap(df, level, tg):
    r = TS.vwap_layer(df)
    L = ["④ VWAP 六轨（±1σ/±2σ/±3σ，窗口 60）"]
    v = float(r["vwap"][-1])
    sg = float(r["sigma"][-1])
    px = float(df["close"].iloc[-1])
    z = (px - v) / sg if sg else 0.0
    L.append("  VWAP %.2f ｜ 现价 %.2f（%+.2fσ）" % (v, px, z))
    L.append("  +3σ %.2f（全平）｜ +2σ %.2f（减半）｜ +1σ %.2f" % (
        float(r["up3"][-1]), float(r["up2"][-1]), float(r["up1"][-1])))
    L.append("  −1σ %.2f（全止损）｜ −2σ %.2f" % (
        float(r["dn1"][-1]), float(r["dn2"][-1])))
    L.append("  MA40 方向：%s" % r["ma_dir"][-1])
    if z >= 3:
        L.append("  判定：≥+3σ 全平")
    elif z >= 2:
        L.append("  判定：≥+2σ 减半")
    elif z <= -1:
        L.append("  判定：≤−1σ 全止损")
    else:
        L.append("  判定：轨道内，正常持有")
    return L, r


RUNNERS = {
    "fractal": ("分型版", run_fractal),
    "ma": ("均线版", run_ma),
    "smc": ("SMC", run_smc),
    "vwap": ("VWAP", run_vwap),
}


def report(tg, df, level, strategy="all"):
    head = "📐 %s ｜ %s ｜ %s ｜ %d 根 ｜ 末根 %s" % (
        tg["disp"], LV.LABEL[level], "四层汇总" if strategy == "all" else strategy,
        len(df), _fmt_dt(df["dt"].iloc[-1]))
    lines = [head, "=" * 68]
    payload = {}
    for key in ("fractal", "ma", "smc", "vwap"):
        if strategy not in ("all", key):
            continue
        _, fn = RUNNERS[key]
        part, raw = fn(df, level, tg)
        lines += part + [""]
        payload[key] = raw
    return "\n".join(lines).rstrip(), payload


def main(argv=None):
    ap = argparse.ArgumentParser(description="按级别跑四套技术口径")
    ap.add_argument("--target", "--index", dest="target", default="中证500")
    ap.add_argument("--level", default="day", choices=list(LV.LEVELS) + ["all"])
    ap.add_argument("--strategy", default="all", choices=list(RUNNERS) + ["all"])
    ap.add_argument("--n", type=int, default=320, help="m15/h1 取多少根（默认 320）")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON（机器用）")
    a = ap.parse_args(argv)

    levels = list(LV.LEVELS) if a.level == "all" else [a.level]
    for lv in levels:
        try:
            tg, df = LV.load(a.target, lv, n=a.n)
        except Exception as e:
            print("❌ %s %s：%s" % (a.target, LV.LABEL[lv], e))
            continue
        txt, payload = report(tg, df, lv, a.strategy)
        print(txt)
        if a.json:
            print(json.dumps(payload, ensure_ascii=False, default=str))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
