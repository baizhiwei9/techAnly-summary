"""
回测数据获取模块 - 支持工业硅期货真实数据 + 模拟数据
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def _generate_trading_timestamps(start_date: str, end_date: str) -> list:
    """生成期货交易时间戳（日盘+夜盘）"""
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    timestamps = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            sessions = [
                (9, [0, 15, 30, 45]),
                (10, [0, 15, 30, 45]),
                (10, [30, 45]),
                (11, [0, 15, 30]),
                (13, [30, 45]),
                (14, [0, 15, 30, 45]),
            ]
            for hour, minutes in sessions:
                for minute in minutes:
                    timestamps.append(current.replace(hour=hour, minute=minute, second=0))
            
            night_hours = [
                (21, [0, 15, 30, 45]),
                (22, [0, 15, 30, 45]),
                (23, [0, 15, 30, 45]),
                (0, [0, 15, 30, 45]),
                (1, [0, 15, 30, 45]),
                (2, [0, 15, 30]),
            ]
            for hour, minutes in night_hours:
                for minute in minutes:
                    if hour >= 21:
                        timestamps.append(current.replace(hour=hour, minute=minute, second=0))
                    else:
                        timestamps.append((current + timedelta(days=1)).replace(hour=hour, minute=minute, second=0))
        
        current += timedelta(days=1)
    
    return timestamps


def generate_silver_data(start_date: str, end_date: str) -> pd.DataFrame:
    """生成模拟白银期货15分钟K线数据"""
    np.random.seed(2025)
    
    timestamps = _generate_trading_timestamps(start_date, end_date)
    total_bars = len(timestamps)
    
    if total_bars == 0:
        return pd.DataFrame()
    
    base_price = 7200.0
    
    segments = [
        (0.00, 0.08, 0.00015),
        (0.08, 0.14, -0.00008),
        (0.14, 0.25, 0.00012),
        (0.25, 0.32, -0.00005),
        (0.32, 0.38, -0.00015),
        (0.38, 0.42, -0.00008),
        (0.42, 0.50, 0.00018),
        (0.50, 0.56, 0.00005),
        (0.56, 0.62, -0.00010),
        (0.62, 0.72, 0.00015),
        (0.72, 0.78, -0.00006),
        (0.78, 0.85, 0.00012),
        (0.85, 0.92, 0.00004),
        (0.92, 1.00, -0.00005),
    ]
    
    prices = []
    price = base_price
    
    for i in range(total_bars):
        progress = i / total_bars
        
        trend = 0.0
        for seg_start, seg_end, seg_trend in segments:
            if seg_start <= progress < seg_end:
                trend = seg_trend
                break
        
        noise = np.random.normal(0, 0.0006)
        if np.random.random() < 0.02:
            noise *= 3
        
        change = trend + noise
        price = price * (1 + change)
        price = max(price, 5500)
        price = min(price, 9500)
        
        volatility = abs(np.random.normal(0, 0.0003))
        open_price = price
        close_price = price * (1 + np.random.normal(0, 0.0003))
        high_price = max(open_price, close_price) * (1 + volatility)
        low_price = min(open_price, close_price) * (1 - volatility)
        volume = np.random.uniform(2000, 15000)
        
        prices.append({
            'timestamp': timestamps[i],
            'open': open_price,
            'high': high_price,
            'low': low_price,
            'close': close_price,
            'volume': volume
        })
    
    return pd.DataFrame(prices)


# 工业硅主力合约映射表（按月份覆盖全年）
_SI_CONTRACTS_2025 = ['SI2412', 'SI2501', 'SI2502', 'SI2503', 'SI2504', 'SI2505',
                       'SI2506', 'SI2507', 'SI2508', 'SI2509', 'SI2510', 'SI2511', 'SI2512']
_SI_CONTRACTS_2026 = ['SI2512', 'SI2601', 'SI2602', 'SI2603', 'SI2604', 'SI2605', 'SI2606']


def fetch_silicon_data(start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取工业硅期货15分钟K线真实数据（通过akshare从新浪获取）
    拼接多个主力合约的数据，按日期去重。
    """
    import akshare as ak
    
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    
    # 选择需要拼接的合约
    if start_dt.year <= 2025:
        contracts = _SI_CONTRACTS_2025
    else:
        contracts = _SI_CONTRACTS_2026
    
    all_dfs = []
    for contract in contracts:
        try:
            df = ak.futures_zh_minute_sina(symbol=contract, period='15')
            df['datetime'] = pd.to_datetime(df['datetime'])
            all_dfs.append(df)
        except Exception as e:
            print(f"  获取{contract}失败: {e}")
            continue
    
    if not all_dfs:
        return pd.DataFrame()
    
    # 拼接并去重
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=['datetime'], keep='last')
    combined = combined.sort_values('datetime').reset_index(drop=True)
    
    # 按日期范围筛选
    combined['date'] = combined['datetime'].dt.date
    start_d = start_dt.date()
    end_d = end_dt.date()
    combined = combined[(combined['date'] >= start_d) & (combined['date'] <= end_d)]
    combined = combined.drop(columns=['date'])
    
    # 重命名列以匹配回测系统
    combined = combined.rename(columns={'datetime': 'timestamp'})
    for col in ['open', 'high', 'low', 'close', 'volume']:
        combined[col] = combined[col].astype(float)
    
    combined = combined[['timestamp', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)
    return combined




def fetch_astock_data(symbol: str, start_date: str, end_date: str, period: str = 'daily') -> pd.DataFrame:
    """
    获取 A 股股票真实数据（通过 akshare 从东方财富获取）
    
    :param symbol: 股票代码，如 '000001'（平安银行）、'600519'（贵州茅台）
    :param start_date: 开始日期，格式 'YYYY-MM-DD'
    :param end_date: 结束日期，格式 'YYYY-MM-DD'
    :param period: K线周期，可选 'daily'/'weekly'/'monthly' 或 '1'/'5'/'15'/'30'/'60'（分钟线）
    """
    import akshare as ak
    
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    
    if period in ['daily', 'weekly', 'monthly']:
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start_dt.strftime('%Y%m%d'),
                end_date=end_dt.strftime('%Y%m%d'),
                adjust='qfq'
            )
        except Exception as e:
            print(f"获取 {symbol} 数据失败: {e}")
            return pd.DataFrame()
        
        if df.empty:
            return pd.DataFrame()
        
        df = df.rename(columns={
            '日期': 'timestamp',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'volume'
        })
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)
        return df
    
    else:
        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=symbol,
                start_date=f"{start_date} 09:30:00",
                end_date=f"{end_date} 15:00:00",
                period=period,
                adjust='qfq'
            )
        except Exception as e:
            print(f"获取 {symbol} 分钟线数据失败: {e}")
            return pd.DataFrame()
        
        if df.empty:
            return pd.DataFrame()
        
        df = df.rename(columns={
            '时间': 'timestamp',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'volume'
        })
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)
        return df

def fetch_silver_data(start_date: str, end_date: str) -> pd.DataFrame:
    """获取白银期货数据（模拟）"""
    print("注意：使用模拟数据进行回测演示")
    print("实际使用时请替换为真实期货数据源（如CTP接口、通达信等）")
    return generate_silver_data(start_date, end_date)
