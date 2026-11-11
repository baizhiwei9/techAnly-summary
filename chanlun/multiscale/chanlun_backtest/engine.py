"""
回测引擎 - 缠论买卖点交易系统（多空双向）
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Trade:
    """交易记录"""
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    signal_type: str = ''  # '二买', '三买', '二卖', '三卖'
    direction: str = 'long'  # 'long' or 'short'
    stop_loss: float = 0.0
    structure_level: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    holding_bars: int = 0


@dataclass
class Position:
    """持仓"""
    is_open: bool = False
    direction: str = 'long'  # 'long' or 'short'
    entry_price: float = 0.0
    entry_time: Optional[datetime] = None
    signal_type: str = ''
    stop_loss: float = 0.0
    structure_level: float = 0.0
    entry_idx: int = 0
    units: float = 0.0
    position_value: float = 0.0


@dataclass
class BacktestResult:
    """回测结果"""
    initial_capital: float
    final_capital: float
    total_return: float
    total_return_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    long_trades: int = 0
    short_trades: int = 0
    long_wins: int = 0
    short_wins: int = 0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, initial_capital: float = 200000.0, 
                 commission: float = 0.0003, 
                 slippage: float = 0.0001,
                 position_size: float = 1.0):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.position_size = position_size
        
        self.capital = initial_capital
        self.position = Position()
        self.trades = []
        self.equity_curve = []
    
    def open_position(self, entry_price: float, entry_time: datetime, 
                      signal_type: str, direction: str,
                      stop_loss: float, structure_level: float, idx: int):
        """开仓"""
        if self.position.is_open:
            return
        
        # 滑点处理
        if direction == 'long':
            actual_price = entry_price * (1 + self.slippage)
        else:
            actual_price = entry_price * (1 - self.slippage)
        
        cost_per_unit = actual_price * (1 + self.commission)
        position_value = self.capital * self.position_size
        units = position_value / cost_per_unit
        
        self.position = Position(
            is_open=True,
            direction=direction,
            entry_price=actual_price,
            entry_time=entry_time,
            signal_type=signal_type,
            stop_loss=stop_loss,
            structure_level=structure_level,
            entry_idx=idx,
            units=units,
            position_value=position_value
        )
    
    def close_position(self, exit_price: float, exit_time: datetime, 
                       reason: str, idx: int) -> Optional[Trade]:
        """平仓"""
        if not self.position.is_open:
            return None
        
        pos = self.position
        
        # 滑点处理
        if pos.direction == 'long':
            actual_price = exit_price * (1 - self.slippage)
            gross_pnl = (actual_price - pos.entry_price) * pos.units
        else:
            actual_price = exit_price * (1 + self.slippage)
            gross_pnl = (pos.entry_price - actual_price) * pos.units
        
        # 扣除双边手续费
        commission_cost = pos.position_value * self.commission * 2
        net_pnl = gross_pnl - commission_cost
        
        pnl_pct = net_pnl / pos.position_value
        self.capital += net_pnl
        
        trade = Trade(
            entry_time=pos.entry_time,
            entry_price=pos.entry_price,
            exit_time=exit_time,
            exit_price=actual_price,
            signal_type=pos.signal_type,
            direction=pos.direction,
            stop_loss=pos.stop_loss,
            structure_level=pos.structure_level,
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            holding_bars=idx - pos.entry_idx
        )
        self.trades.append(trade)
        self.position = Position()
        
        return trade
    
    def check_exit_conditions(self, df: pd.DataFrame, idx: int) -> tuple:
        """检查平仓条件"""
        if not self.position.is_open:
            return False, ''
        
        pos = self.position
        close = df.loc[idx, 'close']
        ma40 = df.loc[idx, 'MA40']
        
        if pos.direction == 'long':
            # 多头平仓条件
            if close < ma40:
                return True, '收盘价跌破MA40，趋势破坏'
            if close < pos.structure_level:
                return True, f"跌破买点结构低点{pos.structure_level:.2f}"
        else:
            # 空头平仓条件
            if close > ma40:
                return True, '收盘价突破MA40，趋势破坏'
            if close > pos.structure_level:
                return True, f"突破卖点结构高点{pos.structure_level:.2f}"
        
        return False, ''
    
    def run(self, df: pd.DataFrame, signals: list) -> BacktestResult:
        """运行回测"""
        signal_dict = {s[0]: s for s in signals}
        
        for idx in range(len(df)):
            # 记录权益曲线
            if self.position.is_open:
                pos = self.position
                if pos.direction == 'long':
                    current_value = self.capital + (df.loc[idx, 'close'] - pos.entry_price) * pos.units
                else:
                    current_value = self.capital + (pos.entry_price - df.loc[idx, 'close']) * pos.units
            else:
                current_value = self.capital
            self.equity_curve.append(current_value)
            
            # 检查平仓条件
            should_exit, reason = self.check_exit_conditions(df, idx)
            if should_exit:
                self.close_position(df.loc[idx, 'close'], df.loc[idx, 'timestamp'], reason, idx)
                continue
            
            # 检查开仓信号
            if not self.position.is_open and idx in signal_dict:
                _, signal_type, stop_loss, structure_level = signal_dict[idx]
                
                if signal_type in ('二买', '三买'):
                    direction = 'long'
                else:
                    direction = 'short'
                
                self.open_position(
                    df.loc[idx, 'close'],
                    df.loc[idx, 'timestamp'],
                    signal_type,
                    direction,
                    stop_loss,
                    structure_level,
                    idx
                )
        
        # 回测结束平仓
        if self.position.is_open:
            last_idx = len(df) - 1
            self.close_position(
                df.loc[last_idx, 'close'],
                df.loc[last_idx, 'timestamp'],
                '回测结束平仓',
                last_idx
            )
        
        return self._calculate_results()
    
    def _calculate_results(self) -> BacktestResult:
        """计算回测结果"""
        if not self.trades:
            return BacktestResult(
                initial_capital=self.initial_capital,
                final_capital=self.capital,
                total_return=self.capital - self.initial_capital,
                total_return_pct=0.0,
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                avg_profit=0.0,
                avg_loss=0.0,
                profit_factor=0.0,
                trades=[],
                equity_curve=self.equity_curve
            )
        
        winning_trades = [t for t in self.trades if t.pnl > 0]
        losing_trades = [t for t in self.trades if t.pnl <= 0]
        
        total_return = self.capital - self.initial_capital
        total_return_pct = total_return / self.initial_capital
        
        equity = np.array(self.equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        max_drawdown = np.max(drawdown) * self.initial_capital
        max_drawdown_pct = np.max(drawdown)
        
        avg_profit = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t.pnl for t in losing_trades]) if losing_trades else 0
        
        total_profit = sum(t.pnl for t in winning_trades)
        total_loss = abs(sum(t.pnl for t in losing_trades))
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
        
        # 多空统计
        long_trades = [t for t in self.trades if t.direction == 'long']
        short_trades = [t for t in self.trades if t.direction == 'short']
        long_wins = [t for t in long_trades if t.pnl > 0]
        short_wins = [t for t in short_trades if t.pnl > 0]
        
        return BacktestResult(
            initial_capital=self.initial_capital,
            final_capital=self.capital,
            total_return=total_return,
            total_return_pct=total_return_pct,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            total_trades=len(self.trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=len(winning_trades) / len(self.trades),
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            long_trades=len(long_trades),
            short_trades=len(short_trades),
            long_wins=len(long_wins),
            short_wins=len(short_wins),
            trades=self.trades,
            equity_curve=self.equity_curve
        )
