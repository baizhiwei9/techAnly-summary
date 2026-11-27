#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务① 行业资金诊断日报（吸筹/派发/换筹 + 长钱短钱）

内容：Top10 大成交簇 → 位置/量价/中军/涨跌结构/龙虎榜/机构游资/融资/ETF →
      【吸筹/派发/换筹/无法判断】+【长钱/短钱/混合】+ 行业流动性变化 + 四类池
推送：飞书群卡片 + QQ 文字版
纯脚本 0 token。
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json

WS = os.path.expanduser("~/.openclaw/workspace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flow_diag_lib as L


def build_fund(topn=10):
    A = L.stage_a(topn)
    for r in A:
        r['tag_a'], r['nature'] = L.verdict_a(r)
    LQ = L.stage_liquidity(topn)
    days = L.next_trading_days(1)

    out = []
    out.append(f'💰 行业资金诊断 · {L.DATE[4:6]}/{L.DATE[6:]}（吸筹/派发）')
    out.append('口径：占比 / 5日Δpp / 额÷20均额（大盘0.85=缩量环境）')
    out.append('')
    out.append('【大成交簇 Top10】')
    for r in A:
        out.append(f"·{r['board']} {r['share']:.1f}% 额Δ{r['d_share']:+.1f}pp 额比{r['amt_ratio']:.2f} "
                   f"涨{r['pct1d']:+.1f}% 位置{r['pos']:.0f}% {r['vp']} → 【{r['tag_a']}】{r['nature']}")
        det = []
        if 'up' in r:
            det.append(f"涨{r['up']}/跌{r['dn']} 涨停{r['zt']} 换手{r['turn']:.1f}%")
        if r.get('lead_mv'):
            det.append(f"领涨{r['lead_pct']:+.0f}%@{r['lead_mv']:.0f}亿")
        if r.get('lb_n'):
            det.append(f"龙虎榜{r['lb_n']}家{r['lb_net']:+.1f}亿")
        if r.get('inst') or r.get('youzi'):
            det.append(f"机构{r['inst']:+.1f}亿/游资{r['youzi']:+.1f}亿")
        if r.get('margin') is not None:
            det.append(f"融资{r['margin']:+.1f}亿")
        if r.get('etf') is not None:
            det.append(f"ETF{r['etf']:+.2f}亿份")
        out.append('   ' + ' | '.join(det))
        if r.get('mid'):
            out.append('   中军：' + ' '.join(f'{c}({mv:.0f}亿){p:+.1f}%' for c, mv, p in r['mid']))
    out.append('')
    out.append('【行业流动性变化】liq=今日额占比÷20日均占比')
    top = sorted(LQ, key=lambda x: -x['liq'])[:3]
    bot = sorted(LQ, key=lambda x: x['liq'])[:3]
    out.append('  流入 ' + ' ｜ '.join(f"{r['board']}{r['liq']:.2f}" for r in top))
    out.append('  流出 ' + ' ｜ '.join(f"{r['board']}{r['liq']:.2f}" for r in bot))
    out.append('  连放 ' + '、'.join(f"{r['board']}+{r['streak']}" for r in LQ if r['streak'] >= 3) or '  连放 无')
    out.append('  连缩 ' + '、'.join(f"{r['board']}{r['streak']}" for r in LQ if r['streak'] <= -3) or '  连缩 无')
    out.append('')
    out.append('【四类池】')
    acc = [r['board'] for r in A if r['tag_a'] == '吸筹']
    dist = [r['board'] for r in A if r['tag_a'] == '派发']
    swap = [r['board'] for r in A if r['tag_a'] == '换筹']
    shortp = [r['board'] for r in A if '短钱' in r['nature']]
    longp = [r['board'] for r in A if '长钱' in r['nature']]
    out.append('🟢吸筹(可回踩跟踪)：' + ('、'.join(acc) or '无'))
    out.append('🔴派发(只卖不追)：' + ('、'.join(dist) or '无'))
    out.append('⚪换筹(等方向)：' + ('、'.join(swap) or '无'))
    out.append('⚡短钱(隔日交易)：' + ('、'.join(shortp) or '无'))
    out.append('🏦长钱(中线观察)：' + ('、'.join(longp) or '无'))
    out.append('')
    if days:
        out.append(f"【明日验证】{days[0][4:6]}/{days[0][6:]}")
        for r in A:
            if r['tag_a'] == '派发':
                out.append(f"  {r['board']}派发：缩量(额比<0.8)或跌家数收敛→减弱；再放量跌→确认")
            elif r['tag_a'] == '吸筹':
                out.append(f"  {r['board']}吸筹：中军站MA20+额比>1→成立；缩量破20日低→失败")
    out.append('数据缺口：北向个股持仓、股东户数 = 无数据')
    return '\n'.join(out), A


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
    txt, A = build_fund(a.topn)
    print(txt)
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(txt)
    title = f'💰 行业资金诊断 · {L.DATE[4:6]}/{L.DATE[6:]}'
    if a.push in ('qq', 'both'):
        rc, msg = L.push_qq(txt)
        print(f'[qq] rc={rc} {msg}')
    if a.push in ('feishu', 'both'):
        ok, msg = L.push_feishu_card(txt, title, tag='fund')
        print(f'[feishu] ok={ok} {msg}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
