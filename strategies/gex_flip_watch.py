#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gex_flip_watch.py —— 期权 Gamma Flip 距离监控（口径统一的自算模块）

用途
----
给「期权日报」提供**统一口径**的净 GEX + Gamma Flip 距离，并在 |距离| ≤ 阈值（默认 1.5%）
时发预警邮件。群日报里各家口径不一致（有的 DTE 30~180 加权、有的全月、中证1000 单位还飘），
所以这里全部自己算一遍，保证横向可比、纵向可跟踪。

口径（dealer 视角，与 bin/gex_core.py / opt_gex.py / gex_multi.py 一致）
--------------------------------------------------------------------------
    Γ_i = e^{-qT} φ(d1) / (S σ √T),  d1 = [ln(S/K)+(r−q+σ²/2)T]/(σ√T), T = DTE/365
    GEX_i = sign_i · Γ_i · OI_i · MULT · S² · 0.01       单位：元 / 每 1% 标的变动
    sign_i = +1(Call) / −1(Put)；输出 ÷1e8 → 亿元
    IV：BSM 从中价反解（close>0 用 close，否则 settle），IV ∉ (0.02,4.5) 丢弃
    MULT：ETF 期权 10000 股/张；股指期权 100 元/点
    DTE：**默认 ≥3（剔除当月）**—— 近月 Γ∝1/√T 会爆炸且两日后到期即失效，不作判风险依据；
         需要含当月看全量时用 --min-dte 1

Flip 距离
---------
    dist% = (S − Flip) / S × 100
      > 0 → 现价在 Flip 上方（正 Gamma 区，做市商压波动）
      < 0 → 现价已跌破 Flip（负 Gamma 区，做市商追涨杀跌、放大波动）
    |dist| ≤ 阈值 → 贴近翻转点（Gamma 衰减最快、方向易反转）→ 预警
    翻转点的寻找：S* ∈ [0.90S, 1.10S] 网格（步长 0.25%）重算全链净 GEX，找过零点，
    再用过零点左右两点做二次插值细化；多个零点取离现价最近者。

用法
----
    python3 strategies/gex_flip_watch.py                      # 打印最新交易日
    python3 strategies/gex_flip_watch.py --date 20261026
    python3 strategies/gex_flip_watch.py --alert              # |dist|≤1.5% 时发邮件
    python3 strategies/gex_flip_watch.py --json /tmp/x.json
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import json
import math
import argparse
import datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

import gex_multi as GM

CACHE = os.path.join(WS, '.cache')
DIST_ALERT = 1.5          # % —— 距 Flip 小于等于此值即预警
GRID_LO, GRID_HI, GRID_STEP = 0.90, 1.10, 0.0025

# 展示顺序 & 与日报现有标的名的映射
ORDER = ['50ETF', '300ETF', '500ETF', '科创50ETF', '创业板ETF',
         '沪深300', '中证1000', '上证50']
ALIAS = {
    '50ETF期权': '50ETF', '300ETF期权': '300ETF', '500ETF期权': '500ETF',
    '科创50ETF期权': '科创50ETF', '创业板ETF期权': '创业板ETF',
    '沪深300股指期权': '沪深300', '中证1000股指期权': '中证1000',
    '上证50股指期权': '上证50',
}
FULLNAME = {
    '50ETF': '上证50ETF(510050)', '300ETF': '沪深300ETF(510300)',
    '500ETF': '中证500ETF(510500)', '科创50ETF': '科创50ETF(588000)',
    '创业板ETF': '创业板ETF(159915)', '沪深300': '沪深300股指IO',
    '中证1000': '中证1000股指MO', '上证50': '上证50股指HO',
}


# ---------------------------------------------------------------- 计算

def curve(rows, S):
    grid = np.arange(S * GRID_LO, S * GRID_HI + 1e-12, S * GRID_STEP)
    ys = np.array([GM.gex_at(rows, float(s))[0] for s in grid], dtype=float)
    return grid, ys


def flips_from_curve(grid, ys, S):
    """过零点 → 线性初值 + 二次插值细化；返回 [(price, refined)]"""
    out = []
    for i in range(len(grid) - 1):
        y0, y1 = ys[i], ys[i + 1]
        if y0 == 0:
            x = float(grid[i])
        elif y0 * y1 < 0:
            x = float(grid[i] + (grid[i + 1] - grid[i]) * (-y0) / (y1 - y0))
        else:
            continue
        # 二次细化：取左右最近两点（含自己）三点拟合
        idx = [max(0, i - 1), i, min(len(grid) - 1, i + 2)]
        try:
            xs = [float(grid[j]) for j in idx]
            ys3 = [float(ys[j]) for j in idx]
            a, b, _ = np.polyfit(xs, ys3, 2)
            if abs(a) > 1e-15:
                disc = b * b - 4 * a * np.polyval([a, b, 0], 0)  # placeholder
                # 解 a x² + b x + c = 0
                c = ys3[1] - a * xs[1] ** 2 - b * xs[1]
                disc = b * b - 4 * a * c
                if disc >= 0:
                    cands = [(-b + math.sqrt(disc)) / (2 * a), (-b - math.sqrt(disc)) / (2 * a)]
                    cands = [v for v in cands if min(xs) - S * 0.01 <= v <= max(xs) + S * 0.01]
                    if cands:
                        x = min(cands, key=lambda v: abs(v - x))
        except Exception:
            pass
        out.append(float(x))
    out.sort(key=lambda v: abs(v - S))
    return out


def build(date, min_dte=3, max_dte=9999, use_cache=True):
    cp = os.path.join(CACHE, 'gex_flip_watch_%s_%d_%d.json' % (date, min_dte, max_dte))
    if use_cache and os.path.exists(cp):
        try:
            d = json.load(open(cp, encoding='utf-8'))
            if d.get('items'):
                return d
        except Exception:
            pass

    pro = GM.get_pro()
    basic_cache = {}
    out = {'date': date, 'min_dte': min_dte, 'max_dte': max_dte, 'items': [],
           'built_at': _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    for name, opt_code, und, kind, mult in GM.TARGETS:
        short = ALIAS.get(name, name)
        ex = 'SSE' if opt_code.endswith('.SH') else ('SZSE' if opt_code.endswith('.SZ') else 'CFFEX')
        if opt_code.startswith('OP000'):
            ex = 'CFFEX'
        try:
            S = GM.get_spot(pro, date, kind, und)
            if not S:
                continue
            if ex not in basic_cache:
                basic_cache[ex] = pro.opt_basic(exchange=ex)
            b = basic_cache[ex]
            bb = b[b.opt_code == opt_code].copy()
            if not len(bb):
                continue
            bb['maturity_date'] = pd.to_datetime(bb['maturity_date'], format='%Y%m%d')
            dl = pro.opt_daily(trade_date=date, exchange=ex)
            if dl is None or not len(dl):
                continue
            x = dl.merge(bb[['ts_code', 'call_put', 'exercise_price', 'maturity_date']],
                         on='ts_code', how='inner')
            rows = GM.legs_for(x, date, S, min_dte, max_dte)
            for it in rows:
                it['mult'] = mult
            rows = GM.prep_iv(rows, S)
            if not rows:
                continue
            net, gc, gp, n = GM.gex_at(rows, S)
            grid, ys = curve(rows, S)
            fl = flips_from_curve(grid, ys, S)
            flip = fl[0] if fl else None
            dist = ((S - flip) / S * 100.0) if flip else None
            # 30/90 加权（仅供对照）
            wg, _ = GM.weighted_gex(rows, S)
            out['items'].append(dict(
                key=short, name=name, full=FULLNAME.get(short, short),
                und=und, S=round(S, 4), net_gex=round(net, 3),
                gex_call=round(gc, 3), gex_put=round(gp, 3), n_legs=n,
                flip=(round(flip, 4) if flip else None),
                dist=(round(dist, 3) if dist is not None else None),
                n_flip=len(fl),
                gex30=(round(wg[30], 3) if wg else None),
                gex90=(round(wg[90], 3) if wg else None),
                gex_w=(round(0.5 * wg[30] + 0.5 * wg[90], 3) if wg else None),
                alert=(dist is not None and abs(dist) <= DIST_ALERT),
            ))
        except Exception as ex2:
            print('[warn] %s: %s' % (name, str(ex2)[:100]), file=sys.stderr)

    out['items'].sort(key=lambda r: ORDER.index(r['key']) if r['key'] in ORDER
                      else len(ORDER))
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(cp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return out


def prev_close(date):
    """上一交易日（用于算 GEX 日增/日减）"""
    try:
        pro = GM.get_pro()
        d0 = _dt.datetime.strptime(date, '%Y%m%d')
        cal = pro.trade_cal(exchange='SSE',
                            start_date=(d0 - _dt.timedelta(days=20)).strftime('%Y%m%d'),
                            end_date=(d0 - _dt.timedelta(days=1)).strftime('%Y%m%d'),
                            is_open='1')
        if cal is not None and len(cal):
            return str(sorted(cal['cal_date'])[-1])
    except Exception:
        pass
    return None


def enrich_delta(d, date):
    """给每个标的加 prev_gex / d_gex，便于回答「今天到底是增还是减」"""
    pd_ = prev_close(date)
    if not pd_:
        return d, None
    try:
        p = build(pd_, min_dte=d.get('min_dte', 3), max_dte=d.get('max_dte', 9999))
    except Exception as e:
        print('[warn] prev build fail %s' % str(e)[:80], file=sys.stderr)
        return d, None
    pm = {r['key']: r for r in p.get('items', [])}
    for r in d['items']:
        q = pm.get(r['key'])
        r['prev_date'] = pd_
        r['prev_gex'] = q['net_gex'] if q else None
        r['d_gex'] = (round(r['net_gex'] - q['net_gex'], 3) if q else None)
        r['prev_flip'] = q['flip'] if q else None
        r['d_flip'] = (round(r['flip'] - q['flip'], 4) if (q and q['flip'] and r['flip']) else None)
        r['prev_dist'] = q['dist'] if q else None
    d['prev_date'] = pd_
    json.dump(d, open(os.path.join(CACHE, 'gex_flip_watch_%s_%d_%d.json'
                                   % (date, d.get('min_dte', 3), d.get('max_dte', 9999))),
                      'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return d, pd_


# ---------------------------------------------------------------- 文本 / 邮件

def fmt_row(r):
    dist = r.get('dist')
    flag = '⚠️' if (dist is not None and abs(dist) <= DIST_ALERT) else ''
    ds = '—' if dist is None else '%+.2f%%' % dist
    fs = '无翻转' if r.get('flip') is None else ('%.4f' % r['flip'])
    dg = r.get('d_gex')
    dgs = '—' if dg is None else '%+.2f' % dg
    return '%-16s %10.4f  %8.2f  %7s  %12s  %8s %s' % (
        r['full'], r['S'], r['net_gex'], dgs, fs, ds, flag)


def text_block(d, title='🎯 Gamma Flip 距离（自算·统一口径）'):
    L = ['**%s**' % title]
    for r in d['items']:
        dist = r.get('dist')
        if dist is None:
            L.append('• %s：现价 %.4f｜净GEX %+.2f亿｜无翻转点' % (r['full'], r['S'], r['net_gex']))
            continue
        flag = ''
        if abs(dist) <= DIST_ALERT:
            flag = ' ⚠️贴近翻转点(≤%.1f%%)' % DIST_ALERT
        side = '上方·正Gamma压波动' if dist > 0 else '下方·负Gamma放大波动'
        L.append('• %s：现价 %.4f｜Flip %.4f｜**距Flip %+.2f%%**（%s）｜净GEX %+.2f亿%s'
                 % (r['full'], r['S'], r['flip'], dist, side, r['net_gex'], flag))
    return '\n'.join(L)


def plain_block(d):
    L = ['【Gamma Flip 距离】']
    for r in d['items']:
        dist = r.get('dist')
        if dist is None:
            L.append('  %-18s 现价%.4f  净GEX%+.2f亿  无翻转' % (r['full'], r['S'], r['net_gex']))
            continue
        flag = '  ⚠️' if abs(dist) <= DIST_ALERT else ''
        L.append('  %-18s 现价%.4f  Flip %.4f  距%+.2f%%  净GEX%+.2f亿%s'
                 % (r['full'], r['S'], r['flip'], dist, r['net_gex'], flag))
    return '\n'.join(L)


def mail_body(d, date):
    L = []
    hits = [r for r in d['items'] if r.get('alert')]
    L.append('期权 Gamma Flip 距离预警 · %s' % date)
    L.append('')
    L.append('触发条件：|现价 − Flip| / 现价 ≤ %.1f%%' % DIST_ALERT)
    L.append('')
    L.append('一、触发明细（%d 个标的）' % len(hits))
    for r in hits:
        L.append('  %-16s 现价 %.4f   Flip %.4f   距 %+.2f%%   %s'
                 % (r['full'], r['S'], r['flip'], r['dist'],
                    '正Gamma区（压波动）' if r['dist'] > 0 else '负Gamma区（放大波动）'))
    L.append('')
    L.append('二、全部标的')
    L.append('  %-16s %10s  %8s  %7s  %12s  %8s' % ('标的', '现价', '净GEX', '日增减', 'Flip', '距Flip'))
    for r in d['items']:
        L.append('  ' + fmt_row(r))
    L.append('')
    L.append('三、怎么读')
    L.append('  · 距 Flip 越近，Gamma 在 Flip 附近换符号，做市商对冲方向随之反转')
    L.append('  · 跌破 Flip（距为负）→ 负 Gamma 区，做市商追涨杀跌，波动被放大')
    L.append('  · 站上 Flip（距为正）→ 正 Gamma 区，做市商高抛低吸，波动被压制')
    L.append('  · |距| ≤ %.1f%% 视为「贴近翻转点」，方向易反转、突破/失守后加速' % DIST_ALERT)
    L.append('')
    L.append('口径：Γ·OI·MULT·S²·0.01，Call+/Put−，÷1e8=亿元；ETF 乘数 10000、股指 100；'
             'DTE≥%d；IV 由 BSM 反解；Flip 在 [0.90S,1.10S] 网格重算全链净GEX求过零点。'
             % d.get('min_dte', 3))
    L.append('📎 来自 OpenClaw.Deepseek')
    return '\n'.join(L)


def send_mail(subject, body, to=None):
    import subprocess
    sender = os.path.join(WS, 'scripts', 'send_email.py')
    if not os.path.exists(sender):
        raise RuntimeError('scripts/send_email.py 不存在')
    tmp = os.path.join('/tmp', 'gex_flip_mail_%s.txt' % _dt.datetime.now().strftime('%H%M%S'))
    open(tmp, 'w', encoding='utf-8').write(body)
    cmd = [sys.executable, sender, '--subject', subject, '--body-file', tmp]
    if to:
        cmd += ['--to', to]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    out = (r.stdout or '') + (r.stderr or '')
    if r.returncode != 0:
        raise RuntimeError(out.strip()[-200:])
    return out.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=_dt.date.today().strftime('%Y%m%d'))
    ap.add_argument('--min-dte', type=int, default=3, help='最小DTE（默认3=剔当月，主口径）')
    ap.add_argument('--max-dte', type=int, default=9999)
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--json', default='')
    ap.add_argument('--alert', action='store_true', help='有标的 |dist|≤1.5%% 时发预警邮件')
    ap.add_argument('--mail-to', default='')
    ap.add_argument('--force-mail', action='store_true')
    a = ap.parse_args()

    d = build(a.date, a.min_dte, a.max_dte, use_cache=not a.no_cache)
    d, pd_ = enrich_delta(d, a.date)
    print('日期 %s（上一交易日 %s）' % (a.date, pd_))
    print('%-16s %10s  %8s  %7s  %12s  %8s' % ('标的', '现价', '净GEX', '日增减', 'Flip', '距Flip'))
    for r in d['items']:
        print(fmt_row(r))
    hits = [r for r in d['items'] if r.get('alert')]
    print('\n贴近翻转点（|距|≤%.1f%%）：%s' % (DIST_ALERT,
                                       '、'.join(r['key'] for r in hits) if hits else '无'))
    if a.json:
        json.dump(d, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('json ->', a.json)

    if (a.alert and hits) or a.force_mail:
        subj = '⚠️ 期权 Gamma Flip 预警 · %s · %s' % (
            a.date, '、'.join(r['key'] for r in hits) if hits else '全部标的')
        try:
            out = send_mail(subj, mail_body(d, a.date), a.mail_to or None)
            print('mail ->', out.splitlines()[-1] if out else 'ok')
        except Exception as e:
            print('[ERR] mail fail:', str(e)[:200], file=sys.stderr)
            return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
