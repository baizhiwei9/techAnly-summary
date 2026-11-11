"""
缠论三买点回测系统 - 主程序
"""
import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chanlun_backtest.data import fetch_silver_data
from chanlun_backtest.indicators import calculate_all_indicators
from chanlun_backtest.signals import scan_signals
from chanlun_backtest.engine import BacktestEngine
from chanlun_backtest.report import generate_report


def main():
    parser = argparse.ArgumentParser(description='缠论三买点回测系统')
    parser.add_argument('--start-date', type=str, default='2025-01-01',
                        help='回测开始日期 (default: 2025-01-01)')
    parser.add_argument('--end-date', type=str, default='2025-06-13',
                        help='回测结束日期 (default: 2025-06-13)')
    parser.add_argument('--capital', type=float, default=200000.0,
                        help='初始资金 (default: 200000)')
    parser.add_argument('--commission', type=float, default=0.0003,
                        help='手续费率 (default: 0.0003)')
    parser.add_argument('--output', type=str, default='./backtest_report',
                        help='报告输出目录 (default: ./backtest_report)')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("缠论三买点量化交易系统 - 回测")
    print("=" * 70)
    print(f"回测区间: {args.start_date} 至 {args.end_date}")
    print(f"初始资金: {args.capital:,.2f} 元")
    print(f"手续费率: {args.commission:.4f}")
    print(f"交易品种: 白银期货 (15分钟)")
    print()
    
    # 获取数据
    print("正在获取白银期货历史数据...")
    df = fetch_silver_data(args.start_date, args.end_date)
    
    if df.empty:
        print("错误：无法获取数据")
        return
    
    print(f"获取到 {len(df)} 根K线")
    print(f"数据范围: {df.iloc[0]['timestamp']} 至 {df.iloc[-1]['timestamp']}")
    print()
    
    # 计算指标
    print("正在计算技术指标...")
    df = calculate_all_indicators(df)
    print("指标计算完成")
    print()
    
    # 扫描信号
    print("正在扫描买点信号...")
    signals = scan_signals(df)
    print(f"发现 {len(signals)} 个买点信号")
    
    if signals:
        print("\n信号列表:")
        for idx, signal_type, stop_loss, pullback_low in signals:
            time_str = df.loc[idx, 'timestamp'].strftime('%Y-%m-%d %H:%M')
            print(f"  [{time_str}] {signal_type} - 止损: {stop_loss:.4f}, 回调低点: {pullback_low:.4f}")
    print()
    
    # 运行回测
    print("正在运行回测...")
    engine = BacktestEngine(
        initial_capital=args.capital,
        commission=args.commission,
        slippage=0.0001,
        position_size=0.95
    )
    
    result = engine.run(df, signals)
    print("回测完成")
    print()
    
    # 生成报告
    print("=" * 70)
    generate_report(result, args.output)


if __name__ == '__main__':
    main()
