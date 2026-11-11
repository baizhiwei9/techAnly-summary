#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论分型版策略监控程序（结构版）
核心算法: K线包含 → 分型 → 笔 → 中枢 → 背驰 → B1/S1/B2/S2/B3/S3
MACD参数: (10,20,5) — 2026-11-11 对齐远程 techAnly-summary 定稿
监控品种: 期货 + 股指期货 + ETF
周期: 15分钟 / 1小时 / 日线 / 周线 四级
  · 15分钟：仅结构参考，不发交易信号
  · 1小时 ：只认三买（一买须 MA10>MA20 金叉；不认二买）
  · 日线  ：一/二/三买 + 卖点，带年龄/距离/均线/结构闸门
  · 周线  ：只认一买/二买（周线放宽）
持仓状态持久化到 $FRACTAL_STATE_FILE（默认 state/chanlun_fractal_state.json）

接口: init_chan_fractal() / process_cycle() / get_views() / get_cycle_signals()
"""

import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime

from data_fetcher import FUTURES_LIST, INDEX_FUTURES_LIST, ETF_LIST

# 导入 codex 的分型版核心模块
from chanlun_backtest.structure_indicators import (
    merge_klines, find_fractals, build_bi, detect_zhongshu,
    calc_macd as calc_macd_10205, calculate_structure_indicators,
)
from chanlun_backtest.structure_signals import detect_structure_signals
from chanlun_backtest.signal_gates import apply_level_gates, LEVEL_RULES


# 合并监控列表：期货 + 股指期货 + ETF（去重）
_FUTURES_SYMBOLS = {s for s, _, _ in FUTURES_LIST}
ALL_SYMBOLS = [(s, n, c) for s, n, c in FUTURES_LIST] + [
    (s, n, "指数") for s, n, _, _ in INDEX_FUTURES_LIST if s not in _FUTURES_SYMBOLS
] + [
    (s, n, "ETF") for s, n, _ in ETF_LIST
]

# 时间级别配置: (timeframe_key, data_cache_key, label)
TIMEFRAMES = [
    ("15m", "15m", "15分钟"),
    ("1h", "1h", "1小时"),
    ("daily", "daily", "日线"),
    ("weekly", "daily", "周线"),   # 由日线合成周线（W-FRI）
]

# 通知新鲜度：只推最近 N 根本级别K线内新形成的点；更老的只用于 get_views 判方向
# （级别闸门另有 40/60/90/12 的有效期，两者取更严）
NOTIFY_MAX_AGE = int(os.environ.get("FRACTAL_NOTIFY_MAX_AGE_BARS", "10"))

POLL_INTERVAL = 60
STATE_FILE = os.environ.get("FRACTAL_STATE_FILE", "state/chanlun_fractal_state.json")
HEARTBEAT_FILE = "/tmp/chanlun_fractal.heartbeat"

# 信号冷却: 同品种同信号类型同级别，N秒内不重复通知
SIGNAL_COOLDOWN = 3600  # 1小时

# 夜盘模式：夜盘时跳过 ETF（A-share ETF 不在夜盘交易）
_night_session = False


def set_night_session(is_night: bool):
    """由 main.py 调用，设置当前是否为夜盘"""
    global _night_session
    _night_session = is_night


# 买卖点中文名映射
SIGNAL_CN = {
    'B1': '一买', 'S1': '一卖',
    'B2': '二买', 'S2': '二卖',
    'B3': '三买', 'S3': '三卖',
}

# 本轮信号（供统一报告使用）
_latest_signals = []


def get_cycle_signals():
    """获取本轮周期产生的所有信号 [(title, content), ...]"""
    return list(_latest_signals)


def write_heartbeat():
    """写入心跳时间戳"""
    try:
        with open(HEARTBEAT_FILE, 'w') as f:
            f.write(str(time.time()))
    except Exception:
        pass


def load_state():
    """加载状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"  分型版加载状态失败: {e}")
    return {"notified_signals": {}}


def save_state(state):
    """保存状态"""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  分型版保存状态失败: {e}")


# === 数据转换 ===

def bars_to_dataframe(bars):
    """将 bar list[dict] 转为 DataFrame（兼容 structure_indicators 输入格式）"""
    if not bars:
        return None
    records = []
    for b in bars:
        records.append({
            'timestamp': b.get('datetime', ''),
            'open': float(b['open']),
            'high': float(b['high']),
            'low': float(b['low']),
            'close': float(b['close']),
            'volume': float(b.get('volume', 0)),
        })
    df = pd.DataFrame(records)
    # timestamp 列保持字符串即可，structure_indicators 不用它
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['close']).reset_index(drop=True)
    return df


def resample_to_weekly(bars):
    """日线 → 周线（W-FRI），与远程 tech_summary 周线口径一致"""
    if not bars:
        return []
    try:
        df = pd.DataFrame(bars).copy()
        df['dt'] = pd.to_datetime(df['datetime'], errors='coerce')
        df = df.dropna(subset=['dt']).set_index('dt')
        agg = df.resample('W-FRI').agg({
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum',
        }).dropna(subset=['open', 'close'])
        out = []
        for dt, row in agg.iterrows():
            out.append({
                'datetime': dt.strftime('%Y-%m-%d'),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
            })
        return out
    except Exception as e:
        print(f"    周线合成失败: {e}")
        return []


# === 信号分析 ===

def analyze_symbol(bars, min_bars=50, tf_id="daily", use_gates=True):
    """
    对单个品种单一级别进行分型版分析
    use_gates=True 时按级别闸门过滤（15m 不发信号；1h 只认三买；日/周线带年龄距离闸门）
    返回: list of {kind, price, stop_loss, dt, idx, age}  或 []
    """
    if not bars or len(bars) < min_bars:
        return []

    df = bars_to_dataframe(bars)
    if df is None or len(df) < min_bars:
        return []

    try:
        df = calculate_structure_indicators(df)
        signals = detect_structure_signals(df)
    except Exception as e:
        print(f"    分型版分析异常: {e}")
        return []

    if not signals:
        return []

    # 级别闸门（15m 直接返回空）
    if use_gates:
        signals = apply_level_gates(df, signals, tf_id)
        if not signals:
            return []

    n = len(df)
    recent = []
    for idx, sig_type, stop_loss, struct_level in signals:
        recent.append({
            'kind': sig_type,
            'price': round(float(df.loc[idx, 'close']), 4),
            'stop_loss': round(float(stop_loss), 4),
            'dt': str(df.loc[idx, 'timestamp']),
            'idx': int(idx),
            'age': int(n - 1 - idx),
        })
    return recent


def _build_fractal_messages(signals):
    """
    将原始信号转为 [(title, content), ...] 格式
    每个信号一条消息，标题包含级别
    """
    if not signals:
        return []

    messages = []
    for sig in signals:
        tf = sig['timeframe']
        kind = sig['kind']
        kind_cn = SIGNAL_CN.get(kind, kind)
        name = sig['name']
        symbol = sig['symbol']
        price = sig['price']
        stop_loss = sig['stop_loss']

        # 买/卖方向
        is_buy = kind in ('B1', 'B2', 'B3')
        emoji = '🟢' if is_buy else '🔴'
        signal_key = f"{emoji}{kind_cn}"

        title = f"缠论分型版({tf}){signal_key}"
        dt = sig.get('dt', '')
        now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"**品种**: {name}({symbol})\n"
        line += f"**周期**: {tf}\n"
        line += f"**信号**: {kind_cn}({kind})\n"
        line += f"**价格**: {price:.2f}\n"
        line += f"**止损**: {stop_loss:.2f}\n"
        if dt:
            line += f"**K线时间**: {dt}\n"
        line += f"**分析时间**: {now_ts}"
        messages.append((title, line))

    return messages


# === 增量信号过滤 ===

def _filter_incremental(signals, state):
    """
    增量更新：同品种+同信号类型+同级别，只推最新位置的信号。
    用 bar idx 去重 — idx 不更新就重复推同一位置信号。
    状态只存每个 key 的最新 idx，不会无限膨胀。
    """
    last_idx_map = state.get("last_signal_idx", {})
    filtered = []

    for sig in signals:
        key = f"{sig['symbol']}|{sig['kind']}|{sig['timeframe']}"
        cur_idx = sig['idx']
        last_idx = last_idx_map.get(key, -1)

        # 同一位置或更老的信号 → 跳过
        if cur_idx <= last_idx:
            continue

        filtered.append(sig)
        last_idx_map[key] = cur_idx

    state["last_signal_idx"] = last_idx_map
    return filtered


# === 策略接口 ===

def init_chan_fractal():
    """
    初始化缠论分型版策略
    返回: (strategies_dict, state)
    strategies_dict: {symbol: {"name": str, "category": str}}
    """
    state = load_state()

    strategies = {}
    for symbol, name, category in ALL_SYMBOLS:
        strategies[symbol] = {"name": name, "category": category}

    return (strategies, state)


def process_cycle(data_cache, strategies, state):
    """
    执行一个周期的分型版策略分析
    信号存入 _latest_signals，返回 []
    """
    global _latest_signals
    write_heartbeat()
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n[{ts}] 缠论分型版策略开始处理...")

    raw_signals = []

    for tf_id, cache_key, tf_label in TIMEFRAMES:
        print(f"  --- {tf_label} ({len(ALL_SYMBOLS)}个品种) ---")

        # 15分钟只做结构参考，不发交易信号（远程口径）
        if tf_id == "15m":
            print(f"    {tf_label}: 仅结构参考，不发交易信号")
            continue

        for symbol, name, category in ALL_SYMBOLS:
            # 夜盘跳过 ETF（A-share ETF 不在夜盘交易，数据过时）
            if _night_session and category == "ETF":
                continue
            try:
                bars = data_cache.get(cache_key, symbol)
                if not bars:
                    continue
                if tf_id == "weekly":
                    bars = resample_to_weekly(bars)
                if not bars:
                    continue

                # 各级别最少需要的K线数量
                min_bars = {"15m": 80, "1h": 60, "daily": 50, "weekly": 60}.get(tf_id, 50)
                sigs = analyze_symbol(bars, min_bars=min_bars, tf_id=tf_id)

                # 只推新鲜形成的点；更老的点仍保留在 get_views 里判方向
                fresh = [s for s in sigs if s.get('age', 999) <= NOTIFY_MAX_AGE]

                for s in fresh:
                    s['symbol'] = symbol
                    s['name'] = name
                    s['timeframe'] = tf_label

                if fresh:
                    for s in fresh:
                        kind_cn = SIGNAL_CN.get(s['kind'], s['kind'])
                        print(f"    {name}({symbol}) [{tf_label}]: {kind_cn} @ {s['price']:.2f} "
                              f"({s.get('age', 0)}根前)")
                    raw_signals.extend(fresh)

            except Exception as e:
                print(f"    {name}({symbol}) [{tf_label}]: 错误 - {e}")
                continue

    # 增量过滤：只推新位置信号，不再用时间冷却
    filtered = _filter_incremental(raw_signals, state)
    save_state(state)

    # 构建消息
    _latest_signals = _build_fractal_messages(filtered)

    total = len(_latest_signals)
    if total > 0:
        print(f"  缠论分型版: {total} 条信号（过滤前 {len(raw_signals)} 条）")
    else:
        print(f"  缠论分型版: 无新信号")

    return []


def get_views(data_cache, strategies, state):
    """
    获取所有品种的当前观点，用于多策略汇总
    返回: {symbol: {"name", "direction", "reason", "timeframe", "sub_directions"}}
    direction: 1=看多, -1=看空, 0=中性, None=本轮跳过
    sub_directions: {tf_label: direction} 各级别独立方向，供报告展开用
    """
    views = {}

    for symbol, info in strategies.items():
        name = info["name"]
        category = info.get("category", "")

        # 夜盘跳过 ETF（A-share ETF 不在夜盘交易）
        if _night_session and category == "ETF":
            continue

        reason_parts = []
        direction_sum = 0
        sub_directions = {}  # 各级别独立方向

        for tf_id, cache_key, tf_label in TIMEFRAMES:
            try:
                bars = data_cache.get(cache_key, symbol)
                if not bars or len(bars) < 50:
                    continue
                if tf_id == "weekly":
                    bars = resample_to_weekly(bars)
                if not bars:
                    continue

                min_bars = {"15m": 80, "1h": 60, "daily": 50, "weekly": 60}.get(tf_id, 50)
                # 15分钟只做结构参考：不过交易闸门，也不计入总方向
                is_ref = (tf_id == "15m")
                sigs = analyze_symbol(bars, min_bars=min_bars, tf_id=tf_id,
                                      use_gates=not is_ref)

                if not sigs:
                    reason_parts.append(f"{tf_label}:观望")
                    sub_directions[tf_label] = 0
                    continue

                # 最新信号方向
                last_sig = sigs[-1]
                kind = last_sig['kind']
                kind_cn = SIGNAL_CN.get(kind, kind)

                if kind in ('B1', 'B2', 'B3'):
                    direction = 1
                elif kind in ('S1', 'S2', 'S3'):
                    direction = -1
                else:
                    direction = 0

                if is_ref:
                    reason_parts.append(f"{tf_label}:{kind_cn}(结构参考)")
                else:
                    direction_sum += direction
                    reason_parts.append(f"{tf_label}:{kind_cn}")

                sub_directions[tf_label] = direction

            except Exception:
                continue

        if not reason_parts:
            views[symbol] = {"name": name, "direction": 0, "reason": "数据不足",
                             "timeframe": "分型版", "sub_directions": {}}
            continue

        # 汇总方向
        if direction_sum > 0:
            direction = 1
        elif direction_sum < 0:
            direction = -1
        else:
            direction = 0

        reason = " | ".join(reason_parts)
        views[symbol] = {
            "name": name,
            "direction": direction,
            "reason": reason,
            "timeframe": "分型版(15m参考+1h+日线+周线)",
            "sub_directions": sub_directions,
        }

    return views
