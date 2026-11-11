"""
大盘指数资金面因子模块（日线级别）
适配 A 股大盘指数的日线数据，与期货15分钟版本的区别：
1. 日线波动更大，回调判断需要更宽松的阈值
2. 指数没有夜盘，K线数量少，回溯窗口需要更长
3. 指数不会像个股那样被单一主力控盘，筹码分散度天然较高

场外资金口径说明：
    「场外资金」衡量的是整个 A 股市场的资金充裕度，因此统一使用
    全 A（中证全指）成交额计算，与所选观测指数无关——单个指数/个股
    的量能变化不能代表、也不会改变全市场场外资金的总量。
"""
import pandas as pd
import numpy as np


def calc_index_capital_factor(df: pd.DataFrame, idx: int, lookback: int = 120) -> dict:
    """
    计算大盘指数的资金面综合因子（日线适配版）
    """
    if idx < lookback:
        return {
            'outside_capital': 0.0,
            'trapped_chips': 0.5,
            'chip_dispersion': 0.5,
            'composite': 0.0,
            'passed': False,
        }

    outside_capital = _calc_index_outside_capital(df, idx, lookback)
    trapped_chips = _calc_index_trapped_chips(df, idx, lookback)
    chip_dispersion = _calc_index_chip_dispersion(df, idx, lookback)

    # 指数版通过条件（更宽松）
    outside_passed = outside_capital >= 0.4
    trapped_passed = trapped_chips >= 0.4
    dispersion_passed = chip_dispersion >= 0.4

    passed = outside_passed and trapped_passed and dispersion_passed

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


def _calc_index_outside_capital(df: pd.DataFrame, idx: int, lookback: int) -> float:
    """
    场外资金充足度（日线适配版）

    资金口径：优先使用 df['mkt_amount']（全 A 成交额），
    因为场外资金是全市场概念，与具体观测哪个指数无关；
    没有该列时回退为指数自身成交额。

    日线级别的调整：
    - 回溯窗口更长（用 lookback 参数）
    - 回调判断更宽松：允许更深的回调
    - 上涨段起点回溯更远
    """
    start_idx = max(0, idx - lookback)
    window = df.loc[start_idx:idx]

    if len(window) < 30:
        return 0.0

    closes = window['close'].values
    highs = window['high'].values
    lows = window['low'].values

    # 量能序列：优先全 A 成交额，其次指数成交额（volume*close），最后原始 volume
    if 'mkt_amount' in window.columns and window['mkt_amount'].notna().all():
        flows = window['mkt_amount'].values.astype(float)
    elif 'amount' in window.columns and window['amount'].notna().all():
        flows = window['amount'].values.astype(float)
    else:
        flows = (window['volume'] * window['close']).values.astype(float)

    # 找到最近的高点（取首次出现位置，避免重复最高价导致上涨段为空）
    high_pos = int(np.argmax(highs))

    # 如果高点在最后几根，说明没有回调（日线版放宽到5根）
    if high_pos >= len(highs) - 5:
        return 0.0

    # 从高点之后找最低点（回调低点）
    pullback_lows = lows[high_pos:]
    low_offset = int(np.argmin(pullback_lows))
    pullback_end = high_pos + low_offset

    # 日线版：回溯更远找上涨段起点
    if high_pos < 10:
        return 0.0

    segment_start = max(0, high_pos - 40)  # 日线版回溯40根

    up_start = segment_start
    up_end = high_pos
    pull_start = high_pos
    pull_end = pullback_end

    if up_end <= up_start:
        # 上涨段为空（窗口内几乎单边上涨），用全部可用K线作为上涨段
        up_start = 0
    if up_end <= up_start or pull_end <= pull_start:
        return 0.0

    up_volumes = flows[up_start:up_end + 1]
    pull_volumes = flows[pull_start:pull_end + 1]

    if len(up_volumes) == 0 or len(pull_volumes) == 0:
        return 0.0

    up_avg_vol = np.mean(up_volumes)
    pull_avg_vol = np.mean(pull_volumes)

    if up_avg_vol == 0:
        return 0.0

    # 缩量比
    shrink_ratio = pull_avg_vol / up_avg_vol

    # 日线版评分映射（更宽松）
    if shrink_ratio <= 0.4:
        score = 1.0
    elif shrink_ratio <= 0.6:
        score = 0.9 - (shrink_ratio - 0.4) * 0.5
    elif shrink_ratio <= 0.8:
        score = 0.8 - (shrink_ratio - 0.6) * 0.75
    elif shrink_ratio <= 1.0:
        score = 0.65 - (shrink_ratio - 0.8) * 0.75
    elif shrink_ratio <= 1.3:
        score = 0.5 - (shrink_ratio - 1.0) * 0.67
    else:
        score = max(0.0, 0.3 - (shrink_ratio - 1.3) * 0.2)

    return min(1.0, max(0.0, score))


def _calc_index_trapped_chips(df: pd.DataFrame, idx: int, lookback: int) -> float:
    """
    套牢盘稀少度（日线适配版）
    指数版用更多档位，因为日线价格波动范围更大
    """
    start_idx = max(0, idx - lookback)
    window = df.loc[start_idx:idx + 1].copy()

    if len(window) < 20:
        return 0.5

    current_price = df.loc[idx, 'close']
    price_min = window['low'].min()
    price_max = window['high'].max()

    if price_max <= price_min:
        return 0.5

    n_bins = 30  # 日线版用更多档位
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

    score = 1.0 - trapped_ratio
    return min(1.0, max(0.0, score))


def _calc_index_chip_dispersion(df: pd.DataFrame, idx: int, lookback: int) -> float:
    """
    筹码分散度（日线适配版）
    """
    start_idx = max(0, idx - lookback)
    window = df.loc[start_idx:idx + 1].copy()

    if len(window) < 20:
        return 0.5

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
    return (
        f"【资金面因子】{passed_str} | 质量: {quality} (综合: {factor_result['composite']:.2f})\n"
        f"  场外资金: {factor_result['outside_capital']:.2f} | "
        f"套牢盘稀少: {factor_result['trapped_chips']:.2f} | "
        f"筹码分散: {factor_result['chip_dispersion']:.2f}"
    )
