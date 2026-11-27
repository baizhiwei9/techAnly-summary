#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五大宽基 ETF 净申赎监控 · CLI 入口
====================================
逻辑全在 `bin/wide_etf_alert.py`（供 market_tech_daily 直接 import）；
本文件只是命令行壳，方便手工跑 / 单独挂 cron。

用法：
    python3 strategies/wide_etf_flow.py                  # 只打印结果
    python3 strategies/wide_etf_flow.py --mail            # 触发时统一发一封邮件
    python3 strategies/wide_etf_flow.py --lookback 20     # 回看交易日数（默认 20）
    python3 strategies/wide_etf_flow.py --force-refresh   # 忽略份额缓存强制重取
    python3 strategies/wide_etf_flow.py --json            # 机器可读输出
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import json
import os
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (WS, os.path.join(WS, 'bin')):
    if p not in sys.path:
        sys.path.insert(0, p)

import wide_etf_alert as A  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mail', action='store_true', help='触发时统一发一封邮件')
    ap.add_argument('--lookback', type=int, default=A.DEFAULT_LOOKBACK)
    ap.add_argument('--force-refresh', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    r = A.run(send=a.mail, lookback=a.lookback, force_refresh=a.force_refresh)
    if not r['ok']:
        print('[ERR] 取数失败: %s' % r['error'], file=sys.stderr)
        return 2

    if a.json:
        slim = [{k: v for k, v in x.items() if k != 'vals'} for x in r['results']]
        print(json.dumps({'fired': len(r['fired']), 'mail_sent': r['mail_sent'],
                          'results': slim}, ensure_ascii=False, default=str))
    else:
        print(r['block'])

    if a.mail:
        if not r['fired']:
            print('[mail] 无触发，不发信', file=sys.stderr)
        else:
            print('[mail] %s' % ('已发送' if r['mail_sent'] else '未发送（去重或失败）'),
                  file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
