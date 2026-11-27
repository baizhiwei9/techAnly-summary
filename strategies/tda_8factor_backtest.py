#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
8因子 TDA 策略回测（2026-01-01 至今）
===================================
按 tda_rotation.py 的实际信号逻辑回溯：
  - 8 ETF → 每天算夏普 → 动量-防御平衡权重（新改的 compute_defensive_weights）
  - T 日生成权重，T+1 生效
  - 50/50 股债配比：股票端 = 8 ETF 加权组合，债券端 = 511260 国债ETF
输出：累计收益、年化、最大回撤、夏普、卡玛
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import sys, warnings, json, math
warnings.filterwarnings('ignore')
sys.path.insert(0, 'bin')
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tda_rotation as T   # 复用权重/夏普逻辑

TZ = timezone(timedelta(hours=8))
REPORT_DIR = Path('memory/tda_reports')
STOCK_RATIO = 0.5
BOND_RATIO = 0.5
BOND_ETF = '511260.SH'


def get_bond_etf(days=400):
    """国债ETF 价格序列"""
    end = datetime.now(TZ).strftime('%Y%m%d')
    start = (datetime.now(TZ) - timedelta(days=days)).strftime('%Y%m%d')
    tds = T.trade_date_list(start, end)
    df = T.safe_pro_call('fund_daily', ts_code=BOND_ETF, start_date=tds[0], end_date=tds[-1])
    if df is None or df.empty:
        return None
    df = df.sort_values('trade_date').set_index('trade_date')['close']
    return df


def main():
    # 1. 取 8 ETF 数据（从 2025 年中起，足够 60 日回看）
    etf = T.get_etf_prices([f['ts_code'] for f in T.FACTORS], days=380)
    # 也拉国债ETF
    bond = get_bond_etf()

    start_date = '20260101'
    dates = [d for d in etf.index if d >= start_date]
    print(f"8因子回测: {len(dates)} 交易日 ({dates[0]} ~ {dates[-1]})", flush=True)

    nav = 1.0
    navs = []
    holds = None   # (weight_dict) 上次信号权重
    prev_date = None

    # 权重逻辑也要用"过去60日"滚动算，所以需要全历史
    for i, date in enumerate(dates):
        # 股票端组合收益（用 T-1 权重）
        stock_ret = 0.0
        if holds is not None and i > 0:
            ret_row = etf.loc[date] / etf.loc[prev_date] - 1
            # holds 是 {因子名:权重}，需映射到 ETF 代码
            stock_ret = sum(
                holds.get(f['name'], 0) * ret_row.get(f['ts_code'], 0)
                for f in T.FACTORS
            )

        # 债券端收益（国债ETF 日收益）
        bond_ret = 0.0
        if bond is not None and prev_date in bond.index and date in bond.index:
            bond_ret = bond.loc[date] / bond.loc[prev_date] - 1

        # 组合（50/50）
        combo_ret = STOCK_RATIO * stock_ret + BOND_RATIO * bond_ret
        nav *= (1 + combo_ret)
        navs.append(nav)

        # 生成 T 日信号（用截至 date 的历史数据），T+1 生效
        hist = etf.loc[:date].tail(T.WINDOW + 5)
        weights, _ = T.compute_defensive_weights(hist)
        holds = weights
        prev_date = date

    nav_s = pd.Series(navs, index=dates)
    cum = nav_s.iloc[-1] / nav_s.iloc[0] - 1
    n = len(nav_s)
    ann = ((nav_s.iloc[-1] / nav_s.iloc[0]) ** (252 / n) - 1) if n > 0 else 0
    dd = (nav_s / nav_s.cummax() - 1).min()
    rets = nav_s.pct_change().dropna()
    vol = rets.std() * np.sqrt(252) if len(rets) > 1 else 0
    sharpe = (rets.mean() * 252) / vol if vol > 0 else 0
    calmar = ann / abs(dd) if dd < 0 else 0

    result = {
        '区间': f'{dates[0]} ~ {dates[-1]}',
        '累计收益': f'{cum*100:+.2f}%',
        '年化收益': f'{ann*100:+.2f}%',
        '最大回撤': f'{dd*100:+.2f}%',
        '夏普': f'{sharpe:.2f}',
        '卡玛': f'{calmar:.2f}',
        '交易日': len(dates),
    }
    print("\n" + "=" * 56)
    print("8因子 TDA 策略回测 (2026-01-01 至今, 50/50 股债)")
    print("=" * 56)
    for k, v in result.items():
        print(f"  {k:<10}: {v}")
    print("=" * 56)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    Path('memory/tda_8factor_backtest_2026.json').write_text(
        json.dumps({'summary': result, 'nav': navs}, ensure_ascii=False, indent=1), encoding='utf-8')
    print("已存 memory/tda_8factor_backtest_2026.json")


if __name__ == '__main__':
    main()
