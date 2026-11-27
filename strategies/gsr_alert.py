#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股债比值 R（近一年锚）· 布林带 + 交叉 告警（纯脚本 0 token）
================================================================
2026-11-04 大佬定稿：
  「还是用这个近一年的吧。然后这个布林线触及上下轨，要发邮件提醒。
    还有那个交叉，就是这个股债收益相对一年的收益，如果出现交叉，
    比如股票上穿债券或下穿债券，你也要发邮件。
    然后合并到晚上那个大盘的技术拆解里边，把这个图片也带上，
    都是用程序来生成，不要消耗 token」

口径：
  · 锚点 = 今天往前推 12 个月（交易日取样），两条线在锚日都 = 1
  · R = 沪深300全收益 ÷ 二级债基指数（原始比值，不受归一影响）
  · 布林带 = R 的 MA20 ± 2σ
  · 交叉 = 归一后「股线 − 债线」符号翻转（向上穿 = 股票走强 / 向下穿 = 股票走弱）

告警（去重：同一信号 5 个交易日内只发一次，存 .cache/gsratio/gsr_alert_state.json）：
  ① 🔴 触及/突破布林上轨
  ② 🟢 触及/跌破布林下轨
  ③ ⚡ 股线上穿债线
  ④ ⚡ 股线下穿债线

用法：
  python3 strategies/gsr_alert.py                 # 计算 + 出图 + 打印 + 有必要时发邮件
  python3 strategies/gsr_alert.py --no-email      # 只算不发
  python3 strategies/gsr_alert.py --chart-out x.png
  python3 strategies/gsr_alert.py --json
"""
import os
import sys
import json
import argparse
import datetime as dt

import socket
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

WS = os.path.expanduser("~/.openclaw/workspace")
BIN = os.path.join(WS, 'bin')
sys.path.insert(0, BIN)
sys.path.insert(0, os.path.join(BIN, 'gsratio'))

import pandas as pd
import numpy as np

import gsratio_monitor as GM

STATE = os.path.join(WS, '.cache', 'gsratio', 'gsr_alert_state.json')
DEF_PNG = os.path.join(WS, '.cache', 'gsratio', 'gsr_anchor1y_boll.png')
ANCHOR_MONTHS = 12
WIN, K = 20, 2.0
DEDUP_DAYS = 7          # 同一信号 7 个自然日内不重复发
CONFIRM_DAYS = 3        # 交叉需连续 N 日在同一侧确认（防抖：实测全历史裸交叉 83 次，噪音太大）


# ---------------------------------------------------------------- 计算
def compute(df=None):
    if df is None:
        df = GM.build_series()
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    end = df['date'].iloc[-1]
    base = df[df['date'] >= end - pd.DateOffset(months=ANCHOR_MONTHS)]
    if not len(base):
        base = df.head(1)
    i0 = base.index[0]
    hs0, cb0 = float(df.loc[i0, 'hs']), float(df.loc[i0, 'den'])

    df['hs_a'] = df['hs'] / hs0          # 近一年锚：股线
    df['cb_a'] = df['den'] / cb0         # 近一年锚：债线
    df['gap'] = df['hs_a'] - df['cb_a']

    R = df['R_proxy']                     # 比值（不受归一影响）
    mid = R.rolling(WIN, min_periods=5).mean()
    sd = R.rolling(WIN, min_periods=5).std()
    df['R'] = R
    df['R_mid'] = mid
    df['R_up'] = mid + K * sd
    df['R_lo'] = mid - K * sd
    df['anchor_date'] = df.loc[i0, 'date'].strftime('%Y-%m-%d')
    return df


def detect(df, upto=None):
    """检测最新一日的告警。返回 (signals, info)。"""
    d = df if upto is None else df[df['date'] <= pd.Timestamp(upto)]
    d = d.reset_index(drop=True)
    if len(d) < 3:
        return [], {}
    last, prev = d.iloc[-1], d.iloc[-2]
    sig = []

    # ① / ② 布林带上下轨 —— 只在**首次触及**时报警（昨天还没触，今天触了）
    #    否则会天天报（实测 9/14→9/15→9/28→9/29 连着报 4 次同一件事）
    if pd.notna(last['R_up']) and pd.notna(prev['R_up']):
        # 容差 0.3%：贴轨即算（实测 9/14 R=2.0542 对下轨 2.0545，差 0.01% 但没穿透，
        # 用严格 >=/<= 会漏报「贴着下轨」这种情况）
        TOL = 0.002
        now_up = last['R'] >= last['R_up'] * (1 - TOL)
        now_lo = last['R'] <= last['R_lo'] * (1 + TOL)
        # 已处于轨外 / 有反复「擦轨」时，需「离开轨道并回归」才算新事件：
        # 用「已连续 N 日轨外」屏蔽重复，靠 DEDUP_DAYS 与 cross 确认制共同降噪
        lookback = d['R']
        up_hist = d['R_up']
        lo_hist = d['R_lo']
        was_up = bool((lookback.iloc[-2] >= up_hist.iloc[-2] * (1 - TOL)))
        was_lo = bool((lookback.iloc[-2] <= lo_hist.iloc[-2] * (1 + TOL)))
        if now_up and not was_up:
            sig.append(('boll_up', '🔴 触及布林上轨',
                        'R=%.4f ≥ 上轨 %.4f（股票相对债券偏贵）'
                        % (last['R'], last['R_up'])))
        elif now_lo and not was_lo:
            sig.append(('boll_lo', '🟢 触及布林下轨',
                        'R=%.4f ≤ 下轨 %.4f（股票相对债券便宜）'
                        % (last['R'], last['R_lo'])))

    # ③ / ④ 交叉（归一后股线 − 债线 符号翻转）**需连续 CONFIRM_DAYS 日同侧确认**
    #     2026-11-04 实测：裸交叉全历史 83 次、近 3 个月 7 次（8/5上穿 8/6下穿 8/7又上穿），
    #     纯噪音。改为「翻转后连续 N 日站稳同一侧」才报警，且只在确认当日发一次。
    g1 = float(last['gap'])
    n = max(2, CONFIRM_DAYS)
    if len(d) >= n + 1:
        gn = d['gap'].iloc[-n:].astype(float).tolist()
        gm1 = float(d['gap'].iloc[-n - 1])
        # 「新翻转」：确认窗口之前那一段仍在另一侧，且再往前一日也在另一侧
        prev_neg = prev_pos = False
        if len(d) >= n + 2:
            gp = float(d['gap'].iloc[-n - 2])
            prev_neg = (gm1 < 0 and gp <= 0)
            prev_pos = (gm1 > 0 and gp >= 0)
        all_pos = all(x > 0 for x in gn) and gm1 < 0
        all_neg = all(x < 0 for x in gn) and gm1 > 0
        flips = sum(1 for i in range(1, len(gn)) if (gn[i] > 0) != (gn[i - 1] > 0))
        if all_pos and flips == 0 and prev_neg:
            sig.append(('cross_up', '⚡ 股线上穿债线（%d日确认）' % n,
                        '近一年锚：股票收益转为领先，已连续 %d 日站在债线上方（当前差 %+.4f）'
                        % (n, g1)))
        elif all_neg and flips == 0 and prev_pos:
            sig.append(('cross_dn', '⚡ 股线下穿债线（%d日确认）' % n,
                        '近一年锚：股票收益转为落后，已连续 %d 日落在债线下方（当前差 %+.4f）'
                        % (n, g1)))

    info = {
        'date': last['date'].strftime('%Y-%m-%d'),
        'anchor_date': last.get('anchor_date') or '',
        'R': round(float(last['R']), 4),
        'R_up': (None if pd.isna(last['R_up']) else round(float(last['R_up']), 4)),
        'R_mid': (None if pd.isna(last['R_mid']) else round(float(last['R_mid']), 4)),
        'R_lo': (None if pd.isna(last['R_lo']) else round(float(last['R_lo']), 4)),
        'hs_a': round(float(last['hs_a']), 4),
        'cb_a': round(float(last['cb_a']), 4),
        'gap': round(float(last['gap']), 4),
        'dist_up_pct': (None if pd.isna(last['R_up']) else round((last['R_up'] / last['R'] - 1) * 100, 2)),
        'dist_lo_pct': (None if pd.isna(last['R_lo']) else round((last['R_lo'] / last['R'] - 1) * 100, 2)),
        'zone': ('上轨外' if (pd.notna(last['R_up']) and last['R'] >= last['R_up'])
                 else ('下轨外' if (pd.notna(last['R_lo']) and last['R'] <= last['R_lo'])
                       else ('上半区' if last['R'] >= (last['R_mid'] or 0) else '下半区'))),
    }
    return sig, info


# ---------------------------------------------------------------- 去重状态
def load_state():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def dedup(sig, date):
    """同一 key 在 DEDUP_DAYS 个自然日内只发一次。返回需要发的新信号。"""
    st = load_state()
    today = pd.Timestamp(date)
    fresh = []
    for key, title, detail in sig:
        rec = st.get(key) or {}
        last = rec.get('date')
        if last:
            try:
                if (today - pd.Timestamp(last)).days < DEDUP_DAYS:
                    continue
            except Exception:
                pass
        fresh.append((key, title, detail))
        st[key] = {'date': date, 'detail': detail}
    save_state(st)
    return fresh


# ---------------------------------------------------------------- 出图
def _zh_font(plt):
    from matplotlib import font_manager
    for c in ('Noto Sans CJK JP', 'Noto Sans CJK SC', 'WenQuanYi Zen Hei', 'DejaVu Sans'):
        if any(c in f.name for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.sans-serif'] = [c]
            break
    plt.rcParams['axes.unicode_minus'] = False


def make_chart(df, out_png, months=3, win=WIN, k=K):
    """近 months 个月放大显示（锚点仍是近一年起点）。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _zh_font(plt)

    end = df['date'].iloc[-1]
    cut = df[df['date'] >= end - pd.DateOffset(months=months)].reset_index(drop=True)
    if len(cut) < 8:
        cut = df.tail(15).reset_index(drop=True)

    hs, cb, R = cut['hs_a'], cut['cb_a'], cut['R']
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7.8), sharex=True,
                                   gridspec_kw={'height_ratios': [1.15, 1]})
    ad = cut['anchor_date'].iloc[0]

    ax1.plot(cut['date'], hs, lw=2.0, color='#c0392b', marker='o', ms=2.4,
             label='沪深300全收益（锚 %s = 1）' % ad)
    ax1.plot(cut['date'], cb, lw=2.0, color='#2c6fbb', marker='o', ms=2.4,
             label='二级债基指数（锚 %s = 1）' % ad)
    ax1.fill_between(cut['date'], hs, cb, where=(hs >= cb), color='#c0392b', alpha=0.12, lw=0)
    ax1.fill_between(cut['date'], hs, cb, where=(hs < cb), color='#2c6fbb', alpha=0.12, lw=0)
    # 交叉点标记
    prev = None
    for i in range(len(cut)):
        g = cut['gap'].iloc[i]
        if prev is not None and pd.notna(g):
            if prev <= 0 < g:
                ax1.scatter([cut['date'].iloc[i]], [hs.iloc[i]], s=90, marker='^',
                            color='#1a9850', zorder=7)
            elif prev >= 0 > g:
                ax1.scatter([cut['date'].iloc[i]], [hs.iloc[i]], s=90, marker='v',
                            color='#d73027', zorder=7)
        prev = g
    for v, nm in ((hs, '股'), (cb, '债')):
        ax1.annotate('%s %.4f' % (nm, v.iloc[-1]),
                     xy=(cut['date'].iloc[-1], v.iloc[-1]), xytext=(6, 0),
                     textcoords='offset points', fontsize=8.5, va='center')
    ax1.set_ylabel('近一年锚归一 = 1')
    ax1.set_title('股债相对关系 · 近 %d 个月（近一年收益为锚 %s=1）'
                  % (months, ad), fontsize=11.5)
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(alpha=0.25)
    ax1.margins(x=0.07)

    w = min(win, max(3, len(R) // 3))
    mid = cut['R_mid']
    up, lo = cut['R_up'], cut['R_lo']
    ax2.plot(cut['date'], R, lw=2.0, color='#1f3f7a', marker='o', ms=2.4, label='比值 R')
    ax2.plot(cut['date'], up, lw=1.0, ls='--', color='#d73027', label='上轨 +%gσ' % k)
    ax2.plot(cut['date'], mid, lw=1.0, ls='-', color='#888', alpha=0.75, label='中轴 MA%d' % w)
    ax2.plot(cut['date'], lo, lw=1.0, ls='--', color='#1a9850', label='下轨 -%gσ' % k)
    ax2.fill_between(cut['date'], lo, up, color='#9bb7d4', alpha=0.15, lw=0)
    ax2.annotate('%.4f' % R.iloc[-1], xy=(cut['date'].iloc[-1], R.iloc[-1]),
                 xytext=(6, 0), textcoords='offset points', fontsize=9,
                 color='#1f3f7a', va='center')
    ax2.set_ylabel('R = 股 / 债')
    ax2.set_title('股债比值 R · 布林带（MA%d ±%gσ）｜上轨=R偏贵 下轨=R偏便宜' % (w, k), fontsize=10.5)
    ax2.legend(loc='upper left', fontsize=8.5, ncol=2)
    ax2.grid(alpha=0.25)
    ax2.margins(x=0.07)

    fig.tight_layout()
    fig.subplots_adjust(right=0.90)
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return out_png


# ---------------------------------------------------------------- 邮件
def send_email(sig, info, png):
    import subprocess
    items = "\n".join("- %s（%s）：%s" % (t, k, det) for k, t, det in sig)
    subj = "🚨 股债比值告警 · %s · %s" % (info['date'], "、".join(t for _, t, _ in sig))
    body = ("# 🚨 股债比值 R 告警（近一年锚）\n\n**%s**（锚点 %s）\n\n%s\n\n"
            "---\n\n"
            "| 项 | 数值 |\n|---|---|\n"
            "| 比值 R | **%.4f** |\n"
            "| 布林上轨 | %s |\n| 中轴 MA20 | %s |\n| 布林下轨 | %s |\n"
            "| 股线（锚=1） | %.4f |\n| 债线（锚=1） | %.4f |\n"
            "| 股−债 | %+.4f |\n\n"
            "距上轨 %s ｜ 距下轨 %s\n\n**图见附件。**"
            % (info['date'], info['anchor_date'], items,
               info['R'],
               ('%.4f' % info['R_up']) if info['R_up'] else '—',
               ('%.4f' % info['R_mid']) if info['R_mid'] else '—',
               ('%.4f' % info['R_lo']) if info['R_lo'] else '—',
               info['hs_a'], info['cb_a'], info['gap'],
               ('%+.2f%%' % info['dist_up_pct']) if info['dist_up_pct'] is not None else '—',
               ('%+.2f%%' % info['dist_lo_pct']) if info['dist_lo_pct'] is not None else '—'))
    cmd = [sys.executable, os.path.join(WS, 'scripts', 'send_email.py'),
           '--subject', subj, '--body', body, '--html']
    if png and os.path.exists(png):
        cmd += ['--attach', png]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    print('[email]', r.returncode, (r.stdout or '')[-200:], (r.stderr or '')[-300:])
    return r.returncode == 0


# ---------------------------------------------------------------- 报告文字
def report_md(df, sig, info):
    L = ["**📈 股债比值 R（近一年锚）**"]
    L.append("锚点 %s = 1　｜　股线 **%.4f**　债线 **%.4f**　差 **%+.4f**"
             % (info['anchor_date'], info['hs_a'], info['cb_a'], info['gap']))
    L.append("比值 **R = %.4f**　｜　布林上轨 %s / 中轴 %s / 下轨 %s"
             % (info['R'],
                ('%.4f' % info['R_up']) if info['R_up'] else '—',
                ('%.4f' % info['R_mid']) if info['R_mid'] else '—',
                ('%.4f' % info['R_lo']) if info['R_lo'] else '—'))
    if sig:
        for _k, t, det in sig:
            L.append("%s — %s" % (t, det))
    else:
        L.append("✅ 无告警：R 在布林带内、股债线未交叉")
    return "\n".join(L)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chart-out', default=None)
    ap.add_argument('--months', type=int, default=3, help='图显示窗口（锚点固定 12 个月）')
    ap.add_argument('--no-chart', action='store_true')
    ap.add_argument('--no-email', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--upto', default=None, help='YYYY-MM-DD，回放到该日')
    a = ap.parse_args()

    df = compute()
    sig, info = detect(df, a.upto)
    if a.upto:
        df = df[df['date'] <= pd.Timestamp(a.upto)].reset_index(drop=True)

    png = None
    if not a.no_chart:
        out = a.chart_out or DEF_PNG
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        try:
            png = make_chart(df, out, months=a.months)
        except Exception as e:
            print('[warn] 出图失败: %r' % (e,), file=sys.stderr)

    md = report_md(df, sig, info)

    # 告警去重 → 邮件
    sent = False
    if sig and not a.no_email:
        fresh = dedup(sig, info['date'])
        if fresh:
            print('[alert] 新告警 %s → 发邮件' % [k for k, _, _ in fresh])
            sent = send_email(fresh, info, png)
        else:
            print('[alert] 信号 %s 已在 %d 日内发过，跳过邮件'
                  % ([k for k, _, _ in sig], DEDUP_DAYS))

    res = {'info': info, 'signals': [{'key': k, 'title': t, 'detail': d} for k, t, d in sig],
           'png': png, 'md': md, 'email_sent': sent}
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(md)
        print('\n[info]', json.dumps(info, ensure_ascii=False))
        if png:
            print('[png]', png)
    return 0


if __name__ == '__main__':
    sys.exit(main())
