"""
大盘指数资金面因子分析 - 用于判断大盘环境（择时）
独立于缠论选股系统，专门分析上证/沪深300等指数的资金结构
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
from chanlun_backtest.data import fetch_astock_data
from chanlun_backtest.indicators import calculate_all_indicators
from chanlun_backtest.index_factors import calc_index_capital_factor as calc_capital_factor, format_capital_factor, evaluate_capital_quality


# 常用大盘指数代码
INDEX_MAP = {
    '上证指数': '000001',  # 注意：akshare 中上证指数代码是 000001，但和平安银行冲突
    '沪深300': '000300',
    '上证50': '000016',
    '中证500': '000905',
    '中证1000': '000852',
    '科创50': '000688',
    '创业板指': '399006',
}


# 全市场基准指数：中证全指（000985），覆盖全部A股，用于衡量全A资金量
MARKET_INDEX_SYMBOL = 'sh000985'


def fetch_market_amount_data(start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取全A市场（中证全指）每日成交额，作为场外资金计算的量级基准。
    用全市场成交额而不是单个指数自身的成交量，避免「某个指数/股票影响所有场外资金」的偏差。
    """
    import akshare as ak

    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')

    try:
        df = ak.stock_zh_index_daily_tx(symbol=MARKET_INDEX_SYMBOL)
    except Exception as e:
        print(f"获取全市场(中证全指)成交额失败: {e}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    df = df.rename(columns={'date': 'timestamp', 'amount': 'mkt_amount'})
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df[(df['timestamp'] >= start_dt) & (df['timestamp'] <= end_dt)]
    return df[['timestamp', 'mkt_amount']].reset_index(drop=True)


def fetch_index_data(index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取大盘指数数据（通过 akshare 的 stock_zh_index_daily）。
    返回的 DataFrame 附带 mkt_amount 列（全A每日成交额），供场外资金因子使用。
    """
    import akshare as ak
    
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    
    # 判断交易所前缀
    if index_code.startswith('399'):
        symbol = f"sz{index_code}"  # 深证指数
    else:
        symbol = f"sh{index_code}"  # 上证指数
    
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
    except Exception as e:
        print(f"获取指数 {index_code} 数据失败: {e}")
        return pd.DataFrame()
    
    if df.empty:
        return pd.DataFrame()
    
    # 重命名列
    df = df.rename(columns={'date': 'timestamp'})
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 按日期范围筛选
    df = df[(df['timestamp'] >= start_dt) & (df['timestamp'] <= end_dt)]
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)

    # 合并全A成交额（缺失时回退为指数自身成交额，保证可用）
    mkt = fetch_market_amount_data(start_date, end_date)
    if not mkt.empty:
        df = df.merge(mkt, on='timestamp', how='left')
        df['mkt_amount'] = df['mkt_amount'].fillna(df['volume'] * df['close'])
    else:
        df['mkt_amount'] = df['volume'] * df['close']
    return df


def analyze_index_capital(index_code: str, index_name: str, start_date: str, end_date: str, lookback: int = 120):
    """
    分析大盘指数的资金面环境
    
    :param index_code: 指数代码
    :param index_name: 指数名称
    :param start_date: 开始日期
    :param end_date: 结束日期
    :param lookback: 回溯窗口
    """
    print("=" * 70)
    print(f"大盘指数资金面分析 - {index_name} ({index_code})")
    print("=" * 70)
    print(f"分析区间: {start_date} 至 {end_date}")
    print()
    
    # 获取数据
    print(f"正在获取 {index_name} 历史数据...")
    df = fetch_index_data(index_code, start_date, end_date)
    
    if df.empty:
        print("错误：无法获取指数数据")
        return None
    
    print(f"获取到 {len(df)} 根K线")
    print(f"数据范围: {df.iloc[0]['timestamp'].strftime('%Y-%m-%d')} 至 {df.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}")
    print()
    
    # 计算技术指标
    print("计算技术指标...")
    df = calculate_all_indicators(df)
    print()
    
    # 逐日计算资金面因子
    print(f"计算 {len(df)} 个交易日的资金面因子...")
    n = len(df)
    results = []
    
    for i in range(n):
        factor = calc_capital_factor(df, i, lookback)
        results.append({
            'date': df.loc[i, 'timestamp'],
            'close': df.loc[i, 'close'],
            'outside_capital': factor['outside_capital'],
            'trapped_chips': factor['trapped_chips'],
            'chip_dispersion': factor['chip_dispersion'],
            'composite': factor['composite'],
            'passed': factor['passed'],
            'quality': evaluate_capital_quality(factor)
        })
    
    result_df = pd.DataFrame(results)
    print("计算完成")
    print()
    
    # 输出最新状态
    latest = result_df.iloc[-1]
    print("=" * 70)
    print(f"【{index_name} 最新资金面状态】{latest['date'].strftime('%Y-%m-%d')}")
    print("=" * 70)
    print(f"  收盘价: {latest['close']:.2f}")
    print(f"  场外资金充足度: {latest['outside_capital']:.3f}")
    print(f"  套牢盘稀少度: {latest['trapped_chips']:.3f}")
    print(f"  筹码分散度: {latest['chip_dispersion']:.3f}")
    print(f"  综合评分: {latest['composite']:.3f}")
    print(f"  资金面质量: {latest['quality']}")
    print(f"  是否适合做多: {'✓ 是' if latest['passed'] else '✗ 否'}")
    print()
    
    # 统计信息
    total_days = len(result_df)
    passed_days = result_df['passed'].sum()
    pass_rate = passed_days / total_days if total_days > 0 else 0
    
    print("【统计信息】")
    print(f"  总交易日: {total_days} 天")
    print(f"  资金面良好天数: {passed_days} 天 ({pass_rate:.1%})")
    print(f"  资金面较差天数: {total_days - passed_days} 天 ({1-pass_rate:.1%})")
    print()
    
    # 质量分布
    quality_counts = result_df['quality'].value_counts()
    print("【资金面质量分布】")
    for q in ['优秀', '良好', '一般', '较差']:
        count = quality_counts.get(q, 0)
        pct = count / total_days if total_days > 0 else 0
        print(f"  {q}: {count} 天 ({pct:.1%})")
    print()
    
    # 最近10天趋势
    print("【最近10个交易日资金面变化】")
    print("-" * 80)
    print(f"{'日期':>12} | {'收盘价':>8} | {'场外资金':>8} | {'套牢盘':>8} | {'分散度':>8} | {'综合':>8} | {'质量':>6}")
    print("-" * 80)
    for _, row in result_df.tail(10).iterrows():
        print(f"{row['date'].strftime('%Y-%m-%d'):>12} | {row['close']:>8.2f} | "
              f"{row['outside_capital']:>8.3f} | {row['trapped_chips']:>8.3f} | "
              f"{row['chip_dispersion']:>8.3f} | {row['composite']:>8.3f} | {row['quality']:>6}")
    print("-" * 80)
    print()
    
    # 生成图表
    plot_capital_trend(result_df, index_name)
    
    # 保存数据
    output_dir = f'./index_capital_{index_code}'
    os.makedirs(output_dir, exist_ok=True)
    result_df.to_csv(f'{output_dir}/capital_data.csv', index=False)
    print(f"数据已保存: {output_dir}/capital_data.csv")
    
    return result_df


def plot_capital_trend(result_df: pd.DataFrame, index_name: str):
    """绘制资金面趋势图"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    
    plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    
    dates = result_df['date']
    
    # 1. 价格走势
    axes[0].plot(dates, result_df['close'], color='#2E86AB', linewidth=1.5)
    axes[0].set_title(f'{index_name} - 价格走势', fontsize=14, fontweight='bold')
    axes[0].set_ylabel('收盘价')
    axes[0].grid(True, alpha=0.3)
    
    # 2. 场外资金充足度
    axes[1].plot(dates, result_df['outside_capital'], color='#e74c3c', linewidth=1)
    axes[1].axhline(y=0.6, color='gray', linestyle='--', alpha=0.5, label='阈值 0.6')
    axes[1].set_title('场外资金充足度', fontsize=12)
    axes[1].set_ylabel('分数')
    axes[1].set_ylim(0, 1)
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)
    
    # 3. 套牢盘稀少度
    axes[2].plot(dates, result_df['trapped_chips'], color='#27ae60', linewidth=1)
    axes[2].axhline(y=0.5, color='gray', linestyle='--', alpha=0.5, label='阈值 0.5')
    axes[2].set_title('套牢盘稀少度', fontsize=12)
    axes[2].set_ylabel('分数')
    axes[2].set_ylim(0, 1)
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)
    
    # 4. 综合评分
    colors = ['#27ae60' if p else '#e74c3c' for p in result_df['passed']]
    axes[3].bar(dates, result_df['composite'], color=colors, alpha=0.7, width=1)
    axes[3].axhline(y=0.5, color='gray', linestyle='--', alpha=0.5, label='阈值 0.5')
    axes[3].set_title('综合评分（绿色=通过，红色=未通过）', fontsize=12)
    axes[3].set_ylabel('分数')
    axes[3].set_ylim(0, 1)
    axes[3].legend()
    axes[3].grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    output_dir = f'./index_capital_{result_df.iloc[0]["date"].strftime("%Y%m%d")}'
    os.makedirs(output_dir, exist_ok=True)
    chart_file = f'{output_dir}/capital_trend.png'
    plt.savefig(chart_file, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"趋势图已保存: {chart_file}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='大盘指数资金面分析')
    parser.add_argument('--index', type=str, default='000300', help='指数代码，如 000300(沪深300)')
    parser.add_argument('--name', type=str, default='沪深300', help='指数名称')
    parser.add_argument('--start', type=str, default='2024-01-01', help='开始日期')
    parser.add_argument('--end', type=str, default='2025-12-31', help='结束日期')
    parser.add_argument('--lookback', type=int, default=120, help='回溯窗口(日线版建议120)')
    args = parser.parse_args()
    
    analyze_index_capital(args.index, args.name, args.start, args.end, args.lookback)
