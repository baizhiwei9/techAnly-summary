"""
回测报告生成模块 - 多空双向版
"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from .engine import BacktestResult


def generate_report(result: BacktestResult, output_dir: str = './backtest_report') -> str:
    """生成回测报告"""
    import os
    os.makedirs(output_dir, exist_ok=True)
    
    report_lines = []
    report_lines.append("=" * 70)
    report_lines.append("缠论买卖点量化交易系统 - 回测报告（多空双向）")
    report_lines.append("=" * 70)
    report_lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("")
    
    # 基本统计
    report_lines.append("【资金统计】")
    report_lines.append(f"  初始资金: {result.initial_capital:,.2f} 元")
    report_lines.append(f"  最终资金: {result.final_capital:,.2f} 元")
    report_lines.append(f"  总收益: {result.total_return:,.2f} 元")
    report_lines.append(f"  总收益率: {result.total_return_pct:.2%}")
    report_lines.append("")
    
    # 风险指标
    report_lines.append("【风险指标】")
    report_lines.append(f"  最大回撤: {result.max_drawdown:,.2f} 元")
    report_lines.append(f"  最大回撤比例: {result.max_drawdown_pct:.2%}")
    report_lines.append("")
    
    # 交易统计
    report_lines.append("【交易统计】")
    report_lines.append(f"  总交易次数: {result.total_trades}")
    report_lines.append(f"  盈利次数: {result.winning_trades}")
    report_lines.append(f"  亏损次数: {result.losing_trades}")
    report_lines.append(f"  胜率: {result.win_rate:.2%}")
    report_lines.append("")
    
    # 多空统计
    report_lines.append("【多空统计】")
    report_lines.append(f"  做多交易: {result.long_trades} 次")
    report_lines.append(f"  做多盈利: {result.long_wins} 次")
    if result.long_trades > 0:
        report_lines.append(f"  做多胜率: {result.long_wins / result.long_trades:.2%}")
    report_lines.append(f"  做空交易: {result.short_trades} 次")
    report_lines.append(f"  做空盈利: {result.short_wins} 次")
    if result.short_trades > 0:
        report_lines.append(f"  做空胜率: {result.short_wins / result.short_trades:.2%}")
    report_lines.append("")
    
    # 盈亏分析
    report_lines.append("【盈亏分析】")
    report_lines.append(f"  平均盈利: {result.avg_profit:,.2f} 元")
    report_lines.append(f"  平均亏损: {result.avg_loss:,.2f} 元")
    report_lines.append(f"  盈亏比: {result.profit_factor:.2f}")
    report_lines.append("")
    
    # 信号类型统计
    if result.trades:
        report_lines.append("【信号类型统计】")
        signal_types = ['二买', '三买', '二卖', '三卖']
        for sig_type in signal_types:
            type_trades = [t for t in result.trades if t.signal_type == sig_type]
            if type_trades:
                type_wins = [t for t in type_trades if t.pnl > 0]
                type_pnl = sum(t.pnl for t in type_trades)
                report_lines.append(f"  {sig_type}: {len(type_trades)} 次, 盈利 {len(type_wins)} 次, "
                                   f"胜率 {len(type_wins)/len(type_trades):.2%}, 总盈亏 {type_pnl:,.2f} 元")
        report_lines.append("")
    
    # 交易明细
    if result.trades:
        report_lines.append("【交易明细】")
        report_lines.append("-" * 100)
        report_lines.append(f"{'序号':>4} | {'类型':>4} | {'方向':>4} | {'开仓时间':>19} | {'开仓价':>10} | {'平仓时间':>19} | {'平仓价':>10} | {'盈亏':>10} | {'持仓':>4}")
        report_lines.append("-" * 100)
        
        for i, trade in enumerate(result.trades, 1):
            entry_str = trade.entry_time.strftime('%Y-%m-%d %H:%M') if trade.entry_time else ''
            exit_str = trade.exit_time.strftime('%Y-%m-%d %H:%M') if trade.exit_time else ''
            direction_str = '做多' if trade.direction == 'long' else '做空'
            
            report_lines.append(
                f"{i:>4} | {trade.signal_type:>4} | {direction_str:>4} | {entry_str:>19} | {trade.entry_price:>10.2f} | "
                f"{exit_str:>19} | {trade.exit_price:>10.2f} | {trade.pnl:>10.2f} | {trade.holding_bars:>4}"
            )
        
        report_lines.append("-" * 100)
    
    # 保存文本报告
    report_text = '\n'.join(report_lines)
    report_file = os.path.join(output_dir, 'backtest_report.txt')
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print(report_text)
    print(f"\n报告已保存至: {report_file}")
    
    # 生成权益曲线图
    plot_equity_curve(result, output_dir)
    
    return report_text


def plot_equity_curve(result: BacktestResult, output_dir: str):
    """绘制权益曲线"""
    import os
    
    equity = np.array(result.equity_curve)
    
    fig, ax = plt.subplots(figsize=(14, 6))
    
    ax.plot(equity, linewidth=1.5, color='#2E86AB', label='账户权益')
    ax.axhline(y=result.initial_capital, color='gray', linestyle='--', alpha=0.5, label='初始资金')
    
    ax.set_xlabel('K线序号', fontsize=12)
    ax.set_ylabel('账户权益 (元)', fontsize=12)
    ax.set_title('缠论买卖点交易系统 - 权益曲线（多空双向）', fontsize=14, fontweight='bold')
    ax.legend(loc='upper left', fontsize=10)
    ax.grid(True, alpha=0.3)
    
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:,.0f}'))
    
    plt.tight_layout()
    
    chart_file = os.path.join(output_dir, 'equity_curve.png')
    plt.savefig(chart_file, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"权益曲线图已保存至: {chart_file}")
