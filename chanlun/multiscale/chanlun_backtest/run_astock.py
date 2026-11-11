"""
A 股股票回测脚本 - 支持指定股票代码和日期范围
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chanlun_backtest.data import fetch_astock_data
from chanlun_backtest.indicators import calculate_all_indicators
from chanlun_backtest.signals import scan_signals_astock
from chanlun_backtest.engine import BacktestEngine
from chanlun_backtest.report import generate_report


def run_backtest(symbol: str, start_date: str, end_date: str, period: str = 'daily', output_dir: str = None):
    """
    运行 A 股回测
    
    :param symbol: 股票代码，如 '000001'（平安银行）、'600519'（贵州茅台）
    :param start_date: 开始日期，格式 'YYYY-MM-DD'
    :param end_date: 结束日期，格式 'YYYY-MM-DD'
    :param period: K线周期，默认 'daily'
    :param output_dir: 报告输出目录
    """
    if output_dir is None:
        output_dir = f'./backtest_report_{symbol}_{period}'
    
    print("=" * 70)
    print(f"缠论买卖点量化交易系统 - A 股回测")
    print("=" * 70)
    print(f"股票代码: {symbol}")
    print(f"回测区间: {start_date} 至 {end_date}")
    print(f"K线周期: {period}")
    print(f"初始资金: 200,000.00 元")
    print(f"手续费率: 0.0003")
    print()
    
    # 获取数据
    print(f"正在获取 {symbol} 历史数据...")
    df = fetch_astock_data(symbol, start_date, end_date, period)
    
    if df.empty:
        print("错误：无法获取数据")
        return None
    
    print(f"获取到 {len(df)} 根K线")
    print(f"数据范围: {df.iloc[0]['timestamp']} 至 {df.iloc[-1]['timestamp']}")
    print()
    
    # 计算指标
    print("正在计算技术指标...")
    df = calculate_all_indicators(df)
    print("指标计算完成")
    print()
    
    # 扫描信号（A 股只做多）
    print("正在扫描买点信号...")
    signals = scan_signals_astock(df)
    print(f"发现 {len(signals)} 个买点信号")
    
    if signals:
        print("\n信号列表:")
        for idx, signal_type, stop_loss, structure_level in signals[:20]:
            time_str = df.loc[idx, 'timestamp'].strftime('%Y-%m-%d %H:%M') if period != 'daily' else df.loc[idx, 'timestamp'].strftime('%Y-%m-%d')
            print(f"  [{time_str}] {signal_type} - 止损: {stop_loss:.2f}, 结构位: {structure_level:.2f}")
        if len(signals) > 20:
            print(f"  ... 还有 {len(signals) - 20} 个信号")
    print()
    
    # 运行回测
    print("正在运行回测...")
    engine = BacktestEngine(
        initial_capital=200000.0,
        commission=0.0003,
        slippage=0.0001,
        position_size=0.95
    )
    
    result = engine.run(df, signals)
    print("回测完成")
    print()
    
    # 生成报告
    print("=" * 70)
    generate_report(result, output_dir)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='A 股缠论回测系统')
    parser.add_argument('--symbol', type=str, required=True, help='股票代码，如 000001')
    parser.add_argument('--start', type=str, default='2024-01-01', help='开始日期 (default: 2024-01-01)')
    parser.add_argument('--end', type=str, default='2025-12-31', help='结束日期 (default: 2025-12-31)')
    parser.add_argument('--period', type=str, default='daily', help='K线周期: daily/weekly/15/30/60 (default: daily)')
    parser.add_argument('--output', type=str, default=None, help='报告输出目录')
    args = parser.parse_args()
    
    run_backtest(args.symbol, args.start, args.end, args.period, args.output)
