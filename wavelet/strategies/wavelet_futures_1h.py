"""
小波多尺度期货策略 - 1小时级别
核心逻辑与 v5 日线版一致，但适配小时K线：
  1. 用日线数据做趋势过滤（替代周线）
  2. 在1小时级别上做小波分解 + 缠论分型检测
  3. 日线+1h 双级别共振：日线定方向，1h 定时机
  4. ATR追踪止损 + MA二次确认 + 动量止损

参数适配（1h K线，每天约4根bar）：
  - wt_w=80（约20个交易日=4周的趋势窗口）
  - exit_ma=40（约10个交易日）
  - atr_period=20（约5个交易日）
  - momentum_window=40（约10个交易日）
"""

import numpy as np
import pandas as pd
import pywt


def causal_wt(prices, w=80, wavelet='db4', level=4, keep=1):
    """因果小波分解：第i根bar只用0~i的数据，避免未来函数"""
    n = len(prices)
    wt = np.full(n, np.nan)
    for i in range(w - 1, n):
        seg = prices[i - w + 1:i + 1]
        coeffs = pywt.wavedec(seg, wavelet, level=level, mode='symmetric')
        approx = coeffs[0]
        details = [np.zeros_like(c) for c in coeffs[1:]]
        rec = pywt.waverec([approx] + details, wavelet, mode='symmetric')
        if len(rec) > w:
            rec = rec[:w]
        elif len(rec) < w:
            rec = np.pad(rec, (0, w - len(rec)), mode='edge')
        wt[i] = rec[-1]
    return wt


def chan_bottom_strength(close, low, high, i):
    """缠论底分型强度：0=无 1=普通 2=强势"""
    if i < 2:
        return 0
    left_l, mid_l, right_l = low[i-2], low[i-1], low[i]
    left_c, mid_c, right_c = close[i-2], close[i-1], close[i]
    left_h, mid_h, right_h = high[i-2], high[i-1], high[i]
    if not (mid_l < left_l and mid_l < right_l):
        return 0
    if right_c > left_h and right_c > mid_h:
        return 2
    if right_c > mid_c:
        return 1
    return 0


def chan_top_strength(close, low, high, i):
    """缠论顶分型强度：0=无 1=普通 2=强势"""
    if i < 2:
        return 0
    left_l, mid_l, right_l = low[i-2], low[i-1], low[i]
    left_c, mid_c, right_c = close[i-2], close[i-1], close[i]
    left_h, mid_h, right_h = high[i-2], high[i-1], high[i]
    if not (mid_h > left_h and mid_h > right_h):
        return 0
    if right_c < left_l and right_c < mid_l:
        return 2
    if right_c < mid_c:
        return 1
    return 0


def calc_atr(high, low, close, period=20):
    """计算ATR（小时级别用更长周期）"""
    n = len(close)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i-1]),
                    abs(low[i] - close[i-1]))
    atr = np.zeros(n)
    atr[:period] = np.nan
    atr[period-1] = np.mean(tr[1:period])
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr


def build_sig_futures_1h(df_1h, df_daily=None,
                          wt_w=80, level=4, exit_ma=40,
                          atr_mult=1.5, confirm_bars=4,
                          pos_long=0.10, pos_short=0.05,
                          short_strong_only=True,
                          daily_filter=True):
    """
    1小时级别期货双向信号生成

    参数：
        df_1h: 1小时K线 DataFrame (columns: open, high, low, close, volume)
        df_daily: 日线 DataFrame（用于趋势过滤）。None 则用 1h 合成日线
        wt_w: 小波窗口（默认80根1h bar ≈ 20交易日）
        level: 小波分解层数
        exit_ma: 离场均线（默认40根1h bar ≈ 10交易日）
        atr_mult: ATR止损倍数（1h 波动小，用1.5）
        confirm_bars: 二次确认 bar 数（默认4 ≈ 1交易日）
        pos_long/pos_short: 仓位比例
        short_strong_only: 做空只做强势顶分型
        daily_filter: 是否用日线趋势过滤

    返回：
        {'sig': array, 'stop_price': array, 'daily_trend': array}
    """
    close = df_1h['close'].values
    high = df_1h['high'].values
    low = df_1h['low'].values
    n = len(close)
    dates = df_1h.index

    # === 日线趋势过滤 ===
    if daily_filter:
        if df_daily is not None:
            daily_close = df_daily['close'].values
            daily_dates = df_daily.index
        else:
            # 从1h合成日线
            df_d = pd.DataFrame({'close': close}, index=dates).resample('D').last().dropna()
            daily_close = df_d['close'].values
            daily_dates = df_d.index

        # 日线小波
        wt_daily = causal_wt(daily_close, w=50, wavelet='db4', level=3)

        # 日线MA20
        daily_ma20 = pd.Series(daily_close).rolling(20).mean().values

        # 确定日线趋势方向：小波+MA双确认
        daily_trend_raw = np.zeros(len(daily_close), dtype=int)
        for i in range(len(daily_close)):
            bull_wt = (not np.isnan(wt_daily[i]) and daily_close[i] > wt_daily[i])
            bull_ma = (not np.isnan(daily_ma20[i]) and daily_close[i] > daily_ma20[i])
            bear_wt = (not np.isnan(wt_daily[i]) and daily_close[i] < wt_daily[i])
            bear_ma = (not np.isnan(daily_ma20[i]) and daily_close[i] < daily_ma20[i])
            if bull_wt and bull_ma:
                daily_trend_raw[i] = 1   # 明确多头
            elif bear_wt and bear_ma:
                daily_trend_raw[i] = -1  # 明确空头
            else:
                daily_trend_raw[i] = 0   # 中性

        # 将日线趋势映射到1h bar
        daily_trend = np.zeros(n, dtype=int)
        for i in range(len(daily_dates)):
            d = daily_dates[i]
            # 这个日线对应次日之前的所有1h bar
            if i + 1 < len(daily_dates):
                next_d = daily_dates[i + 1]
            else:
                next_d = d + pd.Timedelta(days=5)
            mask = (dates >= d) & (dates < next_d)
            daily_trend[mask] = daily_trend_raw[i]
    else:
        daily_trend = np.zeros(n, dtype=int)

    # === 1小时小波 ===
    wt_1h = causal_wt(close, w=wt_w, wavelet='db4', level=level)

    # === 技术指标 ===
    ma_exit = pd.Series(close).rolling(exit_ma).mean().values
    ma20_ret = pd.Series(close).pct_change(40).values  # 40根1h ≈ 10交易日
    atr = calc_atr(high, low, close, period=20)

    # === 信号生成 ===
    sig = np.zeros(n)
    stop_price = np.zeros(n)

    in_long = False
    in_short = False
    hold_bars = 0
    entry_price = 0
    extreme_price = 0
    confirm_count = 0

    gate_tol = 0.01  # 闸门容差1%（1h比日线更严格）

    for i in range(2, n):
        c = close[i]
        h = high[i]
        l = low[i]
        cur_atr = atr[i] if not np.isnan(atr[i]) else 0

        # ===== 持仓：多头 =====
        if in_long:
            if h > extreme_price:
                extreme_price = h

            # ATR追踪止损
            atr_stop = extreme_price - atr_mult * cur_atr
            if l <= atr_stop and cur_atr > 0:
                sig[i] = 0
                stop_price[i] = atr_stop
                in_long = False
                hold_bars = 0
                confirm_count = 0
                continue

            # MA二次确认离场
            if not np.isnan(ma_exit[i]) and c < ma_exit[i]:
                confirm_count += 1
                if confirm_count >= confirm_bars and hold_bars >= 4:
                    sig[i] = 0
                    stop_price[i] = ma_exit[i]
                    in_long = False
                    hold_bars = 0
                    confirm_count = 0
                    continue
            else:
                confirm_count = 0

            # 动量止损（10日跌幅超3%）
            if not np.isnan(ma20_ret[i]) and ma20_ret[i] < -0.03 and hold_bars >= 6:
                sig[i] = 0
                stop_price[i] = c
                in_long = False
                hold_bars = 0
                confirm_count = 0
                continue

            sig[i] = sig[i-1]
            stop_price[i] = atr_stop if cur_atr > 0 else 0
            hold_bars += 1
            continue

        # ===== 持仓：空头 =====
        if in_short:
            if l < extreme_price:
                extreme_price = l

            # ATR追踪止损
            atr_stop = extreme_price + atr_mult * cur_atr
            if h >= atr_stop and cur_atr > 0:
                sig[i] = 0
                stop_price[i] = atr_stop
                in_short = False
                hold_bars = 0
                confirm_count = 0
                continue

            # MA二次确认离场
            if not np.isnan(ma_exit[i]) and c > ma_exit[i]:
                confirm_count += 1
                if confirm_count >= confirm_bars and hold_bars >= 4:
                    sig[i] = 0
                    stop_price[i] = ma_exit[i]
                    in_short = False
                    hold_bars = 0
                    confirm_count = 0
                    continue
            else:
                confirm_count = 0

            # 动量止损
            if not np.isnan(ma20_ret[i]) and ma20_ret[i] > 0.03 and hold_bars >= 6:
                sig[i] = 0
                stop_price[i] = c
                in_short = False
                hold_bars = 0
                confirm_count = 0
                continue

            sig[i] = sig[i-1]
            stop_price[i] = atr_stop if cur_atr > 0 else 0
            hold_bars += 1
            continue

        # ===== 空仓：寻找入场 =====
        sig[i] = 0
        stop_price[i] = 0

        # 做多：日线非空头 + 1h底分型 + 小波闸门
        if daily_trend[i] >= 0:
            strength = chan_bottom_strength(close, low, high, i)
            if strength >= 1 and not np.isnan(wt_1h[i]) and c >= wt_1h[i] * (1 - gate_tol):
                sig[i] = pos_long
                in_long = True
                hold_bars = 0
                confirm_count = 0
                entry_price = low[i-1]
                extreme_price = h
                if cur_atr > 0:
                    stop_price[i] = c - atr_mult * cur_atr
                continue

        # 做空：日线空头 + 1h顶分型 + 小波闸门
        if daily_trend[i] <= 0:
            strength = chan_top_strength(close, low, high, i)
            min_str = 2 if short_strong_only else 1
            if strength >= min_str and not np.isnan(wt_1h[i]) and c <= wt_1h[i] * (1 + gate_tol):
                sig[i] = -pos_short
                in_short = True
                hold_bars = 0
                confirm_count = 0
                entry_price = high[i-1]
                extreme_price = l
                if cur_atr > 0:
                    stop_price[i] = c + atr_mult * cur_atr
                continue

    return {'sig': sig, 'stop_price': stop_price, 'daily_trend': daily_trend}
