"""
回测信号生成模块 - 双向交易版（二买三买 + 二卖三卖）
"""
import pandas as pd
import numpy as np
from .factors import calc_capital_factor, calc_capital_factor_short
from typing import Optional


def _precompute_trends(df: pd.DataFrame) -> tuple:
    """预计算所有K线的趋势状态"""
    n = len(df)
    local_trend = np.full(n, 'unknown', dtype=object)
    big_trend = np.full(n, 'unknown', dtype=object)
    
    for i in range(1, n):
        if pd.isna(df.loc[i, 'MA40']) or pd.isna(df.loc[i-1, 'MA40']):
            continue
        close = df.loc[i, 'close']
        ma40 = df.loc[i, 'MA40']
        ma40_prev = df.loc[i-1, 'MA40']
        
        if close > ma40 and ma40 > ma40_prev:
            local_trend[i] = 'uptrend'
        elif close < ma40 and ma40 < ma40_prev:
            local_trend[i] = 'downtrend'
        else:
            local_trend[i] = 'consolidation'
        
        if pd.isna(df.loc[i, 'MA160']) or pd.isna(df.loc[i-1, 'MA160']):
            continue
        ma160 = df.loc[i, 'MA160']
        ma160_prev = df.loc[i-1, 'MA160']
        
        if close > ma160 and ma160 >= ma160_prev * 0.998:
            big_trend[i] = 'bullish'
        elif close < ma160:
            big_trend[i] = 'bearish'
        else:
            big_trend[i] = 'neutral'
    
    return local_trend, big_trend


def _precompute_pullbacks_up(df: pd.DataFrame) -> list:
    """
    预计算所有次级别回调（上涨中的回调，用于二买三买）
    返回: list of (start_idx, low_price, low_idx, completion_idx)
    """
    n = len(df)
    pullbacks = []
    in_pullback = False
    pullback_start = 0
    pullback_low = float('inf')
    pullback_low_idx = 0
    
    for i in range(1, n):
        if pd.isna(df.loc[i, 'MA20']):
            continue
        
        close = df.loc[i, 'close']
        ma20 = df.loc[i, 'MA20']
        
        if not in_pullback:
            if close < ma20 and i > 5:
                lookback_start = max(0, i - 25)
                high_idx = df.loc[lookback_start:i, 'high'].idxmax()
                if high_idx < i - 2:
                    in_pullback = True
                    pullback_start = high_idx
                    pullback_low = df.loc[high_idx:i, 'low'].min()
                    pullback_low_idx = df.loc[high_idx:i, 'low'].idxmin()
        else:
            if df.loc[i, 'low'] < pullback_low:
                pullback_low = df.loc[i, 'low']
                pullback_low_idx = i
            
            if close > ma20:
                pullbacks.append((pullback_start, pullback_low, pullback_low_idx, i))
                in_pullback = False
    
    return pullbacks


def _precompute_pullbacks_down(df: pd.DataFrame) -> list:
    """
    预计算所有次级别反弹（下跌中的反弹，用于二卖三卖）
    返回: list of (start_idx, high_price, high_idx, completion_idx)
    """
    n = len(df)
    pullbacks = []
    in_pullback = False
    pullback_start = 0
    pullback_high = float('-inf')
    pullback_high_idx = 0
    
    for i in range(1, n):
        if pd.isna(df.loc[i, 'MA20']):
            continue
        
        close = df.loc[i, 'close']
        ma20 = df.loc[i, 'MA20']
        
        if not in_pullback:
            # 反弹开始：价格从低位反弹站上MA20
            if close > ma20 and i > 5:
                lookback_start = max(0, i - 25)
                low_idx = df.loc[lookback_start:i, 'low'].idxmin()
                if low_idx < i - 2:
                    in_pullback = True
                    pullback_start = low_idx
                    pullback_high = df.loc[low_idx:i, 'high'].max()
                    pullback_high_idx = df.loc[low_idx:i, 'high'].idxmax()
        else:
            # 更新反弹高点
            if df.loc[i, 'high'] > pullback_high:
                pullback_high = df.loc[i, 'high']
                pullback_high_idx = i
            
            # 反弹结束：跌破MA20
            if close < ma20:
                pullbacks.append((pullback_start, pullback_high, pullback_high_idx, i))
                in_pullback = False
    
    return pullbacks


def _precompute_pivots_up(df: pd.DataFrame) -> list:
    """
    预计算上涨中枢（用于三买）
    返回: list of (start_idx, end_idx, high, low)
    """
    n = len(df)
    pivots = []
    i = 160
    
    while i < n - 15:
        if pd.isna(df.loc[i, 'MA40']):
            i += 1
            continue
        
        window = 20
        if i + window >= n:
            break
        
        segment = df.iloc[i:i + window]
        ma40_seg = segment['MA40'].dropna()
        
        if len(ma40_seg) < 15:
            i += 5
            continue
        
        above = (segment['high'] > ma40_seg * 1.01).sum()
        below = (segment['low'] < ma40_seg * 0.99).sum()
        
        if above >= 3 and below >= 3:
            pivot_high = segment['high'].max()
            pivot_low = segment['low'].min()
            
            if pivot_high > pivot_low * 1.01:
                pivots.append((i, i + window - 1, pivot_high, pivot_low))
                i += window
                continue
        
        i += 5
    
    return pivots


def _precompute_pivots_down(df: pd.DataFrame) -> list:
    """
    预计算下跌中枢（用于三卖）
    返回: list of (start_idx, end_idx, high, low)
    """
    n = len(df)
    pivots = []
    i = 160
    
    while i < n - 15:
        if pd.isna(df.loc[i, 'MA40']):
            i += 1
            continue
        
        window = 20
        if i + window >= n:
            break
        
        segment = df.iloc[i:i + window]
        ma40_seg = segment['MA40'].dropna()
        
        if len(ma40_seg) < 15:
            i += 5
            continue
        
        above = (segment['high'] > ma40_seg * 1.01).sum()
        below = (segment['low'] < ma40_seg * 0.99).sum()
        
        if above >= 3 and below >= 3:
            pivot_high = segment['high'].max()
            pivot_low = segment['low'].min()
            
            if pivot_high > pivot_low * 1.01:
                pivots.append((i, i + window - 1, pivot_high, pivot_low))
                i += window
                continue
        
        i += 5
    
    return pivots


def scan_signals(df: pd.DataFrame) -> list:
    """
    扫描所有买卖点信号（双向）
    Returns: [(idx, signal_type, stop_loss, structure_level), ...]
    signal_type: '二买', '三买', '二卖', '三卖'
    """
    n = len(df)
    
    # 预计算
    local_trend, big_trend = _precompute_trends(df)
    pullbacks_up = _precompute_pullbacks_up(df)  # 回调（做多用）
    pullbacks_down = _precompute_pullbacks_down(df)  # 反弹（做空用）
    pivots_up = _precompute_pivots_up(df)  # 上涨中枢
    pivots_down = _precompute_pivots_down(df)  # 下跌中枢
    
    signals = []
    last_signal_idx = -15
    
    # 建立索引映射
    pullback_up_ends = sorted([pb[3] for pb in pullbacks_up])
    pullback_up_map = {pb[3]: pb for pb in pullbacks_up}
    
    pullback_down_ends = sorted([pb[3] for pb in pullbacks_down])
    pullback_down_map = {pb[3]: pb for pb in pullbacks_down}
    
    for idx in range(160, n):
        if idx - last_signal_idx < 30:
            continue
        
        # === 二买检测 ===
        if big_trend[idx] == 'bullish' and local_trend[idx] == 'uptrend':
            for pb_end in reversed(pullback_up_ends):
                if pb_end < idx - 15 or pb_end > idx:
                    continue
                
                pb = pullback_up_map[pb_end]
                pb_start, pb_low, pb_low_idx, pb_completion = pb
                
                ma40 = df.loc[idx, 'MA40']
                if pd.isna(ma40):
                    continue
                
                if pb_low > ma40 * 0.995:
                    macd = df.loc[idx, 'MACD']
                    if not pd.isna(macd) and macd > -0.001:
                        # 资金面因子过滤
                        factor = calc_capital_factor(df, idx)
                        if factor.get('passed'):
                            signals.append((idx, '二买', pb_low, pb_low))
                            last_signal_idx = idx
                            break
        
        # === 三买检测 ===
        if big_trend[idx] == 'bullish' and idx - last_signal_idx >= 10:
            for piv in reversed(pivots_up):
                piv_start, piv_end, piv_high, piv_low = piv
                
                if piv_end >= idx - 6:
                    continue
                
                if df.loc[idx, 'close'] <= piv_high:
                    continue
                
                bars_since = idx - piv_end
                if bars_since <= 5:
                    continue
                
                for pb_end in reversed(pullback_up_ends):
                    if pb_end < piv_end or pb_end > idx:
                        continue
                    
                    pb = pullback_up_map[pb_end]
                    pb_start, pb_low, pb_low_idx, pb_completion = pb
                    
                    if pb_low > piv_high * 0.998:
                        macd = df.loc[idx, 'MACD']
                        if not pd.isna(macd) and macd > -0.001:
                            # 资金面因子过滤
                            factor = calc_capital_factor(df, idx)
                            if factor.get('passed'):
                                signals.append((idx, '三买', piv_high, pb_low))
                                last_signal_idx = idx
                                break
                
                if idx == last_signal_idx:
                    break
        
        # === 二卖检测（镜像二买）===
        if big_trend[idx] == 'bearish' and local_trend[idx] == 'downtrend':
            for pb_end in reversed(pullback_down_ends):
                if pb_end < idx - 15 or pb_end > idx:
                    continue
                
                pb = pullback_down_map[pb_end]
                pb_start, pb_high, pb_high_idx, pb_completion = pb
                
                ma40 = df.loc[idx, 'MA40']
                if pd.isna(ma40):
                    continue
                
                # 反弹高点低于MA40
                if pb_high < ma40 * 1.002:
                    macd = df.loc[idx, 'MACD']
                    macd_hist = df.loc[idx, 'MACD_HIST']
                    
                    # MACD条件更严格：MACD为负且柱状图为负
                    if not pd.isna(macd) and not pd.isna(macd_hist):
                        if macd < -0.005 and macd_hist < 0:
                            # 成交量确认：当前成交量应该小于反弹期间的平均成交量
                            vol_current = df.loc[idx, 'volume']
                            vol_avg = df.loc[pb_start:idx, 'volume'].mean()
                            vol_confirmed = vol_current < vol_avg * 1.1
                            
                            # 趋势强度：价格应该在MA160下方
                            ma160 = df.loc[idx, 'MA160']
                            trend_strong = (not pd.isna(ma160)) and (df.loc[idx, 'close'] < ma160 * 0.995)
                            
                            # 使用做空专用资金面因子，要求更高的分数
                            factor = calc_capital_factor_short(df, idx)
                            if factor.get('passed') and factor.get('composite', 0) >= 0.55 and vol_confirmed and trend_strong:
                                signals.append((idx, '二卖', pb_high, pb_high))
                                last_signal_idx = idx
                                break
        
        # === 三卖检测（镜像三买）===
        if big_trend[idx] == 'bearish' and idx - last_signal_idx >= 10:
            for piv in reversed(pivots_down):
                piv_start, piv_end, piv_high, piv_low = piv
                
                if piv_end >= idx - 6:
                    continue
                
                # 价格已跌破中枢下沿
                if df.loc[idx, 'close'] >= piv_low:
                    continue
                
                bars_since = idx - piv_end
                if bars_since <= 5:
                    continue
                
                # 找脱离中枢后的反弹
                for pb_end in reversed(pullback_down_ends):
                    if pb_end < piv_end or pb_end > idx:
                        continue
                    
                    pb = pullback_down_map[pb_end]
                    pb_start, pb_high, pb_high_idx, pb_completion = pb
                    
                    # 反弹高点低于中枢下沿
                    if pb_high < piv_low * 1.002:
                        macd = df.loc[idx, 'MACD']
                        if not pd.isna(macd) and macd < 0.001:
                            # 使用做空专用资金面因子
                            factor = calc_capital_factor_short(df, idx)
                            if factor.get('passed'):
                                signals.append((idx, '三卖', piv_low, pb_high))
                                last_signal_idx = idx
                                break
                
                if idx == last_signal_idx:
                    break
    
    return signals


def scan_signals_astock(df: pd.DataFrame) -> list:
    """
    扫描 A 股买点信号（仅做多：二买 + 三买）
    A 股不能随意做空，所以只检测买点
    """
    n = len(df)
    signals = []
    
    # 预计算趋势
    local_trend, big_trend = _precompute_trends(df)
    
    # 预计算次级别回调（上涨中的回调，用于二买三买）
    pullbacks_up = _precompute_pullbacks_up(df)
    pullback_up_map = {pb[3]: pb for pb in pullbacks_up}
    pullback_up_ends = sorted(pullback_up_map.keys())
    
    # 预计算上涨中枢（用于三买）
    pivots_up = _precompute_pivots_up(df)
    
    last_signal_idx = -10
    
    # 从 MA160 可用后开始扫描
    start_idx = 160
    
    for idx in range(start_idx, n):
        if idx - last_signal_idx < 5:
            continue
        
        # === 二买检测（A 股只做多）===
        if big_trend[idx] == 'bullish' and local_trend[idx] == 'uptrend':
            for pb_end in reversed(pullback_up_ends):
                if pb_end < idx - 15 or pb_end > idx:
                    continue
                
                pb = pullback_up_map[pb_end]
                pb_start, pb_low, pb_low_idx, pb_completion = pb
                
                ma40 = df.loc[idx, 'MA40']
                if pd.isna(ma40):
                    continue
                
                if pb_low > ma40 * 0.995:
                    macd = df.loc[idx, 'MACD']
                    if not pd.isna(macd) and macd > -0.001:
                        # 资金面因子过滤
                        factor = calc_capital_factor(df, idx)
                        if factor.get('passed'):
                            signals.append((idx, '二买', pb_low, pb_low))
                            last_signal_idx = idx
                            break
        
        # === 三买检测 ===
        if big_trend[idx] == 'bullish' and idx - last_signal_idx >= 10:
            for piv in reversed(pivots_up):
                piv_start, piv_end, piv_high, piv_low = piv
                
                if piv_end >= idx - 6:
                    continue
                
                if df.loc[idx, 'close'] <= piv_high:
                    continue
                
                bars_since = idx - piv_end
                if bars_since <= 5:
                    continue
                
                for pb_end in reversed(pullback_up_ends):
                    if pb_end < piv_end or pb_end > idx:
                        continue
                    
                    pb = pullback_up_map[pb_end]
                    pb_start, pb_low, pb_low_idx, pb_completion = pb
                    
                    if pb_low > piv_high * 0.998:
                        macd = df.loc[idx, 'MACD']
                        if not pd.isna(macd) and macd > -0.001:
                            # 资金面因子过滤
                            factor = calc_capital_factor(df, idx)
                            if factor.get('passed'):
                                signals.append((idx, '三买', piv_high, pb_low))
                                last_signal_idx = idx
                                break
                
                if idx == last_signal_idx:
                    break
    
    return signals
