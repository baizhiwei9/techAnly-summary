"""
小波结构确认策略 v2 - 缠论底分型 + MA40二次确认 + 二买结构
基于文档《小波分析测试设计》策略十一（ETF版）升级
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
        # 重构父级别趋势：只保留近似系数（低频）
        approx = coeffs[0]
        # 重建到原长度
        details = [np.zeros_like(c) for c in coeffs[1:]]
        rec = pywt.waverec([approx] + details, wavelet, mode='symmetric')
        if len(rec) > w:
            rec = rec[:w]
        elif len(rec) < w:
            rec = np.pad(rec, (0, w - len(rec)), mode='edge')
        wt[i] = rec[-1]
    return wt


def chan_bottom_type(close, low, high, i):
    """缠论标准底分型：第i-1根为底（low最低），i-2和i的low都高于i-1
    返回: (is_bottom, strength)
    strength: 0=不是, 1=普通底分型, 2=强势底分型（第三根阳包阴+收在二根高点上）
    """
    if i < 2:
        return False, 0
    # 标准底分型：中间low最低
    if low[i-1] >= low[i-2] or low[i-1] >= low[i]:
        return False, 0
    # 第三根是阳线
    if close[i] <= close[i-1]:
        return True, 1  # 普通
    # 强势：第三根实体 > 第二根实体1.5倍 且 收盘价 > 第二根高点
    body2 = abs(close[i-1] - close[i-2])
    body3 = close[i] - close[i-1]
    if body3 > body2 * 1.5 and close[i] > high[i-1]:
        return True, 2
    return True, 1


def build_sig_etf_v2(df, wt_w=150, wavelet='db4', level=4, keep=1,
                      pos_full=0.95, gate='close',
                      min_hold=3, exit_ma=40, confirm_days=2,
                      exit_m20=-0.06, slope_ban_th=-0.05, slope_win=60,
                      second_buy_enable=True, second_buy_pos=0.5):
    """
    v2版信号生成：缠论底分型入场 + MA40二次确认离场 + 二买加仓
    
    入场：
      - 缠论底分型（标准）
      - 父级别闸门：分型日收盘价 >= wt（close模式）或 wt-0.5*ATR（flator模式）
      - 年线斜率过滤：250日斜率 < slope_ban_th 时禁入
    
    离场（任一触发）：
      - 收盘价跌破exit_ma日均线，且连续confirm_days天收在均线下方（二次确认）
      - 20日动量 < exit_m20
      - 跌破底分型低点（硬止损）
    
    二买加仓（second_buy_enable=True）：
      - 持仓中，价格回调不破底分型低点，再次形成底分型且收盘价新高
      - 加仓 second_buy_pos 比例
    """
    df = df.copy()
    close = df['close'].values
    low = df['low'].values
    high = df['high'].values
    n = len(df)
    
    # 父级别趋势（小波近似）
    wt = causal_wt(close, w=wt_w, wavelet=wavelet, level=level, keep=keep)
    df['wt'] = wt
    
    # 均线
    df[f'ma{exit_ma}'] = df['close'].rolling(exit_ma).mean()
    ma_exit = df[f'ma{exit_ma}'].values
    
    # 动量
    df['mm20'] = df['close'] / df['close'].shift(20) - 1
    mm20 = df['mm20'].values
    
    # 年线斜率（60日年化）
    df['ma250'] = df['close'].rolling(250).mean()
    df['ma250_slope'] = df['ma250'].diff(slope_win) / df['ma250'].shift(slope_win)
    ma250_slope = df['ma250_slope'].values
    
    sig = np.zeros(n)
    
    in_pos = False
    hold_days = 0
    bottom_idx = -1  # 底分型底K线索引
    bottom_low = 0   # 底分型低点
    entry_idx = -1
    confirm_count = 0  # 跌破均线确认计数
    second_buy_done = False
    
    for i in range(2, n):
        # 年线斜率过滤
        if not np.isnan(ma250_slope[i]) and ma250_slope[i] < slope_ban_th:
            if in_pos:
                sig[i] = 0
                in_pos = False
                hold_days = 0
                confirm_count = 0
                second_buy_done = False
            else:
                sig[i] = 0
            continue
        
        # ======= 离场判断（持仓中先判断离场）=======
        if in_pos:
            exit_now = False
            
            # 条件1：跌破底分型低点（硬止损，不二次确认）
            if low[i] < bottom_low * 0.995:
                exit_now = True
            
            # 条件2：20日动量深跌
            if not np.isnan(mm20[i]) and mm20[i] < exit_m20:
                exit_now = True
            
            # 条件3：跌破exit_ma + confirm_days二次确认
            if not np.isnan(ma_exit[i]) and close[i] < ma_exit[i]:
                confirm_count += 1
                if confirm_count >= confirm_days:
                    exit_now = True
            else:
                confirm_count = 0  # 收回均线上方，重置计数
            
            # 最小持仓保护
            if hold_days < min_hold:
                # 只有硬止损才走，均线跌破和动量都等
                if low[i] >= bottom_low * 0.995:
                    exit_now = False
            
            if exit_now:
                sig[i] = 0
                in_pos = False
                hold_days = 0
                confirm_count = 0
                second_buy_done = False
                continue
            
            # ======= 二买加仓 =======
            if second_buy_enable and not second_buy_done and hold_days > min_hold:
                # 判断是否形成二买：
                # 1. 当前价格回调过（从入场后高点回落）
                # 2. 回调不破底分型低点
                # 3. 再次形成底分型，且收盘价 > 入场时收盘价
                is_bottom, strength = chan_bottom_type(close, low, high, i)
                if is_bottom:
                    # 找入场后的最高
                    high_since_entry = np.max(high[entry_idx:i])
                    # 回调幅度（从最高到分型底）
                    retrace_pct = 1 - low[i-1] / high_since_entry
                    
                    if (retrace_pct > 0.02 and  # 至少回调2%
                        low[i-1] > bottom_low * 0.995 and  # 不破底分型低点
                        close[i] > close[entry_idx] and  # 价格高于入场价
                        close[i] >= wt[i]):  # 仍在趋势线上
                        sig[i] = pos_full + second_buy_pos  # 加仓
                        second_buy_done = True
                        hold_days += 1
                        continue
            
            sig[i] = sig[i-1] if sig[i-1] > 0 else pos_full
            hold_days += 1
            continue
        
        # ======= 入场判断（空仓中）=======
        is_bottom, strength = chan_bottom_type(close, low, high, i)
        if not is_bottom:
            sig[i] = 0
            continue
        
        # 父级别闸门
        if gate == 'close':
            passed = not np.isnan(wt[i]) and close[i] >= wt[i] - 1e-8
        elif gate == 'flator_close':
            passed = not np.isnan(wt[i]) and close[i] >= wt[i] * 0.985  # 放宽1.5%
        else:
            passed = not np.isnan(wt[i]) and close[i] >= wt[i] - 1e-8
        
        if not passed:
            sig[i] = 0
            continue
        
        # 入场
        sig[i] = pos_full
        in_pos = True
        hold_days = 0
        confirm_count = 0
        bottom_idx = i - 1  # 底在第i-1根
        bottom_low = low[i-1]
        entry_idx = i
        second_buy_done = False
    
    df['sig'] = sig
    return df


def eval_sig(df, cost=0.0006, benchmark_start=None):
    """评估信号（与v1相同的回测引擎）"""
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
            # 持仓
            if not in_pos:
                # 开仓手续费
                trade_cost = cost
                strat_ret[i] = daily_ret - trade_cost
                in_pos = True
            else:
                # 持有或加仓
                if abs(s - prev_sig) > 0.01:
                    # 仓位变化，收差额手续费
                    delta = abs(s - prev_sig)
                    trade_cost = cost * delta
                    strat_ret[i] = daily_ret * s - trade_cost
                else:
                    strat_ret[i] = daily_ret * prev_sig
            
            prev_sig = s
        else:
            if in_pos:
                # 平仓
                trade_cost = cost
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
    
    total_ret = equity[mask][-1] / equity[mask][0] - 1
    
    # 回撤
    eq_seg = equity[mask]
    peak = np.maximum.accumulate(eq_seg)
    dd = eq_seg / peak - 1
    max_dd = np.min(dd)
    
    # 年化
    days = mask.sum()
    years = days / 252
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    
    # 夏普
    daily = strat_ret[mask]
    ann_vol = np.std(daily) * np.sqrt(252) if np.std(daily) > 0 else 0.001
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    # 持仓率
    pos_rate = (sig_eff[mask] > 0.01).sum() / mask.sum()
    
    # 交易段数和胜率
    trades = []
    in_trade = False
    trade_start = 0
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
    for year in range(df.index[mask][0].year, df.index[mask][-1].year + 1):
        ymask = mask & (df.index.year == year)
        if ymask.sum() > 10:
            yret = equity[ymask][-1] / equity[ymask][0] - 1
            yearly[year] = yret
    
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
