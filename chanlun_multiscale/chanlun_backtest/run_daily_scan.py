"""
缠论结构版每日信号扫描
覆盖: 宽基ETF + 宽基指数 + 有色金属指数
数据源: akshare
输出: 飞书通知
"""
import sys
import os
import json
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chanlun_backtest.structure_indicators import calculate_structure_indicators
from chanlun_backtest.structure_signals import detect_structure_signals


# === 监控标的 ===
WATCHLIST = {
    'etf_daily': [
        ('510300', '沪深300ETF', 'stock_zh_a_hist'),
        ('510050', '上证50ETF', 'stock_zh_a_hist'),
        ('510500', '中证500ETF', 'stock_zh_a_hist'),
        ('159915', '创业板ETF', 'stock_zh_a_hist'),
    ],
    'etf_15min': [
        ('510300', '沪深300ETF', 'stock_zh_a_hist_min_em'),
        ('510050', '上证50ETF', 'stock_zh_a_hist_min_em'),
    ],
    'index_daily': [
        ('000300', '沪深300指数', 'stock_zh_index_daily'),
        ('000016', '上证50指数', 'stock_zh_index_daily'),
        ('000905', '中证500指数', 'stock_zh_index_daily'),
        ('000819', '有色金属指数', 'stock_zh_index_daily'),
    ],
}


def fetch_data_akshare(symbol, name, func_name, period='daily'):
    """通过 akshare 获取数据"""
    import akshare as ak
    import pandas as pd
    
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=365 if period=='daily' else 30)).strftime('%Y%m%d')
    
    try:
        if func_name == 'stock_zh_a_hist':
            df = ak.stock_zh_a_hist(
                symbol=symbol, period=period,
                start_date=start_date, end_date=end_date, adjust='qfq'
            )
            df = df.rename(columns={'日期':'timestamp','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume'})
        elif func_name == 'stock_zh_a_hist_min_em':
            df = ak.stock_zh_a_hist_min_em(
                symbol=symbol, period=period,
                start_date=f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]} 09:30:00",
                end_date=f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]} 15:00:00",
                adjust='qfq'
            )
            df = df.rename(columns={'时间':'timestamp','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume'})
        elif func_name == 'stock_zh_index_daily':
            df = ak.stock_zh_index_daily(symbol=f"sh{symbol}")
            # 过滤日期范围
            df['timestamp'] = pd.to_datetime(df['date'])
            mask = (df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)
            df = df[mask].copy()
            df = df.rename(columns={'open':'open','high':'high','low':'low','close':'close','volume':'volume'})
        else:
            return None
    except Exception as e:
        print(f"  [{name}] 获取失败: {e}")
        return None
    
    if df is None or df.empty:
        return None
    
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df[['timestamp','open','high','low','close','volume']].reset_index(drop=True)
    for c in ['open','high','low','close','volume']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['close'])
    return df


def scan_symbol(symbol, name, func_name, period='daily', level='日线'):
    """扫描单个标的"""
    df = fetch_data_akshare(symbol, name, func_name, period)
    if df is None or len(df) < 50:
        return [], f"{name}: 数据不足({len(df) if df is not None else 0}根)"
    
    # 计算结构指标
    df = calculate_structure_indicators(df)
    
    # 检测信号
    signals = detect_structure_signals(df)
    
    if not signals:
        return [], None
    
    # 只保留最近10根K线内的信号
    recent = [(idx, sig, sl, struct) for idx, sig, sl, struct in signals if idx >= len(df) - 10]
    if not recent:
        return [], None
    
    results = []
    for idx, sig, sl, struct in recent:
        price = df.loc[idx, 'close']
        dt = df.loc[idx, 'timestamp'].strftime('%Y-%m-%d %H:%M' if period != 'daily' else '%Y-%m-%d')
        results.append({
            'symbol': symbol, 'name': name, 'level': level,
            'kind': sig, 'price': round(float(price), 4),
            'dt': dt, 'stop_loss': round(float(sl), 4),
        })
    return results, None


def scan_all():
    """扫描所有标的"""
    all_signals = []
    logs = []
    
    for code, name, func in WATCHLIST['etf_daily'] + WATCHLIST['index_daily']:
        sigs, err = scan_symbol(code, name, func, period='daily', level='日线')
        all_signals.extend(sigs)
        if err: logs.append(err)
    
    for code, name, func in WATCHLIST['etf_15min']:
        sigs, err = scan_symbol(code, name, func, period='15', level='15分钟')
        all_signals.extend(sigs)
        if err: logs.append(err)
    
    return all_signals, logs


def format_message(signals, logs):
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = [f"📊 缠论结构版每日信号扫描  {today}\n"]
    if not signals:
        lines.append("✅ 今日无新交易信号")
    else:
        lines.append(f"🔔 发现 {len(signals)} 个交易信号:\n")
        for s in signals:
            emoji = '🟢' if s['kind'] in ('B1','B2','B3') else ''
            kind_name = {'B1':'一买','B2':'二买','B3':'三买','S1':'一卖','S2':'二卖','S3':'三卖'}
            lines.append(f"  {emoji} [{s['level']}] {s['name']}  {kind_name.get(s['kind'],s['kind'])} @ {s['price']:.4f}  止损: {s['stop_loss']:.4f}")
    if logs:
        lines.append(f"\n⚠️ 日志:")
        for l in logs: lines.append(f"  - {l}")
    return '\n'.join(lines)


def main():
    print("=" * 60)
    print(f"  缠论结构版每日扫描  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    signals, logs = scan_all()
    msg = format_message(signals, logs)
    print(msg)
    
    os.makedirs('output', exist_ok=True)
    result = {
        'timestamp': datetime.now().isoformat(),
        'signal_count': len(signals),
        'signals': signals,
        'logs': logs
    }
    with open('output/chan_daily_signal.json', 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    webhook = os.environ.get('FEISHU_WEBHOOK', '')
    if webhook:
        import urllib.request
        payload = json.dumps({"msg_type":"text","content":{"text":msg}}).encode()
        try:
            req = urllib.request.Request(webhook, data=payload, headers={'Content-Type':'application/json'})
            urllib.request.urlopen(req, timeout=10)
            print("\n[飞书] 发送成功")
        except Exception as e:
            print(f"\n[飞书] 发送失败: {e}")
    else:
        print("\n[飞书] Webhook未配置")
    print(f"\n结果: output/chan_daily_signal.json")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='缠论结构版每日信号扫描')
    parser.add_argument('--output', type=str, default='output', help='输出目录')
    args = parser.parse_args()
    main()
