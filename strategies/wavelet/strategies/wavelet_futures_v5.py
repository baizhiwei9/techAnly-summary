"""
小波多尺度期货策略 v5 - 优化版（降回撤+提胜率）
核心改进：
  1. 做多/做空不对称仓位（轻仓做空，重质重量做多）
  2. 做空只做强势顶分型（提高胜率，减少假信号）
  3. ATR追踪止损 + MA40二次确认 + 动量止损三层离场
  4. 二买加仓（做多加仓用普通底分型，做空不加仓）
  5. 周线趋势过滤 + 日线小波闸门
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


def chan_bottom_strength(close, low, high, i):
    """缠论底分型强度：0=无 1=普通 2=强势（阳包阴+收在前高之上）"""
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
    """缠论顶分型强度：0=无 1=普通 2=强势（阴包阳+收在前低之下）"""
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


def calc_atr(high, low, close, period=14):
    """计算ATR"""
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


def build_sig_futures_v5(df, atr_mult=2.0, exit_ma=40, confirm_days=2,
                          gate='flator_close', wt_w=100, level=4,
                          pos_long=0.10, pos_short=0.05,
                          sb_long=0.08, sb_short=0.0,
                          retrace_min=0.02, short_strong_only=True,
                          weekly_w=30):
    """
    v5期货双向信号生成
    参数：
        pos_long: 做多初始仓位（如0.10=10%资金）
        pos_short: 做空初始仓位
        sb_long: 做多二买加仓仓位
        sb_short: 做空二卖加仓仓位（默认0=不加仓）
        short_strong_only: 做空只做强势顶分型
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    n = len(close)
    dates = df.index

    # 日线小波
    wt = causal_wt(close, w=wt_w, wavelet='db4', level=level)

    # 周线趋势（用30周窗口）
    df_w = pd.DataFrame({'close': close}, index=dates).resample('W-FRI').last().dropna()
    w_close = df_w['close'].values
    wt_w = causal_wt(w_close, w=weekly_w, wavelet='db4', level=3)
    weekly_trend = np.zeros(n, dtype=int)
    for i in range(len(df_w)):
        if np.isnan(wt_w[i]):
            continue
        fri_date = df_w.index[i]
        mon_date = fri_date - pd.Timedelta(days=4)
        mask = (dates >= mon_date) & (dates <= fri_date)
        idxs = np.where(mask)[0]
        if len(idxs) > 0:
            weekly_trend[idxs] = 1 if w_close[i] > wt_w[i] else -1

    # MA离场线
    ma_exit = pd.Series(close).rolling(exit_ma).mean().values
    # 20日动量（用于动量止损）
    ma20_ret = pd.Series(close).pct_change(20).values
    # ATR
    atr = calc_atr(high, low, close, period=14)

    sig = np.zeros(n)
    stop_price = np.zeros(n)

    in_long = False
    in_short = False
    pos_level = 0
    hold_days = 0
    entry_price = 0
    entry_idx = -1
    extreme_price = 0
    confirm_count = 0
    added = False

    gate_tol = 0.015  # 闸门容差1.5%

    for i in range(2, n):
        c = close[i]
        h = high[i]
        l = low[i]
        cur_atr = atr[i] if not np.isnan(atr[i]) else 0

        # ===== 持仓：多头 =====
        if in_long:
            # 更新极值
            if h > extreme_price:
                extreme_price = h

            # ATR追踪止损（只上移不下移）
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

            # 动量止损（20日跌幅超5%）
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
            if not added and hold_days >= 3 and pos_level == 1 and sb_long > 0:
                strength = chan_bottom_strength(close, low, high, i)
                if strength >= 1:
                    cur_low = low[i-1]
                    high_since = np.max(high[entry_idx+1:i])
                    retrace = 1 - cur_low / high_since if high_since > 0 else 0
                    if (cur_low > entry_price * 0.99 and
                        retrace >= retrace_min and
                        (i-1) - entry_idx >= 5):
                        sig[i] = pos_long + sb_long
                        pos_level = 2
                        added = True
                        hold_days += 1
                        stop_price[i] = atr_stop if cur_atr > 0 else 0
                        continue

            sig[i] = sig[i-1]
            stop_price[i] = atr_stop if cur_atr > 0 else 0
            hold_days += 1
            continue

        # ===== 持仓：空头 =====
        if in_short:
            # 更新极值（向下）
            if l < extreme_price:
                extreme_price = l

            # ATR追踪止损（只下移不上移）
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

            # MA40二次确认离场（向上突破）
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

            # 动量止损（20日涨幅超5%）
            if not np.isnan(ma20_ret[i]) and ma20_ret[i] > 0.05 and hold_days >= 3:
                sig[i] = 0
                stop_price[i] = c
                in_short = False
                pos_level = 0
                hold_days = 0
                confirm_count = 0
                added = False
                continue

            # 二卖加仓（默认不加）
            if not added and hold_days >= 3 and pos_level == 1 and sb_short > 0:
                strength = chan_top_strength(close, low, high, i)
                if strength >= 2:  # 做空加仓必须强势
                    cur_high = high[i-1]
                    low_since = np.min(low[entry_idx+1:i])
                    retrace = cur_high / low_since - 1 if low_since > 0 else 0
                    if (cur_high < entry_price * 1.01 and
                        retrace >= retrace_min and
                        (i-1) - entry_idx >= 5):
                        sig[i] = -(pos_short + sb_short)
                        pos_level = 2
                        added = True
                        hold_days += 1
                        stop_price[i] = atr_stop if cur_atr > 0 else 0
                        continue

            sig[i] = sig[i-1]
            stop_price[i] = atr_stop if cur_atr > 0 else 0
            hold_days += 1
            continue

        # ===== 空仓：寻找入场 =====
        sig[i] = 0
        stop_price[i] = 0

        # 做多：周线非空头 + 普通底分型 + 闸门
        if weekly_trend[i] >= 0:
            strength = chan_bottom_strength(close, low, high, i)
            if strength >= 1 and not np.isnan(wt[i]) and c >= wt[i] * (1 - gate_tol):
                sig[i] = pos_long
                in_long = True
                pos_level = 1
                hold_days = 0
                confirm_count = 0
                added = False
                entry_price = low[i-1]
                entry_idx = i - 1
                extreme_price = h
                if cur_atr > 0:
                    stop_price[i] = c - atr_mult * cur_atr
                continue

        # 做空：周线空头 + (强势/普通)顶分型 + 闸门
        if weekly_trend[i] < 0:
            strength = chan_top_strength(close, low, high, i)
            min_str = 2 if short_strong_only else 1
            if strength >= min_str and not np.isnan(wt[i]) and c <= wt[i] * (1 + gate_tol):
                sig[i] = -pos_short
                in_short = True
                pos_level = 1
                hold_days = 0
                confirm_count = 0
                added = False
                entry_price = high[i-1]
                entry_idx = i - 1
                extreme_price = l
                if cur_atr > 0:
                    stop_price[i] = c + atr_mult * cur_atr
                continue

    return {'sig': sig, 'stop_price': stop_price}


def backtest_futures_v5(df, sig_result, margin_rate=0.1, fee_rate=0.00005,
                         init_capital=200000, contract_size=15,
                         min_margin_ratio=0.05, slippage=0):
    """
    期货回测引擎 v5
    支持双向、盯市盈亏、保证金管理、强平判断、手续费、滑点
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    n = len(close)
    dates = df.index

    sig = sig_result['sig']
    stop_price = sig_result.get('stop_price', np.zeros(n))

    capital = init_capital
    n_contracts = 0  # 正=多 负=空
    entry_price = 0
    avg_cost = 0

    equity = np.zeros(n)
    margin_used = np.zeros(n)
    trade_log = []
    trade_count = 0
    forced_liquidations = 0

    in_pos = False
    pos_direction = 0  # 1=多 -1=空
    entry_idx = -1
    entry_capital = 0
    total_pnl = 0
    win_count = 0
    lose_count = 0
    long_trades = 0
    short_trades = 0
    long_wins = 0
    short_wins = 0
    long_pnl_total = 0
    short_pnl_total = 0

    for i in range(n):
        c = close[i]
        h = high[i]
        l = low[i]
        target_sig = sig[i]
        sp = stop_price[i]

        # ---- 持仓中处理 ----
        if in_pos:
            # 止损触发（用high/low判断是否触及）
            if sp > 0 and pos_direction == 1 and l <= sp:
                # 多头止损，成交价=min(sp, close) 实际用sp
                exit_p = max(sp, l * 0.999)
                pnl = (exit_p - avg_cost) * n_contracts * contract_size
                fee = abs(exit_p * n_contracts * contract_size * fee_rate)
                pnl -= fee
                capital += pnl
                total_pnl += pnl
                trade_count += 1
                if pnl > 0:
                    win_count += 1
                    if pos_direction == 1: long_wins += 1
                    else: short_wins += 1
                else:
                    lose_count += 1
                if pos_direction == 1:
                    long_trades += 1
                    long_pnl_total += pnl
                else:
                    short_trades += 1
                    short_pnl_total += pnl
                trade_log.append({
                    'entry_date': str(dates[entry_idx])[:10],
                    'exit_date': str(dates[i])[:10],
                    'direction': 'long' if pos_direction == 1 else 'short',
                    'entry_price': round(avg_cost, 2),
                    'exit_price': round(exit_p, 2),
                    'contracts': abs(n_contracts),
                    'pnl': round(pnl, 2),
                    'reason': 'atr_stop'
                })
                n_contracts = 0
                in_pos = False
                pos_direction = 0
                entry_idx = -1
                equity[i] = capital
                margin_used[i] = 0
                continue

            if sp > 0 and pos_direction == -1 and h >= sp:
                # 空头止损
                exit_p = min(sp, h * 1.001)
                pnl = (avg_cost - exit_p) * abs(n_contracts) * contract_size
                fee = abs(exit_p * n_contracts * contract_size * fee_rate)
                pnl -= fee
                capital += pnl
                total_pnl += pnl
                trade_count += 1
                if pnl > 0:
                    win_count += 1
                    if pos_direction == 1: long_wins += 1
                    else: short_wins += 1
                else:
                    lose_count += 1
                if pos_direction == 1:
                    long_trades += 1
                    long_pnl_total += pnl
                else:
                    short_trades += 1
                    short_pnl_total += pnl
                trade_log.append({
                    'entry_date': str(dates[entry_idx])[:10],
                    'exit_date': str(dates[i])[:10],
                    'direction': 'long' if pos_direction == 1 else 'short',
                    'entry_price': round(avg_cost, 2),
                    'exit_price': round(exit_p, 2),
                    'contracts': abs(n_contracts),
                    'pnl': round(pnl, 2),
                    'reason': 'atr_stop'
                })
                n_contracts = 0
                in_pos = False
                pos_direction = 0
                entry_idx = -1
                equity[i] = capital
                margin_used[i] = 0
                continue

            # 信号变化：平仓或反向
            if pos_direction == 1 and target_sig <= 0:
                # 多头平仓
                exit_p = c
                pnl = (exit_p - avg_cost) * n_contracts * contract_size
                fee = abs(exit_p * n_contracts * contract_size * fee_rate)
                pnl -= fee
                capital += pnl
                total_pnl += pnl
                trade_count += 1
                reason = 'ma_exit' if target_sig == 0 else 'reverse'
                if pnl > 0:
                    win_count += 1
                    long_wins += 1
                else:
                    lose_count += 1
                long_trades += 1
                long_pnl_total += pnl
                trade_log.append({
                    'entry_date': str(dates[entry_idx])[:10],
                    'exit_date': str(dates[i])[:10],
                    'direction': 'long',
                    'entry_price': round(avg_cost, 2),
                    'exit_price': round(exit_p, 2),
                    'contracts': abs(n_contracts),
                    'pnl': round(pnl, 2),
                    'reason': reason
                })
                n_contracts = 0
                in_pos = False
                pos_direction = 0
                entry_idx = -1
                equity[i] = capital
                margin_used[i] = 0
                continue

            if pos_direction == -1 and target_sig >= 0:
                # 空头平仓
                exit_p = c
                pnl = (avg_cost - exit_p) * abs(n_contracts) * contract_size
                fee = abs(exit_p * n_contracts * contract_size * fee_rate)
                pnl -= fee
                capital += pnl
                total_pnl += pnl
                trade_count += 1
                reason = 'ma_exit' if target_sig == 0 else 'reverse'
                if pnl > 0:
                    win_count += 1
                    short_wins += 1
                else:
                    lose_count += 1
                short_trades += 1
                short_pnl_total += pnl
                trade_log.append({
                    'entry_date': str(dates[entry_idx])[:10],
                    'exit_date': str(dates[i])[:10],
                    'direction': 'short',
                    'entry_price': round(avg_cost, 2),
                    'exit_price': round(exit_p, 2),
                    'contracts': abs(n_contracts),
                    'pnl': round(pnl, 2),
                    'reason': reason
                })
                n_contracts = 0
                in_pos = False
                pos_direction = 0
                entry_idx = -1
                equity[i] = capital
                margin_used[i] = 0
                continue

            # 加仓：仓位变大
            if pos_direction == 1 and target_sig > 0 and abs(target_sig) > abs(sig[i-1] if i > 0 else 0):
                # 多头加仓
                old_n = n_contracts
                new_n = max(int(capital * target_sig / (c * contract_size * margin_rate)), 1)
                add_n = new_n - old_n
                if add_n > 0:
                    new_cost = c
                    avg_cost = (avg_cost * old_n + new_cost * add_n) / (old_n + add_n)
                    n_contracts = new_n
                    fee = new_cost * add_n * contract_size * fee_rate
                    capital -= fee
                equity[i] = capital + (c - avg_cost) * n_contracts * contract_size
                margin_used[i] = c * n_contracts * contract_size * margin_rate
                continue

            if pos_direction == -1 and target_sig < 0 and abs(target_sig) > abs(sig[i-1] if i > 0 else 0):
                # 空头加仓
                old_n = abs(n_contracts)
                new_n = max(int(capital * abs(target_sig) / (c * contract_size * margin_rate)), 1)
                add_n = new_n - old_n
                if add_n > 0:
                    new_cost = c
                    avg_cost = (avg_cost * old_n + new_cost * add_n) / (old_n + add_n)
                    n_contracts = -new_n
                    fee = new_cost * add_n * contract_size * fee_rate
                    capital -= fee
                equity[i] = capital + (avg_cost - c) * abs(n_contracts) * contract_size
                margin_used[i] = c * abs(n_contracts) * contract_size * margin_rate
                continue

            # 正常持仓，盯市盈亏
            if pos_direction == 1:
                equity[i] = capital + (c - avg_cost) * n_contracts * contract_size
            else:
                equity[i] = capital + (avg_cost - c) * abs(n_contracts) * contract_size
            margin_used[i] = c * abs(n_contracts) * contract_size * margin_rate

            # 强平判断
            unrealized = equity[i] - capital
            margin_required = margin_used[i]
            if equity[i] < margin_required * min_margin_ratio / margin_rate:
                # 强平
                forced_liquidations += 1
                pnl = unrealized
                capital = equity[i]
                total_pnl += pnl
                trade_count += 1
                if pnl > 0:
                    win_count += 1
                else:
                    lose_count += 1
                if pos_direction == 1:
                    long_trades += 1
                    long_pnl_total += pnl
                else:
                    short_trades += 1
                    short_pnl_total += pnl
                trade_log.append({
                    'entry_date': str(dates[entry_idx])[:10],
                    'exit_date': str(dates[i])[:10],
                    'direction': 'long' if pos_direction == 1 else 'short',
                    'entry_price': round(avg_cost, 2),
                    'exit_price': round(c, 2),
                    'contracts': abs(n_contracts),
                    'pnl': round(pnl, 2),
                    'reason': 'forced_liquidation'
                })
                n_contracts = 0
                in_pos = False
                pos_direction = 0
                entry_idx = -1
                continue

            continue

        # ---- 空仓：开仓 ----
        if target_sig != 0:
            direction = 1 if target_sig > 0 else -1
            pos_ratio = abs(target_sig)
            max_contracts = int(capital * pos_ratio / (c * contract_size * margin_rate))
            if max_contracts < 1:
                max_contracts = 1

            n_contracts = max_contracts * direction
            avg_cost = c
            entry_price = c
            entry_idx = i
            in_pos = True
            pos_direction = direction
            entry_capital = capital

            # 开仓手续费
            fee = abs(c * n_contracts * contract_size * fee_rate)
            capital -= fee

            equity[i] = capital  # 刚开仓当日无盈亏
            margin_used[i] = c * abs(n_contracts) * contract_size * margin_rate
            continue

        # 空仓信号为0
        equity[i] = capital
        margin_used[i] = 0

    # ===== 计算统计指标 =====
    total_return = equity[-1] / init_capital - 1

    # 年化收益
    trading_days = 252
    years = n / trading_days
    if years > 0:
        annual_return = (1 + total_return) ** (1 / years) - 1
    else:
        annual_return = 0

    # 最大回撤
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    max_drawdown = np.min(drawdown)

    # 夏普
    daily_returns = np.diff(equity) / equity[:-1]
    daily_returns = daily_returns[~np.isnan(daily_returns)]
    if len(daily_returns) > 1 and np.std(daily_returns) > 0:
        sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(trading_days)
    else:
        sharpe = 0

    win_rate = win_count / trade_count if trade_count > 0 else 0

    return {
        'equity_curve': equity,
        'margin_curve': margin_used,
        'total_return': total_return,
        'annual_return': annual_return,
        'max_drawdown': max_drawdown,
        'sharpe': sharpe,
        'win_rate': win_rate,
        'trade_count': trade_count,
        'forced_liquidations': forced_liquidations,
        'total_pnl': total_pnl,
        'win_count': win_count,
        'lose_count': lose_count,
        'long_trades': long_trades,
        'short_trades': short_trades,
        'long_win_rate': long_wins / long_trades if long_trades > 0 else 0,
        'short_win_rate': short_wins / short_trades if short_trades > 0 else 0,
        'long_pnl': long_pnl_total,
        'short_pnl': short_pnl_total,
        'trade_log': trade_log,
    }
