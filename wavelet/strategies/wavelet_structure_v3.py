"""
小波结构确认策略 v3 - 最终优化版
核心改进：
1. 缠论强势底分型入场（阳包阴+收在上一根高点之上）
2. MA40 + 2日二次确认离场
3. 20日动量止损（-4%）
4. 二买加仓（30%仓位）
5. 硬止损：跌破底分型低点
"""

import numpy as np
import pandas as pd
import pywt


def causal_wt(prices, w=150, wavelet='db4', level=4, keep=1):
    """因果小波分解：第i天只用0~i的数据，返回第keep层重构序列"""
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
    """缠论底分型强度评级
    返回: 0=不是, 1=普通底分型, 2=强势底分型（阳包阴+收在前高之上）
    """
    if i < 2:
        return 0
    # 标准底分型：中间low最低
    if low[i-1] >= low[i-2] or low[i-1] >= low[i]:
        return 0
    # 第三根必须是阳线
    if close[i] <= close[i-1]:
        return 1
    # 强势：第三根实体 > 第二根实体1.5倍 且 收盘价 > 第二根高点
    body2 = abs(close[i-1] - close[i-2])
    body3 = close[i] - close[i-1]
    if body3 > body2 * 1.5 and close[i] > high[i-1]:
        return 2
    return 1


def build_sig_etf_v3(df, wt_w=150, wavelet='db4', level=4, keep=1,
                      pos_full=0.95, gate='close',
                      min_hold=3, exit_ma=40, confirm_days=2,
                      exit_m20=-0.04, hard_stop_pct=0.01,
                      second_buy_enable=True, second_buy_pos=0.3,
                      retrace_min=0.02, second_buy_min_days=5,
                      slope_ban_th=-0.05, slope_win=60,
                      strong_bottom_only=True):
    """
    v3最终版：强势底分型 + MA40二次确认 + 二买加仓
    
    参数（最优均衡配置）：
      gate='close', exit_ma=40, confirm_days=2, exit_m20=-0.04
      second_buy_enable=True, second_buy_pos=0.3, strong_bottom_only=True
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
    
    # 动量
    ma20_ret = pd.Series(close).pct_change(20).values
    df['mm20'] = ma20_ret
    
    # 年线斜率
    df['ma250'] = df['close'].rolling(250).mean()
    df['ma250_slope'] = df['ma250'].diff(slope_win) / df['ma250'].shift(slope_win)
    ma250_slope = df['ma250_slope'].values
    
    sig = np.zeros(n)
    bottom_mark = np.zeros(n)  # 标注底分型位置
    
    in_pos = False
    pos_level = 0  # 0空 1一买 2二买
    hold_days = 0
    bottom_low = 0
    bottom_idx = -1
    confirm_count = 0
    added = False  # 是否加过仓
    
    for i in range(2, n):
        # 年线斜率过滤（熊市禁入）
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
            
            # 1. 硬止损：跌破底分型低点
            if low[i] < bottom_low * (1 - hard_stop_pct):
                exit_now = True
            
            # 2. 20日动量止损
            if not np.isnan(ma20_ret[i]) and ma20_ret[i] < exit_m20 and hold_days >= min_hold:
                exit_now = True
            
            # 3. MA + 二次确认
            if not np.isnan(ma_exit[i]) and close[i] < ma_exit[i]:
                confirm_count += 1
                if confirm_count >= confirm_days and hold_days >= min_hold:
                    exit_now = True
            else:
                confirm_count = 0  # 收回均线上方，重置
            
            if exit_now:
                sig[i] = 0
                in_pos = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                continue
            
            # ===== 二买加仓 =====
            if second_buy_enable and not added and hold_days >= min_hold and pos_level == 1:
                strength = chan_bottom_strength(close, low, high, i)
                if strength >= 1:  # 任意底分型都可以作为二买确认
                    cur_low = low[i-1]
                    high_since = np.max(high[bottom_idx + 1:i])
                    retrace = 1 - cur_low / high_since if high_since > 0 else 0
                    not_break = cur_low > bottom_low * (1 - hard_stop_pct)
                    far = (i - 1) - bottom_idx >= second_buy_min_days
                    
                    if not_break and retrace >= retrace_min and far:
                        # 二买加仓
                        sig[i] = pos_full + second_buy_pos
                        pos_level = 2
                        added = True
                        hold_days += 1
                        continue
            
            sig[i] = sig[i-1]
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
        
        # 标记底分型
        bottom_mark[i] = strength
        
        # 父级别闸门
        if np.isnan(wt[i]):
            sig[i] = 0
            continue
        
        if gate == 'close':
            if close[i] < wt[i] - 1e-8:
                sig[i] = 0
                continue
        elif gate == 'flator_close':
            if close[i] < wt[i] * 0.985:
                sig[i] = 0
                continue
        
        # 入场
        sig[i] = pos_full
        in_pos = True
        pos_level = 1
        hold_days = 0
        confirm_count = 0
        bottom_low = low[i-1]
        bottom_idx = i - 1
        added = False
    
    df['sig'] = sig
    df['bottom_mark'] = bottom_mark
    return df


def eval_sig(df, cost=0.0006, benchmark_start=None):
    """评估信号"""
    df = df.copy()
    sig_eff = df['sig'].shift(1).fillna(0).values
    close = df['close'].values
    ret = df['close'].pct_change().fillna(0).values
    
    n = len(df)
    strat_ret = np.zeros(n)
    equity = np.ones(n)
    
    in_pos = False
    prev_sig = 0
    
    for i in range(1, n):
        s = sig_eff[i]
        pc = close[i-1]
        c = close[i]
        daily_ret = c / pc - 1 if pc > 0 else 0
        
        if s > 0.01:
            if not in_pos:
                trade_cost = cost * s  # 按仓位比例算手续费
                strat_ret[i] = daily_ret * s - trade_cost
                in_pos = True
            else:
                if abs(s - prev_sig) > 0.01:
                    delta = abs(s - prev_sig)
                    trade_cost = cost * delta
                    strat_ret[i] = daily_ret * s - trade_cost
                else:
                    strat_ret[i] = daily_ret * prev_sig
            prev_sig = s
        else:
            if in_pos:
                trade_cost = cost * prev_sig
                strat_ret[i] = daily_ret * prev_sig - trade_cost
                in_pos = False
                prev_sig = 0
            else:
                strat_ret[i] = 0
                prev_sig = 0
        
        equity[i] = equity[i-1] * (1 + strat_ret[i])
    
    df['strat_ret'] = strat_ret
    df['equity'] = equity
    df['sig_eff'] = sig_eff
    
    # 基准
    if benchmark_start:
        mask = df.index >= benchmark_start
    else:
        mask = pd.Series(True, index=df.index)
    
    # 策略
    eq_masked = equity[mask]
    total_ret = eq_masked[-1] / eq_masked[0] - 1
    
    peak = np.maximum.accumulate(eq_masked)
    dd = eq_masked / peak - 1
    max_dd = np.min(dd)
    
    days = mask.sum()
    years = days / 252
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    
    daily = strat_ret[mask]
    ann_vol = np.std(daily) * np.sqrt(252) if np.std(daily) > 0 else 0.001
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    pos_rate = (sig_eff[mask] > 0.01).sum() / mask.sum()
    
    # 交易段
    trades = []
    in_trade = False
    trade_ret = 1.0
    for i in range(1, n):
        if mask[i]:
            if sig_eff[i] > 0.01 and sig_eff[i-1] <= 0.01:
                in_trade = True
                trade_ret = 1.0
            elif sig_eff[i] > 0.01 and in_trade:
                trade_ret *= (1 + strat_ret[i])
            elif sig_eff[i] <= 0.01 and in_trade:
                trades.append(trade_ret)
                in_trade = False
    if in_trade:
        trades.append(trade_ret)
    
    n_trades = len(trades)
    win_rate = np.mean([r > 1 for r in trades]) if n_trades > 0 else 0
    
    # 分年
    yearly = {}
    first_year = df.index[mask][0].year
    last_year = df.index[mask][-1].year
    for year in range(first_year, last_year + 1):
        ymask = mask & (df.index.year == year)
        if ymask.sum() > 10:
            eq_y = equity[ymask]
            yearly[year] = eq_y[-1] / eq_y[0] - 1
    
    stats = {
        'total_ret': total_ret,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'pos_rate': pos_rate,
        'n_trades': n_trades,
        'win_rate': win_rate,
        'yearly': yearly,
        'n_days': int(days),
    }
    
    return df, stats
