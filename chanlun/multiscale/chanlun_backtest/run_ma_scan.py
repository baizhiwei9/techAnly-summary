#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论·均线版（新版）每日扫描

用法:
  python -m chanlun_backtest.run_ma_scan                 # 默认宽基ETF，日线
  python -m chanlun_backtest.run_ma_scan --level week    # 周线（日线合成）
  python -m chanlun_backtest.run_ma_scan --symbol sh510300 --symbol sh000300
  python -m chanlun_backtest.run_ma_scan --json          # 机器可读

口径：见 chanlun_backtest/ma_trend.py（直接复用远程 bin/chan_ma_trend_v2.py）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from chanlun_backtest import ma_trend


WATCHLIST = [
    ("sh510300", "沪深300ETF"),
    ("sh510050", "上证50ETF"),
    ("sh510500", "中证500ETF"),
    ("sz159915", "创业板ETF"),
    ("sh588000", "科创50ETF"),
]


def _load(symbol):
    from data_fetcher import get_index_daily
    bars = get_index_daily(symbol)
    if not bars:
        return None
    df = pd.DataFrame(bars)
    for c in ("open", "high", "low", "close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description="缠论·均线版（新版）扫描")
    ap.add_argument("--symbol", action="append", default=None,
                    help="标的代码，可重复；默认宽基ETF")
    ap.add_argument("--level", default="day", choices=["day", "week"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    items = a.symbol or [s for s, _ in WATCHLIST]
    names = dict(WATCHLIST)
    payload = {}
    for sym in items:
        df = _load(sym)
        if df is None:
            print("跳过 %s：取数失败" % sym)
            continue
        try:
            r = ma_trend.analyze(df, a.level)
        except Exception as e:
            print("跳过 %s：%s" % (sym, e))
            continue
        print(ma_trend.format_report(r, names.get(sym, sym), a.level))
        print()
        if a.json:
            payload[sym] = {k: v for k, v in (r or {}).items() if k not in ("df", "bi")}
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
