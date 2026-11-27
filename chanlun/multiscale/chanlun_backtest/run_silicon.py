"""
工业硅期货回测脚本 - 支持指定年份
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chanlun_backtest.data import fetch_silicon_data
from chanlun_backtest.indicators import calculate_all_indicators
from chanlun_backtest.signals import scan_signals
from chanlun_backtest.engine import BacktestEngine
from chanlun_backtest.report import generate_report


def run_backtest(year: int, output_dir: str):
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31" if year < 2026 else "2026-06-13"
    
    print("=" * 70)
    print(f"缠论买卖点量化交易系统 - 工业硅期货回测 ({year}年)")
    print("=" * 70)
    print(f"回测区间: {start_date} 至 {end_date}")
    print(f"初始资金: 200,000.00 元")
    print(f"手续费率: 0.0003")
    print(f"交易品种: 工业硅期货 (15分钟)")
    print()
    
    # 获取数据
    print("正在获取工业硅期货历史数据...")
    df = fetch_silicon_data(start_date, end_date)
    
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
    
    # 扫描信号
    print("正在扫描买卖点信号...")
    signals = scan_signals(df)
    print(f"发现 {len(signals)} 个信号")
    
    if signals:
        print("\n信号列表:")
        for idx, signal_type, stop_loss, structure_level in signals:
            time_str = df.loc[idx, 'timestamp'].strftime('%Y-%m-%d %H:%M')
            print(f"  [{time_str}] {signal_type} - 止损: {stop_loss:.2f}, 结构位: {structure_level:.2f}")
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--year', type=int, required=True, help='回测年份')
    parser.add_argument('--output', type=str, default=None, help='报告输出目录')
    args = parser.parse_args()
    
    output = args.output or f'./backtest_report_silicon_{args.year}'
    run_backtest(args.year, output)
