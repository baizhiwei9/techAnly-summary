"""
因子有效性回测 - 合成指数 + 均值回归验证
核心逻辑：
1. 用资金面因子（场外资金、套牢盘、筹码分散度）合成一个综合指数
2. 当合成指数超出2倍标准差时，观察后续是否出现回调
3. 计算IC值（因子值与未来收益的秩相关系数）验证因子预测能力
"""
import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chanlun_backtest.data import fetch_silicon_data
from chanlun_backtest.indicators import calculate_all_indicators
from chanlun_backtest.factors import calc_capital_factor


def build_factor_index(df: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
    """
    逐根K线计算资金面因子，构建合成指数
    """
    n = len(df)
    outside_list = []
    trapped_list = []
    dispersion_list = []
    composite_list = []

    print(f"正在计算 {n} 根K线的资金面因子...")
    for i in range(n):
        if i % 500 == 0 and i > 0:
            print(f"  进度: {i}/{n} ({100*i/n:.1f}%)")
        factor = calc_capital_factor(df, i, lookback)
        outside_list.append(factor['outside_capital'])
        trapped_list.append(factor['trapped_chips'])
        dispersion_list.append(factor['chip_dispersion'])
        composite_list.append(factor['composite'])

    df = df.copy()
    df['outside_capital'] = outside_list
    df['trapped_chips'] = trapped_list
    df['chip_dispersion'] = dispersion_list
    df['composite'] = composite_list

    # 合成指数 = 加权综合
    df['factor_index'] = (
        0.35 * df['outside_capital'] +
        0.35 * df['trapped_chips'] +
        0.30 * df['chip_dispersion']
    )

    return df


def compute_std_bands(df: pd.DataFrame, window: int = 120) -> pd.DataFrame:
    """
    计算合成指数的滚动均值和标准差
    """
    df = df.copy()
    df['fi_mean'] = df['factor_index'].rolling(window=window).mean()
    df['fi_std'] = df['factor_index'].rolling(window=window).std()
    df['fi_upper2'] = df['fi_mean'] + 2 * df['fi_std']
    df['fi_upper1'] = df['fi_mean'] + 1 * df['fi_std']
    df['fi_lower1'] = df['fi_mean'] - 1 * df['fi_std']
    df['fi_lower2'] = df['fi_mean'] - 2 * df['fi_std']
    df['fi_zscore'] = (df['factor_index'] - df['fi_mean']) / df['fi_std']
    return df


def compute_forward_returns(df: pd.DataFrame, periods: list = [5, 10, 20, 40]) -> pd.DataFrame:
    """
    计算未来N根K线的收益率（用于IC计算）
    """
    df = df.copy()
    for p in periods:
        df[f'fwd_ret_{p}'] = df['close'].shift(-p) / df['close'] - 1
    return df


def run_mean_reversion_test(df: pd.DataFrame) -> dict:
    """
    均值回归测试：当合成指数超出2倍标准差时，后续价格是否回调
    """
    valid = df.dropna(subset=['fi_zscore', 'fwd_ret_5', 'fwd_ret_10', 'fwd_ret_20']).copy()

    if len(valid) == 0:
        return {"error": "无有效数据"}

    # 超出+2σ的信号
    upper_signals = valid[valid['fi_zscore'] > 2.0]
    # 低于-2σ的信号
    lower_signals = valid[valid['fi_zscore'] < -2.0]

    results = {}

    # === 超出上轨(+2σ)后的回调统计 ===
    if len(upper_signals) > 0:
        results['upper_2sigma'] = {
            'signal_count': len(upper_signals),
            'fwd_ret_5_mean': upper_signals['fwd_ret_5'].mean(),
            'fwd_ret_5_win_rate': (upper_signals['fwd_ret_5'] < 0).mean(),
            'fwd_ret_10_mean': upper_signals['fwd_ret_10'].mean(),
            'fwd_ret_10_win_rate': (upper_signals['fwd_ret_10'] < 0).mean(),
            'fwd_ret_20_mean': upper_signals['fwd_ret_20'].mean(),
            'fwd_ret_20_win_rate': (upper_signals['fwd_ret_20'] < 0).mean(),
        }
    else:
        results['upper_2sigma'] = {'signal_count': 0}

    # === 低于下轨(-2σ)后的反弹统计 ===
    if len(lower_signals) > 0:
        results['lower_2sigma'] = {
            'signal_count': len(lower_signals),
            'fwd_ret_5_mean': lower_signals['fwd_ret_5'].mean(),
            'fwd_ret_5_win_rate': (lower_signals['fwd_ret_5'] > 0).mean(),
            'fwd_ret_10_mean': lower_signals['fwd_ret_10'].mean(),
            'fwd_ret_10_win_rate': (lower_signals['fwd_ret_10'] > 0).mean(),
            'fwd_ret_20_mean': lower_signals['fwd_ret_20'].mean(),
            'fwd_ret_20_win_rate': (lower_signals['fwd_ret_20'] > 0).mean(),
        }
    else:
        results['lower_2sigma'] = {'signal_count': 0}

    # === 不同阈值区间的统计 ===
    for threshold in [1.0, 1.5, 2.0, 2.5]:
        above = valid[valid['fi_zscore'] > threshold]
        if len(above) > 0:
            results[f'above_{threshold}sigma'] = {
                'signal_count': len(above),
                'fwd_ret_5_mean': above['fwd_ret_5'].mean(),
                'fwd_ret_5_down_rate': (above['fwd_ret_5'] < 0).mean(),
                'fwd_ret_10_mean': above['fwd_ret_10'].mean(),
                'fwd_ret_10_down_rate': (above['fwd_ret_10'] < 0).mean(),
                'fwd_ret_20_mean': above['fwd_ret_20'].mean(),
                'fwd_ret_20_down_rate': (above['fwd_ret_20'] < 0).mean(),
            }

    return results


def compute_ic(df: pd.DataFrame, periods: list = [5, 10, 20, 40]) -> dict:
    """
    计算IC值 (Information Coefficient)
    IC = 因子值与未来收益的秩相关系数 (Spearman Rank Correlation)
    逐截面计算，然后取均值和标准差，得到IC均值、IC_IR等
    """
    valid = df.dropna(subset=['composite']).copy()
    # 去掉因子值全为0的前lookback段
    valid = valid[valid['composite'] > 0].copy()

    ic_results = {}

    for p in periods:
        col = f'fwd_ret_{p}'
        if col not in valid.columns:
            continue

        sub = valid.dropna(subset=['composite', col])
        if len(sub) < 30:
            ic_results[f'IC_{p}bar'] = {'ic_mean': 0, 'ic_std': 0, 'icir': 0, 'ic_positive_rate': 0, 'sample_count': 0}
            continue

        # 整体IC（全样本Spearman相关）
        ic_full, p_value = stats.spearmanr(sub['composite'], sub[col])

        # 滚动IC（按窗口分段计算，模拟时序IC序列）
        window = 60
        ic_series = []
        for start in range(0, len(sub) - window, window // 2):
            end = min(start + window, len(sub))
            chunk = sub.iloc[start:end]
            if len(chunk) < 20:
                continue
            ic_val, _ = stats.spearmanr(chunk['composite'], chunk[col])
            if not np.isnan(ic_val):
                ic_series.append(ic_val)

        if ic_series:
            ic_mean = np.mean(ic_series)
            ic_std = np.std(ic_series)
            icir = ic_mean / ic_std if ic_std > 0 else 0
            ic_positive_rate = np.mean(np.array(ic_series) > 0)
        else:
            ic_mean = ic_full if not np.isnan(ic_full) else 0
            ic_std = 0
            icir = 0
            ic_positive_rate = 0

        ic_results[f'IC_{p}bar'] = {
            'ic_mean': round(ic_mean, 4),
            'ic_std': round(ic_std, 4),
            'icir': round(icir, 4),
            'ic_positive_rate': round(ic_positive_rate, 4),
            'ic_full': round(ic_full, 4) if not np.isnan(ic_full) else 0,
            'p_value': round(p_value, 6),
            'sample_count': len(sub),
            'ic_series_count': len(ic_series),
        }

    # 分维度IC
    for dim_name, dim_col in [('场外资金', 'outside_capital'), ('套牢盘', 'trapped_chips'), ('筹码分散', 'chip_dispersion')]:
        for p in [10, 20]:
            col = f'fwd_ret_{p}'
            if col not in valid.columns:
                continue
            sub = valid.dropna(subset=[dim_col, col])
            if len(sub) < 30:
                continue
            ic_val, p_val = stats.spearmanr(sub[dim_col], sub[col])
            ic_results[f'{dim_name}_IC_{p}bar'] = {
                'ic': round(ic_val, 4) if not np.isnan(ic_val) else 0,
                'p_value': round(p_val, 6),
            }

    return ic_results


def compute_factor_group_returns(df: pd.DataFrame, n_groups: int = 5) -> dict:
    """
    因子分组回测：按因子值分5组，看各组未来收益的单调性
    """
    valid = df.dropna(subset=['composite', 'fwd_ret_10', 'fwd_ret_20']).copy()
    valid = valid[valid['composite'] > 0].copy()

    if len(valid) < 100:
        return {"error": "样本不足"}

    valid['group'] = pd.qcut(valid['composite'], q=n_groups, labels=False, duplicates='drop')

    group_stats = {}
    for g in range(n_groups):
        gdata = valid[valid['group'] == g]
        if len(gdata) == 0:
            continue
        group_stats[f'group_{g+1}'] = {
            'count': len(gdata),
            'factor_mean': round(gdata['composite'].mean(), 4),
            'fwd_ret_10_mean': round(gdata['fwd_ret_10'].mean() * 100, 4),
            'fwd_ret_20_mean': round(gdata['fwd_ret_20'].mean() * 100, 4),
            'fwd_ret_10_win_rate': round((gdata['fwd_ret_10'] > 0).mean(), 4),
            'fwd_ret_20_win_rate': round((gdata['fwd_ret_20'] > 0).mean(), 4),
        }

    return group_stats


def format_report(mean_reversion: dict, ic_results: dict, group_returns: dict, df: pd.DataFrame) -> str:
    """格式化输出回测报告"""
    lines = []
    lines.append("=" * 70)
    lines.append("因子有效性回测报告 - 资金面合成指数 + 均值回归验证")
    lines.append("=" * 70)
    lines.append(f"回测区间: {df['timestamp'].iloc[0]} 至 {df['timestamp'].iloc[-1]}")
    lines.append(f"总K线数: {len(df)}")
    valid_count = df['fi_zscore'].notna().sum()
    lines.append(f"有效样本数: {valid_count}")
    lines.append("")

    # === 合成指数基本统计 ===
    lines.append("【合成指数统计】")
    fi = df['factor_index'].dropna()
    lines.append(f"  均值: {fi.mean():.4f}")
    lines.append(f"  标准差: {fi.std():.4f}")
    lines.append(f"  最大值: {fi.max():.4f}")
    lines.append(f"  最小值: {fi.min():.4f}")
    lines.append(f"  偏度: {fi.skew():.4f}")
    lines.append(f"  峰度: {fi.kurtosis():.4f}")
    lines.append("")

    # === 各因子基本统计 ===
    lines.append("【各因子统计】")
    for col, name in [('outside_capital', '场外资金充足度'), ('trapped_chips', '套牢盘稀少度'), ('chip_dispersion', '筹码分散度')]:
        s = df[col].dropna()
        if len(s) > 0:
            lines.append(f"  {name}: 均值={s.mean():.4f}, 标准差={s.std():.4f}, 有效占比(>0.5)={( s > 0.5).mean():.2%}")
    lines.append("")

    # === 均值回归测试 ===
    lines.append("【均值回归测试 - 超出标准差后的价格行为】")
    lines.append("-" * 60)

    mr = mean_reversion.get('upper_2sigma', {})
    if mr.get('signal_count', 0) > 0:
        lines.append(f"  超出+2σ 信号次数: {mr['signal_count']}")
        lines.append(f"    后续5根K线: 平均收益={mr['fwd_ret_5_mean']:.4%}, 下跌概率={mr['fwd_ret_5_win_rate']:.2%}")
        lines.append(f"    后续10根K线: 平均收益={mr['fwd_ret_10_mean']:.4%}, 下跌概率={mr['fwd_ret_10_win_rate']:.2%}")
        lines.append(f"    后续20根K线: 平均收益={mr['fwd_ret_20_mean']:.4%}, 下跌概率={mr['fwd_ret_20_win_rate']:.2%}")
    else:
        lines.append("  超出+2σ: 无信号")

    lines.append("")
    mr_lower = mean_reversion.get('lower_2sigma', {})
    if mr_lower.get('signal_count', 0) > 0:
        lines.append(f"  低于-2σ 信号次数: {mr_lower['signal_count']}")
        lines.append(f"    后续5根K线: 平均收益={mr_lower['fwd_ret_5_mean']:.4%}, 上涨概率={mr_lower['fwd_ret_5_win_rate']:.2%}")
        lines.append(f"    后续10根K线: 平均收益={mr_lower['fwd_ret_10_mean']:.4%}, 上涨概率={mr_lower['fwd_ret_10_win_rate']:.2%}")
        lines.append(f"    后续20根K线: 平均收益={mr_lower['fwd_ret_20_mean']:.4%}, 上涨概率={mr_lower['fwd_ret_20_win_rate']:.2%}")
    else:
        lines.append("  低于-2σ: 无信号")

    lines.append("")
    lines.append("  不同阈值区间统计:")
    for threshold in [1.0, 1.5, 2.0, 2.5]:
        key = f'above_{threshold}sigma'
        if key in mean_reversion:
            d = mean_reversion[key]
            lines.append(f"    >{threshold}σ: {d['signal_count']}次 | "
                        f"5根下跌率={d['fwd_ret_5_down_rate']:.2%} | "
                        f"10根下跌率={d['fwd_ret_10_down_rate']:.2%} | "
                        f"20根下跌率={d['fwd_ret_20_down_rate']:.2%}")
    lines.append("")

    # === IC值 ===
    lines.append("【IC值 (Information Coefficient)】")
    lines.append("-" * 60)
    for key, val in ic_results.items():
        if key.startswith('IC_'):
            lines.append(f"  {key}:")
            lines.append(f"    IC均值={val['ic_mean']}, IC标准差={val['ic_std']}, ICIR={val['icir']}")
            lines.append(f"    IC>0占比={val['ic_positive_rate']:.2%}, 全样本IC={val['ic_full']}, p值={val['p_value']}")
            lines.append(f"    样本数={val['sample_count']}, 分段数={val['ic_series_count']}")
        else:
            lines.append(f"  {key}: IC={val['ic']}, p值={val['p_value']}")
    lines.append("")

    # === 分组回测 ===
    lines.append("【因子分组回测 (5组)】")
    lines.append("-" * 60)
    if 'error' not in group_returns:
        lines.append(f"  {'组别':>8} | {'样本数':>6} | {'因子均值':>8} | {'10根收益%':>10} | {'20根收益%':>10} | {'10根胜率':>8} | {'20根胜率':>8}")
        for key in sorted(group_returns.keys()):
            g = group_returns[key]
            lines.append(f"  {key:>8} | {g['count']:>6} | {g['factor_mean']:>8.4f} | "
                        f"{g['fwd_ret_10_mean']:>10.4f} | {g['fwd_ret_20_mean']:>10.4f} | "
                        f"{g['fwd_ret_10_win_rate']:>8.2%} | {g['fwd_ret_20_win_rate']:>8.2%}")

        # 单调性检验
        rets_10 = [group_returns[k]['fwd_ret_10_mean'] for k in sorted(group_returns.keys())]
        rets_20 = [group_returns[k]['fwd_ret_20_mean'] for k in sorted(group_returns.keys())]
        mono_10 = all(rets_10[i] <= rets_10[i+1] for i in range(len(rets_10)-1)) or \
                  all(rets_10[i] >= rets_10[i+1] for i in range(len(rets_10)-1))
        mono_20 = all(rets_20[i] <= rets_20[i+1] for i in range(len(rets_20)-1)) or \
                  all(rets_20[i] >= rets_20[i+1] for i in range(len(rets_20)-1))
        lines.append(f"\n  10根收益单调性: {'✓ 通过' if mono_10 else '✗ 不通过'}")
        lines.append(f"  20根收益单调性: {'✓ 通过' if mono_20 else '✗ 不通过'}")
    else:
        lines.append(f"  {group_returns['error']}")

    lines.append("")
    lines.append("=" * 70)
    lines.append("【结论】")
    lines.append("=" * 70)

    # 自动判断结论
    ic_10 = ic_results.get('IC_10bar', {})
    ic_20 = ic_results.get('IC_20bar', {})
    ic10_val = ic_10.get('ic_mean', 0)
    ic20_val = ic_20.get('ic_mean', 0)
    icir10 = ic_10.get('icir', 0)
    icir20 = ic_20.get('icir', 0)

    upper = mean_reversion.get('upper_2sigma', {})
    upper_count = upper.get('signal_count', 0)
    upper_down_10 = upper.get('fwd_ret_10_win_rate', 0)

    lines.append(f"  1. 因子IC值: 10根IC={ic10_val}, 20根IC={ic20_val}")
    if abs(ic10_val) > 0.05:
        lines.append(f"     → IC绝对值>0.05，因子有一定预测能力")
    else:
        lines.append(f"     → IC绝对值<0.05，因子预测能力较弱")

    if abs(icir10) > 0.5:
        lines.append(f"     → ICIR={icir10}>0.5，因子稳定性较好")
    else:
        lines.append(f"     → ICIR={icir10}<0.5，因子稳定性一般")

    lines.append(f"  2. 均值回归: 超出+2σ后{upper_count}次，10根内下跌概率={upper_down_10:.2%}")
    if upper_down_10 > 0.55:
        lines.append(f"     → 超出+2σ后回调概率>55%，均值回归效应存在")
    elif upper_down_10 > 0.50:
        lines.append(f"     → 超出+2σ后回调概率略>50%，均值回归效应较弱")
    else:
        lines.append(f"     → 超出+2σ后回调概率<50%，均值回归效应不显著")

    lines.append("")
    return '\n'.join(lines)


def main():
    print("=" * 70)
    print("因子有效性回测 - 合成指数均值回归验证")
    print("=" * 70)
    print()

    # 1. 获取数据
    print("正在获取工业硅期货数据...")
    df = fetch_silicon_data('2025-01-01', '2026-06-13')
    if df.empty:
        print("错误：无法获取数据")
        return

    print(f"获取到 {len(df)} 根K线")
    print(f"数据范围: {df.iloc[0]['timestamp']} 至 {df.iloc[-1]['timestamp']}")
    print()

    # 2. 计算技术指标
    print("计算技术指标...")
    df = calculate_all_indicators(df)

    # 3. 构建合成指数
    df = build_factor_index(df, lookback=60)
    print("合成指数构建完成")
    print()

    # 4. 计算标准差通道
    df = compute_std_bands(df, window=120)

    # 5. 计算未来收益
    df = compute_forward_returns(df, periods=[5, 10, 20, 40])

    # 6. 均值回归测试
    print("运行均值回归测试...")
    mean_reversion = run_mean_reversion_test(df)

    # 7. IC值计算
    print("计算IC值...")
    ic_results = compute_ic(df, periods=[5, 10, 20, 40])

    # 8. 分组回测
    print("运行因子分组回测...")
    group_returns = compute_factor_group_returns(df, n_groups=5)

    # 9. 生成报告
    report = format_report(mean_reversion, ic_results, group_returns, df)
    print()
    print(report)

    # 保存报告
    output_dir = './backtest_report_factor_validity'
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, 'factor_validity_report.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"报告已保存: {report_path}")

    # 保存带因子数据的CSV
    csv_path = os.path.join(output_dir, 'factor_data.csv')
    df.to_csv(csv_path, index=False)
    print(f"因子数据已保存: {csv_path}")


if __name__ == '__main__':
    main()
