#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据获取模块
支持AKShare（主）和新浪爬虫（备选）
"""

import requests
import json
import time
import akshare as ak


def get_futures_15min_akshare(symbol):
    """AKShare获取期货15分钟K线数据"""
    df = ak.futures_zh_minute_sina(symbol=symbol, period="15")
    result = []
    for _, row in df.iterrows():
        result.append({
            "datetime": str(row["datetime"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        })
    return result


def get_futures_5min_akshare(symbol):
    """AKShare获取期货5分钟K线数据"""
    df = ak.futures_zh_minute_sina(symbol=symbol, period="5")
    result = []
    for _, row in df.iterrows():
        result.append({
            "datetime": str(row["datetime"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        })
    return result


def get_futures_15min_sina(symbol):
    """新浪爬虫获取期货15分钟K线数据（备选）"""
    url = f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}_15=/InnerFuturesNewService.getFewMinLine"
    params = {"symbol": symbol, "type": "15"}
    resp = requests.get(url, params=params, timeout=10)
    text = resp.text
    json_str = text[text.find("(") + 1:text.rfind(")")]
    data = json.loads(json_str)

    result = []
    for item in data:
        result.append({
            "datetime": item["d"],
            "open": float(item["o"]),
            "high": float(item["h"]),
            "low": float(item["l"]),
            "close": float(item["c"]),
            "volume": int(item["v"])
        })
    return result


def get_futures_5min_sina(symbol):
    """新浪爬虫获取期货5分钟K线数据（备选）"""
    url = f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}_5=/InnerFuturesNewService.getFewMinLine"
    params = {"symbol": symbol, "type": "5"}
    resp = requests.get(url, params=params, timeout=10)
    text = resp.text
    json_str = text[text.find("(") + 1:text.rfind(")")]
    data = json.loads(json_str)

    result = []
    for item in data:
        result.append({
            "datetime": item["d"],
            "open": float(item["o"]),
            "high": float(item["h"]),
            "low": float(item["l"]),
            "close": float(item["c"]),
            "volume": int(item["v"])
        })
    return result


def get_futures_15min(symbol):
    """获取期货15分钟K线数据（AKShare优先，失败则用新浪）"""
    try:
        data = get_futures_15min_akshare(symbol)
        if data and len(data) > 0:
            return data
    except Exception as e:
        print(f"    AKShare失败: {e}，切换新浪...")

    try:
        data = get_futures_15min_sina(symbol)
        if data and len(data) > 0:
            return data
    except Exception as e:
        print(f"    新浪也失败: {e}")

    return []


def get_futures_5min(symbol):
    """获取期货5分钟K线数据（AKShare优先，失败则用新浪）"""
    try:
        data = get_futures_5min_akshare(symbol)
        if data and len(data) > 0:
            return data
    except Exception as e:
        print(f"    AKShare失败: {e}，切换新浪...")

    try:
        data = get_futures_5min_sina(symbol)
        if data and len(data) > 0:
            return data
    except Exception as e:
        print(f"    新浪也失败: {e}")

    return []


def get_futures_60min_akshare(symbol):
    """AKShare获取期货60分钟K线数据"""
    df = ak.futures_zh_minute_sina(symbol=symbol, period="60")
    result = []
    for _, row in df.iterrows():
        result.append({
            "datetime": str(row["datetime"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        })
    return result


def get_futures_60min_sina(symbol):
    """新浪爬虫获取期货60分钟K线数据（备选）"""
    url = f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}_60=/InnerFuturesNewService.getFewMinLine"
    params = {"symbol": symbol, "type": "60"}
    resp = requests.get(url, params=params, timeout=10)
    text = resp.text
    json_str = text[text.find("(") + 1:text.rfind(")")]
    data = json.loads(json_str)

    result = []
    for item in data:
        result.append({
            "datetime": item["d"],
            "open": float(item["o"]),
            "high": float(item["h"]),
            "low": float(item["l"]),
            "close": float(item["c"]),
            "volume": int(item["v"])
        })
    return result


def get_futures_60min(symbol):
    """获取期货60分钟K线数据（AKShare优先，失败则用新浪）"""
    try:
        data = get_futures_60min_akshare(symbol)
        if data and len(data) > 0:
            return data
    except Exception as e:
        print(f"    AKShare失败: {e}，切换新浪...")

    try:
        data = get_futures_60min_sina(symbol)
        if data and len(data) > 0:
            return data
    except Exception as e:
        print(f"    新浪也失败: {e}")

    return []


def get_index_60min(symbol):
    """获取指数60分钟K线数据（新浪API）
    symbol: sh000300(沪深300), sh000905(中证500), sh000852(中证1000)
    """
    url = "https://quotes.sina.cn/cn/api/jsonp.php/var%20_{}_60=/CN_MarketDataService.getKLineData"
    params = {
        "symbol": symbol,
        "scale": "60",
        "ma": "no",
        "datalen": "1023"
    }

    try:
        resp = requests.get(url.format(symbol), params=params, timeout=10)
        text = resp.text
        json_str = text[text.find("(") + 1:text.rfind(")")]
        data = json.loads(json_str)

        result = []
        for item in data:
            result.append({
                "datetime": item["day"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": int(item["volume"]),
            })
        return result
    except Exception as e:
        print(f"    指数数据获取失败: {e}")
        return []


def get_index_15min(symbol):
    """获取指数15分钟K线数据（新浪API）
    symbol: sh000300(沪深300), sh000905(中证500), sh000852(中证1000)
    """
    url = "https://quotes.sina.cn/cn/api/jsonp.php/var%20_{}_15=/CN_MarketDataService.getKLineData"
    params = {
        "symbol": symbol,
        "scale": "15",
        "ma": "no",
        "datalen": "1023"
    }

    try:
        resp = requests.get(url.format(symbol), params=params, timeout=10)
        text = resp.text
        json_str = text[text.find("(") + 1:text.rfind(")")]
        data = json.loads(json_str)

        result = []
        for item in data:
            result.append({
                "datetime": item["day"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": int(item["volume"]),
            })
        return result
    except Exception as e:
        print(f"    指数数据获取失败: {e}")
        return []


def get_index_5min(symbol):
    """获取指数5分钟K线数据（新浪API）
    symbol: sh000300(沪深300), sh000905(中证500), sh000852(中证1000)
    """
    url = "https://quotes.sina.cn/cn/api/jsonp.php/var%20_{}_5=/CN_MarketDataService.getKLineData"
    params = {
        "symbol": symbol,
        "scale": "5",
        "ma": "no",
        "datalen": "1023"
    }

    try:
        resp = requests.get(url.format(symbol), params=params, timeout=10)
        text = resp.text
        json_str = text[text.find("(") + 1:text.rfind(")")]
        data = json.loads(json_str)

        result = []
        for item in data:
            result.append({
                "datetime": item["day"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": int(item["volume"]),
            })
        return result
    except Exception as e:
        print(f"    指数数据获取失败: {e}")
        return []


def get_futures_daily(symbol):
    """获取期货日线数据（新浪API）
    symbol: CU0, AL0, AG0 等期货主力合约代码
    返回: [{"date": str, "open": float, "high": float, "low": float, "close": float, "volume": int}, ...]
    """
    url = f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}_d=/InnerFuturesNewService.getDailyKLine"
    params = {"symbol": symbol}

    try:
        resp = requests.get(url, params=params, timeout=10)
        text = resp.text
        json_str = text[text.find("(") + 1:text.rfind(")")]
        data = json.loads(json_str)

        result = []
        for item in data:
            result.append({
                "datetime": item["d"],
                "open": float(item["o"]),
                "high": float(item["h"]),
                "low": float(item["l"]),
                "close": float(item["c"]),
                "volume": int(item["v"])
            })
        return result
    except Exception as e:
        print(f"    日线数据获取失败({symbol}): {e}")
        return []


def get_index_daily(symbol):
    """获取指数日线数据（新浪API）
    symbol: sh000300(沪深300), sh000905(中证500), sh000852(中证1000)
    """
    url = "https://quotes.sina.cn/cn/api/jsonp.php/var%20_{}_d=/CN_MarketDataService.getKLineData"
    params = {
        "symbol": symbol,
        "scale": "240",  # 日线=240分钟
        "ma": "no",
        "datalen": "1023"
    }

    try:
        resp = requests.get(url.format(symbol), params=params, timeout=10)
        text = resp.text
        json_str = text[text.find("(") + 1:text.rfind(")")]
        data = json.loads(json_str)

        result = []
        for item in data:
            result.append({
                "datetime": item["day"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": int(item["volume"]),
            })
        return result
    except Exception as e:
        print(f"    指数日线获取失败({symbol}): {e}")
        return []


def resample_to_15m(bars):
    """将5分钟K线合成15分钟K线
    按交易时段自然边界分组（间隔>2小时视为新时段），每3根5分钟K线合成1根15分钟K线
    bars: list of dict with keys: datetime, open, high, low, close, volume
    Returns: list of dict with same keys
    """
    if not bars:
        return []

    sessions = []  # list of lists
    current_session = [bars[0]]

    for i in range(1, len(bars)):
        try:
            prev_dt = bars[i-1]["datetime"]
            curr_dt = bars[i]["datetime"]
            # 解析时间差（兼容 "2024-01-01 09:00:00" 和 "2024-01-01 09:00" 格式）
            prev_parts = prev_dt.replace("-", " ").replace(":", " ").split()
            curr_parts = curr_dt.replace("-", " ").replace(":", " ").split()
            prev_h, prev_m = int(prev_parts[3]), int(prev_parts[4])
            curr_h, curr_m = int(curr_parts[3]), int(curr_parts[4])
            prev_day = int(prev_parts[2])
            curr_day = int(curr_parts[2])

            # 计算分钟差
            prev_total_min = prev_day * 24 * 60 + prev_h * 60 + prev_m
            curr_total_min = curr_day * 24 * 60 + curr_h * 60 + curr_m
            gap_min = curr_total_min - prev_total_min

            # 间隔>120分钟 或 当前session已满3根 → 新session
            if gap_min > 120 or len(current_session) >= 3:
                sessions.append(current_session)
                current_session = [bars[i]]
            else:
                current_session.append(bars[i])
        except (IndexError, ValueError):
            current_session.append(bars[i])

    if current_session:
        sessions.append(current_session)

    # 合成每根15分钟K线
    bars_15m = []
    for session in sessions:
        if not session:
            continue
        bars_15m.append({
            "datetime": session[-1]["datetime"],
            "open": session[0]["open"],
            "high": max(b["high"] for b in session),
            "low": min(b["low"] for b in session),
            "close": session[-1]["close"],
            "volume": sum(b.get("volume", 0) for b in session),
        })

    return bars_15m


def resample_to_1h(bars):
    """将5分钟K线合成1小时K线
    按交易时段自然边界分组（间隔>2小时视为新时段），每12根5分钟K线合成1根1小时K线
    bars: list of dict with keys: datetime, open, high, low, close, volume
    Returns: list of dict with same keys
    """
    if not bars:
        return []

    sessions = []  # list of lists
    current_session = [bars[0]]

    for i in range(1, len(bars)):
        try:
            prev_dt = bars[i-1]["datetime"]
            curr_dt = bars[i]["datetime"]
            # 解析时间差（兼容 "2024-01-01 09:00:00" 和 "2024-01-01 09:00" 格式）
            prev_parts = prev_dt.replace("-", " ").replace(":", " ").split()
            curr_parts = curr_dt.replace("-", " ").replace(":", " ").split()
            prev_h, prev_m = int(prev_parts[3]), int(prev_parts[4])
            curr_h, curr_m = int(curr_parts[3]), int(curr_parts[4])
            prev_day = int(prev_parts[2])
            curr_day = int(curr_parts[2])

            # 计算分钟差
            prev_total_min = prev_day * 24 * 60 + prev_h * 60 + prev_m
            curr_total_min = curr_day * 24 * 60 + curr_h * 60 + curr_m
            gap_min = curr_total_min - prev_total_min

            # 间隔>120分钟 或 当前session已满12根 → 新session
            if gap_min > 120 or len(current_session) >= 12:
                sessions.append(current_session)
                current_session = [bars[i]]
            else:
                current_session.append(bars[i])
        except (IndexError, ValueError):
            current_session.append(bars[i])

    if current_session:
        sessions.append(current_session)

    # 合成每根1小时K线
    hourly_bars = []
    for session in sessions:
        if not session:
            continue
        hourly_bars.append({
            "datetime": session[-1]["datetime"],
            "open": session[0]["open"],
            "high": max(b["high"] for b in session),
            "low": min(b["low"] for b in session),
            "close": session[-1]["close"],
            "volume": sum(b.get("volume", 0) for b in session),
        })

    return hourly_bars


# 期货品种列表
FUTURES_LIST = [
    # 有色金属
    ("CU0", "沪铜", "有色"),
    ("AL0", "沪铝", "有色"),
    ("ZN0", "沪锌", "有色"),
    ("PB0", "沪铅", "有色"),
    ("NI0", "沪镍", "有色"),
    ("SN0", "沪锡", "有色"),
    # 农产品
    ("M0", "豆粕", "农产品"),
    ("Y0", "豆油", "农产品"),
    ("P0", "棕榈油", "农产品"),
    ("OI0", "菜油", "农产品"),
    ("RM0", "菜粕", "农产品"),
    ("C0", "玉米", "农产品"),
    ("SR0", "白糖", "农产品"),
    ("CF0", "棉花", "农产品"),
    ("AP0", "苹果", "农产品"),
    ("LH0", "生猪", "农产品"),
    # 贵金属
    ("AG0", "白银", "贵金属"),
    ("AU0", "黄金", "贵金属"),
]

# 股指期货列表 (symbol, name, contract_multiplier, margin_rate)
INDEX_FUTURES_LIST = [
    ("IF0", "沪深300股指", 300, 0.12),
    ("IH0", "上证50股指", 300, 0.12),
    ("IC0", "中证500股指", 200, 0.14),
    ("IM0", "中证1000股指", 200, 0.14),
    ("AU0", "沪金", 1000, 0.10),
    ("AG0", "沪银", 15, 0.10),
]

# 指数列表
INDEX_LIST = [
    ("sh000300", "沪深300", "指数"),
    ("sh000905", "中证500", "指数"),
    ("sh000852", "中证1000", "指数"),
]

# ETF列表（替代指数观测，假设可做空）
ETF_LIST = [
    ("sh588000", "科创50ETF", "ETF"),
    ("sz159915", "创业板ETF", "ETF"),
    ("sh563300", "中证2000ETF", "ETF"),
    # ── 板块热度 ETF（来自 sector_heat.py 映射） ──
    ("sh515070", "人工智能ETF", "板块ETF"),
    ("sz159633", "算力ETF", "板块ETF"),
    ("sh562500", "机器人ETF", "板块ETF"),
    ("sh512660", "军工ETF", "板块ETF"),
    ("sh512480", "半导体ETF", "板块ETF"),
    ("sh515880", "通信ETF", "板块ETF"),
    ("sz159755", "电池ETF", "板块ETF"),
    ("sz159635", "金融科技ETF", "板块ETF"),
    ("sz159992", "创新药ETF", "板块ETF"),
    ("sh515790", "光伏ETF", "板块ETF"),
    ("sz512690", "酒ETF", "板块ETF"),
]

# 兼容旧代码
MONITOR_LIST = FUTURES_LIST


class DataCache:
    """统一数据缓存：一次获取所有品种-周期组合的数据，供所有策略共享"""

    def __init__(self):
        self._cache = {}  # key: (timeframe, symbol) -> list[dict]

    def fetch_all(self):
        """获取所有品种的时间帧数据
        只从 API 获取 5m 数据，然后合成为 15m 和 1h
        日线数据单独从 API 获取（无法从 5m 合成）
        """
        t0 = time.time()
        count = 0

        # 收集所有需要获取 5m 数据的品种
        symbols_5m = []  # [(symbol, name, fetcher_5m), ...]

        # 期货品种
        for symbol, name, _ in FUTURES_LIST:
            symbols_5m.append((symbol, name, get_futures_5min))

        # 股指期货（跳过已在期货列表中的AU0/AG0）
        futures_symbols = {s for s, _, _ in FUTURES_LIST}
        for symbol, name, _, _ in INDEX_FUTURES_LIST:
            if symbol in futures_symbols:
                continue
            symbols_5m.append((symbol, name, get_futures_5min))

        # 指数（使用指数API）
        for symbol, name, _ in INDEX_LIST:
            symbols_5m.append((symbol, name, get_index_5min))

        # ETF（使用指数API）
        for symbol, name, _ in ETF_LIST:
            symbols_5m.append((symbol, name, get_index_5min))

        # 获取 5m 数据并合成 15m、1h
        for symbol, name, fetcher in symbols_5m:
            try:
                bars_5m = fetcher(symbol)
                self._cache[("5m", symbol)] = bars_5m
                count += 1

                # 合成 15m
                bars_15m = resample_to_15m(bars_5m)
                self._cache[("15m", symbol)] = bars_15m
                count += 1

                # 合成 1h
                bars_1h = resample_to_1h(bars_5m)
                self._cache[("1h", symbol)] = bars_1h
                count += 1
            except Exception as e:
                print(f"  DataCache: {name}({symbol}) 5m 获取失败: {e}")
                self._cache[("5m", symbol)] = []
                self._cache[("15m", symbol)] = []
                self._cache[("1h", symbol)] = []
            time.sleep(0.3)

        # 日线数据（所有品种，无法从 5m 合成）
        daily_symbols = []
        for symbol, name, _ in FUTURES_LIST:
            daily_symbols.append((symbol, name, get_futures_daily))
        for symbol, name, _, _ in INDEX_FUTURES_LIST:
            if symbol not in futures_symbols:
                daily_symbols.append((symbol, name, get_futures_daily))
        for symbol, name, _ in INDEX_LIST:
            daily_symbols.append((symbol, name, get_index_daily))
        for symbol, name, _ in ETF_LIST:
            daily_symbols.append((symbol, name, get_index_daily))

        for symbol, name, fetcher in daily_symbols:
            try:
                bars = fetcher(symbol)
                self._cache[("daily", symbol)] = bars
                count += 1
            except Exception as e:
                print(f"  DataCache: {name}({symbol}) daily 获取失败: {e}")
                self._cache[("daily", symbol)] = []
            time.sleep(0.3)

        elapsed = time.time() - t0
        print(f"  DataCache: 完成 {count} 次数据获取，耗时 {elapsed:.1f}s")

    def get(self, timeframe, symbol):
        """获取缓存数据
        timeframe: "5m"（API获取）, "15m"/"1h"（从5m合成）, "daily"（API获取）
        symbol: 品种代码，如 "CU0", "sh000300"
        """
        return self._cache.get((timeframe, symbol), [])


if __name__ == "__main__":
    # 测试
    data = get_futures_15min("AG0")
    print(f"白银15分钟数据: {len(data)} 条")
    if data:
        print(f"最新: {data[-1]}")
