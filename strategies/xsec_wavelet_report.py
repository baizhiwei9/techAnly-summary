#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xsec_wavelet_report.py —— 「期货基差横截面交易报告」日报（纯脚本 0 token）
================================================================================
1) 更新模拟（调 xsec_wavelet_sim.run）
2) 生成报告：各栏净值 + 每栏持仓 + 每栏【交易规则与依据 + 回测】+ 今日开平仓
3) 发飞书卡片到「多尺度交易」群（署名在正文首行 = 消息头）
   每次推两张：① 报告主卡 ② 交易单卡

用户 2026-10-14 定稿（两栏）：
  ① 商品栏（50 万）—— 商品 60 日反转横截面多空（全池，已剔有色）
  ② 股指栏（300 万）—— 股指期货(IF/IH/IC/IM) 年化基差率横截面多空（多深贴水/空浅贴水）
     **× 小波(OI/量/价)方向确认（2026-10-14 21:2x 追加，w=0.7）**
  * 早盘 09:00(--session morning，用上一交易日) + 夜盘 21:00(--session evening，用当日) 各推一次
  * 非交易日不发；等名义额建仓（美元中性）

用法：
  python3 strategies/xsec_wavelet_report.py [--date D] [--no-fetch] [--no-push]
"""
from __future__ import annotations

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, argparse
from datetime import datetime
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import xsec_wavelet_sim as SIM

TITLE = '期货基差横截面交易报告'
SIGN = '📎 来自 OpenClaw.Deepseek'
CHATS = ['oc_a2ed4ec3a45ea32fa3456a60fa3c91de']   # 只发「多尺度交易」群
BOOK_LABEL = {'commodity': '① 商品栏', 'idx': '② 股指栏'}
BOOK_DESC = {
    'commodity': '商品（已剔有色）· 价格 60 日反转 横截面多空（多跌空涨）',
    'idx': '股指期货(IF/IH/IC/IM) · 基差(贴水)横截面多空 × 小波(OI/量/价)方向确认',
}
NO_CHANCE = {'commodity': '商品目前没有交易机会', 'idx': '股指目前无信号'}
SECT_ORDER = ['股指', '黑色', '化工', '农产品', '贵金属', '其他']
BOOK_ORDER = ['commodity', 'idx']


def money(x):
    return f'{x:,.0f}'


def _pct(x, d=2):
    try:
        return f'{float(x)*100:+.{d}f}%'
    except Exception:
        return '—'


def _wan(x):
    return f'{x/10000:.1f}万'


def _leg_lines(pos, sig=None, sigfmt=None, wtag=None):
    """按板块分组输出，多腿/空腿各一行；sig 可选标注，wtag 可选逐腿小波确认标记。"""
    PL = []
    groups = {}
    for p in pos:
        groups.setdefault(SIM.SECT.get(p['key'], '其他'), []).append(p)
    for g in SECT_ORDER:
        gs = groups.get(g)
        if not gs:
            continue
        longs = sorted([p for p in gs if p['side'] == '多'], key=lambda x: -abs(x['weight']))
        shorts = sorted([p for p in gs if p['side'] == '空'], key=lambda x: -abs(x['weight']))
        ln = sum(p['notional'] for p in longs); sn = sum(p['notional'] for p in shorts)

        def fmt(p):
            s = (sig or {}).get(p['key'])
            tag = sigfmt(s) if (s is not None and sigfmt) else ''
            wt = (wtag or {}).get(p['key'], '')
            return f'{p["name"]} {p["qty"]}手 {_wan(p["notional"])}{tag}{wt}'
        PL.append(f'　*{g}*（多{len(longs)}空{len(shorts)}：{_wan(ln)} ⟷ {_wan(sn)}）')
        if longs:
            PL.append('　· 🟢 多 ' + '＋'.join(fmt(p) for p in longs))
        if shorts:
            PL.append('　· 🔴 空 ' + '＋'.join(fmt(p) for p in shorts))
    return PL


def _book_idx(pos, out, sig, wave=None):
    PL, RL = [], []
    wt = {}
    if wave:
        for p in pos:
            d = wave.get(p['key'])
            if d is None:
                continue
            agree = (d >= 0 and p['side'] == '多') or (d < 0 and p['side'] == '空')
            wt[p['key']] = f' 小波{float(d):+.2f}{"✓" if agree else "✗"}'
    PL.extend(_leg_lines(pos, sig, lambda s: f'（基差 {s:+.2%}）', wtag=wt))
    b = out.get('balance', {}).get('idx', {})
    if b:
        PL.append(f'· 多空名义额：{_wan(b.get("long",0))} ⟷ {_wan(b.get("short",0))}（美元中性）')
    RL.append('· **信号** = 各期指**年化基差率** annuity = (期货−现货)/现货 × 365/剩余天数，取横截面 z')
    RL.append('· **规则** = 按贴水**深浅排序**：**多贴水最深 2 个 / 空贴水最浅 2 个**（横截面相对比较，比“谁深谁浅”）')
    RL.append('· ⚠️ **不是“跌了就做多”**：看的是**基差（贴水）**不是价格；也不看绝对阈值，只比当下 4 个谁深谁浅')
    RL.append('· 🌀 **小波确认层（新加）**：对价/持仓量/成交量做 db4 低频小波，取末段 40 日斜率定长周期方向；')
    RL.append('　· 腿持仓与**小波方向同向 → 满仓，反向 → 权重 ×(1−w)=0.3**（w=0.7）；即小波反对时减仓、不反向')
    RL.append('　· ⚠️ **它是确认/减仓层，不是收益来源**（纯小波 −8.6%）。后测：z权重口径 **+8.3%→+8.9%、回撤 −11.7%→−8.4%**（额外滞后1日）')
    RL.append('　· ⚠️ **诚实归因**：uplift 主要来自“额外滞后1日 + 风格择时”；确认后**重选腿/同风格干净配对下收益反而下降**（+7.1%→+6.4% / +5.1%→+2.9%，但回撤更小）→ 本质是**降回撤/降波**，别当 α')
    RL.append('· **本金 300 万**｜两侧名义额按手数配平（多空比≈1）')
    RL.append('· ⚠️ 回测（2019 至今、1bp）：基差单独 **+7.1%/t=2.8/回撤−9.7%**（topk等权）或 **+8.3%/t=3.0/回撤−11.7%**（z权重）')
    RL.append('　· ⚠️ **归因**：约 2/3 收益来自「多小盘(IC/IM)空大盘(IF/IH)」的 **size 风格暴露**；同 size 干净配对年化仅 **+5.1%**，但回撤更小、t=3.0')
    RL.append('· ⚠️ 小盘风格反转的年份（如 2019）会亏；已有五年 7 正 1 负')
    return PL, RL


def _book_commodity(pos, out, sig):
    PL, RL = [], []
    PL.extend(_leg_lines(pos, sig, lambda s: f'（60日 {_pct(s,0)}）'))
    b = out.get('balance', {}).get('commodity', {})
    if b:
        PL.append(f'· 多空名义额：{_wan(b.get("long",0))} ⟷ {_wan(b.get("short",0))}（美元中性）')
    RL.append('· **信号** = 每品种 **60 日涨幅** r60 = ln(C_t / C_{t−60})，取负号 → 横截面 z')
    RL.append('· **规则** = **多 60 日跌得最多、空 60 日涨得最多**（多空各 6 腿，等名义额，日频 T+1）')
    RL.append('· **池** = 商品全池（已剔有色），共 40 品种')
    RL.append('· ✅ 回测（含成本 1.5x）：**2017至今 +16%/年、t=2.9**（2019至今 +14.4%/年）')
    RL.append('· **美元中性**：净敞口恒 ≈0，对商品等权 beta≈−0.16 → 非裸多裸空，纯横截面多空')
    RL.append('· ⚠️ 仍需扣滑点/流动性（主力连续换月）后终验')
    return PL, RL


def card_md(out, sig):
    L = []
    tb = out['total']
    L.append(f'**本金 {money(tb["capital"])}（商品 50 万 + 股指 300 万）｜权益 {money(tb["equity"])}｜累计 {_pct(tb["ret"])}**')
    L.append('')
    L.append('**📊 各栏净值**')
    for b in BOOK_ORDER:
        bk = out['books'].get(b)
        if not bk:
            continue
        L.append(f'· {BOOK_LABEL[b]}　当日 {_pct(out["pnl"][b],3)}　权益 {money(bk.get("equity",0))}　累计 {_pct(bk.get("ret",0))}')
    L.append('')
    L.append('**🧩 持仓 + 交易规则**')
    for b in BOOK_ORDER:
        pos = out['books'].get(b, {}).get('positions', [])
        L.append(f'*{BOOK_LABEL[b]}*　{BOOK_DESC[b]}　（{len(pos)}腿）')
        if not pos:
            L.append(f'· ⚪ {NO_CHANCE[b]}')
            L.append('')
            continue
        if b == 'commodity':
            pl, rl = _book_commodity(pos, out, sig.get('commodity'))
        else:
            _wv = out['books'].get(b, {}).get('wave') or sig.get('idx_wave')
            pl, rl = _book_idx(pos, out, sig.get('idx'), wave=_wv)
        L.extend(pl)
        L.append('　📖 **规则与依据**')
        L.extend(('　' + x) for x in rl)
        L.append('')
    ntr = sum(len(out['trades'].get(b, [])) for b in out['trades'])
    L.append(f'**🔔 今日交易（{ntr} 笔）**')
    for b in BOOK_ORDER:
        ev = out['trades'].get(b, [])
        if not ev:
            continue
        L.append(f'*{BOOK_LABEL[b]}*')
        for e in ev[:22]:
            L.append('· ' + e['text'])
        if len(ev) > 22:
            L.append(f'· …其余 {len(ev)-22} 笔见文件')
    if ntr == 0:
        L.append('· 无（持仓无变化，继续持有）')
    L.append('')
    L.append('**⚙️ 口径**')
    L.append('· 各栏本金：**商品 50 万（1.5x）· 股指 300 万（1x）**｜成本单边 商品 2bp / 股指 1bp')
    L.append('· **等名义额（美元中性）**：每栏多头总名义额 ≈ 空头总名义额，净值 ≠ 裸多/裸空')
    L.append('· 信号 T 日收盘生成，**T+1 开盘执行**；每交易日**早盘 09:00 / 夜盘 21:00** 各推送一次（净值/持仓主卡 + 交易单卡）')
    return '\n'.join(L)


def build_signals():
    sig = {}
    try:
        C, O, V, meta = SIM.load_commodity_panel(no_fetch=True)
        if C is not None:
            sig['commodity'] = {}
            for k in C.columns:
                try:
                    r60 = float(np.log(C[k].iloc[-1] / C[k].iloc[-61]))
                except Exception:
                    r60 = float('nan')
                sig['commodity'][k] = r60
        fut, basis, oi, vol, nmap, mmap = SIM.load_idx_panel_aligned(no_fetch=True)
        if basis is not None:
            sig['idx'] = {}
            last = basis.dropna(how='all').index[-1]
            for k in basis.columns:
                try:
                    sig['idx'][k] = float(basis.loc[last, k])
                except Exception:
                    pass
        # 小波方向（供报告逐腿 ✓/✗ 展示；build_targets 已算一份，这里单独取当天快照）
        if oi is not None and vol is not None and fut is not None:
            sig.setdefault('idx_wave', {})
            for k in fut.columns:
                try:
                    dd = SIM.wavelet_dir(fut[k], oi[k].ffill(), vol[k].ffill())
                    sig['idx_wave'][k] = float(dd.iloc[-1])
                except Exception:
                    pass
    except Exception as e:
        print('sig fail', repr(e)[:100])
    return sig


def card_json(title, body_md, template='blue'):
    return {
        'config': {'wide_screen_mode': True},
        'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': template},
        'elements': [{'tag': 'markdown', 'content': body_md}],
    }


def push_feishu(card, chats=None):
    sys.path.insert(0, os.path.join(WS, 'smc'))
    import smc_feishu as F
    ok = []
    for c in (chats or CHATS):
        try:
            r = F.send_card(c, card)
            ok.append((c, r.get('code'), (r.get('data') or {}).get('message_id')))
        except Exception as e:
            ok.append((c, 'ERR', repr(e)[:60]))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--no-fetch', action='store_true')
    ap.add_argument('--no-push', action='store_true')
    ap.add_argument('--session', default='evening', choices=['morning', 'evening'],
                    help='morning=早盘(09:00，用上一交易日数据) / evening=夜盘(21:00，用当日数据)')
    ap.add_argument('--force', action='store_true', help='即使本日该时段已推送过也重发')
    a = ap.parse_args()

    # 非交易日不发（用户要求所有日报只在交易日发）
    try:
        sys.path.insert(0, HERE)
        import trade_day as TD
        _td, is_open, _note = TD.resolve(a.date)
        if not is_open:
            print(f'非交易日（{_note}），跳过'); return 0
    except Exception as e:
        print('trade_day 检查失败，继续:', repr(e)[:80])

    date_str = a.date
    if a.session == 'morning' and not date_str:
        try:
            import trade_day as TD
            date_str = TD.prev_trade()            # 上一交易日（早盘播报昨日收盘信号）
            date_str = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}'
        except Exception:
            date_str = None
    out = SIM.run(date_str, dry=a.no_push, no_fetch=a.no_fetch)
    if not out:
        print('❌ 无输出'); return 1
    d = out['date']
    sig = build_signals()
    sess = '早盘' if a.session == 'morning' else '夜盘'
    body = SIGN + '\n' + card_md(out, sig)
    card = card_json(f'{TITLE} · {d} · {sess}', body)
    txt = f'# {TITLE} · {d} · {sess}\n\n{body}\n'
    open(os.path.join(SIM.SIM_DIR, f'日报_{d}_{sess}.md'), 'w').write(txt)
    print(txt)
    if a.no_push:
        print('(--no-push，未发送)'); return 0
    # 本日该时段是否已推送过（防早/夜盘重复发）
    skey = f'{d}|{a.session}'
    try:
        _st = json.load(open(SIM.STATE_FP))
        _sent = _st.get('sent', {})
    except Exception:
        _st, _sent = {}, {}
    if skey in _sent and not a.force:
        print(f'本日该时段已推送过（{skey}），跳过发送'); return 0
    for c, code, mid in push_feishu(card):
        print(f'push feishu {c} code={code} mid={mid}')
    try:
        _sent[skey] = True
        _st['sent'] = _sent
        json.dump(_st, open(SIM.STATE_FP, 'w'), ensure_ascii=False, indent=1)
    except Exception as e:
        print('sent marker 写入失败', repr(e)[:60])
    try:
        import smc_feishu as F
        tlines = [f'**📋 交易单 · {d} · {sess}**', SIGN]
        n = 0
        for b in BOOK_ORDER:
            ev = out['trades'].get(b, [])
            tlines.append('')
            tlines.append(f'**{BOOK_LABEL[b]}**')
            if not ev:
                tlines.append('· 无开平仓（持仓不变）')
            for e in ev:
                tlines.append('· ' + e['text']); n += 1
        body_t = '\n'.join(tlines)
        tcard = card_json(f'{TITLE} · 交易单 · {d} · {sess}', body_t, template='turquoise')
        for c in CHATS:
            try:
                r = F.send_card(c, tcard)
                print(f'push ticket {c} code={r.get("code")} mid={(r.get("data") or {}).get("message_id")}')
            except Exception as e:
                print('ticket push fail', c, repr(e)[:60])
        # 本地归档
        open(os.path.join(SIM.SIM_DIR, f'交易单_{d}_{sess}.md'), 'w').write(f'# {TITLE} · 交易单 · {d} · {sess}\n\n{body_t}\n')
    except Exception as e:
        print('ticket push err', repr(e)[:80])
    return 0


if __name__ == '__main__':
    sys.exit(main())
