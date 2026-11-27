#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
横向截面动量 · 近一周排名流转 —— 周一至周五 20:30 推送到飞书。
口径：涨幅动量（与成交量无关）。锚定**今日前十**，倒退回看近一周各自排第几。
两个口径都给：5 日动量（快，跟得上轮动）+ 30 日动量（慢，看中长期）。
用法：python3 scripts/sector_rotation/send_mom_week.py [YYYY-MM-DD] [--no-send] [--no-fetch]
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, argparse, datetime, subprocess, urllib.request

WS = '/root/.openclaw/workspace'
SR = os.path.join(WS, 'scripts/sector_rotation')
PY = sys.executable
WEBHOOK = __import__('ocsecrets').feishu_hook()
SIGN = '📎 来自 OpenClaw.Deepseek'
# 2026-09-28 起：同一份内容额外推一版到 QQ（终端），文字版（QQ 不渲染飞书卡片）
QQ_TARGET = 'qqbot:c2c:D3056302911060130E3DEEC57C618084'
sys.path.insert(0, os.path.join(WS, 'bin'))
try:
    import trade_day
except Exception:
    trade_day = None


def resolve(date=None):
    d = date or datetime.date.today().strftime('%Y-%m-%d')
    if trade_day is None:
        return d
    r, _ok, _ = trade_day.resolve(d)
    return '%s-%s-%s' % (r[:4], r[4:6], r[6:])


def fmt(d):
    return '%s-%s-%s' % (d[:4], d[4:6], d[6:])


def run(cmd):
    p = subprocess.run(cmd, cwd=WS, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


# 名次 → 等级（越低越好）；柱越高 = 名次越靠前
_BARS = ' ▁▂▃▄▅▆▇█'


def _lv(rk):
    if not rk:
        return 0
    if rk <= 3:
        return 7
    if rk <= 5:
        return 6
    if rk <= 10:
        return 5
    if rk <= 20:
        return 4
    if rk <= 40:
        return 3
    if rk <= 60:
        return 2
    return 1


def spark_plain(ranks):
    """名次序列 → sparkline（柱越高=名次越前）"""
    return ''.join(_BARS[_lv(r)] for r in ranks)


def trend(ranks):
    if len(ranks) < 2:
        return '—'
    d0, d1 = ranks[-1], ranks[-2]
    if not (d0 and d1):
        return '新进榜' if d0 else '出榜'
    if d0 < d1:
        return '↑%d' % (d1 - d0)
    if d0 > d1:
        return '↓%d' % (d0 - d1)
    return '持平'


def block(MM, title):
    """把 mom_matrix 结构渲染成 markdown 段"""
    L = ['**%s**' % title, '']
    L.append('| 板块 | 今日%d日动量 | %s |' % (MM['mom_days'], ' | '.join(fmt(d)[5:] for d in MM['days'])))
    L.append('|%s|' % ('---|' * (2 + len(MM['days']))))
    for r in MM['rows']:
        cells = []
        for k, rk in enumerate(r['ranks']):
            last = (k == len(r['ranks']) - 1)
            cells.append(('**第%d**' % rk) if (rk and last) else (('第%d' % rk) if rk else '—'))
        L.append('| **%s** | %+.2f%% | %s |' % (r['name'], r['mom'], ' | '.join(cells)))
    L.append('')
    L.append('今日 ← 前一日：')
    for r in MM['rows']:
        seq = [('%d' % x if x else '出榜') for x in r['ranks']]
        d0, d1 = r['ranks'][-1], (r['ranks'][-2] if len(r['ranks']) > 1 else None)
        tag = '—'
        if d0 and d1:
            tag = '↑%d' % (d1 - d0) if d0 < d1 else ('↓%d' % (d0 - d1) if d0 > d1 else '持平')
        L.append('- **%s**：%s → 今日第 %s（%s）' % (r['name'], ' → '.join(seq), d0 or '出榜', tag))
    return '\n'.join(L)


def c_row(cells):
    return {
        'tag': 'column_set', 'flex_mode': 'none', 'horizontal_spacing': 'small',
        'columns': [
            {'tag': 'column', 'width': 'weighted', 'weight': w, 'vertical_align': 'center',
             'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': t, 'text_align': a}}]}
            for t, w, a in cells
        ],
    }


def build_card(date, M):
    m5 = M.get('mom_matrix5') or {}
    els = []
    # 消息头（署名）紧跟在标题后面，不放末尾
    els.append({'tag': 'markdown', 'content': '**%s**' % SIGN})
    els.append({'tag': 'markdown', 'content':
                '**口径**：涨幅动量（与成交量无关）｜锚定**今日前十**，倒退回看近一周各自排第几（5 日动量）'})
    els.append({'tag': 'hr'})
    els.append({'tag': 'markdown', 'content': block(m5, '📈 近一周横截面动量 · 5 日口径（今日前十 → 回看一周）')
                if m5 else '（5 日口径无数据）'})
    # 【名次轨迹图】卡片内"图表"：柱越高 = 名次越前（纯文本，不用发图片）
    if m5.get('rows'):
        els.append({'tag': 'hr'})
        els.append({'tag': 'markdown', 'content':
                    '**📉 名次轨迹图**｜柱越高 = 名次越前（▁ 低 → █ 高｜由左向右：%s）'
                    % ' → '.join(fmt(d)[5:] for d in m5['days'])})
        els.append(c_row([('**板块**', 4, 'left'), ('**名次轨迹**', 5, 'left'), ('**趋势**', 3, 'center')]))
        for r in m5['rows'][:10]:
            els.append(c_row([('**%s**' % r['name'], 4, 'left'),
                              (spark_plain(r['ranks']), 5, 'left'),
                              (trend(r['ranks']), 3, 'center')]))
    els.append({'tag': 'hr'})
    els.append({'tag': 'markdown', 'content': '_以上为复盘观察，不构成投资建议，股市有风险。_'})
    return {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue',
                       'title': {'tag': 'plain_text',
                                 'content': '📈 横截面动量 · 近一周排名 · %s' % date}},
            'elements': els}


def build_text(date, M):
    parts = ['📈 横截面动量 · 近一周排名 · %s' % date, SIGN, '']
    if M.get('mom_matrix5'):
        parts.append(block(M['mom_matrix5'], '【5 日口径】今日前十 → 回看一周'))
        parts.append('')
        parts.append('名次轨迹（柱越高=名次越前）：')
        for r in M['mom_matrix5']['rows'][:10]:
            parts.append('- **%s** %s（%s）' % (r['name'], spark_plain(r['ranks']), trend(r['ranks'])))
        parts.append('')
    parts.append('以上为复盘观察，不构成投资建议，股市有风险。')
    return '\n'.join(parts)


def qq_trend(ranks):
    if len(ranks) < 2:
        return '—'
    d0, d1 = ranks[-1], ranks[-2]
    if not d1:
        return '↗新'
    if not d0:
        return '↘出'
    if d0 < d1:
        return '↑%d' % (d1 - d0)
    if d0 > d1:
        return '↓%d' % (d0 - d1)
    return '持平'


def build_qq(date, M):
    """QQ / 终端版：纯文本，名次流转一行一块板块"""
    mm = M.get('mom_matrix5') or {}
    days = mm.get('days') or []
    L = ['📈 横截面动量 · 近一周排名 · %s' % date,
         '口径：5 日涨幅动量｜锚定今日前十，倒退回看一周各自名次']
    if days:
        L.append('回看：%s → %s' % (fmt(days[0])[5:], fmt(days[-1])[5:]))
    L.append('')
    L.append('【今日前十 · 名次流转】')
    for i, r in enumerate(mm.get('rows', [])[:10], 1):
        seq = '→'.join(('%d' % x) if x else '—' for x in r['ranks'])
        L.append('%d %s %s %s %+.2f%%' % (i, r['name'].ljust(6), seq, qq_trend(r['ranks']), r['mom']))
    L.append('')
    L.append('名次越小越好；↑升 ↓降，新=当日进前十')
    L.append('以上为复盘观察，不构成投资建议。')
    return '\n'.join(L)


def send_qq(text, target):
    p = subprocess.run(['openclaw', 'message', 'send', '--channel', 'qqbot',
                        '--target', target, '--message', text],
                       cwd=WS, capture_output=True, text=True, timeout=90)
    tail = [x for x in (p.stdout or '').splitlines() if x.strip()][-1:] or ['']
    print('qq send: rc=%d %s' % (p.returncode, tail[0].strip()))
    return p.returncode == 0


def push(body):
    req = urllib.request.Request(WEBHOOK, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('date', nargs='?', default=datetime.date.today().strftime('%Y-%m-%d'))
    ap.add_argument('--no-send', action='store_true')
    ap.add_argument('--no-fetch', action='store_true')
    ap.add_argument('--qq', action='store_true', help='同时推一版到 QQ（终端）')
    ap.add_argument('--qq-target', default=QQ_TARGET, help='QQ 推送目标（默认当前私聊）')
    ap.add_argument('--qq-only', action='store_true', help='只推 QQ，不发飞书')
    ap.add_argument('--force', action='store_true', help='非交易日也发（默认非交易日跳过）')
    a = ap.parse_args()
    # 非交易日不发（用户 2026-10-09：日报只在交易日总结）
    if not a.force and trade_day is not None:
        try:
            _r, _ok, _n = trade_day.resolve(a.date)
            if not _ok:
                print('非交易日，跳过横截面动量日报：%s' % _n)
                return
        except Exception:
            pass
    date = resolve(a.date)
    d8 = date.replace('-', '')

    if not a.no_fetch:
        # 20:00 的日报已拓过数 → 默认复用当日缓存，避免重复调 tushare（代理 token 有 ip 限制）
        sj = os.path.join(WS, '.cache/sector_rotation', date, 'summary.json')
        if os.path.exists(sj):
            print('[fetch] skip（当日缓存已存在 %s）' % sj)
        else:
            rc, out, err = run([PY, os.path.join(SR, 'fetch_sector_data.py'), date])
            print('[fetch] rc=%d %s' % (rc, (out.strip().splitlines() or [''])[-1]), err.strip()[-200:])
    rc, out, err = run([PY, os.path.join(SR, 'build_report.py'), date])
    if rc != 0:
        print('build_report failed:', err[-500:]); sys.exit(1)
    print('report ok')

    M = json.load(open(os.path.join(WS, '.cache/sector_rotation', date, 'metrics.json')))
    if not M.get('mom_matrix5'):
        print('mom_matrix5 missing'); sys.exit(1)

    if a.no_send:
        print('===== 飞书卡片文本 =====')
        print(build_text(date, M)[:2000])
        print('\n===== QQ 文本 =====')
        print(build_qq(date, M))
        return
    if a.qq or a.qq_only:
        try:
            send_qq(build_qq(date, M), a.qq_target)
        except Exception as e:
            print('qq exc:', str(e)[:150])
        if a.qq_only:
            return
    ok = False
    try:
        r = push({'msg_type': 'interactive', 'card': build_card(date, M)})
        print('card send:', r.get('code'), r.get('msg'))
        ok = r.get('code') == 0
    except Exception as e:
        print('card exc:', str(e)[:150])
    if not ok:
        print('fallback to text')
        r = push({'msg_type': 'text', 'content': {'text': build_text(date, M)}})
        print('text send:', r.get('code'), r.get('msg'))
        ok = r.get('code') == 0
    print('RESULT', ok)
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
