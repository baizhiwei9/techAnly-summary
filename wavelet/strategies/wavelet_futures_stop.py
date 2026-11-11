"""
期货版小波结构确认策略 v3
含：强平钳制、三层止损（dd/atr/ma）、换月roll0口径
"""
import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wavelet_structure import causal_wt


def build_sig_futures(df, wt_w=150, wavelet="db4", level=4, keep=1,
                       flook=5, up_days=3, pos_target=1.0,
                       gate="close", gate_k=60, gate_th=0.0,
                       min_hold=3, grace=20, exit_ma=30,
                       need_madown=True, exit_m20=-0.05):
    """
    期货版信号生成（同ETF入场逻辑，输出多头仓位信号 0~1）
    """
    df = df.copy()
    close = df['close'].values
    low = df['low'].values
    n = len(close)
    
    # 小波趋势层
    wt = causal_wt(close, w=wt_w, wavelet=wavelet, level=level, keep=keep)
    df['wt'] = wt
    
    # 均线
    df['ma30'] = df['close'].rolling(exit_ma).mean()
    df['ma40'] = df['close'].rolling(40).mean()
    df['wt_ma40'] = df['wt'].rolling(40).mean()
    
    # 20日动量
    df['mm20'] = df['close'].pct_change(20)
    
    # ATR(14)
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs()
        )
    )
    df['atr14'] = df['tr'].rolling(14).mean()
    
    # flook日低点
    df['low_flook'] = df['low'].rolling(flook).min()
    
    # 生成信号
    sig = np.zeros(n)
    in_pos = False
    entry_idx = -1
    entry_low = 0.0
    hold_days = 0
    
    for i in range(wt_w + 2, n):
        if np.isnan(wt[i]):
            continue
        
        if in_pos:
            hold_days += 1
            should_exit = False
            
            # 条件1：跌破底分型低点
            if close[i] < entry_low:
                should_exit = True
            
            # 条件2：grace天后检查
            if hold_days >= grace and not should_exit:
                ma_val = df['ma30'].iloc[i]
                ma_prev = df['ma30'].iloc[i-1]
                if need_madown:
                    if (close[i] < ma_val and ma_val < ma_prev) or (df['mm20'].iloc[i] < exit_m20):
                        should_exit = True
                else:
                    if close[i] < ma_val or df['mm20'].iloc[i] < exit_m20:
                        should_exit = True
            
            if should_exit and hold_days >= min_hold:
                sig[i] = 0
                in_pos = False
                entry_idx = -1
                hold_days = 0
            else:
                sig[i] = sig[i-1]
        else:
            base = i - up_days
            if base < flook:
                sig[i] = 0
                continue
            
            # 条件1：base日最低价创flook日新低
            low_at_base = df['low_flook'].iloc[base]
            if low[base] > low_at_base + 1e-8:
                sig[i] = 0
                continue
            
            # 条件2：base+1...i 最低价 >= 底分型低点
            all_hold = True
            for k in range(base + 1, i + 1):
                if low[k] < low[base] - 1e-8:
                    all_hold = False
                    break
            if not all_hold:
                sig[i] = 0
                continue
            
            # 条件3：当日收阳
            if close[i] <= close[base] + 1e-8:
                sig[i] = 0
                continue
            
            # 条件4：父级别闸门
            if gate == "close":
                if close[i] < wt[i] - 1e-8:
                    sig[i] = 0
                    continue
            
            # 入场
            in_pos = True
            entry_idx = i
            entry_low = low[base]
            hold_days = 0
            sig[i] = pos_target
    
    df['sig'] = sig
    return df


def backtest_futures(df, capital=200000, margin_rate=0.10, cost_nominal=0.0005,
                     mult=15.0, stop_cfg=None, roll_mode="roll0",
                     bt_start="2026-01-01"):
    """
    期货回测引擎（重写版，逻辑清晰）
    
    执行顺序：
    1. 计算当日T+1生效的信号（用昨日sig）
    2. 若持仓：先盯市（按当日收盘价结算）
    3. 若持仓：检查强平 → 检查三层止损
    4. 根据信号换仓（开仓/平仓/不动）
    """
    df = df.copy()
    
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    n = len(df)
    
    # 信号 T+1：昨日信号今日生效
    sig_eff = df['sig'].shift(1).fillna(0).values
    
    # 换月标记
    is_roll = df.get('is_roll', pd.Series([False]*n, index=df.index)).values
    
    # 辅助指标
    atr14 = df.get('atr14', pd.Series([np.nan]*n, index=df.index)).values
    ma40 = df.get('ma40', pd.Series([np.nan]*n, index=df.index)).values
    wt_ma40 = df.get('wt_ma40', pd.Series([np.nan]*n, index=df.index)).values
    wt_arr = df.get('wt', pd.Series([np.nan]*n, index=df.index)).values
    
    # === 状态变量 ===
    equity = np.zeros(n)
    n_contracts_arr = np.zeros(n, dtype=int)
    equity[0] = capital
    
    in_pos = False          # 是否持仓
    n_contracts = 0         # 持仓手数
    entry_px = 0.0          # 入场价
    atr_entry = 0.0         # 入场日ATR
    seg_peak_eq = capital   # 段内权益峰值
    seg_start_eq = capital  # 段起始权益
    wait_sig_zero = False   # 止损/强平后等信号归零
    
    n_forced_liquid = 0
    n_stop_loss = 0
    n_trades = 0
    n_wins = 0
    
    stop_use = stop_cfg is not None
    
    for i in range(1, n):
        current_eq = equity[i-1]
        pc = close[i-1]
        c = close[i]
        s = sig_eff[i]  # 今日生效的目标仓位
        
        # === 1. 持仓中：盯市 ===
        if in_pos and n_contracts > 0:
            # 换月日收益归零（roll0口径）
            if roll_mode == "roll0" and is_roll[i]:
                daily_ret = 0.0
            else:
                daily_ret = c / pc - 1
            
            notional_prev = n_contracts * mult * pc
            pnl = notional_prev * daily_ret
            current_eq += pnl
            
            # 更新段内峰值
            seg_peak_eq = max(seg_peak_eq, current_eq)
        
        # === 2. 持仓中：强平检查 ===
        forced_out = False
        if in_pos and n_contracts > 0:
            notional_now = n_contracts * mult * c
            margin_req = notional_now * margin_rate
            # 强平阈值：权益 < 保证金要求 * 0.999
            if current_eq < margin_req * 0.999:
                # 强平：当日平仓（按收盘价）
                n_forced_liquid += 1
                n_contracts = 0
                in_pos = False
                forced_out = True
                wait_sig_zero = True
                
                n_trades += 1
                if current_eq > seg_start_eq:
                    n_wins += 1
        
        # === 3. 持仓中：三层止损检查 ===
        stop_out = False
        if not forced_out and stop_use and in_pos and n_contracts > 0:
            # (a) 段内权益回撤
            dd_triggered = False
            if stop_cfg.get('dd', 0) > 0:
                dd_th = seg_peak_eq * (1 - stop_cfg['dd'])
                if current_eq < dd_th:
                    dd_triggered = True
            
            # (b) ATR止损
            atr_triggered = False
            if not dd_triggered and stop_cfg.get('atr_mult', 0) > 0 and atr_entry > 0:
                atr_stop_px = entry_px - stop_cfg['atr_mult'] * atr_entry
                if c < atr_stop_px:
                    atr_triggered = True
            
            # (c) 均线止损
            ma_triggered = False
            if not dd_triggered and not atr_triggered and stop_cfg.get('ma', 0) > 0:
                ma_p = stop_cfg['ma']
                # 价格跌破MA40 或 wt跌破MA40(wt)
                if not np.isnan(ma40[i]) and c < ma40[i]:
                    ma_triggered = True
                elif not np.isnan(wt_ma40[i]) and wt_arr[i] < wt_ma40[i]:
                    ma_triggered = True
            
            if dd_triggered or atr_triggered or ma_triggered:
                n_stop_loss += 1
                n_contracts = 0
                in_pos = False
                stop_out = True
                wait_sig_zero = True
                
                n_trades += 1
                if current_eq > seg_start_eq:
                    n_wins += 1
        
        # === 4. 信号换仓 ===
        # 止损/强平后需等信号归零
        if wait_sig_zero:
            if s <= 0.01:
                wait_sig_zero = False
            else:
                s = 0  # 屏蔽开仓信号
        
        if not forced_out and not stop_out:
            if s > 0.01 and not in_pos:
                # 开仓
                # 名义价值目标 = 权益 / 保证金率 × 仓位比例
                notional_target = current_eq * s / margin_rate
                contracts = int(notional_target / (c * mult))
                
                if contracts > 0:
                    # 扣除手续费（双边成本的1/2 = 单边）
                    trade_notional = contracts * mult * c
                    cost = trade_notional * cost_nominal
                    current_eq -= cost
                    
                    n_contracts = contracts
                    in_pos = True
                    entry_px = c
                    atr_entry = atr14[i] if not np.isnan(atr14[i]) else 0
                    seg_peak_eq = current_eq
                    seg_start_eq = current_eq
            
            elif s <= 0.01 and in_pos:
                # 平仓
                trade_notional = n_contracts * mult * c
                cost = trade_notional * cost_nominal
                current_eq -= cost
                
                n_trades += 1
                if current_eq > seg_start_eq:
                    n_wins += 1
                
                n_contracts = 0
                in_pos = False
        
        # === 记录 ===
        equity[i] = current_eq
        n_contracts_arr[i] = n_contracts
    
    # 最后持仓中也计入
    if in_pos:
        n_trades += 1
        if equity[-1] > seg_start_eq:
            n_wins += 1
    
    # 组装结果
    df['equity'] = equity
    df['n_contracts'] = n_contracts_arr
    df['sig_eff'] = sig_eff
    
    # 截取回测段
    df_bt = df[df.index >= bt_start].copy()
    
    if len(df_bt) < 2:
        return df_bt, {'error': '数据不足'}
    
    # === 统计指标 ===
    eq_start = df_bt['equity'].iloc[0]
    eq_end = df_bt['equity'].iloc[-1]
    total_ret = eq_end / eq_start - 1
    n_days = len(df_bt)
    ann_ret = (1 + total_ret) ** (252 / n_days) - 1
    
    df_bt['daily_ret'] = df_bt['equity'].pct_change()
    ann_vol = df_bt['daily_ret'].std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    peak = df_bt['equity'].cummax()
    drawdown = (df_bt['equity'] - peak) / peak
    max_dd = drawdown.min()
    
    pos_rate = (df_bt['n_contracts'] > 0).mean()
    win_rate = n_wins / n_trades if n_trades > 0 else 0
    
    stats = {
        'total_ret': total_ret,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'pos_rate': pos_rate,
        'n_trades': n_trades,
        'win_rate': win_rate,
        'n_forced_liquid': n_forced_liquid,
        'n_stop_loss': n_stop_loss,
        'final_equity': eq_end,
    }
    
    return df_bt, stats
