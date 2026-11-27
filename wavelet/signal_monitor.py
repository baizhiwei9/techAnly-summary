"""
小波策略实盘信号监控
每天收盘后运行，扫描ETF和期货的交易信号，推送到飞书群
"""

import sys
import os

# 交易日检查是部署机的可选依赖；公开库 clone 后没有这个模块，跳过即可（crontab 只在交易日跑）
try:
    from trading_day_check import check_trading_day
    check_trading_day()
except ImportError:
    pass

import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'strategies'))
from wavelet_portfolio_v4 import build_sig_single, causal_wt, chan_bottom_strength
from wavelet_futures_v5 import build_sig_futures_v5, backtest_futures_v5
# 1h级别已停用: from wavelet_futures_1h import build_sig_futures_1h

# ===== 配置 =====
# 飞书群 ID 只走环境变量，公开库不放真实 chat_id（部署机在 crontab/env 里配置）
FEISHU_CHAT_ID = os.environ.get('WAVELET_FEISHU_CHAT_ID', '')
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
STATE_DIR = os.path.join(os.path.dirname(__file__), 'state')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

# ETF配置
ETF_CONFIG = {
    '510300': {'name': '沪深300ETF', 'market': 'sh'},
    '159915': {'name': '创业板ETF', 'market': 'sz'},
    '510500': {'name': '中证500ETF', 'market': 'sh'},
}

# 期货配置（白银AG）
FUTURES_CONFIG = {
    'AG': {
        'name': '白银期货',
        'exchange': 'SHFE',
        'contract_size': 15,  # 15kg/手
        'margin_rate': 0.1,
    }
}

# 策略参数
ETF_PARAMS = {
    'weekly_w': 30,
    'wt_w': 100,
    'wavelet': 'db4',
    'level': 4,
    'weekly_level': 3,
    'exit_ma': 40,
    'confirm_days': 2,
    'gate': 'flator_close',
    'pos_full': 0.95,
    'second_buy_pos': 0.5,
    'retrace_min': 0.03,
    'strong_only': False,
    'exit_m20': -0.05,
    'min_hold': 2,
}

FUTURES_PARAMS = {
    'atr_mult': 2.0,
    'exit_ma': 40,
    'confirm_days': 2,
    'pos_long': 0.10,
    'pos_short': 0.05,
    'sb_long': 0.08,
    'sb_short': 0.0,
    'short_strong_only': True,
    'weekly_w': 30,
}


# ===== 数据获取 =====
def fetch_etf_data(code, market='sh', days=500):
    """获取ETF日线数据（优先本地parquet，否则AKShare）"""
    local_file = os.path.join(DATA_DIR, f'etf_{code}.parquet')
    
    # 检查本地文件是否最新
    if os.path.exists(local_file):
        try:
            df = pd.read_parquet(local_file)
            last_date = df.index[-1]
            # 如果本地数据已是最新（最后一个交易日），直接用
            today = pd.Timestamp.now().normalize()
            if (today - last_date).days < 3:  # 3天内算新鲜
                return df
        except:
            pass
    
    # 拉取新数据
    try:
        import akshare as ak
        symbol = f'{market}{code}'
        df = ak.fund_etf_hist_sina(symbol=symbol)
        if df is not None and len(df) > 0:
            df.index = pd.to_datetime(df['date'])
            df = df[['open', 'high', 'low', 'close', 'volume']].sort_index()
            df.columns = ['open', 'high', 'low', 'close', 'volume']
            # 保存本地
            df.to_parquet(local_file)
            return df
    except Exception as e:
        print(f'  [WARN] AKShare拉取{code}失败: {e}')
    
    # 降级：读本地旧数据
    if os.path.exists(local_file):
        df = pd.read_parquet(local_file)
        print(f'  [INFO] 使用本地缓存数据，最新到 {df.index[-1].strftime("%Y-%m-%d")}')
        return df
    
    raise RuntimeError(f'无法获取{code}数据')


def fetch_futures_data(symbol='AG', days=800):
    """获取期货主力连续日线"""
    local_file = os.path.join(DATA_DIR, f'futures_{symbol}.parquet')
    
    if os.path.exists(local_file):
        try:
            df = pd.read_parquet(local_file)
            today = pd.Timestamp.now().normalize()
            if (today - df.index[-1]).days < 3:
                return df
        except:
            pass
    
    # 用AKShare拉取
    try:
        import akshare as ak
        # 白银主力
        df = ak.futures_zh_daily_sina(symbol=f'{symbol}0')
        if df is not None and len(df) > 0:
            df.index = pd.to_datetime(df['date'])
            df = df[['open', 'high', 'low', 'close', 'volume', 'hold']].sort_index()
            df.columns = ['open', 'high', 'low', 'close', 'volume', 'open_interest']
            df.to_parquet(local_file)
            return df
    except Exception as e:
        print(f'  [WARN] AKShare拉取{symbol}期货失败: {e}')
    
    if os.path.exists(local_file):
        df = pd.read_parquet(local_file)
        print(f'  [INFO] 使用本地期货缓存，最新到 {df.index[-1].strftime("%Y-%m-%d")}')
        return df
    
    raise RuntimeError(f'无法获取{symbol}期货数据')


def fetch_futures_1h_data(symbol='AG', days=200):
    """获取期货1小时K线"""
    local_file = os.path.join(DATA_DIR, f'futures_{symbol}_1h.parquet')

    # AKShare 拉取1小时数据
    try:
        import akshare as ak
        df = ak.futures_zh_minute_sina(symbol=f'{symbol}0', period='60')
        if df is not None and len(df) > 0:
            df.index = pd.to_datetime(df['datetime'])
            df = df[['open', 'high', 'low', 'close', 'volume']].sort_index()
            df.to_parquet(local_file)
            return df
    except Exception as e:
        print(f'  [WARN] AKShare拉取{symbol} 1h数据失败: {e}')

    if os.path.exists(local_file):
        df = pd.read_parquet(local_file)
        print(f'  [INFO] 使用本地1h缓存，最新到 {df.index[-1]}')
        return df

    raise RuntimeError(f'无法获取{symbol} 1h数据')


# ===== 信号检测 =====
def detect_etf_signals(df, params, code, name):
    """检测ETF信号变化"""
    close = df['close'].values
    low = df['low'].values
    high = df['high'].values
    dates = df.index
    
    sig_result = build_sig_single(close=close, low=low, high=high, dates=dates, **params)
    sig = sig_result['sig']
    
    # 找最近的信号变化点
    changes = []
    for i in range(1, len(sig)):
        if sig[i] != sig[i-1] and not np.isnan(sig[i]):
            changes.append({
                'idx': i,
                'date': df.index[i],
                'from_pos': float(sig[i-1]) if not np.isnan(sig[i-1]) else 0,
                'to_pos': float(sig[i]) if not np.isnan(sig[i]) else 0,
                'close': float(df['close'].iloc[i]),
            })
    
    # 分类
    latest = changes[-1] if changes else None
    
    return {
        'code': code,
        'name': name,
        'all_changes': changes,
        'latest_change': latest,
        'current_pos': float(sig[-1]) if not np.isnan(sig[-1]) else 0,
        'last_close': float(df['close'].iloc[-1]),
        'last_date': df.index[-1],
    }


def detect_futures_signals(df, params, symbol, name):
    """检测期货信号变化"""
    sig_result = build_sig_futures_v5(df, **params)
    sig = sig_result['sig']
    stop_price = sig_result.get('stop_price', np.zeros(len(sig)))
    
    changes = []
    for i in range(1, len(sig)):
        if sig[i] != sig[i-1]:
            direction = 'long' if sig[i] > 0 else ('short' if sig[i] < 0 else 'flat')
            prev_dir = 'long' if sig[i-1] > 0 else ('short' if sig[i-1] < 0 else 'flat')
            changes.append({
                'idx': i,
                'date': df.index[i],
                'from_sig': float(sig[i-1]),
                'to_sig': float(sig[i]),
                'from_dir': prev_dir,
                'to_dir': direction,
                'close': float(df['close'].iloc[i]),
                'stop': float(stop_price[i]) if stop_price[i] > 0 else 0,
            })
    
    latest = changes[-1] if changes else None
    cur_sig = float(sig[-1])
    cur_dir = 'long' if cur_sig > 0 else ('short' if cur_sig < 0 else 'flat')
    
    return {
        'symbol': symbol,
        'name': name,
        'all_changes': changes,
        'latest_change': latest,
        'current_sig': cur_sig,
        'current_dir': cur_dir,
        'current_stop': float(stop_price[-1]) if stop_price[-1] > 0 else 0,
        'last_close': float(df['close'].iloc[-1]),
        'last_date': df.index[-1],
    }


# ===== 状态管理 =====
def load_state():
    """加载上次运行状态"""
    state_file = os.path.join(STATE_DIR, 'signal_state.json')
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'etf': {}, 'futures': {}, 'last_run': None}


def save_state(state):
    """保存运行状态"""
    state_file = os.path.join(STATE_DIR, 'signal_state.json')
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2, default=str)


def is_new_signal(date_str, state, key):
    """判断是否是新信号（根据日期和信号值去重）"""
    last_info = state.get(key, {})
    last_date = last_info.get('last_signal_date')
    return last_date != date_str


# ===== 飞书推送 =====
def send_feishu_message(chat_id, content):
    """发送飞书消息（卡片）"""
    import subprocess

    if not chat_id:
        return False, "未设置 WAVELET_FEISHU_CHAT_ID，跳过推送"

    cmd = [
        'lark-cli', 'api', 'POST', '/open-apis/im/v1/messages',
        '--params', '{"receive_id_type":"chat_id"}',
        '--data', json.dumps({
            'receive_id': chat_id,
            'msg_type': 'interactive',
            'content': json.dumps(content, ensure_ascii=False),
        }, ensure_ascii=False),
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        resp = json.loads(result.stdout) if result.stdout else {}
        if resp.get('ok'):
            return True, resp
        else:
            err = resp.get('error', {}).get('message', 'unknown')
            print(f'  [WARN] 卡片发送失败: {err}，尝试文本消息')
            return send_feishu_text(chat_id, content.get('header', {}).get('title', {}).get('content', '小波策略信号'))
    except Exception as e:
        print(f'  [ERROR] 飞书推送异常: {e}')
        return False, str(e)


def send_feishu_text(chat_id, text):
    """发送纯文本消息"""
    import subprocess
    cmd = [
        'lark-cli', 'api', 'POST', '/open-apis/im/v1/messages',
        '--params', '{"receive_id_type":"chat_id"}',
        '--data', json.dumps({
            'receive_id': chat_id,
            'msg_type': 'text',
            'content': json.dumps({'text': text}, ensure_ascii=False),
        }, ensure_ascii=False),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        resp = json.loads(result.stdout) if result.stdout else {}
        return resp.get('ok', False), resp
    except Exception as e:
        return False, str(e)


def build_etf_signal_card(info, action, price):
    """构建ETF信号卡片"""
    direction = '📈 买入建仓' if action == 'buy' else ('📊 加仓' if action == 'add' else '📉 卖出平仓')
    color = 'green' if '买' in direction else ('blue' if '加' in direction else 'red')
    
    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': f'小波策略 · ETF信号 - {info["name"]}'},
            'template': color,
        },
        'elements': [
            {
                'tag': 'div',
                'fields': [
                    {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**方向**\n{direction}'}},
                    {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**价格**\n{price:.3f}'}},
                    {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**日期**\n{info["last_date"].strftime("%Y-%m-%d")}'}},
                    {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**仓位**\n{info["current_pos"]*100:.0f}%'}},
                ]
            },
            {'tag': 'hr'},
            {
                'tag': 'note',
                'elements': [
                    {'tag': 'plain_text', 'content': '周线过滤+日线底分型+MA40二次确认 · v4策略'}
                ]
            }
        ]
    }


def build_futures_signal_card(info, action, price):
    """构建期货信号卡片"""
    dir_map = {
        'long_open': ('🚀 做多开仓', 'green'),
        'long_add': ('📊 做多加仓', 'blue'),
        'long_close': ('💰 做多平仓', 'orange'),
        'short_open': ('⬇️ 做空开仓', 'red'),
        'short_add': ('📉 做空加仓', 'purple'),
        'short_close': ('💸 做空平仓', 'yellow'),
    }
    label, color = dir_map.get(action, ('信号', 'blue'))
    
    elements = [
        {
            'tag': 'div',
            'fields': [
                {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**方向**\n{label}'}},
                {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**价格**\n{price:.0f}'}},
                {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**日期**\n{info["last_date"].strftime("%Y-%m-%d")}'}},
                {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**信号强度**\n{abs(info["current_sig"])*100:.0f}%'}},
            ]
        },
    ]
    
    if info.get('current_stop', 0) > 0:
        elements.append({
            'tag': 'div',
            'text': {'tag': 'lark_md', 'content': f'🎯 **止损位**: {info["current_stop"]:.0f}'}
        })
    
    elements += [
        {'tag': 'hr'},
        {
            'tag': 'note',
            'elements': [
                {'tag': 'plain_text', 'content': '小波v5策略 · 2xATR止损+MA40确认+周线过滤 · 10%保证金'}
            ]
        }
    ]
    
    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': f'小波策略 · 期货信号 - {info["name"]}'},
            'template': color,
        },
        'elements': elements,
    }


def build_status_summary(state):
    """构建持仓汇总卡片 — 多/空分栏格式，每个策略按条目列"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lines = []
    lines.append(f'**时间**: {ts}')
    lines.append('')

    # 收集所有看多/看空条目
    bull_items = []  # [(strategy_label, instrument, reason)]
    bear_items = []

    futures_section = state.get('futures', {})

    # ETF
    for code, cfg in ETF_CONFIG.items():
        key = f'etf_{code}'
        info = futures_section.get(key, state.get('etf', {}).get(key, {}))
        pos = info.get('current_pos', 0)
        if pos != 0:
            entry = {
                'strategy': '小波多尺度',
                'timeframe': '日线',
                'name': cfg['name'],
                'price': info.get('last_price', 0),
            }
            if pos > 0:
                bull_items.append(entry)
            else:
                bear_items.append(entry)

    # 期货日线
    for symbol, cfg in FUTURES_CONFIG.items():
        key = f'futures_{symbol}'
        info = futures_section.get(key, {})
        cur_dir = info.get('current_dir', 'flat')
        if cur_dir != 'flat':
            entry = {
                'strategy': '小波多尺度',
                'timeframe': '日线',
                'name': cfg['name'],
                'price': info.get('last_price', 0),
                'stop': info.get('current_stop', 0),
            }
            if cur_dir == 'long':
                bull_items.append(entry)
            else:
                bear_items.append(entry)

    # 期货1h
    for symbol, cfg in FUTURES_CONFIG.items():
        key_1h = f'futures_1h_{symbol}'
        info_1h = futures_section.get(key_1h, {})
        cur_dir_1h = info_1h.get('current_dir', 'flat')
        if cur_dir_1h != 'flat':
            entry = {
                'strategy': '小波多尺度',
                'timeframe': '1h',
                'name': cfg['name'],
                'price': info_1h.get('last_price', 0),
                'stop': info_1h.get('current_stop', 0),
                'last_bar': info_1h.get('last_bar', ''),
            }
            if cur_dir_1h == 'long':
                bull_items.append(entry)
            else:
                bear_items.append(entry)

    # 冲突集合：同一品种名既有多又有空
    bull_names = {e['name'] for e in bull_items}
    bear_names = {e['name'] for e in bear_items}
    conflict_names = bull_names & bear_names

    # 输出看多栏
    if bull_items:
        lines.append(f'**🟢 看多** ({len(bull_items)}个)')
        lines.append('')
        for entry in bull_items:
            stop_str = f' 止损{entry["stop"]:.0f}' if entry.get('stop', 0) > 0 else ''
            bar_str = f' ({entry["last_bar"]})' if entry.get('last_bar') else ''
            star = ' *' if entry['name'] in conflict_names else ''
            lines.append(f'  - ✅ {entry["strategy"]}({entry["timeframe"]}): {entry["name"]} @ {entry["price"]:.0f}{stop_str}{bar_str}{star}')
        lines.append('')

    # 输出看空栏
    if bear_items:
        lines.append(f'**🔴 看空** ({len(bear_items)}个)')
        lines.append('')
        for entry in bear_items:
            stop_str = f' 止损{entry["stop"]:.0f}' if entry.get('stop', 0) > 0 else ''
            bar_str = f' ({entry["last_bar"]})' if entry.get('last_bar') else ''
            star = ' *' if entry['name'] in conflict_names else ''
            lines.append(f'  - ❌ {entry["strategy"]}({entry["timeframe"]}): {entry["name"]} @ {entry["price"]:.0f}{stop_str}{bar_str}{star}')
        lines.append('')

    if conflict_names:
        lines.append(f'*\\* 策略方向分歧（不同级别看多看空不一致，注意仓位控制）*')
        lines.append('')

    if not bull_items and not bear_items:
        lines.append('全部空仓观望')
        lines.append('')

    lines.append('---')
    lines.append('*消息来自于 coze.Claude*')

    content = '\n'.join(lines)

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': '📋 小波策略 · 持仓汇总'},
            'template': 'grey',
        },
        'elements': [
            {
                'tag': 'markdown',
                'content': content,
            }
        ]
    }


def detect_futures_1h_signals(df_1h, df_daily, symbol, name):
    """检测1小时级别期货信号变化"""
    sig_result = build_sig_futures_1h(df_1h, df_daily=df_daily)
    sig = sig_result['sig']
    stop_price = sig_result.get('stop_price', np.zeros(len(sig)))

    changes = []
    for i in range(1, len(sig)):
        if sig[i] != sig[i-1]:
            direction = 'long' if sig[i] > 0 else ('short' if sig[i] < 0 else 'flat')
            prev_dir = 'long' if sig[i-1] > 0 else ('short' if sig[i-1] < 0 else 'flat')
            changes.append({
                'idx': i,
                'bar': str(df_1h.index[i]),
                'from_sig': float(sig[i-1]),
                'to_sig': float(sig[i]),
                'from_dir': prev_dir,
                'to_dir': direction,
                'close': float(df_1h['close'].iloc[i]),
                'stop': float(stop_price[i]) if stop_price[i] > 0 else 0,
            })

    latest = changes[-1] if changes else None
    cur_sig = float(sig[-1])
    cur_dir = 'long' if cur_sig > 0 else ('short' if cur_sig < 0 else 'flat')

    return {
        'symbol': symbol,
        'name': name,
        'all_changes': changes,
        'latest_change': latest,
        'current_sig': cur_sig,
        'current_dir': cur_dir,
        'current_stop': float(stop_price[-1]) if stop_price[-1] > 0 else 0,
        'last_close': float(df_1h['close'].iloc[-1]),
        'last_bar': str(df_1h.index[-1]),
        'last_date': df_1h.index[-1],
        'timeframe': '1h',
    }


def build_futures_1h_signal_card(info, action, price):
    """构建期货1小时级别信号卡片"""
    dir_map = {
        'long_open': ('🚀 做多开仓', 'green'),
        'long_close': ('💰 做多平仓', 'orange'),
        'short_open': ('⬇️ 做空开仓', 'red'),
        'short_close': ('💸 做空平仓', 'yellow'),
        'reverse_short': ('🔄 反手做空', 'purple'),
        'reverse_long': ('🔄 反手做多', 'blue'),
    }
    label, color = dir_map.get(action, ('信号', 'blue'))

    elements = [
        {
            'tag': 'div',
            'fields': [
                {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**方向**\n{label}'}},
                {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**价格**\n{price:.0f}'}},
                {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**时间**\n{info["last_bar"]}'}},
                {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**信号强度**\n{abs(info["current_sig"])*100:.0f}%'}},
            ]
        },
    ]

    if info.get('current_stop', 0) > 0:
        elements.append({
            'tag': 'div',
            'text': {'tag': 'lark_md', 'content': f'🎯 **止损位**: {info["current_stop"]:.0f}'}
        })

    elements += [
        {'tag': 'hr'},
        {
            'tag': 'note',
            'elements': [
                {'tag': 'plain_text', 'content': '小波多尺度1h策略 · 日线趋势过滤+1h分型+ATR止损 · 1.5xATR'}
            ]
        }
    ]

    return {
        'config': {'wide_screen_mode': True},
        'header': {
            'title': {'tag': 'plain_text', 'content': f'小波策略 · 期货1h信号 - {info["name"]}'},
            'template': color,
        },
        'elements': elements,
    }


# ===== 主逻辑 =====
def run_monitor():
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] 小波策略信号监控启动')
    
    state = load_state()
    all_new_signals = []
    
    # ---- ETF扫描 ----
    print('\n=== ETF信号扫描 ===')
    for code, cfg in ETF_CONFIG.items():
        print(f'扫描 {code} ({cfg["name"]})...')
        try:
            df = fetch_etf_data(code, cfg['market'])
            info = detect_etf_signals(df, ETF_PARAMS, code, cfg['name'])

            state_key = f'etf_{code}'
            last_state = state.get('etf', {}).get(state_key, {})
            last_pos = last_state.get('current_pos', 0)
            last_notified_date = last_state.get('last_notified_date', '')

            cur_pos = info['current_pos']
            last_date_str = info['last_date'].strftime('%Y-%m-%d')

            if cur_pos != last_pos:
                # 状态发生了变化（相对于上次运行）
                if cur_pos > last_pos:
                    if last_pos == 0:
                        action = 'buy'
                        action_label = f'📈 买入建仓 {cur_pos*100:.0f}%'
                    else:
                        action = 'add'
                        action_label = f'📊 加仓至 {cur_pos*100:.0f}%'
                else:
                    action = 'sell'
                    action_label = f'📉 卖出平仓 (从{last_pos*100:.0f}%)'

                last_signal_date = info['latest_change']['date'].strftime('%Y-%m-%d') if info['latest_change'] else ''

                print(f'  → {action_label} @ {info["last_close"]:.3f} (信号日期: {last_signal_date})')

                all_new_signals.append({
                    'type': 'etf',
                    'info': info,
                    'action': action,
                    'price': info['last_close'],
                    'signal_date': last_signal_date,
                })

                # 更新状态
                if 'etf' not in state:
                    state['etf'] = {}
                state['etf'][state_key] = {
                    'current_pos': cur_pos,
                    'last_date': last_date_str,
                    'last_price': info['last_close'],
                    'last_notified_date': last_signal_date,
                }
            else:
                print(f'  → 持仓不变 ({cur_pos*100:.0f}%)')

        except Exception as e:
            print(f'  [ERROR] {code} 扫描失败: {e}')
            import traceback
            traceback.print_exc()

    # ---- 期货扫描 ----
    print('\n=== 期货信号扫描 ===')
    for symbol, cfg in FUTURES_CONFIG.items():
        print(f'扫描 {symbol} ({cfg["name"]})...')
        try:
            df = fetch_futures_data(symbol)
            info = detect_futures_signals(df, FUTURES_PARAMS, symbol, cfg['name'])

            state_key = f'futures_{symbol}'
            last_state = state.get('futures', {}).get(state_key, {})
            last_sig = last_state.get('current_sig', 0)
            last_dir = last_state.get('current_dir', 'flat')
            last_notified_date = last_state.get('last_notified_date', '')

            cur_sig = info['current_sig']
            cur_dir = info['current_dir']
            last_date_str = info['last_date'].strftime('%Y-%m-%d')

            if cur_sig != last_sig:
                # 状态发生了变化（相对于上次运行）— 不管信号是哪天产生的，都推送
                action = ''
                if last_dir == 'flat' and cur_dir == 'long':
                    action = 'long_open'
                    action_label = '🚀 做多开仓'
                elif last_dir == 'long' and cur_dir == 'flat':
                    action = 'long_close'
                    action_label = '💰 做多平仓'
                elif last_dir == 'flat' and cur_dir == 'short':
                    action = 'short_open'
                    action_label = '⬇️ 做空开仓'
                elif last_dir == 'short' and cur_dir == 'flat':
                    action = 'short_close'
                    action_label = '💸 做空平仓'
                elif last_dir == 'long' and cur_dir == 'long' and abs(cur_sig) > abs(last_sig):
                    action = 'long_add'
                    action_label = '📊 做多加仓'
                elif last_dir == 'short' and cur_dir == 'short' and abs(cur_sig) > abs(last_sig):
                    action = 'short_add'
                    action_label = '📉 做空加仓'
                else:
                    action = 'change'
                    action_label = f'信号变化 {last_sig:.2f} → {cur_sig:.2f}'

                last_signal_date = info['latest_change']['date'].strftime('%Y-%m-%d') if info['latest_change'] else ''

                print(f'  → {action_label} @ {info["last_close"]:.0f} (信号日期: {last_signal_date})')

                all_new_signals.append({
                    'type': 'futures',
                    'info': info,
                    'action': action,
                    'price': info['last_close'],
                    'signal_date': last_signal_date,
                })

                if 'futures' not in state:
                    state['futures'] = {}
                state['futures'][state_key] = {
                    'current_sig': cur_sig,
                    'current_dir': cur_dir,
                    'last_date': last_date_str,
                    'last_price': info['last_close'],
                    'current_stop': info.get('current_stop', 0),
                    'last_notified_date': last_signal_date,
                }
            else:
                dir_cn = '多头持仓' if cur_dir == 'long' else ('空头持仓' if cur_dir == 'short' else '空仓')
                print(f'  → 状态不变 ({dir_cn}, {abs(cur_sig)*100:.0f}%仓位)')

        except Exception as e:
            print(f'  [ERROR] {symbol} 扫描失败: {e}')
            import traceback
            traceback.print_exc()

    # 1h已停用: # ---- 期货1小时级别扫描 ----
    # 1h已停用: print('\n=== 期货1小时级别信号扫描 ===')
    # 1h已停用: for symbol, cfg in FUTURES_CONFIG.items():
        # 1h已停用: print(f'扫描 {symbol} ({cfg["name"]}) 1h...')
        # 1h已停用: try:
            # 1h已停用: df_1h = fetch_futures_1h_data(symbol)
            # 1h已停用: df_daily = fetch_futures_data(symbol)  # 日线用于趋势过滤
            # 1h已停用: info_1h = detect_futures_1h_signals(df_1h, df_daily, symbol, cfg['name'])

            # 1h已停用: state_key = f'futures_1h_{symbol}'
            # 1h已停用: last_state = state.get('futures', {}).get(state_key, {})
            # 1h已停用: last_sig = last_state.get('current_sig', 0)
            # 1h已停用: last_dir = last_state.get('current_dir', 'flat')

            # 1h已停用: cur_sig = info_1h['current_sig']
            # 1h已停用: cur_dir = info_1h['current_dir']
            # 1h已停用: last_bar_str = str(info_1h['last_bar'])

            # 1h已停用: if cur_sig != last_sig:
                # 1h已停用: action = ''
                # 1h已停用: if last_dir == 'flat' and cur_dir == 'long':
                    # 1h已停用: action = 'long_open'
                    # 1h已停用: action_label = '🚀 做多开仓'
                # 1h已停用: elif last_dir == 'long' and cur_dir == 'flat':
                    # 1h已停用: action = 'long_close'
                    # 1h已停用: action_label = '💰 做多平仓'
                # 1h已停用: elif last_dir == 'flat' and cur_dir == 'short':
                    # 1h已停用: action = 'short_open'
                    # 1h已停用: action_label = '⬇️ 做空开仓'
                # 1h已停用: elif last_dir == 'short' and cur_dir == 'flat':
                    # 1h已停用: action = 'short_close'
                    # 1h已停用: action_label = '💸 做空平仓'
                # 1h已停用: elif last_dir == 'long' and cur_dir == 'short':
                    # 1h已停用: action = 'reverse_short'
                    # 1h已停用: action_label = '🔄 反手做空'
                # 1h已停用: elif last_dir == 'short' and cur_dir == 'long':
                    # 1h已停用: action = 'reverse_long'
                    # 1h已停用: action_label = '🔄 反手做多'
                # 1h已停用: else:
                    # 1h已停用: action = 'change'
                    # 1h已停用: action_label = f'信号变化 {last_sig:.2f} → {cur_sig:.2f}'

                # 1h已停用: print(f'  → 1h [{action_label}] @ {info_1h["last_close"]:.0f} (bar: {last_bar_str})')

                # 1h已停用: all_new_signals.append({
                    # 1h已停用: 'type': 'futures_1h',
                    # 1h已停用: 'info': info_1h,
                    # 1h已停用: 'action': action,
                    # 1h已停用: 'price': info_1h['last_close'],
                    # 1h已停用: 'signal_date': last_bar_str,
                # 1h已停用: })

                # 1h已停用: if 'futures' not in state:
                    # 1h已停用: state['futures'] = {}
                # 1h已停用: state['futures'][state_key] = {
                    # 1h已停用: 'current_sig': cur_sig,
                    # 1h已停用: 'current_dir': cur_dir,
                    # 1h已停用: 'last_bar': last_bar_str,
                    # 1h已停用: 'last_price': info_1h['last_close'],
                    # 1h已停用: 'current_stop': info_1h.get('current_stop', 0),
                # 1h已停用: }
            # 1h已停用: else:
                # 1h已停用: dir_cn = '多头' if cur_dir == 'long' else ('空头' if cur_dir == 'short' else '空仓')
                # 1h已停用: print(f'  → 1h 状态不变 ({dir_cn}, {abs(cur_sig)*100:.0f}%仓位)')

        # 1h已停用: except Exception as e:
            # 1h已停用: print(f'  [ERROR] {symbol} 1h扫描失败: {e}')
            # 1h已停用: import traceback
            # 1h已停用: traceback.print_exc()

    # ---- 推送飞书 ----
    if all_new_signals:
        print(f'\n=== 推送飞书 ({len(all_new_signals)}条新信号) ===')
        for sig in all_new_signals:
            if sig['type'] == 'etf':
                card = build_etf_signal_card(sig['info'], sig['action'], sig['price'])
            # 1h已停用: elif sig['type'] == 'futures_1h':
            #     card = build_futures_1h_signal_card(sig['info'], sig['action'], sig['price'])
            else:
                card = build_futures_signal_card(sig['info'], sig['action'], sig['price'])

            ok, resp = send_feishu_message(FEISHU_CHAT_ID, card)
            if ok:
                print(f'  ✅ {sig["info"].get("name", "")} 信号推送成功')
            else:
                print(f'  ❌ 推送失败: {resp}')
    else:
        print('\n本次无新信号变化')

    # ---- 每次运行都发持仓汇总 ----
    status_card = build_status_summary(state)
    ok, resp = send_feishu_message(FEISHU_CHAT_ID, status_card)
    if ok:
        print(f'  ✅ 持仓汇总推送成功')
    else:
        print(f'  ❌ 汇总推送失败: {resp}')

    # 更新状态
    state['last_run'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_state(state)

    # ---- 导出信号到共享文件（供统一流水线合并）----
    export_signals_for_consensus(state)

    print(f'\n完成。共 {len(all_new_signals)} 条新信号')
    return all_new_signals


def export_signals_for_consensus(state):
    """把小波最新信号导出到共享JSON，供缠论分型版/统一流水线读取合并"""
    export = {}

    # ETF信号
    for code, cfg in ETF_CONFIG.items():
        key = f'etf_{code}'
        info = state.get('etf', {}).get(key, {})
        pos = info.get('current_pos', 0)
        if pos != 0:
            direction = '看多' if pos > 0 else '看空'
            export[cfg['name']] = {
                'symbol': code,
                'name': cfg['name'],
                'direction': direction,
                'strength': abs(pos),
                'price': info.get('last_price', 0),
                'date': info.get('last_date', ''),
                'strategy': '小波多尺度',
            }

    # 期货信号（日线）
    for symbol, cfg in FUTURES_CONFIG.items():
        key = f'futures_{symbol}'
        info = state.get('futures', {}).get(key, {})
        cur_dir = info.get('current_dir', 'flat')
        if cur_dir != 'flat':
            direction = '看多' if cur_dir == 'long' else '看空'
            export[cfg['name']] = {
                'symbol': symbol,
                'name': cfg['name'],
                'direction': direction,
                'strength': abs(info.get('current_sig', 0)),
                'price': info.get('last_price', 0),
                'stop': info.get('current_stop', 0),
                'date': info.get('last_date', ''),
                'strategy': '小波多尺度',
            }

    # 期货信号（1小时）—— 合并到同一品种，标注级别
    for symbol, cfg in FUTURES_CONFIG.items():
        key_1h = f'futures_1h_{symbol}'
        info_1h = state.get('futures', {}).get(key_1h, {})
        cur_dir_1h = info_1h.get('current_dir', 'flat')
        if cur_dir_1h != 'flat':
            name = cfg['name']
            direction_1h = '看多' if cur_dir_1h == 'long' else '看空'
            if name in export:
                # 已有日线信号，追加1h级别
                export[name]['direction_1h'] = direction_1h
                export[name]['strength_1h'] = abs(info_1h.get('current_sig', 0))
                export[name]['timeframe'] = '日线+1h'
            else:
                export[name] = {
                    'symbol': symbol,
                    'name': name,
                    'direction': direction_1h,
                    'strength': abs(info_1h.get('current_sig', 0)),
                    'price': info_1h.get('last_price', 0),
                    'stop': info_1h.get('current_stop', 0),
                    'date': info_1h.get('last_bar', ''),
                    'strategy': '小波多尺度1h',
                    'timeframe': '1h',
                }

    shared_file = os.environ.get('WAVELET_SIGNAL_FILE', '/tmp/wavelet_latest_signals.json')
    try:
        with open(shared_file, 'w') as f:
            json.dump(export, f, ensure_ascii=False, indent=2, default=str)
        print(f'  信号已导出: {shared_file} ({len(export)}个品种)')
    except Exception as e:
        print(f'  [WARN] 导出信号失败: {e}')


if __name__ == '__main__':
    run_monitor()
