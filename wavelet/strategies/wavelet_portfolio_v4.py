"""
小波多尺度策略 v4 - 最终版
结构：周线趋势过滤 + 日线强势底分型入场 + MA40二次确认离场 + 二买加仓
组合：3只宽基ETF等权（沪深300/创业板/中证500）
"""

import numpy as np
import pandas as pd
import pywt


def causal_wt(prices, w=150, wavelet='db4', level=4, keep=1):
    """因果小波分解"""
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
    """缠论底分型强度：0=无 1=普通 2=强势（阳包阴+收在前高之上）"""
    if i < 2:
        return 0
    if low[i-1] >= low[i-2] or low[i-1] >= low[i]:
        return 0
    if close[i] <= close[i-1]:
        return 1
    body2 = abs(close[i-1] - close[i-2])
    body3 = close[i] - close[i-1]
    if body3 > body2 * 1.5 and close[i] > high[i-1]:
        return 2
    return 1


def build_sig_single(close, low, high, dates,
                      wt_w=150, wavelet='db4', level=4,
                      weekly_w=30, weekly_level=3,
                      gate='flator_close', exit_ma=40, confirm_days=2,
                      exit_m20=-0.04, min_hold=3,
                      pos_full=0.95, second_buy_pos=0.5,
                      retrace_min=0.02, strong_only=False):
    """
    单标的信号生成：周线过滤 + 日线底分型 + MA40二次确认 + 二买
    返回 sig数组 和 状态dict
    """
    n = len(close)
    wt = causal_wt(close, w=wt_w, wavelet=wavelet, level=level)
    
    # 周线趋势
    df_w = pd.DataFrame({'close': close}, index=dates).resample('W-FRI').last().dropna()
    w_close = df_w['close'].values
    wt_w = causal_wt(w_close, w=weekly_w, wavelet=wavelet, level=weekly_level)
    
    weekly_trend = np.zeros(n, dtype=int)
    weekly_wt_val = np.full(n, np.nan)
    for i in range(len(df_w)):
        if np.isnan(wt_w[i]):
            continue
        fri_date = df_w.index[i]
        mon_date = fri_date - pd.Timedelta(days=4)
        mask = (dates >= mon_date) & (dates <= fri_date)
        idxs = np.where(mask)[0]
        if len(idxs) > 0:
            weekly_trend[idxs] = 1 if w_close[i] > wt_w[i] else -1
            weekly_wt_val[idxs] = wt_w[i]
    
    ma_exit = pd.Series(close).rolling(exit_ma).mean().values
    ma20_ret = pd.Series(close).pct_change(20).values
    
    sig = np.zeros(n)
    in_pos = False
    pos_level = 0
    hold_days = 0
    bottom_low = 0
    bottom_idx = -1
    confirm_count = 0
    added = False
    prev_sig = 0
    
    for i in range(2, n):
        # 周线过滤
        if weekly_trend[i] <= 0:
            if in_pos:
                sig[i] = 0
                in_pos = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                prev_sig = 0
            else:
                sig[i] = 0
                prev_sig = 0
            continue
        
        if in_pos:
            exit_now = False
            
            # 硬止损
            if low[i] < bottom_low * 0.99:
                exit_now = True
            
            # 动量止损
            if not np.isnan(ma20_ret[i]) and ma20_ret[i] < exit_m20 and hold_days >= min_hold:
                exit_now = True
            
            # MA + 二次确认
            if not np.isnan(ma_exit[i]) and close[i] < ma_exit[i]:
                confirm_count += 1
                if confirm_count >= confirm_days and hold_days >= min_hold:
                    exit_now = True
            else:
                confirm_count = 0
            
            if exit_now:
                sig[i] = 0
                in_pos = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                prev_sig = 0
                continue
            
            # 二买加仓
            if not added and hold_days >= min_hold and pos_level == 1:
                strength = chan_bottom_strength(close, low, high, i)
                if strength >= 1:
                    cur_low = low[i-1]
                    high_since = np.max(high[bottom_idx+1:i])
                    retrace = 1 - cur_low / high_since if high_since > 0 else 0
                    not_break = cur_low > bottom_low * 0.99
                    far = (i-1) - bottom_idx >= 5
                    if not_break and retrace >= retrace_min and far:
                        new_sig = pos_full + second_buy_pos
                        sig[i] = new_sig
                        prev_sig = new_sig
                        pos_level = 2
                        added = True
                        hold_days += 1
                        continue
            
            sig[i] = prev_sig
            hold_days += 1
            continue
        
        # 入场
        strength = chan_bottom_strength(close, low, high, i)
        if strong_only and strength < 2:
            sig[i] = 0
            prev_sig = 0
            continue
        if not strong_only and strength < 1:
            sig[i] = 0
            prev_sig = 0
            continue
        if np.isnan(wt[i]):
            sig[i] = 0
            prev_sig = 0
            continue
        if gate == 'close' and close[i] < wt[i]:
            sig[i] = 0
            prev_sig = 0
            continue
        if gate == 'flator_close' and close[i] < wt[i] * 0.985:
            sig[i] = 0
            prev_sig = 0
            continue
        
        sig[i] = pos_full
        prev_sig = pos_full
        in_pos = True
        pos_level = 1
        hold_days = 0
        confirm_count = 0
        bottom_low = low[i-1]
        bottom_idx = i - 1
        added = False
    
    result = {
        'sig': sig,
        'wt': wt,
        'weekly_trend': weekly_trend,
        'weekly_wt': weekly_wt_val,
        'ma_exit': ma_exit,
    }
    return result


def backtest_portfolio(etf_data_dict, etf_codes, cost=0.0006,
                        wt_w=150, weekly_w=30,
                        gate='flator_close', exit_ma=40, confirm_days=2,
                        exit_m20=-0.04, pos_full=0.95, sb_pos=0.5,
                        retrace_min=0.02, strong_only=False):
    """
    多ETF组合回测：每只ETF独立运行策略，等权分配资金
    """
    n_etfs = len(etf_codes)
    pos_per_etf = pos_full / n_etfs
    sb_per_etf = sb_pos / n_etfs
    
    all_strat_rets = {}
    all_sigs = {}
    
    for code in etf_codes:
        d = etf_data_dict[code]
        result = build_sig_single(
            d['close'], d['low'], d['high'], d['dates'],
            wt_w=wt_w, weekly_w=weekly_w,
            gate=gate, exit_ma=exit_ma, confirm_days=confirm_days,
            exit_m20=exit_m20, min_hold=3,
            pos_full=pos_per_etf, second_buy_pos=sb_per_etf,
            retrace_min=retrace_min, strong_only=strong_only
        )
        
        sig = result['sig']
        close = d['close']
        n = len(close)
        
        # 计算逐日收益
        strat_ret = np.zeros(n)
        prev_s = 0
        for i in range(1, n):
            s = sig[i-1]  # T+1成交
            pc = close[i-1]
            c = close[i]
            daily_ret = c / pc - 1 if pc > 0 else 0
            
            if s > 0.01:
                if prev_s <= 0.01:
                    # 开仓
                    strat_ret[i] = daily_ret * s - cost * s
                elif abs(s - prev_s) > 0.01:
                    # 调仓
                    delta = abs(s - prev_s)
                    strat_ret[i] = daily_ret * s - cost * delta
                else:
                    strat_ret[i] = daily_ret * prev_s
            else:
                if prev_s > 0.01:
                    # 平仓
                    strat_ret[i] = daily_ret * prev_s - cost * prev_s
                else:
                    strat_ret[i] = 0
            
            prev_s = s
        
        all_strat_rets[code] = pd.Series(strat_ret, index=d['dates'])
        all_sigs[code] = pd.Series(sig, index=d['dates'])
    
    # 组合收益
    df_ret = pd.concat(all_strat_rets, axis=1).fillna(0)
    port_ret = df_ret.sum(axis=1)
    port_eq = (1 + port_ret).cumprod()
    
    # 统计
    total_ret = port_eq.iloc[-1] - 1
    years = len(port_ret) / 252
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = port_ret.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    peak = port_eq.cummax()
    drawdown = port_eq / peak - 1
    max_dd = drawdown.min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    
    # 分年
    yearly = {}
    for y in range(port_eq.index[0].year, port_eq.index[-1].year + 1):
        ye = port_eq[port_eq.index.year == y]
        if len(ye) > 10:
            yearly[y] = ye.iloc[-1] / ye.iloc[0] - 1
    
    stats = {
        'total_ret': total_ret,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'yearly': yearly,
    }
    
    return port_eq, port_ret, drawdown, stats, all_sigs
