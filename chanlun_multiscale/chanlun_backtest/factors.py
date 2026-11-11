"""
资金面因子模块
设计逻辑：
1. 场外资金充足度：回调时成交量萎缩越多，说明抛压越轻，场外资金等待入场
2. 套牢盘稀少度：当前价格上方的历史套牢筹码占比小
3. 筹码分散度：近期换手分布均匀，持有者众多
"""
import pandas as pd
import numpy as np


def calc_capital_factor(df: pd.DataFrame, idx: int, lookback: int = 60) -> dict:
    """
    计算资金面综合因子
    """
    if idx < lookback:
        return {
            'outside_capital': 0.0,
            'trapped_chips': 0.5,
            'chip_dispersion': 0.5,
            'composite': 0.0,
            'passed': False,
        }

    outside_capital = _calc_outside_capital(df, idx, lookback)
    trapped_chips = _calc_trapped_chips_ratio(df, idx, lookback)
    chip_dispersion = _calc_chip_dispersion(df, idx, lookback)

    # 综合通过条件：三个维度都要达标
    outside_passed = outside_capital >= 0.6
    trapped_passed = trapped_chips >= 0.5
    dispersion_passed = chip_dispersion >= 0.5

    passed = outside_passed and trapped_passed and dispersion_passed

    # 综合评分 (0-1)
    composite = (
        0.35 * outside_capital +
        0.35 * trapped_chips +
        0.30 * chip_dispersion
    )

    return {
        'outside_capital': round(float(outside_capital), 3),
        'trapped_chips': round(float(trapped_chips), 3),
        'chip_dispersion': round(float(chip_dispersion), 3),
        'composite': round(float(composite), 3),
        'passed': bool(passed),
    }


def _calc_outside_capital(df: pd.DataFrame, idx: int, lookback: int) -> float:
    """
    场外资金充足度

    逻辑：
    1. 在回溯窗口内找到最近一次完整的上涨段和回调段
    2. 计算回调段平均成交量 / 上涨段平均成交量 = 缩量比
    3. 缩量比越小，说明抛压越轻，场外资金等待入场，分数越高
    """
    start_idx = max(0, idx - lookback)
    window = df.loc[start_idx:idx]

    if len(window) < 20:
        return 0.0

    closes = window['close'].values
    highs = window['high'].values
    lows = window['low'].values
    volumes = window['volume'].values

    # 找到最近的高点
    high_pos = int(np.argmax(highs))

    # 如果高点在最后几根，说明没有回调
    if high_pos >= len(highs) - 3:
        return 0.0

    # 从高点之后找最低点（回调低点）
    pullback_lows = lows[high_pos:]
    low_offset = int(np.argmin(pullback_lows))
    pullback_end = high_pos + low_offset

    # 找上涨段起点：从高点往前找，找到明显的上涨起点
    # 简单处理：从高点往前找到局部低点
    if high_pos < 5:
        return 0.0

    # 在高点之前找上涨段的起点（回调或盘整的起点）
    segment_start = max(0, high_pos - 20)  # 最多回溯20根K线作为上涨段

    # 上涨段：segment_start 到 high_pos
    # 回调段：high_pos 到 pullback_end
    up_start = segment_start
    up_end = high_pos
    pull_start = high_pos
    pull_end = pullback_end

    if up_end <= up_start or pull_end <= pull_start:
        return 0.0

    up_volumes = volumes[up_start:up_end + 1]
    pull_volumes = volumes[pull_start:pull_end + 1]

    if len(up_volumes) == 0 or len(pull_volumes) == 0:
        return 0.0

    up_avg_vol = np.mean(up_volumes)
    pull_avg_vol = np.mean(pull_volumes)

    if up_avg_vol == 0:
        return 0.0

    # 缩量比
    shrink_ratio = pull_avg_vol / up_avg_vol

    # 缩量比 -> 分数
    # shrink_ratio < 0.5: 明显缩量，高分
    # shrink_ratio = 1.0: 量持平，中等分
    # shrink_ratio > 1.0: 放量下跌，低分
    if shrink_ratio <= 0.3:
        score = 1.0
    elif shrink_ratio <= 0.5:
        score = 0.9 - (shrink_ratio - 0.3) * 0.5  # 0.9 -> 0.8
    elif shrink_ratio <= 0.7:
        score = 0.8 - (shrink_ratio - 0.5) * 1.0  # 0.8 -> 0.6
    elif shrink_ratio <= 1.0:
        score = 0.6 - (shrink_ratio - 0.7) * 1.0  # 0.6 -> 0.3
    else:
        score = 0.3 - min((shrink_ratio - 1.0) * 0.3, 0.3)  # 0.3 -> 0.0

    return min(1.0, max(0.0, score))


def _calc_trapped_chips_ratio(df: pd.DataFrame, idx: int, lookback: int) -> float:
    """
    套牢盘稀少度
    当前价格上方的成交量占比越小，分数越高
    """
    start_idx = max(0, idx - lookback)
    window = df.loc[start_idx:idx + 1].copy()

    if len(window) < 10:
        return 0.5

    current_price = df.loc[idx, 'close']

    price_min = window['low'].min()
    price_max = window['high'].max()

    if price_max <= price_min:
        return 0.5

    n_bins = 20
    bin_size = (price_max - price_min) / n_bins

    typical_price = (window['high'] + window['low'] + window['close']) / 3
    volumes = window['volume'].values

    volume_profile = np.zeros(n_bins)
    for price, vol in zip(typical_price.values, volumes):
        bin_idx = int((price - price_min) / bin_size)
        bin_idx = min(bin_idx, n_bins - 1)
        volume_profile[bin_idx] += vol

    total_volume = volume_profile.sum()
    if total_volume == 0:
        return 0.5

    current_bin = int((current_price - price_min) / bin_size)
    current_bin = min(current_bin, n_bins - 1)

    trapped_volume = volume_profile[current_bin + 1:].sum()
    trapped_ratio = trapped_volume / total_volume

    # 套牢盘比例 -> 分数（越少越好）
    score = 1.0 - trapped_ratio
    return min(1.0, max(0.0, score))


def _calc_chip_dispersion(df: pd.DataFrame, idx: int, lookback: int) -> float:
    """
    筹码分散度：用信息熵衡量成交量在价格区间上的分布均匀程度
    """
    start_idx = max(0, idx - lookback)
    window = df.loc[start_idx:idx + 1].copy()

    if len(window) < 10:
        return 0.5

    price_min = window['low'].min()
    price_max = window['high'].max()

    if price_max <= price_min:
        return 0.5

    n_bins = 15
    bin_size = (price_max - price_min) / n_bins

    typical_price = (window['high'] + window['low'] + window['close']) / 3
    volumes = window['volume'].values

    volume_profile = np.zeros(n_bins)
    for price, vol in zip(typical_price.values, volumes):
        bin_idx = int((price - price_min) / bin_size)
        bin_idx = min(bin_idx, n_bins - 1)
        volume_profile[bin_idx] += vol

    total_volume = volume_profile.sum()
    if total_volume == 0:
        return 0.5

    prob = volume_profile / total_volume
    prob = prob[prob > 0]

    if len(prob) == 0:
        return 0.5

    entropy = -np.sum(prob * np.log(prob))
    max_entropy = np.log(n_bins)

    if max_entropy == 0:
        return 0.5

    normalized_entropy = entropy / max_entropy
    return min(1.0, max(0.0, float(normalized_entropy)))


def evaluate_capital_quality(factor_result: dict) -> str:
    """评估资金面质量"""
    if factor_result.get('passed'):
        return '优秀'
    composite = factor_result.get('composite', 0)
    if composite >= 0.7:
        return '良好'
    elif composite >= 0.5:
        return '一般'
    else:
        return '较差'


def format_capital_factor(factor_result: dict) -> str:
    """格式化资金面因子输出"""
    quality = evaluate_capital_quality(factor_result)
    passed_str = '✓ 通过' if factor_result.get('passed') else '✗ 未通过'

    lines = [
        f"【资金面因子】{passed_str} | 质量: {quality} (综合: {factor_result['composite']:.2f})",
        f"  ├ 场外资金: {factor_result['outside_capital']:.2f} " + _score_bar(factor_result['outside_capital']),
        f"  ├ 套牢盘稀少: {factor_result['trapped_chips']:.2f} " + _score_bar(factor_result['trapped_chips']),
        f"  └ 筹码分散: {factor_result['chip_dispersion']:.2f} " + _score_bar(factor_result['chip_dispersion']),
    ]
    return '\n'.join(lines)


def _score_bar(score: float, width: int = 10) -> str:
    filled = int(score * width)
    return '█' * filled + '░' * (width - filled)


def calc_capital_factor_short(df: pd.DataFrame, idx: int, lookback: int = 60) -> dict:
    """
    计算做空专用资金面因子（做多因子的镜像版本）
    
    逻辑：
    1. 场外资金充足度：反弹时成交量萎缩越多，说明买盘越弱，做空机会越好
    2. 获利盘稀少度：当前价格下方的成交量占比小（套牢盘多）
    3. 筹码分散度：近期换手分布均匀
    """
    if idx < lookback:
        return {
            'outside_capital': 0.0,
            'profitable_chips': 0.5,
            'chip_dispersion': 0.5,
            'composite': 0.0,
            'passed': False,
        }

    outside_capital = _calc_outside_capital_short(df, idx, lookback)
    profitable_chips = _calc_profitable_chips_ratio(df, idx, lookback)
    chip_dispersion = _calc_chip_dispersion(df, idx, lookback)

    # 综合通过条件：放宽一些，允许两个维度达标即可
    outside_passed = outside_capital >= 0.5
    profitable_passed = profitable_chips >= 0.4
    dispersion_passed = chip_dispersion >= 0.5

    # 至少满足2个条件
    passed_count = sum([outside_passed, profitable_passed, dispersion_passed])
    passed = passed_count >= 2

    # 综合评分
    composite = (
        0.35 * outside_capital +
        0.35 * profitable_chips +
        0.30 * chip_dispersion
    )

    return {
        'outside_capital': round(float(outside_capital), 3),
        'profitable_chips': round(float(profitable_chips), 3),
        'chip_dispersion': round(float(chip_dispersion), 3),
        'composite': round(float(composite), 3),
        'passed': bool(passed),
    }


def _calc_outside_capital_short(df: pd.DataFrame, idx: int, lookback: int) -> float:
    """
    做空时的场外资金充足度（镜像版本）
    
    逻辑：反弹时成交量萎缩，说明买盘力量弱，做空机会好
    """
    start_idx = max(0, idx - lookback)
    window = df.loc[start_idx:idx]

    if len(window) < 20:
        return 0.0

    closes = window['close'].values
    highs = window['high'].values
    lows = window['low'].values
    volumes = window['volume'].values

    # 找到最近的低点
    low_pos = int(np.argmin(lows))

    # 如果低点在最后几根，说明没有反弹
    if low_pos >= len(lows) - 3:
        return 0.0

    # 从低点之后找高点（反弹高点）
    rally_highs = highs[low_pos:]
    high_offset = int(np.argmax(rally_highs))
    rally_end = low_pos + high_offset

    # 下跌段：low_pos之前
    if low_pos < 5:
        return 0.0

    segment_start = max(0, low_pos - 20)

    # 下跌段：segment_start 到 low_pos
    # 反弹段：low_pos 到 rally_end
    down_start = segment_start
    down_end = low_pos
    rally_start = low_pos
    rally_end_actual = rally_end

    if down_end <= down_start or rally_end_actual <= rally_start:
        return 0.0

    down_volumes = volumes[down_start:down_end + 1]
    rally_volumes = volumes[rally_start:rally_end_actual + 1]

    if len(down_volumes) == 0 or len(rally_volumes) == 0:
        return 0.0

    down_avg_vol = np.mean(down_volumes)
    rally_avg_vol = np.mean(rally_volumes)

    if down_avg_vol == 0:
        return 0.0

    # 缩量比
    shrink_ratio = rally_avg_vol / down_avg_vol

    # 缩量比 -> 分数（做空时，反弹缩量越好）
    if shrink_ratio <= 0.3:
        score = 1.0
    elif shrink_ratio <= 0.5:
        score = 0.9 - (shrink_ratio - 0.3) * 0.5
    elif shrink_ratio <= 0.7:
        score = 0.8 - (shrink_ratio - 0.5) * 1.0
    elif shrink_ratio <= 1.0:
        score = 0.6 - (shrink_ratio - 0.7) * 1.0
    else:
        score = 0.3 - min((shrink_ratio - 1.0) * 0.3, 0.3)

    return min(1.0, max(0.0, score))


def _calc_profitable_chips_ratio(df: pd.DataFrame, idx: int, lookback: int) -> float:
    """
    获利盘稀少度（做空时的镜像版本）
    
    当前价格下方的成交量占比越小（套牢盘越多），做空分数越高
    """
    start_idx = max(0, idx - lookback)
    window = df.loc[start_idx:idx + 1].copy()

    if len(window) < 10:
        return 0.5

    current_price = df.loc[idx, 'close']

    price_min = window['low'].min()
    price_max = window['high'].max()

    if price_max <= price_min:
        return 0.5

    n_bins = 20
    bin_size = (price_max - price_min) / n_bins

    typical_price = (window['high'] + window['low'] + window['close']) / 3
    volumes = window['volume'].values

    volume_profile = np.zeros(n_bins)
    for price, vol in zip(typical_price.values, volumes):
        bin_idx = int((price - price_min) / bin_size)
        bin_idx = min(bin_idx, n_bins - 1)
        volume_profile[bin_idx] += vol

    total_volume = volume_profile.sum()
    if total_volume == 0:
        return 0.5

    current_bin = int((current_price - price_min) / bin_size)
    current_bin = min(current_bin, n_bins - 1)

    # 做空时：当前价格下方的成交量（获利盘）
    profitable_volume = volume_profile[:current_bin].sum()
    profitable_ratio = profitable_volume / total_volume

    # 获利盘比例 -> 分数（越少越好，套牢盘多）
    score = 1.0 - profitable_ratio
    return min(1.0, max(0.0, score))
