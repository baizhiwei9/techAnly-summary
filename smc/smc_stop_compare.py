#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期货止损开关对比：同一批 60m 数据，变体 A，跑「带止损 / 不带止损」两种配置。
用法：
  python3 smc_stop_compare.py            # 自动跑两次（子进程设环境变量），输出对比
  python3 smc_stop_compare.py --one on   # 只跑一种（内部用）
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smc_config import INSTRUMENTS
import smc_data
import smc_core


def run_all(label):
    out = {}
    for it in INSTRUMENTS:
        h1 = smc_data.get(it["code"], it["kind"], "60m")
        if h1 is None or len(h1) < 200:
            continue
        frames = smc_core.build_frames(h1, level="h1")
        res = smc_core.run_backtest(h1.reset_index(drop=True), frames, variant="A", kind=it["kind"], cls=it.get("cls"))
        s = smc_core.summarize(res)
        stops = sum(1 for t in res["trades"] if str(t.get("reason", "")).startswith("止损"))
        out[it["code"]] = dict(name=it["name"], kind=it["kind"], **s, n_stop=stops)
    print("@@JSON@@" + json.dumps(out, ensure_ascii=False))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--one", choices=["on", "off"])
    a = ap.parse_args()
    if a.one == "on":
        run_all("on")
    else:
        env_on = dict(os.environ)
        env_on.pop("SMC_STOP_OFF", None)
        env_off = dict(os.environ, SMC_STOP_OFF="1")

        def invoke(env):
            r = subprocess.run([sys.executable, os.path.abspath(__file__), "--one", "on"],
                               capture_output=True, text=True, env=env)
            line = [l for l in r.stdout.splitlines() if l.startswith("@@JSON@@")]
            if not line:
                print(r.stdout[-2000:], r.stderr[-2000:])
                raise SystemExit("子进程未返回 JSON")
            return json.loads(line[0][len("@@JSON@@"):])

        on = invoke(env_on)
        off = invoke(env_off)
        print("=== 期货止损对比（变体A，近1年60m）===")
        print(f"{'标的':10s} {'带止损':>10s} {'不带止损':>10s} {'差':>8s} {'止损次数':>8s}")
        for code, v in on.items():
            o = off.get(code, {})
            if v["kind"] != "fut":
                continue
            print(f"{v['name']:10s} {v['total_ret']:>9.2f}% {o.get('total_ret',0):>9.2f}% "
                  f"{v['total_ret']-o.get('total_ret',0):>+7.2f}% {v['n_stop']:>8d}")
        print("\n=== ETF（不适用止损，两者应一致）===")
        for code, v in on.items():
            if v["kind"] != "fut":
                o = off.get(code, {})
                print(f"{v['name']:10s} {v['total_ret']:>9.2f}% vs {o.get('total_ret',0):>9.2f}%")
