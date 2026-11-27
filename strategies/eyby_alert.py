#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股债利差 EY−BY · 底部区间纪律线告警（纯脚本 0 token）
================================================================
2026-11-05 大佬定稿口径：
  「每次真实的大底，E/P − 10Y国债 都在 +5.6pct 以上，最好的机会处达到 +7~+8.7pct。
    底部区间下界 +5.5pct（2022 年 +6.1、2026 年现在 +5.64 是同一档）。
    **5.5P 以下就要报警发邮件**」

口径：
  · 股债利差 = EY − BY = 1/PE(TTM) − 10Y国债收益率（百分点）
  · 计算全在 bin/eyby_spread.py（本文件只做判定 / 出图 / 告警）
  · 纪律线 BOTTOM_FLOOR = 5.50pct
      利差 ≥ 5.50 → 底部区间档位（历史大底都在 5.6 以上）
      利差 < 5.50 → **跌破底部区间下界 → 报警发邮件**

告警规则（防抖）：
  · 只在「首次跌破」时报警（昨日 ≥5.5 且 今日 <5.5），不在轨下天天报
  · 同一信号 DEDUP_DAYS=7 个自然日内只发一次（.cache/eyby/eyby_alert_state.json）

用法：
  python3 strategies/eyby_alert.py                 # 计算 + 出图 + 必要时发邮件
  python3 strategies/eyby_alert.py --no-email      # 只算不发
  python3 strategies/eyby_alert.py --json          # 机器可读
  python3 strategies/eyby_alert.py --chart-out x.png
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

import pandas as pd

import eyby_spread as EB

STATE = os.path.join(WS, '.cache', 'eyby', 'eyby_alert_state.json')
DEF_PNG = os.path.join(WS, '.cache', 'eyby', 'eyby_floor55.png')

BOTTOM_FLOOR = 5.50      # 底部区间下界（大佬定稿）
GOOD_OPP = 7.00          # 很好的机会（仅标注，不单独报警）
DEDUP_DAYS = 7


# ---------------------------------------------------------------- 计算
def compute(df=None):
    if df is None:
        df = EB.build_series()
    df = EB.bollinger_bands(df.copy())
    df['date'] = pd.to_datetime(df['date'])
    return df


def detect(df):
    """检测最新一日是否跌破 5.5pct 底部区间下界。返回 (signals, info)。"""
    d = df.dropna(subset=['spread']).reset_index(drop=True)
    if len(d) < 2:
        return [], {}
    last, prev = d.iloc[-1], d.iloc[-2]

    s = float(last['spread'])
    sp = float(prev['spread'])
    sig = []
    if s < BOTTOM_FLOOR <= sp:
        sig.append(('break_floor', '🔴 跌破底部区间下界 5.5pct',
                    '利差 %.2f%% < 5.50%%（昨日 %.2f%%）—— 历史大底都在 +5.6pct 以上，'
                    '当前已脱离底部区间档位' % (s, sp)))

    def _f(x):
        return None if pd.isna(x) else round(float(x), 4)

    up, mid, lo = _f(last['upper']), _f(last['mid']), _f(last['lower'])
    pos = None
    if up is not None and lo is not None and up > lo:
        pos = max(0.0, min(1.0, (s - lo) / (up - lo)))

    valid = d['spread'].dropna()
    pct = float((valid <= s).mean() * 100) if len(valid) else None

    info = {
        'date': last['date'].strftime('%Y-%m-%d'),
        'spread': round(s, 3),
        'spread_prev': round(sp, 3),
        'chg_pp': round(s - sp, 3),
        'pe': round(float(last['pe_ttm']), 3),
        'ey': round(float(last['ey']), 3),
        'by': round(float(last['by']), 3),
        'floor': BOTTOM_FLOOR,
        'dist_floor_pp': round(s - BOTTOM_FLOOR, 3),          # 距下界（负=已跌破）
        'above_floor': bool(s >= BOTTOM_FLOOR),
        'floor_hit': bool(s < BOTTOM_FLOOR),
        'upper': up, 'mid': mid, 'lower': lo,
        'bb_pos_pct': (None if pos is None else round(pos * 100, 1)),
        'pct': (None if pct is None else round(pct, 1)),
        'src': str(last.get('src', '')),
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


def make_chart(df, out_png, months=36):
    """近 months 个月利差 + 5.5pct 纪律线 + 布林带。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _zh_font(plt)

    end = df['date'].iloc[-1]
    cut = df[df['date'] >= end - pd.DateOffset(months=months)].reset_index(drop=True)
    if len(cut) < 20:
        cut = df.tail(120).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(12.5, 6.2))
    ax.plot(cut['date'], cut['spread'], lw=1.6, color='#1f3f7a', label='股债利差 EY−BY')
    if cut['upper'].notna().any():
        ax.plot(cut['date'], cut['upper'], lw=0.9, ls='--', color='#1a9850', label='布林上轨 +2σ')
        ax.plot(cut['date'], cut['mid'], lw=0.9, ls='-', color='#999', alpha=0.8, label='中轴 MA')
        ax.plot(cut['date'], cut['lower'], lw=0.9, ls='--', color='#d73027', label='布林下轨 −2σ')

    ax.axhline(BOTTOM_FLOOR, color='#c0392b', lw=1.8, ls='-.',
               label='底部区间下界 5.5pct')
    ax.axhline(GOOD_OPP, color='#1e8449', lw=1.2, ls=':', alpha=0.9,
               label='很好的机会 7.0pct')
    ax.fill_between(cut['date'], BOTTOM_FLOOR, GOOD_OPP, color='#f6e3c5', alpha=0.45, lw=0)
    ax.fill_between(cut['date'], GOOD_OPP, cut['spread'].max() * 1.08,
                    color='#c8e6c9', alpha=0.40, lw=0)

    s = float(cut['spread'].iloc[-1])
    col = '#c0392b' if s >= BOTTOM_FLOOR else '#1e8449'
    ax.scatter([cut['date'].iloc[-1]], [s], s=70, color=col, zorder=6,
               edgecolor='white', linewidth=1.2)
    ax.annotate('%.2f%%  (%s)' % (s, cut['date'].iloc[-1].strftime('%m-%d')),
                xy=(cut['date'].iloc[-1], s), xytext=(-95, 18),
                textcoords='offset points', fontsize=10, color=col,
                arrowprops=dict(arrowstyle='->', color=col))

    ax.set_ylabel('股债利差（百分点）')
    ax.set_title('股债利差 EY−BY · 近 %d 个月 ｜ 底部区间下界 5.5pct（跌破报警）' % months,
                 fontsize=11.5)
    ax.legend(loc='upper left', fontsize=8.5, ncol=2)
    ax.grid(alpha=0.25)
    ax.margins(x=0.04)
    fig.tight_layout()
    fig.savefig(out_png, dpi=135)
    plt.close(fig)
    return out_png


# ---------------------------------------------------------------- 邮件
def send_email(sig, info, png):
    import subprocess
    items = "\n".join("- %s：%s" % (t, det) for _k, t, det in sig)
    subj = "🚨 股债利差跌破 5.5pct · %s · %.2f%%" % (info['date'], info['spread'])
    body = ("# 🚨 股债利差 EY−BY 跌破底部区间下界\n\n"
            "**%s**\n\n%s\n\n"
            "---\n\n"
            "| 项 | 数值 |\n|---|---|\n"
            "| 股债利差 EY−BY | **%.2f%%** |\n"
            "| 底部区间下界 | 5.50%% |\n"
            "| 距下界 | %+.2f pct |\n"
            "| 盈利收益率 EY | %.2f%% |\n"
            "| 10年国债收益率 | %.2f%% |\n"
            "| 沪深300 PE(TTM) | %.2f |\n"
            "| 历史分位 | %s |\n\n"
            "> 口径：真实大底利差都在 +5.6pct 以上，最好的机会 +7~+8.7pct；"
            "跌破 5.5pct = 已脱离底部区间档位。\n\n**图见附件。**"
            % (info['date'], items, info['spread'], info['dist_floor_pp'],
               info['ey'], info['by'], info['pe'],
               ('%.1f%%' % info['pct']) if info['pct'] is not None else '—'))
    try:
        import _notify
        res = _notify.send_email(subj, body, images=[png] if png and os.path.exists(png) else None)
        if res.get("ok"):
            print('[email] 已走统一通知（data_hub.notify）')
            return True
        print('[email] 统一库未就绪，回落 send_email.py：%s' % res.get("reason"))
    except Exception as exc:
        print('[email] 统一库异常，回落 send_email.py：%s' % exc)
    cmd = [sys.executable, os.path.join(WS, 'scripts', 'send_email.py'),
           '--subject', subj, '--body', body, '--html']
    if png and os.path.exists(png):
        cmd += ['--attach', png]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    print('[email]', r.returncode, (r.stdout or '')[-200:], (r.stderr or '')[-300:])
    return r.returncode == 0


# ---------------------------------------------------------------- 报告文字
def report_md(info, sig):
    fl = info['floor']
    L = ["**📉 股债利差 EY−BY（底部区间下界 %gpct）**" % fl]
    L.append("利差 **%.2f%%**（%+.2fpp）　｜　EY %.2f%% − 10Y **%.2f%%**　｜　PE(TTM) %.2f"
             % (info['spread'], info['chg_pp'], info['ey'], info['by'], info['pe']))
    if info['above_floor']:
        L.append("底部区间下界 **%.2fpct** → 当前 **高于下界 %+.2fpp**（✅ 仍在底部区间档位）"
                 % (fl, info['dist_floor_pp']))
    else:
        L.append("底部区间下界 **%.2fpct** → 当前 **低于下界 %+.2fpp**（🔴 已脱离底部区间档位）"
                 % (fl, info['dist_floor_pp']))
    if info['upper'] is not None:
        L.append("布林 上轨 %.2f / 中轴 %.2f / 下轨 %.2f（位置 %s%%）　｜　历史分位 %s%%"
                 % (info['upper'], info['mid'], info['lower'],
                    ('%.0f' % info['bb_pos_pct']) if info['bb_pos_pct'] is not None else '—',
                    ('%.0f' % info['pct']) if info['pct'] is not None else '—'))
    if sig:
        for _k, t, det in sig:
            L.append("%s — %s" % (t, det))
    else:
        L.append("✅ 无告警：利差未跌破 %.1fpct" % fl)
    return "\n".join(L)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chart-out', default=None)
    ap.add_argument('--months', type=int, default=36)
    ap.add_argument('--no-chart', action='store_true')
    ap.add_argument('--no-email', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    df = compute()
    sig, info = detect(df)
    if not info:
        print('数据不足', file=sys.stderr)
        return 2

    png = None
    if not a.no_chart:
        out = a.chart_out or DEF_PNG
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        try:
            png = make_chart(df, out, months=a.months)
        except Exception as e:
            print('[warn] 出图失败: %r' % (e,), file=sys.stderr)

    md = report_md(info, sig)
    sent = False
    if sig and not a.no_email:
        fresh = dedup(sig, info['date'])
        if fresh:
            print('[alert] 新告警 %s → 发邮件' % [k for k, _, _ in fresh])
            sent = send_email(fresh, info, png)
        else:
            print('[alert] 信号在 %d 天去重窗口内，跳过' % DEDUP_DAYS)

    if a.json:
        print(json.dumps({'info': info, 'signals': sig, 'md': md,
                          'png': png, 'email_sent': sent}, ensure_ascii=False))
    else:
        print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
