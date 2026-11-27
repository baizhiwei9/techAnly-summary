#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易日解析（非交易日自动回退到最近交易日）
================================================
背景：2026-09-25 中秋节休市，各任务的取数/报告在非交易日拿到空数据 → 被误判为
「数据异常」。正确行为：**非交易日就用最近一个交易日的数据**，报告里明确标注。

提供：
    resolve(date=None)  -> (trading_date8, is_trade_day, days_dict)
        date 为交易日       → 原样返回
        date 为非交易日     → 回退到 <= date 的最近交易日
    is_open(date)       -> (bool, days_dict)
    prev_trade(date)    -> <= date 的最近交易日（含当天，若当天开市）

命令行：
    python3 strategies/trade_day.py                # {"today": "...", "resolved": "...", "is_open": false}
    python3 strategies/trade_day.py 20260925
    python3 strategies/trade_day.py --fmt d8       # 只打印 resolved 的 YYYYMMDD
    python3 strategies/trade_day.py --fmt d        # 只打印 resolved 的 YYYY-MM-DD
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import json
import datetime
import socket

# 容器 IPv6 不通 → 强制 IPv4（必须在任何网络请求前生效）
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET]

BASE = '/root/.openclaw/workspace'
CACHE = os.path.join(BASE, '.cache', 'trade_cal.json')
FMT = '%Y%m%d'


def d8(date=None):
    """统一成 YYYYMMDD 字符串"""
    if date is None:
        return datetime.date.today().strftime(FMT)
    s = str(date).strip().replace('-', '').replace('/', '')
    return s


def d10(date=None):
    s = d8(date)
    return f'{s[:4]}-{s[4:6]}-{s[6:]}'


def _fetch_cal():
    """拉取 SSE 交易日历（前后各扩一年），带磁盘缓存"""
    try:
        sys.path.insert(0, os.path.join(BASE, 'bin'))
        from tushare_init import ts_call
        start = (datetime.date.today() - datetime.timedelta(days=900)).strftime(FMT)
        end = (datetime.date.today() + datetime.timedelta(days=400)).strftime(FMT)
        df = ts_call('trade_cal', exchange='SSE', start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return None
        days = {str(r['cal_date']): int(r['is_open']) for _, r in df.iterrows()}
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            json.dump({'from': start, 'to': end, 'days': days,
                       'ts': datetime.datetime.now().isoformat()},
                      open(CACHE, 'w'), ensure_ascii=False)
        except Exception:
            pass
        return days
    except Exception:
        return None


_CAL = None


def cal_days(force=False):
    """交易日历 {YYYYMMDD: 0/1}；缓存覆盖当天时直接用缓存"""
    global _CAL
    if _CAL is not None and not force:
        return _CAL
    today = datetime.date.today().strftime(FMT)
    days = None
    if not force:
        try:
            c = json.load(open(CACHE, encoding='utf-8'))
            if c.get('from', '') <= today <= c.get('to', '') and c['days'].get(today) is not None:
                days = c['days']
        except Exception:
            days = None
    if days is None:
        days = _fetch_cal()
    if not days:
        # 拿不到日历时兜底：按工作日粗判（周一~周五算开市）
        days = {}
    _CAL = days
    return days


def is_open(date=None, days=None):
    """返回 (是否交易日, 日历dict)。日历缺失时按工作日兜底。"""
    s = d8(date)
    days = days if days is not None else cal_days()
    v = days.get(s)
    if v is None:
        try:
            wd = datetime.datetime.strptime(s, FMT).weekday()
            return wd < 5, days
        except Exception:
            return True, days
    return bool(v), days


def prev_trade(date=None, days=None):
    """<= date 的最近交易日（若当天开市则返回当天）"""
    s = d8(date)
    days = days if days is not None else cal_days()
    ok, days = is_open(s, days)
    if ok:
        return s
    opens = sorted(k for k, v in days.items() if v == 1 and k <= s)
    return opens[-1] if opens else s


def resolve(date=None, days=None):
    """核心入口 → (交易日YYYYMMDD, 请求日是否本身就是交易日, 说明文字)

    说明文字：交易日为空串；非交易日为「今日（X）休市，数据为最近交易日 Y」。
    需要日历时用 cal_days()。
    """
    s = d8(date)
    days = days if days is not None else cal_days()
    ok, days = is_open(s, days)
    if ok:
        return s, True, ''
    r = prev_trade(s, days)
    return r, False, f'今日（{d10(s)}）休市，数据为最近交易日 {d10(r)}'


def note(date=None):
    """给报告用的一句说明；交易日返回空字符串"""
    return resolve(date)[2]


def main():
    args = [a for a in sys.argv[1:]]
    fmt = ''
    if '--fmt' in args:
        i = args.index('--fmt')
        fmt = args[i + 1] if len(args) > i + 1 else ''
        del args[i:i + 2]
    req = args[0] if args else None
    s = d8(req)
    r, ok, _n = resolve(s)
    if fmt == 'd8':
        print(r)
        return
    if fmt == 'd':
        print(d10(r))
        return
    print(json.dumps({'requested': s, 'requested_date': d10(s),
                      'resolved': r, 'resolved_date': d10(r),
                      'is_open': ok, 'note': note(s)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
