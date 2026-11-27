#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五大宽基 · 缠论买卖点 + 拓扑结构 合并总览图（大盘日报用）
================================================================
2026-11-14 大佬要求：「晚上把这个图带上」——即大盘技术拆解日报里
要带上「5 个指数（沪深300/中证500/中证1000/创业板指/科创50）的拓扑结构图」。

口径不在这里重算，分两层：
  ① 数据：/root/.cache/topo5/close_YYYYMMDD.csv（全市场逐日收盘，tushare pro.daily 增量刷新）
  ② 出图：charts/five_index.py
       · 左 = 缠论·分型版（分型→笔→中枢→买卖点）
       · 右 = 成分股拓扑结构（相关矩阵 → UMAP + Ollivier-Ricci 曲率 + Betti + 结构分 + 趋势层）

用法：
  python3 strategies/five_index_topo.py                 # 增量刷新面板 + 出图（默认 60 日窗口）
  python3 strategies/five_index_topo.py --days 20       # 换窗口
  python3 strategies/five_index_topo.py --no-refresh    # 不刷数据，直接用现有面板
  python3 strategies/five_index_topo.py --print         # 只打印输出路径（给日报调用）

返回码：0 = 出图成功（stdout 末行是 png 绝对路径）；非 0 = 失败（日报应跳过该图）
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import glob
import os
import subprocess
import sys
from datetime import datetime

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
REPO = os.environ.get("TECHANLY_DIR", "/root/techAnly-summary")
PANEL_DIR = os.environ.get("TOPO_PANEL_DIR", "/root/.cache/topo5")
OUT_DIR = os.path.join(WS, ".cache")


def _refresh_panel(n_days: int, budget_s: int = 300) -> int:
    """增量刷新 /root/.cache/topo5：补齐最近 n_days+5 个交易日的全市场收盘。返回新增文件数。

    ⚠️ 2026-11-24 事故：tushare 代理（ttx.dailyfetch.top）抽风时 Read timeout 30s × 多次重试，
    把整个大盘日报拖到 cron 的 2400s 超时被杀 —— 文字和图**一张都没推出去**。
    → 加时间预算 budget_s（默认 5 分钟），超了就停手用现有面板继续出图，绝不拖死日报。
    """
    import time as _t
    _t0 = _t.time()
    try:
        import socket
        _o = socket.getaddrinfo
        socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]
        socket.setdefaulttimeout(25)   # 兜底：任何阻塞 socket 25s 必返回
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from tushare_init import get_pro
        import pandas as pd  # noqa: F401
    except Exception as e:
        print("[topo5] 无法初始化 tushare，跳过刷新：%s" % str(e)[:100], flush=True)
        return 0

    os.makedirs(PANEL_DIR, exist_ok=True)
    pro = get_pro()
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now().replace(year=datetime.now().year - 1)).strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
    days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())[-(n_days + 5):]
    added = 0
    for d in days:
        f = os.path.join(PANEL_DIR, "close_%s.csv" % d)
        if os.path.exists(f) and os.path.getsize(f) > 1000:
            continue
        if _t.time() - _t0 > budget_s:
            print("[topo5] 已用 %ds，超预算 %ds → 停止刷新，用现有面板出图"
                  % (_t.time() - _t0, budget_s), flush=True)
            break
        for _try in range(2):
            try:
                df = pro.daily(trade_date=d)
                if df is None or df.empty:
                    break
                df[["ts_code", "close", "pct_chg", "vol"]].to_csv(f, index=False)
                added += 1
                print("[topo5] +%s %d 行" % (d, len(df)), flush=True)
                break
            except Exception as e:
                print("[topo5] %s 取数失败：%s" % (d, str(e)[:80]), flush=True)
    # 只保留最近 n_days+8 个文件，防止目录无限增长
    fs = sorted(glob.glob(os.path.join(PANEL_DIR, "close_*.csv")))
    for f in fs[:max(0, len(fs) - (n_days + 8))]:
        try:
            os.remove(f)
        except Exception:
            pass
    return added


def build(days: int = 60, refresh: bool = True, out: str = "") -> str:
    script = os.path.join(REPO, "charts", "five_index.py")
    if not os.path.exists(script):
        raise RuntimeError("找不到出图脚本：%s" % script)
    if refresh:
        _refresh_panel(days)
    out = out or os.path.join(OUT_DIR, "five_index_topo_%s.png" % datetime.now().strftime("%Y%m%d"))
    env = dict(os.environ)
    env.setdefault("DATA_HUB_PATH", os.path.join(WS, "optstack"))
    env.setdefault("MPLBACKEND", "Agg")
    cmd = [sys.executable, "-u", script, "--panel", PANEL_DIR,
           "--days", str(days), "--out", out]
    print("[five-topo] 开始出图：5 指数 · 拓扑窗口 %d 日" % days, flush=True)
    r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
    tail = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-4:]
    for ln in tail:
        print("[five-topo] %s" % ln, flush=True)
    if r.returncode != 0 or not os.path.exists(out):
        err = (r.stderr or "").strip().splitlines()
        print("[five-topo] 失败 rc=%s %s" % (r.returncode, (err[-1] if err else "")[:200]), flush=True)
        raise RuntimeError("出图失败")
    print("[five-topo] 输出 %s" % out, flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="拓扑收益窗口（交易日）")
    ap.add_argument("--no-refresh", action="store_true", help="不刷新面板数据")
    ap.add_argument("--out", default="")
    ap.add_argument("--print", action="store_true", help="只打印 png 路径")
    a = ap.parse_args()
    try:
        p = build(a.days, not a.no_refresh, a.out)
    except Exception as e:
        print("[five-topo] %s" % str(e)[:200])
        return 1
    if a.print:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
