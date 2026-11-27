#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科创50 三态方向信号：机会（做多）/ 平仓 / 做空

口径来源：夸克包 data/predictions_t4/nf_t4dimraw_spread_000688_SH[_s*]（2026-10-19 用 --conf 0
重训，即「关闭低置信冻结、只保留 VIX>30 冻结」的干净口径），5 种子 bagging。

判定规则（阈值 = amp 在过去 300 个交易日的前 N% 分位，shift(1) 防未来函数）：
  · 机会（做多）：bagged > 0 且 amp >= q90   → 历史方向命中 66.7%（n=129）
  · 做空      ：bagged < 0 且 amp >= q85   → 历史方向命中 64.1%（n=53）
  · 平仓      ：昨日在仓（多/空）且今日不再触发同向 → 提示平掉该仓
  · 其余      ：观望，空仓

用法：
  python3 strategies/kc50_tristate.py                 # 打印报告
  python3 strategies/kc50_tristate.py --push          # 推飞书私聊卡片
  python3 strategies/kc50_tristate.py --force-push    # 无信号也推
  python3 strategies/kc50_tristate.py --md /tmp/x.md  # 同时落盘 markdown
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

WS = os.path.expanduser("~/.openclaw/workspace")
PKG = os.path.join(WS, '.cache/quark_dl/科创50日频预测包_20261018')
PRED_DIR = os.path.join(PKG, 'data/predictions_t4')
CODE = '000688_SH'
PREFIX = 'nf_t4dimraw_spread'
SEEDS = ['', '_s7', '_s123', '_s2026', '_s999']
WINDOW = 300
MINP = 150
LONG_Q = 90
SHORT_Q = 85


def load():
    frames = []
    for i, s in enumerate(SEEDS):
        f = os.path.join(PRED_DIR, f'{PREFIX}_{CODE}{s}', f'pred_{CODE}.csv')
        if not os.path.exists(f):
            raise SystemExit(f'缺少预测文件: {f}')
        df = pd.read_csv(f, parse_dates=['T'])
        cols = {'pred_ret': 'p' + s, 'alarm_freeze': 'af' + s}
        if i == 0:
            cols['y_next_ret'] = 'y'
        frames.append(df.set_index('T')[list(cols)].rename(columns=cols))
    m = pd.concat(frames, axis=1)
    pc = [c for c in m.columns if c.startswith('p') and c != 'pred']
    ac = [c for c in m.columns if c.startswith('af')]
    m['n_seed'] = m[pc].notna().sum(axis=1)
    m['bagged'] = m[pc].mean(axis=1)
    m['div'] = m[pc].std(axis=1)
    m['amp'] = m['bagged'].abs()
    m['frozen'] = m[ac].fillna(0).sum(axis=1) > 0
    for q in range(50, 100, 5):
        m[f'aq{q}'] = m['amp'].shift(1).rolling(WINDOW, min_periods=MINP).quantile(q / 100.0)
    return m


def classify(row):
    if pd.isna(row['bagged']) or pd.isna(row['aq' + str(LONG_Q)]):
        return 'NODATA'
    if row['bagged'] > 0 and row['amp'] >= row[f'aq{LONG_Q}']:
        return 'LONG'
    if row['bagged'] < 0 and row['amp'] >= row[f'aq{SHORT_Q}']:
        return 'SHORT'
    return 'FLAT'


def build(m):
    d = m.dropna(subset=['bagged']).copy()
    d['state'] = [classify(r) for _, r in d.iterrows()]
    prev = d['state'].shift(1).fillna('FLAT')
    close_sig = ((prev == 'LONG') & (d['state'] != 'LONG')) | ((prev == 'SHORT') & (d['state'] != 'SHORT'))
    d['close_sig'] = close_sig
    # 仓位：T 日信号，T 日收盘持有到 T+1 收盘 → 收益作用在 y（T+1 收益）
    d['pos'] = d['state'].map({'LONG': 1.0, 'SHORT': -1.0, 'FLAT': 0.0}).fillna(0.0)
    d['strat'] = d['pos'] * d['y']
    return d


def stats(sel):
    sel = sel.dropna(subset=['y'])
    if len(sel) == 0:
        return None
    hit = (np.sign(sel['bagged']) == np.sign(sel['y'])).mean()
    win = sel.loc[np.sign(sel['bagged']) == np.sign(sel['y']), 'y'].abs()
    los = sel.loc[np.sign(sel['bagged']) != np.sign(sel['y']), 'y'].abs()
    return dict(n=len(sel), hit=hit, avg=sel['y'].mean(),
                win=float(win.mean()) if len(win) else float('nan'),
                los=float(los.mean()) if len(los) else float('nan'))


def _binp(n, k):
    from math import comb
    if n == 0:
        return float('nan')
    return sum(comb(n, i) for i in range(int(k), n + 1)) / 2 ** n


def method_stats(d, q_long, q_short):
    """给定做多/做空分位门槛，返回该方法的完整历史统计（供卡片调用）。"""
    base = d.dropna(subset=['y'])
    base = base[~base['frozen']]
    out = dict(q_long=q_long, q_short=q_short)
    for side, q in (('long', q_long), ('short', q_short)):
        col = f'aq{q}'
        if col not in base.columns:
            continue
        sel = base[base['amp'] >= base[col]]
        if side == 'long':
            sel = sel[sel['bagged'] > 0]
        else:
            sel = sel[sel['bagged'] < 0]
        n = len(sel)
        if n == 0:
            out[side] = dict(n=0)
            continue
        k = int((np.sign(sel['bagged']) == np.sign(sel['y'])).sum())
        out[side] = dict(n=n, hit=k / n, p=_binp(n, k),
                         avg=float(sel['y'].mean()), cover=n / len(base))
    # 合并口径（多空都算“触发”）
    sel = base[base['amp'] >= base[f'aq{min(q_long, q_short)}']]
    n = len(sel)
    if n:
        k = int((np.sign(sel['bagged']) == np.sign(sel['y'])).sum())
        out['all'] = dict(n=n, hit=k / n, p=_binp(n, k), avg=float(sel['y'].mean()),
                          cover=n / len(base))
    return out


def summary_payload():
    """给 signal_card.py 调：返回科创50 方法二 / 方法三的当前状态 + 历史统计。"""
    m = load()
    d = build(m)
    last = d.iloc[-1]
    pct = d.dropna(subset=['y'])
    pct = pct[~pct['frozen']]
    eq = (1 + pct['strat']).cumprod()
    res = dict(date=str(d.index[-1].date()), bagged=float(last['bagged']),
               amp=float(last['amp']),
               strat_cum=float(pct['strat'].sum()), bh_cum=float(pct['y'].sum()),
               mdd=float((eq / eq.cummax() - 1).min()),
               methods={})
    for key, ql, qs, label in (('m2', 90, 85, '方法二 · 干净口径 q90/q85'),
                               ('m3', 95, 95, '方法三 · 高门槛 q95/q95')):
        st = method_stats(d, ql, qs)
        st['label'] = label
        # 当日状态（按该方法自己的门槛）
        need_long = last.get(f'aq{ql}')
        need_short = last.get(f'aq{qs}')
        if last['bagged'] > 0 and need_long is not None and last['amp'] >= need_long:
            state = 'LONG'
        elif last['bagged'] < 0 and need_short is not None and last['amp'] >= need_short:
            state = 'SHORT'
        else:
            state = 'FLAT'
        st['state'] = state
        st['need'] = float(need_long if last['bagged'] > 0 else need_short)
        st['amp_ratio'] = float(last['amp'] / st['need']) if st['need'] else float('nan')
        res['methods'][key] = st
    return res


def fmt_state(s):
    return {'LONG': '🔴★ 机会（做多）', 'SHORT': '🟢★ 风险（做空）',
            'FLAT': '⚪ 观望（空仓/已平仓）', 'NODATA': '— 无数据'}.get(s, s)


def panel_freshness():
    """返回特征面板最后日期，用于提示数据是否滞后。"""
    f = os.path.join(PKG, 'data/features/daily_features_options_spread_000688_SH.csv')
    try:
        df = pd.read_csv(f, usecols=['T'], parse_dates=['T'])
        return df['T'].max()
    except Exception:
        return None


def render(d, m):
    hit_mask = d.dropna(subset=['y'])
    last = d.iloc[-1]
    last_t = d.index[-1]
    has_y = not pd.isna(last['y'])
    pf = panel_freshness()
    L = []
    L.append(f'**科创50 三态方向信号**（干净口径 · 5种子bagging · 阈值=amp 300日分位）')
    L.append('')
    L.append(f'数据 T 日：**{last_t.date()}** ｜ 预测标的：{last_t.date()} 收盘 → 下一交易日收盘')
    if pf is not None:
        lag = (pd.Timestamp.now().normalize() - pf.normalize()).days
        L.append(f'特征面板最后日期：{pf.date()}（距今 {lag} 天）' + ('　⚠️ 数据可能滞后，需要更新面板' if lag > 5 else ''))
    L.append('')

    # ── 今日判定
    st = last['state']
    L.append(f'### 今日判定：{fmt_state(st)}')
    L.append('')
    if st == 'LONG':
        L.append(f'- 预测收益 **{last["bagged"]:+.4f}**（正），波动幅度 amp={last["amp"]:.4f} '
                 f'≥ 90%分位阈值 {last["aq90"]:.4f} → **触发做多**')
    elif st == 'SHORT':
        L.append(f'- 预测收益 **{last["bagged"]:+.4f}**（负），波动幅度 amp={last["amp"]:.4f} '
                 f'≥ 85%分位阈值 {last["aq85"]:.4f} → **触发做空**')
    else:
        need = last['aq' + str(LONG_Q)] if last['bagged'] > 0 else last['aq' + str(SHORT_Q)]
        pct = last['amp'] / need * 100 if need and need > 0 else float('nan')
        L.append(f'- 预测收益 **{last["bagged"]:+.4f}**（{"偏多" if last["bagged"] > 0 else "偏空"}），'
                 f'但幅度 amp={last["amp"]:.4f} 只有触发线 {need:.4f} 的 **{pct:.0f}%** → 不够强，**观望/空仓**')
    if last.get('close_sig'):
        L.append('- ⚠️ **持仓者提示：今日掉出信号区 → 平仓**')
    L.append('')

    # ── 近 10 日轨迹
    L.append('### 近 10 个交易日轨迹')
    L.append('')
    L.append('| T日 | 预测收益 | amp | 多线(q90) | 空线(q85) | 状态 |')
    L.append('|---|---|---|---|---|---|')
    for t, r in d.tail(10).iterrows():
        mark = {'LONG': '🔴机会', 'SHORT': '🟢做空', 'FLAT': '⚪观望'}.get(r['state'], r['state'])
        L.append(f'| {t.date()} | {r["bagged"]:+.4f} | {r["amp"]:.4f} | '
                 f'{r["aq90"]:.4f} | {r["aq85"]:.4f} | {mark} |')
    L.append('')

    # ── 历史胜率
    L.append('### 各状态历史表现（次日方向命中率）')
    L.append('')
    L.append('| 状态 | 交易日数 | 占比 | 方向命中 | 次日平均收益 | 赢时均值 | 亏时均值 |')
    L.append('|---|---|---|---|---|---|---|')
    for s, name in (('LONG', '🔴 机会(做多)'), ('SHORT', '🟢 做空'), ('FLAT', '⚪ 观望')):
        sel = hit_mask[hit_mask['state'] == s]
        stt = stats(sel)
        if not stt:
            continue
        L.append(f'| {name} | {stt["n"]} | {stt["n"]/len(hit_mask)*100:.1f}% | '
                 f'**{stt["hit"]*100:.1f}%** | {stt["avg"]*100:+.3f}% | '
                 f'{stt["win"]*100:.2f}% | {stt["los"]*100:.2f}% |')
    L.append('')

    # ── 逐年
    L.append('### 逐年表现')
    L.append('')
    L.append('| 年 | 出手天数 | 命中率 | 多/空 | 策略收益 | 持有收益 |')
    L.append('|---|---|---|---|---|---|')
    for y, g in d.dropna(subset=['y']).groupby(d.dropna(subset=['y']).index.year):
        n_act = int((g['pos'] != 0).sum())
        act = g[g['pos'] != 0]
        hit = (np.sign(act['bagged']) == np.sign(act['y'])).mean() * 100 if len(act) else float('nan')
        nl = int((g['state'] == 'LONG').sum())
        ns = int((g['state'] == 'SHORT').sum())
        L.append(f'| {y} | {n_act} | {hit:.1f}% | {nl}/{ns} | '
                 f'{g["strat"].sum()*100:+.1f}% | {g["y"].sum()*100:+.1f}% |')
    L.append('')

    # ── 全期
    dd = d.dropna(subset=['y'])
    eq = (1 + dd['strat']).cumprod()
    eqb = (1 + dd['y']).cumprod()
    mdd = (eq / eq.cummax() - 1).min() * 100
    mddb = (eqb / eqb.cummax() - 1).min() * 100
    L.append('### 全期（2021-03 起，含成本前）')
    L.append('')
    L.append(f'- 策略累计 **{dd["strat"].sum()*100:+.1f}%**，最大回撤 {mdd:.1f}%')
    L.append(f'- 买入持有 **{dd["y"].sum()*100:+.1f}%**，最大回撤 {mddb:.1f}%')
    L.append(f'- 出手比例 {(dd["pos"] != 0).mean()*100:.1f}% 的交易日（其余空仓）')
    L.append('')

    # ── 规则
    L.append('### 规则（你可以直接照着做）')
    L.append('')
    L.append('| 卡片显示 | 你做什么 | 依据 |')
    L.append('|---|---|---|')
    L.append('| 🔴★ 机会（做多） | 买入/加仓科创50 | 预测为正 且 强度进前 10% |')
    L.append('| 🟢★ 风险（做空） | 做空 或 清仓回避 | 预测为负 且 强度进前 15% |')
    L.append('| ⚪ 观望 | 已持仓 → 平仓；空仓 → 不动 | 强度不够，模型没把握 |')
    L.append('')
    L.append('> 口径：预测收益 pred_ret 是 T 日收盘→次日收盘的百分比收益；'
             'amp=|5种子平均预测|；阈值取过去 300 交易日分位（shift 1，无未来函数）。'
             '已关闭「低置信冻结」——原冻结把 |pred| 低于 70% 分位的行置 0，这些 0 会污染分位窗口，'
             '把 93% 门槛压低到只剩前 17%，本次已剔除。')
    L.append('')
    L.append('> 免责：历史命中率 ≠ 未来收益，赢亏比接近 1，需配合止损。仅供研究。')
    return '\n'.join(L)


def push_card(title, body, chat=None):
    cmd = ['python3', os.path.join(WS, 'bin/feishu_push_card.py'),
           '--title', title, '--body', body, '--template', 'red']
    if chat:
        cmd += ['--chat', chat]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print('[push]', (r.stdout or '').strip()[:300], (r.stderr or '').strip()[:300])
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--force-push', action='store_true')
    ap.add_argument('--chat', default=None)
    ap.add_argument('--md', default=None)
    ap.add_argument('--json', dest='json_out', default=None,
                    help='把方法二/方法三摘要写成 json（供 signal_card.py 读）')
    a = ap.parse_args()

    m = load()
    d = build(m)
    body = render(d, m)

    if a.json_out:
        payload = summary_payload()
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        json.dump(payload, open(a.json_out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'[json] {a.json_out}')
        if not a.push and not a.force_push and not a.md:
            return 0

    print(body)

    if a.md:
        with open(a.md, 'w', encoding='utf-8') as f:
            f.write(body + '\n')
        print(f'\n[saved] {a.md}')

    last = d.iloc[-1]
    fired = last['state'] in ('LONG', 'SHORT') or bool(last.get('close_sig'))
    if a.push or a.force_push:
        if not (fired or a.force_push):
            print('[push] 无信号（观望），按设计不推送。--force-push 可强制。')
            return 0
        state_cn = {'LONG': '机会·做多', 'SHORT': '风险·做空'}.get(last['state'], '观望')
        title = f'🎯 科创50 三态信号 · {last.name.date()} · {state_cn} · 来自 OpenClaw.Deepseek'
        ok = push_card(title, body, a.chat)
        return 0 if ok else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
