"""
小波多尺度期货策略 v4 - 双向交易版
做多 + 做空，2xATR止损，MA40二次确认离场，强势分型入场
"""

import numpy as np
import pandas as pd
import pywt


def causal_wt(prices, w=100, wavelet='db4', level=4, keep=1):
    """因果小波分解：第i天只用0~i的数据，避免未来函数"""
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


def chan_top_strength(close, low, high, i):
    """缠论顶分型强度：0=无 1=普通 2=强势（阴包阳+收在前低之下）"""
    if i < 2:
        return 0
    # 顶分型：中间K线高点最高
    if high[i-1] <= high[i-2] or high[i-1] <= high[i]:
        return 0
    # 第3根阴线
    if close[i] >= close[i-1]:
        return 1  # 普通顶分型
    # 强势：阴包阳 + 收在前低之下
    body2 = abs(close[i-1] - close[i-2])
    body3 = close[i-1] - close[i]  # 阴线实体
    if body3 > body2 * 1.5 and close[i] < low[i-1]:
        return 2
    return 1


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


def calc_atr(high, low, close, period=14):
    """计算ATR"""
    n = len(close)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
    atr = pd.Series(tr).rolling(period).mean().values
    return atr


def build_sig_futures_v4(df, atr_mult=2.0, exit_ma=40, confirm_days=2,
                          gate='flator_close', wt_w=100, level=4,
                          pos_ratio=0.3, second_pos=0.15,
                          retrace_min=0.02, strong_only=False,
                          enable_short=True):
    """
    期货双向信号生成 v4
    
    做多：周线多头 + 强势底分型 + 父级别闸门
    做空：周线空头 + 强势顶分型 + 父级别闸门（反向）
    止损：atr_mult * ATR 追踪止损
    离场：跌破MA40二次确认 + 动量止损 + 硬止损
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    n = len(close)
    dates = df.index
    
    wt = causal_wt(close, w=wt_w, wavelet='db4', level=level)
    
    # 周线趋势
    df_w = pd.DataFrame({'close': close}, index=dates).resample('W-FRI').last().dropna()
    w_close = df_w['close'].values
    wt_w_val = causal_wt(w_close, w=30, wavelet='db4', level=3)
    
    weekly_trend = np.zeros(n, dtype=int)  # 1=多头, -1=空头, 0=中性
    for i in range(len(df_w)):
        if np.isnan(wt_w_val[i]):
            continue
        fri_date = df_w.index[i]
        mon_date = fri_date - pd.Timedelta(days=4)
        mask = (dates >= mon_date) & (dates <= fri_date)
        idxs = np.where(mask)[0]
        if len(idxs) > 0:
            weekly_trend[idxs] = 1 if w_close[i] > wt_w_val[i] else -1
    
    ma_exit = pd.Series(close).rolling(exit_ma).mean().values
    ma20_ret = pd.Series(close).pct_change(20).values
    atr = calc_atr(high, low, close, period=14)
    
    sig = np.zeros(n)  # 正=多头仓位, 负=空头仓位
    stop_price = np.zeros(n)
    
    # 状态变量
    in_long = False
    in_short = False
    pos_level = 0  # 0=空, 1=底仓, 2=加仓
    hold_days = 0
    entry_price = 0
    entry_idx = -1
    extreme_price = 0  # 多头=最高价, 空头=最低价
    confirm_count = 0  # MA确认计数
    added = False
    
    for i in range(2, n):
        c = close[i]
        h = high[i]
        l = low[i]
        cur_atr = atr[i] if not np.isnan(atr[i]) else 0
        
        # ===== 多头持仓处理 =====
        if in_long:
            # 更新最高价
            if h > extreme_price:
                extreme_price = h
            
            # ATR追踪止损（从最高回退）
            atr_stop = extreme_price - atr_mult * cur_atr
            if l <= atr_stop and cur_atr > 0:
                sig[i] = 0
                stop_price[i] = atr_stop
                in_long = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                continue
            
            # MA40二次确认离场
            if not np.isnan(ma_exit[i]) and c < ma_exit[i]:
                confirm_count += 1
                if confirm_count >= confirm_days and hold_days >= 2:
                    sig[i] = 0
                    stop_price[i] = ma_exit[i]
                    in_long = False
                    pos_level = 0
                    hold_days = 0
                    confirm_count = 0
                    added = False
                    continue
            else:
                confirm_count = 0
            
            # 动量止损（20日涨幅过大后的回调，或者直接跌破）
            if not np.isnan(ma20_ret[i]) and ma20_ret[i] < -0.05 and hold_days >= 3:
                sig[i] = 0
                stop_price[i] = c
                in_long = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                continue
            
            # 二买加仓
            if not added and hold_days >= 3 and pos_level == 1:
                strength = chan_bottom_strength(close, low, high, i)
                if strength >= 1:
                    cur_low = low[i-1]
                    high_since = np.max(high[entry_idx+1:i])
                    retrace = 1 - cur_low / high_since if high_since > 0 else 0
                    not_break = cur_low > entry_price * 0.99
                    far = (i-1) - entry_idx >= 5
                    if not_break and retrace >= retrace_min and far:
                        new_pos = pos_ratio + second_pos
                        sig[i] = new_pos
                        pos_level = 2
                        added = True
                        hold_days += 1
                        stop_price[i] = atr_stop if cur_atr > 0 else 0
                        continue
            
            # 继续持有
            sig[i] = sig[i-1]
            stop_price[i] = atr_stop if cur_atr > 0 else 0
            hold_days += 1
            continue
        
        # ===== 空头持仓处理 =====
        if in_short:
            # 更新最低价
            if l < extreme_price:
                extreme_price = l
            
            # ATR追踪止损（从最低反弹）
            atr_stop = extreme_price + atr_mult * cur_atr
            if h >= atr_stop and cur_atr > 0:
                sig[i] = 0
                stop_price[i] = atr_stop
                in_short = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                continue
            
            # MA40二次确认（向上突破MA40）
            if not np.isnan(ma_exit[i]) and c > ma_exit[i]:
                confirm_count += 1
                if confirm_count >= confirm_days and hold_days >= 2:
                    sig[i] = 0
                    stop_price[i] = ma_exit[i]
                    in_short = False
                    pos_level = 0
                    hold_days = 0
                    confirm_count = 0
                    added = False
                    continue
            else:
                confirm_count = 0
            
            # 动量止损（20日跌幅过大后的反弹）
            if not np.isnan(ma20_ret[i]) and ma20_ret[i] > 0.05 and hold_days >= 3:
                sig[i] = 0
                stop_price[i] = c
                in_short = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                continue
            
            # 二卖加仓（空头加仓）
            if not added and hold_days >= 3 and pos_level == 1:
                strength = chan_top_strength(close, low, high, i)
                if strength >= 1:
                    cur_high = high[i-1]
                    low_since = np.min(low[entry_idx+1:i])
                    retrace = cur_high / low_since - 1 if low_since > 0 else 0
                    not_break = cur_high < entry_price * 1.01  # 没突破入场价
                    far = (i-1) - entry_idx >= 5
                    if not_break and retrace >= retrace_min and far:
                        new_pos = -(pos_ratio + second_pos)
                        sig[i] = new_pos
                        pos_level = 2
                        added = True
                        hold_days += 1
                        stop_price[i] = atr_stop if cur_atr > 0 else 0
                        continue
            
            # 继续持有
            sig[i] = sig[i-1]
            stop_price[i] = atr_stop if cur_atr > 0 else 0
            hold_days += 1
            continue
        
        # ===== 空仓：尝试入场 =====
        sig[i] = 0
        stop_price[i] = 0
        
        # 做多条件
        if weekly_trend[i] > 0 or weekly_trend[i] == 0:  # 周线非空头才做多
            strength = chan_bottom_strength(close, low, high, i)
            if (strong_only and strength >= 2) or (not strong_only and strength >= 1):
                if not np.isnan(wt[i]):
                    # 父级别闸门
                    gate_ok = False
                    if gate == 'close':
                        gate_ok = c >= wt[i]
                    elif gate == 'flator_close':
                        gate_ok = c >= wt[i] * 0.985  # 1.5%容差
                    if gate_ok:
                        # 开多
                        sig[i] = pos_ratio
                        in_long = True
                        pos_level = 1
                        hold_days = 0
                        confirm_count = 0
                        entry_price = low[i-1]  # 底分型低点
                        entry_idx = i - 1
                        extreme_price = h
                        added = False
                        if cur_atr > 0:
                            stop_price[i] = c - atr_mult * cur_atr
                        continue
        
        # 做空条件
        if enable_short and (weekly_trend[i] < 0 or weekly_trend[i] == 0):
            strength = chan_top_strength(close, low, high, i)
            if (strong_only and strength >= 2) or (not strong_only and strength >= 1):
                if not np.isnan(wt[i]):
                    # 父级别闸门（反向：价格在趋势线之下）
                    gate_ok = False
                    if gate == 'close':
                        gate_ok = c <= wt[i]
                    elif gate == 'flator_close':
                        gate_ok = c <= wt[i] * 1.015  # 1.5%容差
                    if gate_ok:
                        # 开空
                        sig[i] = -pos_ratio
                        in_short = True
                        pos_level = 1
                        hold_days = 0
                        confirm_count = 0
                        entry_price = high[i-1]  # 顶分型高点
                        entry_idx = i - 1
                        extreme_price = l
                        added = False
                        if cur_atr > 0:
                            stop_price[i] = c + atr_mult * cur_atr
                        continue
    
    return {
        'sig': sig,
        'stop_price': stop_price,
        'wt': wt,
        'weekly_trend': weekly_trend,
        'ma_exit': ma_exit,
        'atr': atr,
    }


def backtest_futures_v4(df, sig_result, margin_rate=0.1, 
                         fee_rate=0.00005, init_capital=200000,
                         contract_size=15, min_margin_ratio=0.05):
    """
    期货回测引擎 v4 - 支持双向
    盯市盈亏 + 保证金管理 + 强平判断
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    sig = sig_result['sig']
    n = len(close)
    
    equity = np.zeros(n)
    margin = np.zeros(n)
    available = np.zeros(n)
    n_contracts_arr = np.zeros(n)
    trade_log = []
    
    capital = init_capital
    equity[0] = capital
    available[0] = capital
    margin[0] = 0
    
    in_pos = 0  # 0=空, 1=多, -1=空
    n_contracts = 0
    entry_price = 0
    entry_date = None
    position_pnl = 0
    
    for i in range(1, n):
        c = close[i]
        pc = close[i-1]
        
        # 计算持仓盯市盈亏
        if in_pos != 0 and n_contracts > 0:
            daily_pnl = (c - pc) * n_contracts * contract_size * in_pos
            capital += daily_pnl
        
        # 当前目标仓位比例
        target_sig = sig[i-1]  # T+1成交
        target_pos = abs(target_sig)
        target_dir = 1 if target_sig > 0 else (-1 if target_sig < 0 else 0)
        
        # 计算当前实际仓位价值
        pos_value = 0
        if in_pos != 0 and n_contracts > 0:
            pos_value = n_contracts * c * contract_size
        
        current_margin = pos_value * margin_rate if in_pos != 0 else 0
        
        # 需要调仓
        if target_dir != in_pos or (target_dir == in_pos and target_sig != 0 and abs(sig[i-1]) != abs(sig[i-2])):
            # 先平仓
            if in_pos != 0 and n_contracts > 0:
                # 平仓手续费
                close_fee = pos_value * fee_rate
                capital -= close_fee
                trade_log.append({
                    'date': df.index[i].strftime('%Y-%m-%d'),
                    'action': '平多' if in_pos > 0 else '平空',
                    'price': c,
                    'contracts': n_contracts,
                    'pnl': position_pnl,
                    'fee': close_fee,
                })
                in_pos = 0
                n_contracts = 0
                position_pnl = 0
                pos_value = 0
                current_margin = 0
            
            # 再开新仓
            if target_dir != 0 and target_pos > 0:
                # 计算可开仓手数
                max_margin = capital * target_pos
                contracts_per_lot = c * contract_size * margin_rate
                n_new = int(max_margin / contracts_per_lot) if contracts_per_lot > 0 else 0
                
                if n_new > 0:
                    new_pos_value = n_new * c * contract_size
                    open_fee = new_pos_value * fee_rate
                    capital -= open_fee
                    
                    in_pos = target_dir
                    n_contracts = n_new
                    entry_price = c
                    entry_date = df.index[i]
                    position_pnl = 0
                    current_margin = new_pos_value * margin_rate
                    
                    trade_log.append({
                        'date': df.index[i].strftime('%Y-%m-%d'),
                        'action': '开多' if target_dir > 0 else '开空',
                        'price': c,
                        'contracts': n_new,
                        'fee': open_fee,
                    })
        
        # 更新权益
        pos_value = n_contracts * c * contract_size if in_pos != 0 else 0
        current_margin = pos_value * margin_rate if in_pos != 0 else 0
        
        equity[i] = capital
        margin[i] = current_margin
        available[i] = capital - current_margin
        n_contracts_arr[i] = n_contracts * in_pos
        
        # 强平检查（保证金不足）
        if in_pos != 0 and n_contracts > 0:
            min_margin = pos_value * min_margin_ratio
            if capital < min_margin:
                # 强平
                close_fee = pos_value * fee_rate
                capital -= close_fee
                trade_log.append({
                    'date': df.index[i].strftime('%Y-%m-%d'),
                    'action': '强平',
                    'price': c,
                    'contracts': n_contracts,
                    'pnl': position_pnl,
                    'fee': close_fee,
                })
                in_pos = 0
                n_contracts = 0
                position_pnl = 0
                equity[i] = capital
                margin[i] = 0
    
    # 统计
    total_ret = equity[-1] / init_capital - 1
    years = n / 252
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    
    daily_ret = np.diff(equity) / equity[:-1]
    daily_ret = np.concatenate([[0], daily_ret])
    ann_vol = np.std(daily_ret) * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1
    max_dd = np.min(drawdown)
    
    # 统计交易盈亏
    realized_pnls = []
    current_trade = None
    for t in trade_log:
        if t['action'] in ('开多', '开空'):
            current_trade = {
                'entry_price': t['price'],
                'direction': 1 if t['action'] == '开多' else -1,
                'contracts': t['contracts'],
                'date': t['date'],
            }
        elif t['action'] in ('平多', '平空', '强平') and current_trade is not None:
            pnl = (t['price'] - current_trade['entry_price']) * current_trade['contracts'] * contract_size * current_trade['direction']
            t['pnl'] = pnl
            realized_pnls.append(pnl)
            current_trade = None
    
    trades = [t for t in trade_log if t['action'] in ('平多', '平空', '强平')]
    wins = sum(1 for t in trades if t.get('pnl', 0) > 0)
    win_rate = wins / len(trades) if len(trades) > 0 else 0
    forced_liq = sum(1 for t in trade_log if t['action'] == '强平')
    
    result = {
        'equity_curve': equity,
        'drawdown': drawdown,
        'margin_curve': margin,
        'n_contracts': n_contracts_arr,
        'total_return': total_ret,
        'annual_return': ann_ret,
        'annual_vol': ann_vol,
        'sharpe': sharpe,
        'max_drawdown': max_dd,
        'trade_count': len(trades),
        'win_rate': win_rate,
        'forced_liquidations': forced_liq,
        'trade_log': trade_log,
        'final_equity': equity[-1],
    }
    return result
