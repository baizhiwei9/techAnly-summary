#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务① 大成交簇吸筹派发报告

内容：Top10 大成交簇 → 位置/量价/中军/涨跌结构/龙虎榜/机构游资/融资/ETF →
      机会栏（绿·吸筹）/ 风险栏（红·派发）/ 中性栏（换筹·无法判断）+ 流动性变化

推送：飞书双栏卡片（机会=绿头，风险=红头）+ 文字版到 QQ。纯脚本 0 token。
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json

WS = '/root/.openclaw/workspace'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flow_diag_lib as L


def build_data(topn=10):
    A = L.stage_a(topn)
    for r in A:
        r['tag_a'], r['nature'] = L.verdict_a(r)
    LQ = L.stage_liquidity(topn)
    days = L.next_trading_days(1)
    return A, LQ, days


def one_line(r):
    det = []
    if 'up' in r:
        det.append(f"涨{r['up']}/跌{r['dn']}")
    if r.get('zt'):
        det.append(f"涨停{r['zt']}")
    det.append(f"换手{r.get('turn',0):.1f}%")
    if r.get('etf') is not None:
        det.append(f"ETF{r['etf']:+.2f}亿份")
    if r.get('margin') is not None:
        det.append(f"融资{r['margin']:+.1f}亿")
    if r.get('inst') or r.get('youzi'):
        det.append(f"机构{r.get('inst',0):+.1f}/游资{r.get('youzi',0):+.1f}亿")
    return ' '.join(det)


def build_text(A, LQ, days):
    opp = [r for r in A if r['tag_a'] == '吸筹']
    risk = [r for r in A if r['tag_a'] == '派发']
    neu = [r for r in A if r['tag_a'] not in ('吸筹', '派发')]

    o = []
    o.append(f'💰 大成交簇吸筹派发报告 · {L.DATE[4:6]}/{L.DATE[6:]}')
    o.append('口径：占比/5日Δpp/额÷20均额（大盘0.85=缩量）｜行业板块=同花顺一级行业')
    o.append('')
    o.append(f'🟢 机会栏 · 吸筹（{len(opp)}个）')
    if opp:
        for r in opp:
            o.append(f"  {r['board']}（行业）{r['share']:.1f}% 额比{r['amt_ratio']:.2f}")
            o.append(f"    涨{r['pct1d']:+.1f}% 位置{r['pos']:.0f}% {r['vp']} ｜ {r['nature']}")
            o.append(f"    {one_line(r)}")
            if r.get('mid'):
                o.append('    中军：' + ' '.join(f'{c}{p:+.1f}%' for c, mv, p in r['mid']))
    else:
        o.append('  无')
    o.append('')
    o.append(f'🔴 风险栏 · 派发（{len(risk)}个）')
    if risk:
        for r in risk:
            o.append(f"  {r['board']}（行业）{r['share']:.1f}% 额比{r['amt_ratio']:.2f}")
            o.append(f"    涨{r['pct1d']:+.1f}% 位置{r['pos']:.0f}% {r['vp']} ｜ {r['nature']}")
            o.append(f"    {one_line(r)}")
            if r.get('mid'):
                o.append('    中军：' + ' '.join(f'{c}{p:+.1f}%' for c, mv, p in r['mid']))
    else:
        o.append('  无')
    o.append('')
    o.append(f'⚪ 中性栏 · 换筹/无法判断（{len(neu)}个）')
    for r in neu:
        o.append(f"  {r['board']}（行业）{r['tag_a']} 涨{r['pct1d']:+.1f}% 位置{r['pos']:.0f}% "
                 f"额比{r['amt_ratio']:.2f} ｜ {r['nature']}")
    o.append('')
    o.append('【行业流动性】liq=今日额占比÷20日均占比')
    top = sorted(LQ, key=lambda x: -x['liq'])[:3]
    bot = sorted(LQ, key=lambda x: x['liq'])[:3]
    o.append('  流入 ' + ' ｜ '.join(f"{r['board']}{r['liq']:.2f}" for r in top))
    o.append('  流出 ' + ' ｜ '.join(f"{r['board']}{r['liq']:.2f}" for r in bot))
    up_streak = [f"{r['board']}+{r['streak']}" for r in LQ if r['streak'] >= 3]
    dn_streak = [f"{r['board']}{r['streak']}" for r in LQ if r['streak'] <= -3]
    o.append('  连放 ' + ('、'.join(up_streak) if up_streak else '无'))
    o.append('  连缩 ' + ('、'.join(dn_streak) if dn_streak else '无'))
    o.append('')
    if days:
        o.append(f"【明日 {days[0][4:6]}/{days[0][6:]} 盯】")
        for r in opp:
            o.append(f"  {r['board']}：中军站稳MA20+额比>1→吸筹成立；缩量破20日低→失败")
        for r in risk:
            o.append(f"  {r['board']}：缩量(额比<0.8)或跌家数收敛→派发减弱；再放量跌→确认")
    o.append('数据缺口：北向个股持仓、股东户数 = 无数据')
    return '\n'.join(o)


# ---------------- 飞书双栏卡片 ----------------
def _md(t):
    return {'tag': 'div', 'text': {'tag': 'lark_md', 'content': t}}


def _hr():
    return {'tag': 'hr'}


def _fc(txt, color):
    return f'<font color="{color}">{txt}</font>'


def _col(title, rows, mark, color):
    blocks = [_fc(f'**{mark} {title}**', color)]
    if not rows:
        blocks.append(_fc('无', 'grey'))
    for r in rows:
        head = f"**{r['board']}**（行业）占比{r['share']:.1f}% 额比{r['amt_ratio']:.2f}"
        body1 = f"涨{r['pct1d']:+.1f}% 位置{r['pos']:.0f}% · {r['tag_a']}·{r['nature']}"
        body2 = one_line(r)
        t = _fc(head, color) + '\n' + _fc(body1, color) + '\n' + _fc(body2, 'grey')
        if r.get('mid'):
            t += '\n' + _fc('中军 ' + ' '.join(f'{c}{p:+.1f}%' for c, mv, p in r['mid']), color)
        blocks.append(t)
    return {'tag': 'column', 'width': 'weighted', 'weight': 1, 'vertical_align': 'top',
            'elements': [{'tag': 'markdown', 'content': b} for b in blocks]}


def build_card(A, LQ, days):
    opp = [r for r in A if r['tag_a'] == '吸筹']
    risk = [r for r in A if r['tag_a'] == '派发']
    neu = [r for r in A if r['tag_a'] not in ('吸筹', '派发')]

    els = []
    els.append(_md('口径：占比/5日Δpp/**额÷20均额**（大盘 0.85 = 缩量环境）｜'
                   '板块=同花顺一级**行业**（非指数）；中军=成分内成交额前20里市值最大的3只\n'
                   '<font color="green">左栏绿=机会（吸筹）</font>　<font color="red">右栏红=风险（派发）</font>'))
    els.append(_hr())
    els.append({'tag': 'column_set', 'flex_mode': 'none', 'background_style': 'default',
                'columns': [
                    _col(f'机会 · 吸筹（{len(opp)}）', opp, '🟢', 'green'),
                    _col(f'风险 · 派发（{len(risk)}）', risk, '🔴', 'red'),
                ]})
    els.append(_hr())
    if neu:
        t = f"**⚪ 中性 · 换筹/无法判断（{len(neu)}）**\n" + '\n'.join(
            f"{r['board']} {r['tag_a']} 涨{r['pct1d']:+.1f}% 位置{r['pos']:.0f}% 额比{r['amt_ratio']:.2f} · {r['nature']}"
            for r in neu)
        els.append(_md(t))
    els.append(_hr())
    top = sorted(LQ, key=lambda x: -x['liq'])[:3]
    bot = sorted(LQ, key=lambda x: x['liq'])[:3]
    up_streak = [f"{r['board']}+{r['streak']}" for r in LQ if r['streak'] >= 3]
    dn_streak = [f"{r['board']}{r['streak']}" for r in LQ if r['streak'] <= -3]
    els.append(_md(
        '**【行业流动性】** liq=今日额占比÷20日均占比\n'
        + '流入 ' + ' ｜ '.join(f"{r['board']}{r['liq']:.2f}" for r in top) + '\n'
        + '流出 ' + ' ｜ '.join(f"{r['board']}{r['liq']:.2f}" for r in bot) + '\n'
        + '连放 ' + ('、'.join(up_streak) if up_streak else '无') + '\n'
        + '连缩 ' + ('、'.join(dn_streak) if dn_streak else '无')))
    if days:
        t = f"**【明日 {days[0][4:6]}/{days[0][6:]} 盯】**\n"
        for r in opp:
            t += _fc(f"🟢 {r['board']}：中军站稳MA20+额比>1→成立；缩量破20日低→失败", 'green') + '\n'
        for r in risk:
            t += _fc(f"🔴 {r['board']}：缩量(额比<0.8)或跌家数收敛→减弱；再放量跌→确认", 'red') + '\n'
        els.append(_md(t.strip()))
    els.append({'tag': 'note', 'elements': [
        {'tag': 'plain_text', 'content': '数据缺口：北向个股持仓、股东户数 = 无数据（接口当日无返回）'}]})

    return {'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text',
                                 'content': f'💰 大成交簇吸筹派发报告 · {L.DATE[4:6]}/{L.DATE[6:]}'},
                       'template': 'blue'},
            'elements': els}


def push_card(card, tag='fund2'):
    import subprocess
    cf = os.path.join(L.CACHE, f'card_{tag}_{L.D8}.json')
    json.dump(card, open(cf, 'w', encoding='utf-8'), ensure_ascii=False)
    p = subprocess.run([sys.executable, os.path.join(WS, 'bin/feishu_push_card.py'),
                        '--card-file', cf, '--chat', L.FEISHU_CHAT, '--signature', ''],
                       cwd=WS, capture_output=True, text=True, timeout=180)
    out = ((p.stdout or '') + (p.stderr or '')).strip()
    norm = out.replace(' ', '').replace('"', '')
    return (p.returncode == 0 and ('code=0' in norm or 'code:0' in norm)), out[-200:]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', default='', help='feishu | qq | both | 空=不推')
    ap.add_argument('--topn', type=int, default=10)
    ap.add_argument('--out', default='')
    ap.add_argument('--no-init', action='store_true')
    a = ap.parse_args()
    if not a.no_init:
        L.ensure_sector_cache()
    A, LQ, days = build_data(a.topn)
    txt = build_text(A, LQ, days)
    print(txt)
    body = json.dumps({'msg_type': 'interactive', 'card': build_card(A, LQ, days)}, ensure_ascii=False)
    print(f'[card] {len(body.encode())} bytes')
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(txt)
    if a.push in ('qq', 'both'):
        rc, msg = L.push_qq(txt)
        print(f'[qq] rc={rc} {msg}')
    if a.push in ('feishu', 'both'):
        ok, msg = push_card(build_card(A, LQ, days))
        print(f'[feishu] ok={ok} {msg}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
