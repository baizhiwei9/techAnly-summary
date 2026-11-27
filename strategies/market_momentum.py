#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场动量分析（合并版）
=====================
一张飞书 interactive 卡片（默认发**私聊**），三段内容：
  ① 行业分层 · 两栏对照（强势侧 vs 承压侧）+ 各一栏定性结论
  ② 复盘总结（≤200 字）
  ③ 横截面动量 · 近一周排名流转（5 日口径，今日前十反向回看）
另可选推一版纯文本到 QQ。

数据：bin/industry_layer.py（申万一级分层）+ scripts/sector_rotation（板块动量）

用法：
  python3 strategies/market_momentum.py [--date 2026-10-19] [--qq] [--qq-only]
                                  [--chat oc_xxx] [--no-fetch] [--no-send]
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, argparse, datetime, subprocess

WS = os.path.expanduser("~/.openclaw/workspace")
BIN = os.path.join(WS, 'bin')
SR = os.path.join(WS, 'scripts/sector_rotation')
sys.path.insert(0, BIN)
sys.path.insert(0, SR)

SIGN = '📎 来自 OpenClaw.Deepseek'
FEISHU_CHAT = os.environ.get('MM_FEISHU_CHAT') or os.environ.get("FEISHU_CHAT_DM") or os.environ.get("FEISHU_CHAT_ID", "")  # 私聊
QQ_TARGET = os.environ.get('MM_QQ_TARGET') or os.environ.get("QQ_DEFAULT_TARGET", "")
TITLE = '📊 市场动量分析 · %s'

try:
    import trade_day
except Exception:
    trade_day = None

try:
    # 多空标识：🌙=看多/偏多 ⭐=看空/偏空 无标=多空平衡（2026-10-23 大佬要求）
    from duokong import tag_by_score, legend
except Exception:
    def tag_by_score(*a, **k):
        return ''

    def legend(*a, **k):
        return ''


def idx_tag(pct):
    """宽基指数当日涨跌 → 标识。阈值放宽：±0.3% 内算多空平衡不打标。"""
    return tag_by_score(pct, neut=0.3, w1=0.8, w2=1.5)


# ------------------------------------------------------------------ 小工具
def c_row(cells):
    """cells: [(text, weight, align)] -> column_set 表行"""
    return {
        'tag': 'column_set', 'flex_mode': 'none', 'horizontal_spacing': 'small',
        'columns': [
            {'tag': 'column', 'width': 'weighted', 'weight': w, 'vertical_align': 'top',
             'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': t, 'text_align': a}}]}
            for t, w, a in cells
        ],
    }


def md(t):
    return {'tag': 'markdown', 'content': t}


def hr():
    return {'tag': 'hr'}


def yi(v):
    return '%+.0f' % v if abs(v) >= 1 else '%+.1f' % v


# ------------------------------------------------------------------ ① 两栏对照
def side_block(strong, near, qual, weak):
    """返回 (elements, 定性结论)"""
    els = []
    left, right = [], []

    if strong:
        for i, r in enumerate(strong[:4], 1):
            q = qual.get(r['name']) or {}
            seal = '封板%.0f%%' % q['seal'] if q.get('seal') is not None else '无涨停'
            left.append('**%d. %s** %+.2f%%\n净流入 日%s/5日%s亿｜上涨%.0f%%｜换手%.1f%%｜连续%d日\n%s'
                        % (i, r['name'], r['pct'], yi(r['mf1']), yi(r['mf5']),
                           r['up_ratio'] * 100, r['turn'], r['streak'], seal))
    elif near:
        for i, r in enumerate(near[:2], 1):
            b = r
            q = qual.get(b['name']) or {}
            extra = []
            if q.get('seal') is not None:
                extra.append('封板%.0f%%' % q['seal'])
            if q.get('fd') is not None:
                extra.append('龙头涨停封单%.1f亿' % q['fd'])
            left.append('**候补%d · %s** %+.2f%%\n净流入 日%s/5日%s亿｜上涨%.0f%%｜换手%.1f%%｜连续%d日\n'
                        '差门槛：%s%s'
                        % (i, b['name'], b['pct'], yi(b['mf1']), yi(b['mf5']),
                           b['up_ratio'] * 100, b['turn'], b['streak'],
                           '；'.join(b.get('reasons') or [])[:60],
                           ('｜' + '｜'.join(extra)) if extra else ''))
    else:
        left.append('（无）')

    for i, r in enumerate(weak[:5], 1):
        fake = '伪强' in (r.get('tag') or '')
        right.append('**%d. %s** %+.2f%% ｜%s\n净流入 日%s/5日%s/20日%s亿｜上涨%.0f%%'
                     % (i, r['name'], r['pct'], r.get('tag', ''), yi(r['mf1']), yi(r['mf5']),
                        yi(r['mf20']), r['up_ratio'] * 100))
    if not weak:
        right.append('（无）')

    els.append(c_row([('**✅ 强势侧**', 5, 'left'), ('**⚠️ 承压侧**', 5, 'left')]))
    for i in range(max(len(left), len(right))):
        els.append(c_row([(left[i] if i < len(left) else ' ', 5, 'left'),
                          (right[i] if i < len(right) else ' ', 5, 'left')]))

    # 定性结论
    if strong:
        c1 = '强势侧：%s 四项达标（涨幅+资金+广度+换手），资金与广度共振，非单票拉抬。' \
             % '、'.join(r['name'] for r in strong[:3])
    elif near:
        b = near[0]
        q = qual.get(b['name']) or {}
        tail = []
        if q.get('seal') is not None and q['seal'] < 60:
            tail.append('封板率仅%.0f%%' % q['seal'])
        if b['mf20'] < 0:
            tail.append('20日资金%s亿' % yi(b['mf20']))
        c1 = ('强势侧：**0 个通过**门槛（涨幅前30%%且超额≥2pp）。最接近的 %s 资金与广度达标，'
              '但涨幅差 %.2fpp%s，属超跌反弹非趋势启动。'
              % (b['name'], 2.0 - b['excess'], ('、' + '、'.join(tail)) if tail else ''))
    else:
        c1 = '强势侧：无行业通过门槛。'

    real = [r for r in weak if '伪强' not in (r.get('tag') or '')]
    fake = [r for r in weak if '伪强' in (r.get('tag') or '')]
    seg = []
    if real:
        seg.append('真跌是%s（资金真实撤离）' % '、'.join(r['name'] for r in real[:3]))
    if fake:
        seg.append('%s属护盘伪强（低换手+5日净流出）' % '、'.join(r['name'] for r in fake[:2]))
    c2 = '承压侧：' + '；'.join(seg) + '。' if seg else '承压侧：无。'

    els.append(hr())
    els.append(md(c1))
    els.append(md(c2))
    return els, (c1, c2)


# ------------------------------------------------------------------ ② 复盘总结
def summary_block(s):
    return [hr(), md('**🧭 复盘总结**'), md(s)]


# ------------------------------------------------------------------ ③ 横截面动量
def mom_block(M):
    try:
        from send_mom_week import spark_plain, trend, fmt
    except Exception:
        spark_plain = trend = fmt = None
    m5 = (M or {}).get('mom_matrix5') or {}
    els = [hr()]
    if not m5:
        els.append(md('**📈 横截面动量 · 近一周**'))
        els.append(md('（今日无动量缓存，未生成）'))
        return els
    days = m5.get('days') or []
    els.append(md('**📈 横截面动量 · 近一周（今日前十 → 回看一周）**\n'
                  '口径：%d 日涨幅动量｜名次数字越小越靠前' % (m5.get('mom_days') or 5)))
    # 矩阵表（用 column_set，飞书卡片不渲染 markdown 表格）
    head = [('**板块**', 4, 'left'), ('**动量**', 2, 'right')]
    head += [('**%s**' % fmt(d)[5:] if fmt else d[5:], 2, 'center') for d in days]
    els.append(c_row(head))
    for r in m5.get('rows', [])[:10]:
        cells = [('**%s**' % r['name'], 4, 'left'), ('%+.2f%%' % r['mom'], 2, 'right')]
        n = len(r['ranks'])
        for k, rk in enumerate(r['ranks']):
            last = (k == n - 1)
            t = ('**第%d**' % rk) if (rk and last) else ('第%d' % rk if rk else '—')
            cells.append((t, 2, 'center'))
        els.append(c_row(cells))
    # 轨迹图
    if m5.get('rows') and spark_plain:
        els.append(md('**📉 名次轨迹**｜柱越高＝名次越前（▁低→█高）'))
        els.append(c_row([('**板块**', 4, 'left'), ('**轨迹**', 5, 'left'), ('**趋势**', 3, 'center')]))
        for r in m5['rows'][:10]:
            els.append(c_row([('**%s**' % r['name'], 4, 'left'),
                              (spark_plain(r['ranks']), 5, 'left'),
                              (trend(r['ranks']), 3, 'center')]))
    return els


def mom_text(M):
    try:
        from send_mom_week import qq_trend, fmt
    except Exception:
        return []
    mm = (M or {}).get('mom_matrix5') or {}
    L = ['', '【横截面动量 · 近一周（5日口径）】']
    for i, r in enumerate(mm.get('rows', [])[:10], 1):
        seq = '→'.join(('%d' % x) if x else '—' for x in r['ranks'])
        L.append('%d %s %s %s %+.2f%%' % (i, r['name'].ljust(6), seq, qq_trend(r['ranks']), r['mom']))
    return L


# ------------------------------------------------------------------ 卡片
def build_card(date, L, M):
    els = [md('**%s**' % SIGN)]
    mk = L['market']
    tone = '缩量' if (mk.get('amt_ratio') or 1) < 0.95 else ('放量' if (mk.get('amt_ratio') or 1) > 1.05 else '平量')
    _sh = mk.get('sh_pct', 0); _sz = mk.get('sz_pct', 0)
    els.append(md('**① 市场环境**：%s｜沪指 %+.2f%%%s%s｜深成 %+.2f%%%s%s｜两市成交 %.0f亿（前4日均 %.0f亿 = %.0f%%）\n'
                  '上涨 %.0f%%（%d/%d）｜中位 %+.2f%%｜涨停%d/炸板%d/跌停%d｜最大20%%市值组涨占比%s'
                  % (tone, _sh, ' ' if idx_tag(_sh) else '', idx_tag(_sh),
                     _sz, ' ' if idx_tag(_sz) else '', idx_tag(_sz),
                     mk.get('amt_total', 0),
                     mk.get('amt_prev5_avg', 0), (mk.get('amt_ratio') or 0) * 100,
                     (mk.get('up_ratio') or 0) * 100, mk.get('n_up', 0), mk.get('n_total', 0),
                     mk.get('med_pct', 0), mk.get('zt', 0), mk.get('zb', 0), mk.get('dt', 0),
                     ('%.0f%%' % (mk['cap_top_up'] * 100)) if mk.get('cap_top_up') is not None else '—')))
    els.append(hr())
    els.append(md('**② 行业分层 · 两栏对照**（申万一级 31 个）'))
    two, _ = side_block(L['strong'], L['strong_near'], L['quality'], L['weak'])
    els += two
    els += summary_block(L['summary'])
    els += mom_block(M)
    els.append(hr())
    els.append(md('_以上为复盘观察，不构成投资建议。_'))
    return {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue',
                       'title': {'tag': 'plain_text', 'content': TITLE % date}},
            'elements': els}


def build_text(date, L, M):
    mk = L['market']
    t = [TITLE % date, SIGN, '',
         '① 市场环境：沪指 %+.2f%%%s｜成交 %.0f亿（%.0f%%）｜上涨 %.0f%%｜中位 %+.2f%%'
         % (mk.get('sh_pct', 0), (' ' + idx_tag(mk.get('sh_pct', 0))) if idx_tag(mk.get('sh_pct', 0)) else '',
            mk.get('amt_total', 0), (mk.get('amt_ratio') or 0) * 100,
            (mk.get('up_ratio') or 0) * 100, mk.get('med_pct', 0)), '',
         '② 行业分层（强势 vs 承压）']
    if L['strong']:
        for r in L['strong'][:4]:
            t.append('  ✅ %s %+.2f%% 日%s/5日%s亿 连续%d日'
                     % (r['name'], r['pct'], yi(r['mf1']), yi(r['mf5']), r['streak']))
    else:
        for r in (L['strong_near'] or [])[:2]:
            t.append('  候补 %s %+.2f%% 差门槛:%s' % (r['name'], r['pct'], '；'.join(r.get('reasons') or [])[:60]))
        t.append('  强势侧 0 个通过门槛')
    for r in (L['weak'] or [])[:5]:
        t.append('  ⚠️ %s %+.2f%% [%s] 日%s/5日%s/20日%s亿'
                 % (r['name'], r['pct'], r.get('tag', ''), yi(r['mf1']), yi(r['mf5']), yi(r['mf20'])))
    t += ['', '③ 复盘总结', L['summary']]
    t += mom_text(M)
    t += ['', '以上为复盘观察，不构成投资建议。']
    return '\n'.join(t)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD，默认今天')
    ap.add_argument('--chat', default=FEISHU_CHAT)
    ap.add_argument('--qq', action='store_true')
    ap.add_argument('--qq-only', action='store_true')
    ap.add_argument('--qq-target', default=QQ_TARGET)
    ap.add_argument('--no-fetch', action='store_true')
    ap.add_argument('--no-send', action='store_true')
    ap.add_argument('--force', action='store_true', help='非交易日也发')
    a = ap.parse_args()

    date = a.date or datetime.date.today().strftime('%Y-%m-%d')
    if trade_day is not None:
        r, ok, note = trade_day.resolve(date.replace('-', ''))
        date = '%s-%s-%s' % (r[:4], r[4:6], r[6:])
        if not ok and not a.force:
            print('非交易日，跳过：%s' % note)
            return 0
    d8 = date.replace('-', '')
    CACHE = os.path.join(WS, '.cache', 'industry_layer')
    os.makedirs(CACHE, exist_ok=True)

    # ---- ① 行业分层（重算/读缓存）
    lj = os.path.join(CACHE, 'layer_%s.json' % d8)
    if not a.no_fetch or not os.path.exists(lj):
        p = subprocess.run([sys.executable, os.path.join(BIN, 'industry_layer.py'), '--date', d8, '--json'],
                           cwd=WS, capture_output=True, text=True, timeout=1800)
        if p.returncode != 0:
            print('industry_layer failed:', (p.stderr or '')[-600:])
            return 1
    L = json.load(open(lj))
    L.setdefault('strong', []); L.setdefault('strong_near', []); L.setdefault('weak', [])
    L.setdefault('quality', {})

    # ---- ③ 横截面动量（复用 20:00 日报缓存；缺则现抓）
    M = {}
    mpath = os.path.join(WS, '.cache', 'sector_rotation', date, 'metrics.json')
    if not os.path.exists(mpath):
        subprocess.run([sys.executable, os.path.join(SR, 'fetch_sector_data.py'), date],
                       cwd=WS, capture_output=True, text=True, timeout=900)
    subprocess.run([sys.executable, os.path.join(SR, 'build_report.py'), date],
                   cwd=WS, capture_output=True, text=True, timeout=900)
    if os.path.exists(mpath):
        M = json.load(open(mpath))
    else:
        print('[warn] 无板块动量 metrics，第三段将留空')

    card = build_card(date, L, M)
    body = json.dumps({'msg_type': 'interactive', 'card': card}, ensure_ascii=False)
    print('[card] elements=%d bytes=%d' % (len(card['elements']), len(body.encode())))

    if a.no_send:
        print(build_text(date, L, M))
        return 0

    # ---- QQ 文本版
    if a.qq or a.qq_only:
        try:
            txt = build_text(date, L, M)
            p = subprocess.run(['openclaw', 'message', 'send', '--channel', 'qqbot',
                                '--target', a.qq_target, '--message', txt],
                               cwd=WS, capture_output=True, text=True, timeout=120)
            print('qq send rc=%d %s' % (p.returncode, ((p.stdout or '').strip().splitlines() or [''])[-1]))
        except Exception as e:
            print('qq exc:', str(e)[:150])
        if a.qq_only:
            return 0

    # ---- 飞书私聊卡片（走官方应用机器人）
    cf = os.path.join(CACHE, 'card_%s.json' % d8)
    json.dump(card, open(cf, 'w', encoding='utf-8'), ensure_ascii=False)
    p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feishu_push_card.py'),
                        '--card-file', cf, '--chat', a.chat, '--signature', ''],
                       cwd=WS, capture_output=True, text=True, timeout=180)
    out = ((p.stdout or '') + (p.stderr or '')).strip()
    print('feishu card -> %s : %s' % (a.chat[-8:], out[-200:]))
    # feishu_push_card.py 成功时打印 `code=0 msg=success ...`；也兼容 JSON 形式的 "code": 0
    norm = out.replace(' ', '').replace('"', '')
    ok = p.returncode == 0 and ('code=0' in norm or 'code:0' in norm)
    if not ok:
        print('RESULT False')
        return 1
    print('RESULT True')
    return 0


if __name__ == '__main__':
    sys.exit(main())
