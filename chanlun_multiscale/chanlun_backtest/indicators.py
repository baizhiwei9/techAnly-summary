"""
技术指标计算模块（回测系统）
"""
import pandas as pd
import numpy as np
from talib import abstract
from .factors import calc_capital_factor


def calculate_ma(df: pd.DataFrame, periods: list[int]) -> pd.DataFrame:
    """计算移动平均线"""
    for period in periods:
        df[f'MA{period}'] = df['close'].rolling(window=period).mean()
    return df


def calculate_macd(df: pd.DataFrame, fast_period=12, slow_period=26, signal_period=9) -> pd.DataFrame:
    """计算MACD指标"""
    macd, signal, hist = abstract.MACD(
        df['close'],
        fastperiod=fast_period,
        slowperiod=slow_period,
        signalperiod=signal_period
    )
    df['MACD'] = macd
    df['MACD_SIGNAL'] = signal
    df['MACD_HIST'] = hist
    return df


def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算所有需要的技术指标"""
    df = calculate_ma(df, [20, 40, 160])
    df = calculate_macd(df)
    return df


def get_capital_factor(df: pd.DataFrame, idx: int = None, lookback: int = 60) -> dict:
    """获取资金面因子"""
    if idx is None:
        idx = len(df) - 1
    return calc_capital_factor(df, idx, lookback)
