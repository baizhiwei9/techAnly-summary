"""
因果小波分解 + 小波结构确认策略（策略十一）
ETF版本：wavelet_structure.py
"""
import numpy as np
import pandas as pd
import pywt


def causal_wt(close, w=250, wavelet="db4", level=3, keep=1, mode="periodization"):
    """
    因果小波分解：逐日滚动，只用当日及以前数据，无未来函数
    返回 wt 趋势层数组（与输入等长，前 w 个为 NaN）
    """
    n = len(close)
    wt = np.full(n, np.nan)
    
    for i in range(w - 1, n):
        segment = close[i - w + 1: i + 1]
        # 小波分解
        coeffs = pywt.wavedec(segment, wavelet, level=level, mode=mode)
        # 重构：保留近似系数 + 前 keep 个细节系数
        # 近似系数是 coeffs[0]，细节系数从 coeffs[1] 开始
        # keep=1 表示只保留 cA（低频趋势）
        coeffs_recon = [coeffs[0]]  # cA
        for k in range(1, keep + 1):
            if k < len(coeffs):
                coeffs_recon.append(coeffs[k])
            else:
                coeffs_recon.append(np.zeros_like(coeffs[-1]))
        # 其余细节层置零
        for k in range(keep + 1, len(coeffs)):
            coeffs_recon.append(np.zeros_like(coeffs[k]))
        
        recon = pywt.waverec(coeffs_recon, wavelet, mode=mode)
        # waverec 可能返回略长的序列，截取到 w 长度
        recon = recon[:w]
        wt[i] = recon[-1]  # 取最后一个点（当天的趋势值）
    
    return wt


def build_sig_etf(df, wt_w=150, wavelet="db4", level=4, keep=1,
                   flook=5, up_days=3, pos_full=0.95,
                   gate="close", gate_k=60, gate_th=0.0,
                   min_hold=5, grace=20, exit_ma=30,
                   need_madown=True, exit_m20=-0.05,
                   slope_ban_th=-0.05, slope_win=60):
    """
    ETF版小波结构确认策略（策略十一）
    返回带信号的DataFrame
    """
    df = df.copy()
    close = df['close'].values
    low = df['low'].values
    n = len(close)
    
    # === 1. 小波趋势层 ===
    wt = causal_wt(close, w=wt_w, wavelet=wavelet, level=level, keep=keep)
    df['wt'] = wt
    
    # === 2. 均线 ===
    df['ma250'] = df['close'].rolling(250).mean()
    df['ma30'] = df['close'].rolling(exit_ma).mean()
    
    # 年线斜率
    df['ma250_slope'] = (df['ma250'] - df['ma250'].shift(slope_win)) / df['ma250'].shift(slope_win) / slope_win * 252
    
    # 20日动量
    df['mm20'] = df['close'].pct_change(20)
    
    # === 3. 入场：底分型 + 父级别闸门 ===
    # flook日低点
    df['low_flook'] = df['low'].rolling(flook).min()
    
    # 底分型识别
    df['bottom_type'] = False  # 底分型确认日标记
    
    # 入场信号变量
    sig = np.zeros(n)  # 仓位信号 0~1
    in_pos = False
    entry_idx = -1
    entry_low = 0.0  # 底分型低点
    hold_days = 0
    
    for i in range(wt_w + 2, n):
        if np.isnan(wt[i]):
            continue
        
        # 年线斜率禁入检查
        if not np.isnan(df['ma250_slope'].iloc[i]) and df['ma250_slope'].iloc[i] < slope_ban_th:
            if in_pos:
                # 立即离场
                sig[i] = 0
                in_pos = False
                entry_idx = -1
                hold_days = 0
            continue
        
        if in_pos:
            hold_days += 1
            
            # 离场检查
            should_exit = False
            
            # 条件1：跌破底分型低点（全程有效）
            if close[i] < entry_low:
                should_exit = True
            
            # 条件2：grace天后检查均线+动量
            if hold_days >= grace and not should_exit:
                ma_val = df['ma30'].iloc[i]
                ma_prev = df['ma30'].iloc[i-1]
                
                if need_madown:
                    # 收盘<MA30 且 MA30向下，或 20日动量<阈值
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
            # 寻找底分型
            base = i - up_days
            if base < flook:
                sig[i] = 0
                continue
            
            # 条件1：base日最低价创flook日新低（底分型低点）
            low_at_base = df['low_flook'].iloc[base]
            if low[base] > low_at_base + 1e-8:
                sig[i] = 0
                continue
            
            # 条件2：base+1...i共up_days日全部不破该低点（最低价 >= 底分型低点）
            all_hold = True
            for k in range(base + 1, i + 1):
                if low[k] < low[base] - 1e-8:
                    all_hold = False
                    break
            if not all_hold:
                sig[i] = 0
                continue
            
            # 条件3：当日收阳（close[i] > close[base]）
            if close[i] <= close[base] + 1e-8:
                sig[i] = 0
                continue
            
            # 条件4：父级别闸门
            if gate == "close":
                if close[i] < wt[i] - 1e-8:
                    sig[i] = 0
                    continue
            elif gate == "slope60" or gate == "slope20" or gate == "slope10":
                win = int(gate.replace("slope", ""))
                wt_slope = (wt[i] - wt[i - win]) / wt[i - win]
                if wt_slope < gate_th:
                    sig[i] = 0
                    continue
            elif gate == "flat":
                if wt[i] < wt[i-1] - 1e-8:
                    sig[i] = 0
                    continue
            elif gate == "flator_close":
                if wt[i] < wt[i-1] - 1e-8 and close[i] < wt[i] - 1e-8:
                    sig[i] = 0
                    continue
            
            # 条件5：年线斜率禁入
            if not np.isnan(df['ma250_slope'].iloc[i]) and df['ma250_slope'].iloc[i] < slope_ban_th:
                sig[i] = 0
                continue
            
            # 入场！
            in_pos = True
            entry_idx = i
            entry_low = low[base]  # 底分型低点（最低价）
            hold_days = 0
            
            # 仓位 = max(底分型轨, 轨道A防守轨) × pos_full
            # 简化：底分型轨 = close[base] / close[i] 作为风险底线
            # 文档：pos = max(底分型轨, 轨道A防守轨) × pos_full
            # 此处按满仓 * pos_full 处理（单一仓位）
            sig[i] = pos_full
            df.loc[df.index[i], 'bottom_type'] = True
    
    df['sig'] = sig
    return df


def eval_sig_etf(df, cost=0.0006, benchmark_start="2022-01-01"):
    """
    ETF版回测评估：信号T+1生效，i日信号 i+1日收盘成交
    返回逐日权益与统计指标
    """
    df = df.copy()
    df['ret'] = df['close'].pct_change()
    
    # T+1 成交：sig[i] 在 i+1 日生效
    sig_t1 = df['sig'].shift(1).fillna(0)
    df['sig_eff'] = sig_t1
    
    # 仓位变化 → 交易成本
    pos_change = sig_t1.diff().abs()
    df['cost'] = pos_change * cost
    
    # 策略收益
    df['strat_ret'] = sig_t1 * df['ret'] - df['cost']
    
    # 截取回测起点
    df_bt = df[df.index >= benchmark_start].copy()
    
    # 逐日权益
    df_bt['equity'] = (1 + df_bt['strat_ret']).cumprod()
    df_bt['benchmark'] = (1 + df_bt['ret']).cumprod()
    
    # === 统计指标 ===
    total_ret = df_bt['equity'].iloc[-1] - 1
    n_days = len(df_bt)
    ann_ret = (1 + total_ret) ** (252 / n_days) - 1
    ann_vol = df_bt['strat_ret'].std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    # 最大回撤
    peak = df_bt['equity'].cummax()
    drawdown = (df_bt['equity'] - peak) / peak
    max_dd = drawdown.min()
    
    # 持仓率
    pos_rate = (sig_t1[df_bt.index] > 0.01).mean()
    
    # 交易段统计
    trades = _count_trades(df_bt['sig_eff'].values, df_bt['strat_ret'].values)
    
    # 分年收益
    yearly = {}
    for year in df_bt.index.year.unique():
        ydf = df_bt[df_bt.index.year == year]
        if len(ydf) > 1:
            yearly[str(year)] = ydf['equity'].iloc[-1] / ydf['equity'].iloc[0] - 1
    
    stats = {
        'total_ret': total_ret,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'pos_rate': pos_rate,
        'n_trades': trades['n_trades'],
        'win_rate': trades['win_rate'],
        'yearly': yearly,
        'n_days': n_days,
    }
    
    return df_bt, stats


def _count_trades(sig_eff, strat_ret):
    """统计交易段数和胜率"""
    n_trades = 0
    n_wins = 0
    in_trade = False
    trade_ret = 1.0
    
    for i in range(len(sig_eff)):
        if sig_eff[i] > 0.01 and not in_trade:
            in_trade = True
            trade_ret = 1.0
        elif sig_eff[i] > 0.01 and in_trade:
            trade_ret *= (1 + strat_ret[i])
        elif sig_eff[i] <= 0.01 and in_trade:
            # 平仓
            n_trades += 1
            if trade_ret > 1.0:
                n_wins += 1
            in_trade = False
    
    # 如果最后还在持仓中，也算一段
    if in_trade:
        n_trades += 1
        if trade_ret > 1.0:
            n_wins += 1
    
    win_rate = n_wins / n_trades if n_trades > 0 else 0
    return {'n_trades': n_trades, 'n_wins': n_wins, 'win_rate': win_rate}
