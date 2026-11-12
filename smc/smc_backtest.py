#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 多资产 h1 级别回测（变体 A/B/C/D）→ stats json。
只做 1 小时以上级别：--tf h1|day|week
"""
from __future__ import annotations
import os
import json
import argparse
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smc_config import INSTRUMENTS, SMC_DIR, level_of, base_freq
import smc_data
import smc_core


def ltf_of(h1, tf, base):
    if (tf in ("h1", "day")) and (base in ("60m", "1d")):
        return h1.reset_index(drop=True)
    return smc_core.aggregate(h1, tf)


def bt_one(it, tf=None, variants=("A", "B", "C", "D")):
    # 股票/ETF=日线(day) 期货=小时(h1)
    tf = tf or level_of(it)
    base = base_freq(tf)
    h1 = smc_data.get(it["code"], it["kind"], base)
    if h1 is None or len(h1) < 200:
        return None
    ltf = ltf_of(h1, tf, base)
    frames = smc_core.build_frames(h1, level=tf)   # 只构建一次，多变体复用
    out = {}
    for var in variants:
        try:
            res = smc_core.run_backtest(ltf, frames, variant=var, kind=it["kind"], cls=it.get("cls"))
            s = smc_core.summarize(res)
            s["bars"] = len(ltf)
            out[var] = s
        except Exception as e:
            out[var] = {"err": str(e)[:120]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default=None, help="留空按类别自动（股票/ETF=day, 期货=h1）")
    ap.add_argument("--out", default=os.path.join(SMC_DIR, "stats_h1.json"))
    a = ap.parse_args()

    allstats = {}
    for it in INSTRUMENTS:
        tf = a.tf or level_of(it)
        r = bt_one(it, tf=tf)
        if r is None:
            print(f"  {it['name']:10s} 数据不足，跳过")
            continue
        allstats[it["code"]] = dict(name=it["name"], cls=it["cls"], tf=tf, **r)
        row = " | ".join(
            f"{v}:{r[v].get('total_ret','ERR')}%" if "total_ret" in r[v] else f"{v}:ERR"
            for v in ("A", "B", "C", "D") if v in r)
        print(f"  {it['name']:10s} {it['cls']:4s} bars={r['A'].get('bars','-') if 'A' in r else '-'} | {row}")
    json.dump(allstats, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(f"\n已写入 {a.out}")
    print("\n=== 变体汇总（等权平均）===")
    for v in ("A", "B", "C", "D"):
        vals = [allstats[k][v]["total_ret"] for k in allstats if v in allstats[k] and "total_ret" in allstats[k][v]]
        if vals:
            print(f"  变体 {v}: n={len(vals)} | 平均 {sum(vals)/len(vals):+.2f}% | 正收益 {sum(1 for x in vals if x>0)}/{len(vals)}")


if __name__ == "__main__":
    main()
