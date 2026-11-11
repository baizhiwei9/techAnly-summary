"""
小波结构确认策略 v3 - 期货版
核心：强势底分型入场 + 2xATR止损 + MA40二次确认 + 多尺度离场（MA40 + wt趋势线）
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
    """缠论底分型强度：0=无 1=普通 2=强势"""
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


def calc_atr(high, low, close, period=14):
    """计算ATR"""
    n = len(close)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i-1]),
                    abs(low[i] - close[i-1]))
    atr = pd.Series(tr).rolling(period).mean().values
    return atr


def build_sig_futures_v3(df, wt_w=150, wavelet='db4', level=4, keep=1,
                          pos_target=1.0, gate='close',
                          min_hold=3, exit_ma=40, confirm_days=2,
                          atr_period=14, atr_mult=2.0,
                          second_buy_enable=True, second_buy_pos=0.3,
                          retrace_min=0.02, second_buy_min_days=5,
                          slope_ban_th=-0.10, slope_win=60,
                          strong_bottom_only=True,
                          multi_scale_exit=True):
    """
    期货版v3：强势底分型 + 2xATR动态止损 + MA40二次确认 + 多尺度离场
    
    multi_scale_exit=True时：
      离场需要同时满足（更严格，拿得住大行情）：
      - 收盘价跌破MA40 且 confirm_days确认
      - 收盘价跌破wt趋势线（父级别也走坏）
    """
    df = df.copy()
    close = df['close'].values
    low = df['low'].values
    high = df['high'].values
    n = len(df)
    
    # 父级别趋势
    wt = causal_wt(close, w=wt_w, wavelet=wavelet, level=level, keep=keep)
    df['wt'] = wt
    
    # 均线
    ma_exit = pd.Series(close).rolling(exit_ma).mean().values
    df[f'ma{exit_ma}'] = ma_exit
    
    # ATR
    atr = calc_atr(high, low, close, atr_period)
    df['atr'] = atr
    
    # 年线斜率（趋势过滤）
    df['ma250'] = df['close'].rolling(250).mean()
    df['ma250_slope'] = df['ma250'].diff(slope_win) / df['ma250'].shift(slope_win)
    ma250_slope = df['ma250_slope'].values
    
    sig = np.zeros(n)
    stop_price = np.zeros(n)  # 记录止损价
    
    in_pos = False
    pos_level = 0
    hold_days = 0
    bottom_low = 0
    bottom_idx = -1
    confirm_count = 0
    added = False
    atr_stop = 0  # ATR止损价（动态）
    
    for i in range(2, n):
        # 年线斜率过滤
        if not np.isnan(ma250_slope[i]) and ma250_slope[i] < slope_ban_th:
            if in_pos:
                sig[i] = 0
                in_pos = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
            else:
                sig[i] = 0
            continue
        
        # ===== 离场 =====
        if in_pos:
            exit_now = False
            exit_reason = ''
            
            # 1. ATR动态止损
            if not np.isnan(atr[i]) and atr[i] > 0:
                # 更新ATR止损（只上移不下移 = 追踪止损）
                new_stop = close[i] - atr_mult * atr[i]
                if new_stop > atr_stop:
                    atr_stop = new_stop
                
                if low[i] < atr_stop:
                    exit_now = True
                    exit_reason = 'ATR止损'
            
            # 2. MA + 二次确认 + 多尺度验证
            if not np.isnan(ma_exit[i]) and close[i] < ma_exit[i]:
                confirm_count += 1
                if confirm_count >= confirm_days and hold_days >= min_hold:
                    if multi_scale_exit:
                        # 多尺度：MA跌破 + wt趋势线也跌破（双破才走）
                        if close[i] < wt[i]:
                            exit_now = True
                            exit_reason = 'MA+WT双破'
                    else:
                        exit_now = True
                        exit_reason = 'MA跌破'
            else:
                confirm_count = 0
            
            if exit_now:
                sig[i] = 0
                in_pos = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                atr_stop = 0
                continue
            
            # ===== 二买加仓 =====
            if second_buy_enable and not added and hold_days >= min_hold and pos_level == 1:
                strength = chan_bottom_strength(close, low, high, i)
                if strength >= 1:
                    cur_low = low[i-1]
                    high_since = np.max(high[bottom_idx + 1:i])
                    retrace = 1 - cur_low / high_since if high_since > 0 else 0
                    not_break = cur_low > bottom_low * 0.98
                    far = (i - 1) - bottom_idx >= second_buy_min_days
                    
                    if not_break and retrace >= retrace_min and far:
                        sig[i] = pos_target + second_buy_pos
                        pos_level = 2
                        added = True
                        hold_days += 1
                        stop_price[i] = atr_stop
                        continue
            
            sig[i] = sig[i-1]
            stop_price[i] = atr_stop
            hold_days += 1
            continue
        
        # ===== 入场 =====
        strength = chan_bottom_strength(close, low, high, i)
        
        if strong_bottom_only:
            if strength < 2:
                sig[i] = 0
                continue
        else:
            if strength < 1:
                sig[i] = 0
                continue
        
        # 闸门
        if np.isnan(wt[i]):
            sig[i] = 0
            continue
        if gate == 'close' and close[i] < wt[i]:
            sig[i] = 0
            continue
        if gate == 'flator_close' and close[i] < wt[i] * 0.97:
            sig[i] = 0
            continue
        
        # ATR合理性检查（极端无波动不入场）
        if np.isnan(atr[i]) or atr[i] <= 0:
            sig[i] = 0
            continue
        
        # 入场
        sig[i] = pos_target
        in_pos = True
        pos_level = 1
        hold_days = 0
        confirm_count = 0
        bottom_low = low[i-1]
        bottom_idx = i - 1
        added = False
        atr_stop = close[i] - atr_mult * atr[i]
        stop_price[i] = atr_stop
    
    df['sig'] = sig
    df['stop_price'] = stop_price
    return df


def backtest_futures(df_sig, margin_rate=0.10, mult=15.0, cost_nominal=0.0005,
                     init_capital=200000, use_roll0=True):
    """
    期货回测引擎
    - 盯市盈亏
    - 强平钳制：权益 < 名义价值 * margin_rate * 0.999 时强平
    - 换月roll0：换月日按原手数平移，不产生额外盈亏
    - 手续费：成交名义价值的 cost_nominal
    """
    close = df_sig['close'].values
    sig = df_sig['sig'].shift(1).fillna(0).values  # T+1成交
    is_roll = df_sig.get('is_roll', pd.Series(0, index=df_sig.index)).values
    n = len(df_sig)
    
    equity = np.zeros(n)
    cash = init_capital
    n_contracts = 0
    in_position = False
    
    trade_count = 0
    win_trades = 0
    forced_liquidations = 0
    stop_hits = 0
    entry_px = 0
    entry_equity = 0
    
    peak = init_capital
    max_dd = 0
    
    equity[0] = init_capital
    
    for i in range(1, n):
        pc = close[i-1]
        c = close[i]
        s = sig[i]
        
        # 换月处理：roll0，持仓手数不变，价格切换
        if is_roll[i] and in_position:
            # 换月无损益，价格基准切换
            entry_px = c  # 重置入场价基准
            equity[i] = equity[i-1]
            continue
        
        # 1. 盯市
        if in_position and n_contracts > 0:
            daily_ret = c / pc - 1 if pc > 0 else 0
            notional = n_contracts * mult * pc
            pnl = notional * daily_ret
            cash += pnl
        
        # 2. 强平检查
        if in_position and n_contracts > 0:
            notional = n_contracts * mult * c
            margin_req = notional * margin_rate * 0.999
            if cash < margin_req:
                # 强平
                trade_notional = n_contracts * mult * c
                cost = trade_notional * cost_nominal
                cash -= cost
                forced_liquidations += 1
                in_position = False
                n_contracts = 0
                equity[i] = cash
                peak = max(peak, cash)
                max_dd = min(max_dd, cash / peak - 1)
                continue
        
        # 3. 信号换仓
        target_pos = s  # 目标仓位比例
        
        if target_pos > 0.01 and not in_position:
            # 开仓
            notional_target = cash * target_pos / margin_rate
            contracts = int(notional_target / (c * mult))
            if contracts > 0:
                trade_notional = contracts * mult * c
                cost = trade_notional * cost_nominal
                cash -= cost
                n_contracts = contracts
                in_position = True
                entry_px = c
                entry_equity = cash
                trade_count += 1
        elif target_pos > 0.01 and in_position:
            # 调整仓位（加仓/减仓）
            current_notional = n_contracts * mult * c
            target_notional = cash * target_pos / margin_rate
            target_contracts = int(target_notional / (c * mult))
            
            if target_contracts != n_contracts:
                delta = target_contracts - n_contracts
                trade_notional = abs(delta) * mult * c
                cost = trade_notional * cost_nominal
                cash -= cost
                n_contracts = target_contracts
                
                if n_contracts == 0:
                    in_position = False
                    # 记录交易结果
                    if cash > entry_equity:
                        win_trades += 1
        elif target_pos <= 0.01 and in_position:
            # 平仓
            trade_notional = n_contracts * mult * c
            cost = trade_notional * cost_nominal
            cash -= cost
            in_position = False
            
            if cash > entry_equity:
                win_trades += 1
            n_contracts = 0
        
        equity[i] = cash
        peak = max(peak, cash)
        max_dd = min(max_dd, cash / peak - 1)
    
    df_sig = df_sig.copy()
    df_sig['equity'] = equity
    
    total_ret = equity[-1] / init_capital - 1
    years = n / 252
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    
    daily_ret = pd.Series(equity).pct_change().fillna(0).values
    ann_vol = np.std(daily_ret) * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    win_rate = win_trades / trade_count if trade_count > 0 else 0
    
    # 分年
    yearly = {}
    df_sig.index = pd.to_datetime(df_sig.index)
    for year in range(df_sig.index[0].year, df_sig.index[-1].year + 1):
        mask = df_sig.index.year == year
        if mask.sum() > 10:
            eq_y = equity[mask]
            yearly[year] = eq_y[-1] / eq_y[0] - 1
    
    stats = {
        'total_ret': total_ret,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'n_trades': trade_count,
        'win_rate': win_rate,
        'forced_liq': forced_liquidations,
        'stop_hits': stop_hits,
        'yearly': yearly,
        'final_equity': equity[-1],
    }
    
    return df_sig, stats
