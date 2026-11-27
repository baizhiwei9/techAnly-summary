#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
相变异常检测系统 · 主入口 (main)
==============================
三层串联，每层可单独跑：

    # 1) 只更新数据
    python3 strategies/pws_main.py fetch --date 20261103

    # 2) 只算（不推送）
    python3 strategies/pws_main.py compute --date 20261103

    # 3) 完整跑（数据→计算→告警推送）
    python3 strategies/pws_main.py run --date 20261103

    # 4) 回测（不推送，出报告）
    python3 strategies/pws_main.py backtest --start 20260601 --end 20261103

分层边界
--------
pws_data    取数/缓存/校验      ← 无上层依赖
pws_xsec    指标5 截面 λmax     ← 依赖 pws_data
pws_compute 五指标 + 投票       ← 依赖 pws_data / pws_xsec
pws_alert   判级/存档/推送      ← 只接收 Result
pws_main    编排                ← 唯一入口

口径（用户 A/B 对比项）
----------------------
--ar    ret(对日收益率) | resid(对 log价格−MA20 残差)
--lam   drop(丢弃负ρ) | abs(取|ρ|)
--alpha expand(扩张窗口) | roll(500日滚动)
--kmin  α 的 k 门槛，默认 50
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import time
import json
import argparse

import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pws_data as D
import pws_xsec as X
import pws_compute as C
import pws_alert as AL
import pws_absratio as AB

WS = os.environ.get("OPENCLAW_WORKSPACE") or __import__("ocsecrets").workspace()
OUT = os.path.join(WS, '.cache', 'pws')
os.makedirs(OUT, exist_ok=True)


def do_fetch(date, start='20250401'):
    print(f'[1/3 数据层] {start} ~ {date}')
    t0 = time.time()
    D.ensure(date, start)
    print(f'  用时 {time.time()-t0:.1f}s')


def do_xsec(date, start='20250401'):
    print(f'[截面层] {start} ~ {date}')
    X.build_all(start, date)
    AB.build_all(start, date)


def do_compute(date, start='20250401', args=None):
    print(f'[2/3 计算层]')
    res = []
    missing = []
    for code, name in D.IDX_NAME.items():
        px = D.index_px(code, start, date)
        if px is None or not len(px):
            print(f'  {name}: ❌ 取数失败（无数据）')
            missing.append((name, '指数日线取不到'))
            continue
        got = str(px.trade_date.iloc[-1])
        if got < date:
            print(f'  {name}: ⚠️ 当日({date})数据未入库，实际用 {got}（回退）')
            missing.append((name, f'当日数据未入库，用 {got}'))
        lm = X.load_lmax(code, date)
        ar6 = AB.load_ar(code, date)
        r = C.compute(code, name, px, xsec_lmax=lm,
                      ar_mode=args.ar, lam_mode=args.lam,
                      k_mode=args.alpha, k_min=args.kmin,
                      abs_ratio=ar6)
        if r is None:
            print(f'  {name}: ❌ 数据不足，无法计算')
            missing.append((name, '数据不足无法计算'))
            continue
        s = C.snapshot(r)
        print(f"  {name:<8} {s['date']}  票 {s['votes']}/5  "
              f"M1={'Y' if s['m1'] else '-'} M2={'Y' if s['m2'] else '-'} "
              f"M3={'Y' if s['m3'] else '-'} M4={'Y' if s['m4'] else '-'} "
              f"M5={'Y' if s['m5'] else '-'}  "
              f"| A={'Y' if s['chA'] else '-'} B={'Y' if s['chB'] else '-'} "
              f"双道={'Y' if s['dual_lv'] >= 2 else '-'}")
        res.append(r)
    return res, missing


def do_alert(res, args, missing=None):
    print(f'[3/3 报警层]')
    r = AL.run(res, dry=args.dry, only_sys=args.only_sys,
               push=not args.no_push, missing=missing)
    s, text = r[0], r[1]
    icon = AL.LEVEL.get(s['max_votes'], AL.LEVEL[5])[0]
    print(f"  {icon} 最高 {s['max_votes']}/5 票 · {s['level_text']}")
    if s['triggers']:
        print(f"  触发：{'；'.join(s['triggers'])}")
    return s, text


def cmd_backtest(args):
    """回测：多口径 × 3 指数 × 真实峰谷事件"""
    import itertools
    # 用户关心的 7月/10月：段内最高收盘为峰，之后最低为谷
    W = {
        '000852.SH': [('20260601', '20260731', '7月'), ('20260815', '20261031', '10月')],
        '000688.SH': [('20260601', '20260731', '7月'), ('20260810', '20261031', '10月')],
        '399006.SZ': [('20260601', '20260731', '7月'), ('20260810', '20261031', '10月')],
    }
    combos = list(itertools.product(['ret', 'resid'], ['drop', 'abs'], ['expand', 'roll']))
    print(f'回测 {len(combos)} 组口径 × 3 指数 × 2 波（2票/3票双阈值）\n')
    rows = []
    for combo in combos:
        key = f'AR={combo[0]:<5} λ={combo[1]:<4} α={combo[2]:<6}'
        rec = {}
        s3 = s2 = tot = 0
        for code, name in D.IDX_NAME.items():
            px = D.index_px(code, '20250401', args.end)
            lm = X.load_lmax(code, args.end)
            r = C.compute(code, name, px, xsec_lmax=lm,
                          ar_mode=combo[0], lam_mode=combo[1],
                          k_mode=combo[2], k_min=args.kmin)
            if r is None:
                continue
            df = r.df
            for s0, s1, tag in W[code]:
                ii = df.index[(df.date >= s0) & (df.date <= s1)]
                if len(ii) == 0:
                    continue
                i0, i1 = ii[0], ii[-1]
                pk = i0 + int(np.argmax(df.close.iloc[i0:i1 + 1].to_numpy()))
                tr = pk + int(np.argmin(df.close.iloc[pk:i1 + 1].to_numpy()))
                drop = df.close.iloc[tr] / df.close.iloc[pk] - 1
                lo = max(0, pk - 30)
                seg = df.iloc[lo:pk + 1]
                tot += 1

                def first(n):
                    h = seg[seg.votes >= n]
                    if not len(h):
                        return None
                    fi = df.index[df.date == h.date.iloc[0]][0]
                    return pk - fi, h.date.iloc[0]

                a3, a2 = first(3), first(2)
                rec[f'{code}_{tag}'] = {'drop': round(drop * 100, 1),
                                        'v3': a3[0] if a3 else None,
                                        'v2': a2[0] if a2 else None}
                if a3 and a3[0] > 0:
                    s3 += 1
                if a2 and a2[0] > 0:
                    s2 += 1
        print(f'{key}  3票 {s3}/{tot}  2票 {s2}/{tot}')
        rows.append((key, rec, s3, s2, tot))

    print('\n\n' + '=' * 116)
    print('📌 汇总（数字=提前天数，正数有效；—=未触发）')
    print('=' * 116)
    hdr = f"{'口径':<34}"
    for c in ['000852.SH', '000688.SH', '399006.SZ']:
        hdr += f"{D.IDX_NAME[c][:5]+'7月':>8}{D.IDX_NAME[c][:5]+'10月':>8}"
    print(hdr + f"{'3票':>7}{'2票':>7}")
    for key, rec, s3, s2, tot in rows:
        line = f'{key:<34}'
        for c in ['000852.SH', '000688.SH', '399006.SZ']:
            for tag in ['7月', '10月']:
                v = rec.get(f'{c}_{tag}', {}).get('v2')
                line += f"{v if v is not None else '—':>8}"
        print(line + f"{s3:>5}/{tot}{s2:>5}/{tot}")
    rows.sort(key=lambda x: (-x[2], -x[3]))
    print('\n按 3票 排名：')
    for i, (k, _, s3, s2, tot) in enumerate(rows, 1):
        print(f'  {i}. {k}  3票 {s3}/{tot}  2票 {s2}/{tot}')
    fp = os.path.join(OUT, 'backtest.json')
    json.dump([{'cfg': k, 'events': r, 'v3': f'{a}/{t}', 'v2': f'{b}/{t}'}
               for k, r, a, b, t in rows], open(fp, 'w'), ensure_ascii=False, indent=1)
    print(f'\n[saved] {fp}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['fetch', 'xsec', 'compute', 'run', 'backtest', 'all'])
    ap.add_argument('--date', default=None)
    ap.add_argument('--start', default='20250401')
    ap.add_argument('--end', default=None)
    ap.add_argument('--ar', default='ret', choices=['ret', 'resid'])
    ap.add_argument('--lam', default='drop', choices=['drop', 'abs'])
    ap.add_argument('--alpha', default='expand', choices=['expand', 'roll'])
    ap.add_argument('--kmin', type=int, default=50)
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--only-sys', dest='only_sys', action='store_true', default=True,
                    help='（默认开启）邮件只在「某个指数自己有 ≥3 个指标同时亮」时才发')
    ap.add_argument('--mail-any', dest='only_sys', action='store_false',
                    help='调试用：不管几票都发邮件（默认关闭，别在 crontab 里加）')
    ap.add_argument('--no-push', action='store_true')
    a = ap.parse_args()

    date = a.date or time.strftime('%Y%m%d')
    a.end = a.end or date

    if a.cmd == 'fetch':
        do_fetch(date, a.start)
    elif a.cmd == 'xsec':
        do_xsec(date, a.start)
    elif a.cmd == 'compute':
        do_compute(date, a.start, a)
    elif a.cmd == 'run':
        do_fetch(date, a.start)
        do_xsec(date, a.start)
        res, missing = do_compute(date, a.start, a)
        do_alert(res, a, missing)
    elif a.cmd == 'all':
        do_fetch(date, a.start)
        do_xsec(date, a.start)
        res, missing = do_compute(date, a.start, a)
        s, text = do_alert(res, a, missing)
        print('\n' + (text or ''))
    elif a.cmd == 'backtest':
        cmd_backtest(a)


if __name__ == '__main__':
    main()
