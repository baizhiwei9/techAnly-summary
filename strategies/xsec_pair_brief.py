#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xsec_pair_brief.py —— 「期货横截面配对快报」（纯脚本 0 token）
================================================================================
用户 2026-10-25 需求：
  「截面对冲那个任务，每次开盘前（早晨开盘前 / 晚上开盘前 30 分钟）都要发一下
    当前的截面配对情况：最多的和最少的、次多的和次少的，就这几组。」

内容（两栏，与 xsec_wavelet_sim 完全同源同口径）：
  ① 商品栏：60 日反转横截面（多跌得最多 / 空涨得最多），按名次一一配对
  ② 股指栏：IF/IH/IC/IM 年化基差率横截面（多贴水最深 / 空贴水最浅），配对
  + 当前模拟持仓 & 净值状态（对齐用）

用法：
  python3 strategies/xsec_pair_brief.py                 # 自动判断早/夜盘
  python3 strategies/xsec_pair_brief.py --session morning --no-push
  python3 strategies/xsec_pair_brief.py --push-qq
"""
from __future__ import annotations

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, argparse, subprocess, socket
from datetime import datetime

# 容器 IPv6 不通 → 所有对外请求强制 IPv4（大佬 2026-11-03 定规矩）
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import xsec_wavelet_sim as SIM

SIGN = '📎 来自 OpenClaw.Deepseek'
# 2026-10-25 21:48 大佬定稿：只推飞书【私聊】，不推 QQ
DM_CHAT = 'oc_0e4e3335f97551eba50a32d6ccf8c12c'          # 飞书私聊（p2p）
GROUP_CHATS = ['oc_a2ed4ec3a45ea32fa3456a60fa3c91de']    # 多尺度交易群（默认不发，--group 开启）

PAIR_N = 6          # 商品栏展示几组配对
IDX_PAIR_N = 2      # 股指栏展示几组

COM_CACHE = ([], None)
IDX_CACHE = ([], None)


def _pct1(x):
    return f'{x*100:+.1f}%'


def _wan(x):
    return f'{x/10000:.1f}万'


def load_state():
    try:
        return json.load(open(SIM.STATE_FP))
    except Exception:
        return {}


def build_signals():
    """返回 (商品排序 df, 商品日期, 股指排序 df, 股指日期, 持仓 dict)"""
    # ---- ① 商品：60 日反转 ----
    C, O, V, meta = SIM.load_commodity_panel(no_fetch=False)
    com_rows, com_date = [], None
    if C is not None and len(C) > 61:
        C = C.dropna(axis=1, how='all')
        last = C.index[-1]
        com_date = last.strftime('%Y-%m-%d')
        r60 = np.log(C.loc[last] / C.iloc[-61])
        r60 = r60.replace([np.inf, -np.inf], np.nan).dropna()
        s = r60.sort_values()                       # 从小到大：跌最多在前
        for i, k in enumerate(s.index):
            com_rows.append(dict(key=k, name=meta[k][0], r60=float(s[k]),
                                 rank=i + 1, n=len(s)))
    # ---- ② 股指：年化基差率 ----
    fut, basis, oi, vol, nmap, mmap = SIM.load_idx_panel_aligned(no_fetch=False)
    idx_rows, idx_date = [], None
    if basis is not None:
        b = basis.dropna(how='all')
        last = b.index[-1]
        idx_date = last.strftime('%Y-%m-%d')
        v = b.loc[last].dropna()
        s = v.sort_values()                          # 从小到大：贴水最深在前
        for i, k in enumerate(s.index):
            idx_rows.append(dict(key=k, name=nmap.get(k, k), ann=float(s[k]),
                                 rank=i + 1, n=len(s)))
    return com_rows, com_date, idx_rows, idx_date


def held_map(state, book):
    w = (state.get('weights') or {}).get(book) or {}
    return {k: float(v) for k, v in w.items() if abs(float(v)) > 1e-9}


def fmt_com(state):
    rows, date = COM_CACHE
    if not rows:
        return ['· ⚠️ 商品数据缺失'], None
    held = held_map(state, 'commodity')
    L = []
    longs = rows[:PAIR_N]                 # 跌最多 → 多
    shorts = rows[::-1][:PAIR_N]          # 涨最多 → 空
    L.append(f'**① 商品栏 · 60 日反转横截面**（数据 {date} 收盘）')
    L.append('*配对：多「跌最狠」⟷ 空「涨最狠」，按名次一一对应*')
    for i in range(min(PAIR_N, len(longs), len(shorts))):
        a, b = longs[i], shorts[i]
        ha = '★持仓' if held.get(a['key'], 0) > 0 else ''
        hb = '★持仓' if held.get(b['key'], 0) < 0 else ''
        L.append(f'{i+1}. 🟢 {a["name"]}({a["key"]}) {_pct1(a["r60"])}{ha} '
                 f'⟷ 🔴 {b["name"]}({b["key"]}) {_pct1(b["r60"])}{hb}')
    # 多空两侧全名单
    lt = '＋'.join(f'{r["name"]}{_pct1(r["r60"])}' for r in longs)
    st = '＋'.join(f'{r["name"]}{_pct1(r["r60"])}' for r in shorts)
    L.append(f'· 🟢 多头名单：{lt}')
    L.append(f'· 🔴 空头名单：{st}')
    n = rows[0]['n']
    L.append(f'· 池内 {n} 品种，取多 6 / 空 6（横截面 z 排序，等名义额）')
    # 持仓对齐
    pos = ((state.get('books') or {}).get('commodity') or {}).get('positions') or []
    if pos:
        gl = [p['name'] for p in pos if p['side'] == '多']
        gs = [p['name'] for p in pos if p['side'] == '空']
        L.append(f'· 当前模拟持仓：🟢多 {len(gl)}（{"、".join(gl)}）／🔴空 {len(gs)}（{"、".join(gs)}）')
    return L, date


def fmt_idx(state):
    rows, date = IDX_CACHE
    if not rows:
        return ['· ⚠️ 股指基差数据缺失'], None
    held = held_map(state, 'idx')
    L = []
    L.append(f'**② 股指栏 · 年化基差率横截面**（数据 {date} 收盘）')
    L.append('*配对：多「贴水最深」⟷ 空「贴水最浅」（4 个合约两两配对）*')
    order = ' ＞ '.join(f'{r["name"]} {_pct1(r["ann"])}' for r in rows)
    L.append(f'· 贴水深→浅：{order}')
    longs = rows[:IDX_PAIR_N]
    shorts = rows[::-1][:IDX_PAIR_N]
    for i in range(min(len(longs), len(shorts))):
        a, b = longs[i], shorts[i]
        ha = '★持仓' if held.get(a['key'], 0) > 0 else ''
        hb = '★持仓' if held.get(b['key'], 0) < 0 else ''
        L.append(f'{i+1}. 🟢 多 {a["name"]} 基差{_pct1(a["ann"])}{ha} '
                 f'⟷ 🔴 空 {b["name"]} 基差{_pct1(b["ann"])}{hb}')
    wv = ((state.get('books') or {}).get('idx') or {}).get('wave') or {}
    if wv:
        L.append('· 小波方向确认：' + '｜'.join(
            f'{SIM.IDX_NAME.get(k, k).split("(")[0]} {float(v):+.2f}' for k, v in wv.items()))
    return L, date


def card_md(state, session):
    sess = '早盘' if session == 'morning' else '夜盘'
    L = [SIGN]
    d = COM_CACHE[1] or IDX_CACHE[1] or '—'
    L.append(f'**🔀 期货横截面配对快报 · {sess}前 30 分钟 · 数据 {d} 收盘**')
    L.append('')
    lb, _ = fmt_com(state);        L.extend(lb); L.append('')
    li, _ = fmt_idx(state);        L.extend(li); L.append('')
    # 模拟状态（明确持仓所依据的信号日期，避免与最新数据日期混淆）
    bk = state.get('books') or {}
    tot_cap = tot_eq = 0.0
    for b in ('commodity', 'idx'):
        x = bk.get(b) or {}
        if x:
            tot_cap += x.get('capital', 0); tot_eq += x.get('equity', 0)
    if tot_cap:
        L.append('**📊 模拟状态**')
        sd = state.get('date') or '—'
        L.append(f'· 持仓依据：{sd} 收盘信号（T+1 开盘执行）')
        for b, lab in (('commodity', '商品栏'), ('idx', '股指栏')):
            x = bk.get(b) or {}
            if x:
                L.append(f'· {lab}：权益 {x.get("equity",0):,.0f}　累计 {(x.get("ret") or 0)*100:+.2f}%')
        L.append(f'· 合计：权益 {tot_eq:,.0f} / 本金 {tot_cap:,.0f}　累计 {(tot_eq/tot_cap-1)*100:+.2f}%')
        L.append('')
    L.append('*口径：商品=60 日价格反转；股指=年化基差率（期货−现货）/现货×365/剩余天数 横截面。'
             '★=当前模拟持仓。T 日收盘信号 → T+1 开盘执行。*')
    return '\n'.join(L)


def card_json(title, body_md, template='orange'):
    return {'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': template},
            'elements': [{'tag': 'markdown', 'content': body_md}]}


def push_feishu(card, targets=None):
    sys.path.insert(0, os.path.join(WS, 'smc'))
    import smc_feishu as F
    out = []
    for c in (targets or [DM_CHAT]):
        try:
            r = F.send_card(c, card)
            out.append((c, r.get('code'), (r.get('data') or {}).get('message_id')))
        except Exception as e:
            out.append((c, 'ERR', repr(e)[:60]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', default=None, choices=['morning', 'evening'])
    ap.add_argument('--no-push', action='store_true')
    ap.add_argument('--group', action='store_true', help='额外发到「多尺度交易」群')
    ap.add_argument('--no-qq', action='store_true', help='（保留兼容，默认已不发 QQ）')
    ap.add_argument('--force', action='store_true', help='非交易日也发（手动触发用）')
    a = ap.parse_args()

    # 交易日检查（--force 跳过）
    if not a.force:
        try:
            import trade_day as TD
            td, is_open, note = TD.resolve(None)
            if not is_open:
                print(f'非交易日（{note}），跳过'); return 0
        except Exception as e:
            print('trade_day 检查失败，继续:', repr(e)[:80])

    session = a.session or ('morning' if datetime.now().hour < 15 else 'evening')
    state = load_state()

    global COM_CACHE, IDX_CACHE
    com, cd, idx, idd = build_signals()
    COM_CACHE = (com, cd); IDX_CACHE = (idx, idd)
    if not com and not idx:
        print('❌ 无信号数据'); return 1

    sess = '早盘' if session == 'morning' else '夜盘'
    body = card_md(state, session)
    title = f'🔀 期货横截面配对快报 · {sess} · {cd or idd}'
    print(f'# {title}\n\n{body}\n')
    if a.no_push:
        print('(--no-push，未发送)'); return 0

    targets = [DM_CHAT] + (GROUP_CHATS if a.group else [])
    for c, code, mid in push_feishu(card_json(title, body), targets):
        print(f'push feishu {c} code={code} mid={mid}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
