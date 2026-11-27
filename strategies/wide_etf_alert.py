#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基 ETF 净申赎告警模块（被 market_tech_daily 与 wide_etf_flow 共用）
=====================================================================
大佬 2026-11-13：
  「就在大盘技术拆解这块，加一个这几个宽基指数的 ETF 的流入流出的监控。
    数据还是用那个 DataHub 啊，不要自己写。」
  「如果连续 3 天流入流出同向，并且净流入每天大于 100 亿，并且持续增大，
    那就发提醒邮件。流出也是，如果大于 100 亿，也要发邮件提醒。
    是 5 个宽基指数都检查完了再统一发邮件。」

判据（唯一权威）
----------------
  连续 **3 个交易日**同向 且 **每天都 > 100 亿** 且 **逐日放大** → 触发

  流入：w[0] < w[1] < w[2]，且三者都 > +100
  流出：w[0] > w[1] > w[2]，且三者都 < -100（绝对值递增）

  「沪深300 这几个 ETF 加起来」→ 按**指数**汇总（跟踪该指数的 ETF 求当日合计）。

去重
----
同一指数同一方向、且判定窗口的最新日期没变 → 不再重复发信
（状态文件 memory/wide_etf_flow_state.json，7 天滚动）。
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta

_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(WS, 'optstack'), os.path.join(WS, 'bin'),
          os.path.join(WS, 'misc', 'core')):
    if p not in sys.path:
        sys.path.insert(0, p)

THRESHOLD_YI = 100.0        # 单日门槛（亿元）
CONSEC_DAYS = 3             # 连续同向天数
DEFAULT_LOOKBACK = 20       # 回看交易日数（够算 3 天 + 展示）
STATE_FILE = os.path.join(WS, 'memory', 'wide_etf_flow_state.json')
SEND_EMAIL = os.path.join(WS, 'scripts', 'send_email.py')
DEDUP_DAYS = 7


# ══════════════════════════════════════════ 数据
def fetch(lookback=DEFAULT_LOOKBACK, force_refresh=False):
    """取 DataHub 的宽基 ETF 净申赎，按指数×日期汇总。

    Returns (DataFrame|None, info dict)
        DataFrame: trade_date, index_name, n, val
    """
    import data_hub
    r = data_hub.get_etf_net_flow_detail(
        lookback_days=lookback, force_refresh=force_refresh)
    meta = r.get('meta') or {}
    integrity = meta.get('integrity') or {}
    if r.get('data') is None or not len(r['data']):
        return None, {'ok': False, 'error': r.get('error') or '无数据',
                      'meta': meta, 'integrity': integrity}
    df = r['data']
    g = df.groupby(['trade_date', 'index_name']).agg(
        n=('ts_code', 'nunique'),
        val=('net_flow_yi', 'sum'),
    ).reset_index()
    # ⚠️ 注意：r['ok'] 可能是 False（数据不完整），但 data 仍然可用 ——
    #    完整性判定在库内（etf_net_flow._check_integrity），这里把结论原样传上去，
    #    由报告写入明确警告（而不是把整段数据丢掉，让人看不到发生了什么）。
    return g, {'ok': bool(r.get('ok')), 'meta': meta, 'integrity': integrity,
               'error': r.get('error') or ''}


# ══════════════════════════════════════════ 判定
def judge_series(vals, threshold=THRESHOLD_YI, days=CONSEC_DAYS):
    """对按日期升序的净申赎序列做判定。

    Returns: {fired, kind, streak, window, why}
    """
    v = [float(x) for x in (vals or []) if x is not None and x == x]
    if len(v) < days:
        return {'fired': False, 'kind': None, 'streak': len(v), 'window': v,
                'why': '数据不足 %d 天' % days}
    w = v[-days:]

    if all(x > threshold for x in w) and all(w[i] < w[i + 1] for i in range(days - 1)):
        return {'fired': True, 'kind': 'inflow', 'streak': days, 'window': w,
                'why': '连续 %d 天净流入 >%.0f亿 且逐日放大' % (days, threshold)}
    if all(x < -threshold for x in w) and all(w[i] > w[i + 1] for i in range(days - 1)):
        return {'fired': True, 'kind': 'outflow', 'streak': days, 'window': w,
                'why': '连续 %d 天净流出 >%.0f亿 且逐日放大' % (days, threshold)}

    # 未触发：看卡在哪
    sign = 1 if v[-1] > 0 else (-1 if v[-1] < 0 else 0)
    streak = 1 if sign else 0
    if sign:
        for x in reversed(v[:-1]):
            if (x > 0) == (sign > 0) and x != 0:
                streak += 1
            else:
                break
    if all(x > 0 for x in w) or all(x < 0 for x in w):
        if not all(abs(x) > threshold for x in w):
            why = '方向一致，但有单日未过 %.0f 亿' % threshold
        else:
            why = '方向一致，但未逐日放大'
    else:
        why = '方向不一致'
    return {'fired': False, 'kind': None, 'streak': streak, 'window': w, 'why': why}


def check_all(g):
    """5 个指数全部检查完（大佬要求：都查完再统一发一封）。"""
    import data_hub
    out = []
    for idx_name in data_hub.WIDE_INDEX_ETFS:
        sub = g[g['index_name'] == idx_name].sort_values('trade_date') if len(g) else None
        if sub is None or not len(sub):
            out.append({'index': idx_name, 'fired': False, 'kind': None,
                        'streak': 0, 'window': [], 'why': '无数据',
                        'dates': [], 'vals': [], 'ns': [],
                        'last': None, 'last_date': None, 'n': 0,
                        'n_expect': len(data_hub.WIDE_INDEX_ETFS[idx_name]['etfs'])})
            continue
        vals = [float(x) for x in sub['val'].tolist()]
        dates = [str(x) for x in sub['trade_date'].tolist()]
        ns = [int(x) for x in sub['n'].tolist()]
        j = judge_series(vals)
        j.update({'index': idx_name, 'vals': vals, 'dates': dates, 'ns': ns,
                  'last': vals[-1], 'last_date': dates[-1],
                  'n': ns[-1] if ns else 0,
                  'n_expect': len(data_hub.WIDE_INDEX_ETFS[idx_name]['etfs'])})
        out.append(j)
    return out


# ══════════════════════════════════════════ 排版
def _s(x):
    return '%+.1f' % x


def render_block(results, lookback=DEFAULT_LOOKBACK):
    """大盘技术拆解里的文本块（0 token，纯排版）。"""
    L = ['【💵 五大宽基 ETF 净申赎监控】',
         '  口径：Δ份额 × 当日收盘价 ≈ 净申赎（亿元；正=净申购/资金流入，负=净赎回/流出）',
         '  按指数汇总（跟踪该指数的场内 ETF 当日加总）｜数据层：DataHub etf_net_flow',
         '  告警：连续 %d 天同向 且 每天 >%.0f 亿 且 逐日放大 → 统一邮件'
         % (CONSEC_DAYS, THRESHOLD_YI),
         '  ' + '─' * 46]
    for r in results:
        if r.get('last') is None:
            L.append('    %-7s 无数据' % r['index'])
            continue
        w = r.get('window') or []
        win = ' → '.join(_s(x) for x in w) if w else '—'
        n, ne = r.get('n') or 0, r.get('n_expect') or 0
        partial = '' if (ne and n >= ne) else ' ⚠️部分%d/%d' % (n, ne)
        tag = '🚨' if r.get('fired') else '  '
        kind = {'inflow': '净流入', 'outflow': '净流出'}.get(r.get('kind'), '')
        L.append('  %s %-7s 最新 %s 亿（%s）｜近%d日 %s%s'
                 % (tag, r['index'], _s(r['last']), r.get('last_date', '')[-4:],
                    len(w), win, partial))
        if r.get('fired'):
            L.append('      → 🚨 %s触发：%s' % (kind, r.get('why')))
    fired = [r for r in results if r.get('fired')]
    L.append('  ' + '─' * 46)
    if fired:
        L.append('  结论：🚨 %d 个指数触发（%s）'
                 % (len(fired), '、'.join(x['index'] for x in fired)))
    else:
        L.append('  结论：全部未触发（5 个指数均已检查完）')
    return '\n'.join(L)


def mail_body(results, fired):
    d = datetime.now().strftime('%Y-%m-%d %H:%M')
    L = ['# 🚨 五大宽基 ETF 资金异动告警', '',
         '**触发时间**：%s' % d,
         '**规则**：连续 %d 个交易日同向 且 每天 >%.0f 亿 且 逐日放大'
         % (CONSEC_DAYS, THRESHOLD_YI),
         '**口径**：Δ份额 × 当日收盘价 ≈ 净申赎（亿元）；按指数把跟踪它的 ETF 当日加总',
         '**数据源**：DataHub `etf_net_flow`（tushare fund_share × etf_daily）',
         '', '## 触发项（%d 个）' % len(fired), '']
    for r in fired:
        kind = '🟢 持续净流入' if r['kind'] == 'inflow' else '🔴 持续净流出'
        L += ['### %s · %s' % (kind, r['index']), '',
              '| 日期 | 净申赎(亿元) |', '|---|---|']
        for dt, v in zip(r['dates'][-CONSEC_DAYS:], r['window']):
            L.append('| %s | %s |' % (dt, _s(v)))
        L += ['', '**判定**：%s' % r.get('why'), '']
    L += ['## 完整检查结果（5 个宽基）', '',
          '| 指数 | 最新(亿元) | 日期 | 近%d日序列 | 状态 |' % CONSEC_DAYS,
          '|---|---|---|---|---|']
    for r in results:
        if r.get('last') is None:
            L.append('| %s | — | — | — | 无数据 |' % r['index'])
            continue
        w = r.get('window') or []
        seq = ' → '.join(_s(x) for x in w) if w else '—'
        st = ('🚨 触发(%s)' % ('流入' if r['kind'] == 'inflow' else '流出')
              if r.get('fired') else '正常（%s）' % (r.get('why') or ''))
        L.append('| %s | %s | %s | %s | %s |'
                 % (r['index'], _s(r['last']), r.get('last_date', ''), seq, st))
    L += ['', '---', '', '📎 来自 OpenClaw.Deepseek']
    return '\n'.join(L)


# ══════════════════════════════════════════ 去重
def _load_state():
    try:
        return json.load(open(STATE_FILE, encoding='utf-8'))
    except Exception:
        return {}


def _save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        cutoff = (datetime.now() - timedelta(days=DEDUP_DAYS)).strftime('%Y%m%d')
        st = {k: v for k, v in st.items() if str(v.get('date', '')) >= cutoff}
        json.dump(st, open(STATE_FILE, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    except Exception as e:
        print('[etf-flow] 状态写入失败: %s' % str(e)[:80], file=sys.stderr)


def dedup(fired):
    """同一指数 + 同方向 + 同一窗口末日 → 视为已发过，跳过。

    Returns: 需要发的 list（可能为空）
    """
    st = _load_state()
    fresh = []
    for r in fired:
        key = '%s|%s' % (r['index'], r['kind'])
        prev = st.get(key) or {}
        if prev.get('date') == r.get('last_date'):
            continue                    # 同一天同一信号，已发过
        fresh.append(r)
        st[key] = {'date': r.get('last_date'), 'kind': r['kind'],
                   'window': r.get('window'), 'sent': datetime.now().strftime('%Y%m%d%H%M')}
    if fresh:
        _save_state(st)
    return fresh


# ══════════════════════════════════════════ 邮件
def send_mail(results, fired):
    """5 个指数检查完，统一发**一封**。成功返回 True。"""
    if not fired:
        return False
    if not os.path.exists(SEND_EMAIL):
        print('[etf-flow] 找不到 %s' % SEND_EMAIL, file=sys.stderr)
        return False
    body = mail_body(results, fired)
    tmp = '/tmp/wide_etf_flow_%s.md' % datetime.now().strftime('%Y%m%d_%H%M')
    open(tmp, 'w', encoding='utf-8').write(body)
    names = '、'.join(r['index'] for r in fired)
    kinds = {r['kind'] for r in fired}
    head = ('🟢 宽基ETF持续净流入' if kinds == {'inflow'}
            else '🔴 宽基ETF持续净流出' if kinds == {'outflow'}
            else '🚨 宽基ETF资金异动')
    subject = '%s · %s · %s' % (head, names, datetime.now().strftime('%m-%d'))
    try:
        p = subprocess.run([sys.executable, SEND_EMAIL, '--subject', subject,
                            '--body-file', tmp],
                           capture_output=True, text=True, timeout=150)
        print('[etf-flow mail] rc=%d %s'
              % (p.returncode, (p.stdout or '').strip()[:200]))
        if p.returncode != 0:
            print('[etf-flow mail] %s' % (p.stderr or '')[:300], file=sys.stderr)
        return p.returncode == 0
    except Exception as e:
        print('[etf-flow mail] 异常: %s' % str(e)[:200], file=sys.stderr)
        return False


# ══════════════════════════════════════════ 对外入口
def run(send=True, lookback=DEFAULT_LOOKBACK, force_refresh=False):
    """完整跑一遍：取数 → 5 个全查 → 有触发才统一发一封。

    Returns dict: {ok, block, results, fired, mail_sent, error}
    """
    g, info = fetch(lookback, force_refresh)
    if g is None:
        _report_health(False, '取数失败：%s' % (info.get('error') or '无数据'))
        msg = '【💵 五大宽基 ETF 净申赎监控】\n  ⚠️ 取数失败：%s' % (info.get('error') or '无数据')
        return {'ok': False, 'block': msg, 'results': [], 'fired': [],
                'mail_sent': False, 'error': info.get('error')}
    results = check_all(g)
    fired = [r for r in results if r.get('fired')]
    block = render_block(results, lookback)
    # 库内完整性自检未过 → 在报告里直接把问题写在数据上面（让人一眼看到）
    integ = info.get('integrity') or {}
    if integ and not integ.get('ok'):
        warn = ['  ' + '⚠️ ' * 1 + '数据自检未过：']
        warn += ['    · %s' % p for p in (integ.get('problems') or [])]
        block = '\n'.join(warn) + '\n' + block
    sent = False
    if send and fired:
        fresh = dedup(fired)
        if fresh:
            sent = send_mail(results, fresh)
        else:
            print('[etf-flow] 信号在去重窗口内（同一天已发过），跳过')
    _report_health(True, '', g=g, results=results, integrity=integ)
    return {'ok': True, 'block': block, 'results': results,
            'fired': fired, 'mail_sent': sent, 'error': '',
            'integrity': integ}


def _report_health(fetch_ok, err, g=None, results=None, integrity=None):
    """上报健康状态（连续失败 → 报警）。

    ⚠️ 2026-11-27 大佬：「那个获取数据的代码里判断不行吗。不用依赖外部的脚本」
    → **判定本身在 `data_hub.etf_net_flow` 里做**（`_check_integrity()`），
      这里只把库给出的 `integrity` 结果转成报警，**不重复算任何东西**。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import job_health as JH
    except Exception as e:                                   # noqa: BLE001
        print('[etf-flow] job_health 不可用: %s' % str(e)[:100], file=sys.stderr)
        return

    if not fetch_ok:
        JH.report('wide_etf_flow', False, detail=err or '取数失败')
        return

    integ = integrity or {}
    problems = list(integ.get('problems') or [])
    if not problems:
        if g is None or not len(g):
            problems = ['取数成功但结果为空']
        elif integ and integ.get('ok') is False:
            problems = ['完整性自检未过（库未给具体原因）']

    JH.report('wide_etf_flow', not problems,
              detail='；'.join(problems),
              extra={'last_data_date': integ.get('last_date'),
                     'lag_days': integ.get('lag_days'),
                     'coverage': integ.get('coverage'),
                     'max_lag_days': integ.get('max_lag_days')})


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--mail', action='store_true')
    ap.add_argument('--lookback', type=int, default=DEFAULT_LOOKBACK)
    ap.add_argument('--force-refresh', action='store_true')
    a = ap.parse_args()
    r = run(send=a.mail, lookback=a.lookback, force_refresh=a.force_refresh)
    if not r['ok']:
        print('[ERR] %s' % r['error'], file=sys.stderr)
        sys.exit(2)
    print(r['block'])
    if a.mail:
        print('[mail] %s' % ('已发送' if r['mail_sent'] else
                             ('无触发，不发' if not r['fired'] else '去重跳过/发送失败')),
              file=sys.stderr)
