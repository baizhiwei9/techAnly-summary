#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时数据校验（2026-10-30 大佬硬要求）

「一定要获取实时数据，不是昨天的数据；如果是昨天的数据就报错，不执行」

做法：用腾讯行情取上证指数(sh000001)的最新行情时间戳，
  要求 ① 行情日期 == 今天（本地 Asia/Shanghai）
       ② 行情时间 >= 09:30（开盘后）
  任一不满足 → 判定「数据非实时」→ 调用方必须中止，不推送。

用法：
  python3 strategies/live_check.py            # 打印 JSON 并返回码（0=实时，9=不是实时）
  from live_check import require_live  # 代码里调用
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, re, sys, json, socket, datetime

_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

SOURCES = [
    ('tencent', 'https://qt.gtimg.cn/q=sh000001'),
    ('tencent', 'http://qt.gtimg.cn/q=sh000001'),
]
PAT = re.compile(r'(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})')


def _fetch(url, timeout=10):
    import requests
    r = requests.get(url, timeout=timeout, headers={'User-Agent': 'Mozilla/5.0',
                                                   'Referer': 'https://gu.qq.com/'})
    r.encoding = 'gbk'
    return r.text or ''


def quote_time():
    """返回 (datetime or None, raw)"""
    last = None
    for name, url in SOURCES:
        try:
            txt = _fetch(url)
            m = PAT.search(txt)
            if m:
                g = [int(x) for x in m.groups()]
                return datetime.datetime(*g), txt[:120]
            last = 'no-timestamp: %s' % txt[:80]
        except Exception as e:
            last = '%s: %r' % (name, e)
    return None, last or 'no source'


def require_live(now=None):
    """返回 dict(ok, quote_dt, today, msg)。ok=False 时必须中止。"""
    now = now or datetime.datetime.now()
    qt, raw = quote_time()
    today = now.strftime('%Y-%m-%d')
    if qt is None:
        return dict(ok=False, quote_dt=None, today=today,
                    msg='拿不到实时行情时间戳（%s）' % str(raw)[:80])
    ok_date = qt.strftime('%Y-%m-%d') == today
    ok_time = (qt.hour, qt.minute) >= (9, 30)
    ok = ok_date and ok_time
    if not ok:
        if not ok_date:
            msg = '行情日期是 %s，不是今天 %s（昨天/过期数据）' % (qt.strftime('%Y-%m-%d'), today)
        else:
            msg = '行情时间 %s 早于 09:30（尚未开盘）' % qt.strftime('%H:%M')
    else:
        msg = '实时行情时间 %s' % qt.strftime('%Y-%m-%d %H:%M:%S')
    return dict(ok=ok, quote_dt=qt.strftime('%Y-%m-%d %H:%M:%S'), today=today, msg=msg,
                raw=str(raw)[:120])


if __name__ == '__main__':
    r = require_live()
    print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r['ok'] else 9)
