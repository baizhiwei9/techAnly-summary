#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股两融全景图（融资余额 + 融券 + 账户数 + 担保比例 + MACD）
=====================================================================
大佬 2026-11-20 要求：
  「我忘了是哪个任务里边有一个画两融的那个图。还有那个 MACD……
    把它这个两融的这个图放在技术分析汇总这个技能里面。
    如果哪一天我让它给我出 A 股的两融的图，他把那个图给我，
    就是融资啊、账户啊、融资融券的余额啊，还什么的，把那图给我发过来。」

口径（唯一数据源 akshare `stock_margin_account_info`，2012 年至今全量）
-------------------------------------------------------------------
  · 融资余额 / 融券余额（亿元）
  · 融资买入额 / 融券卖出额（亿元）
  · 个人投资者数量 / 机构投资者数量（万名）
  · 参与交易的投资者数量 / 有融资融券负债的投资者数量（万名）
  · 担保物总价值（亿元）
  · 平均维持担保比例（%）

MACD 口径：**10/20/5**（本库全库统一口径，禁止 12/26/9），
      DIF = EMA10 − EMA20，DEA = EMA(DIF, 5)，柱 = 2×(DIF−DEA)，
      作用在**融资余额**上——用来看杠杆存量的动能/背驰。

面板（纵向 5 段，共享 x 轴）：
  ① 融资余额 + MA5/MA20 + MACD(10,20,5) 柱与 DIF/DEA
  ② 融券余额（亿元）
  ③ 融资买入额 vs 融资偿还额 + 单日净买入柱
  ④ 两融账户：个人 / 机构 / 参与交易 / 有负债（万名）
  ⑤ 平均维持担保比例（%）+ 担保物总价值（万亿元，右轴）

用法：
  python3 charts/margin.py                     # 近 250 交易日
  python3 charts/margin.py --days 500
  python3 charts/margin.py --start 2024-01-01 --out /tmp/rzrq.png
  python3 charts/margin.py --json              # 只回数值
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys

# ---------- 强制 IPv4（容器 IPv6 不通，akshare 走 IPv6 会静默卡死） ----------
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k)
                                      if r[0] == socket.AF_INET]

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403,E402  (仓库根/sys.path/net_guard/Agg/中文字体)
from charts.common import WS  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 10, 20, 5   # 全库统一口径，勿改

_CN = dict(fontsize=9)


# ============================ 取数 ============================
def fetch(days: int = 250, start: str = "") -> pd.DataFrame:
    """akshare 全量两融账户汇总（2012 起），返回带规范化列名的 DataFrame。"""
    import akshare as ak
    df = ak.stock_margin_account_info()
    if df is None or df.empty:
        raise SystemExit("[margin] akshare stock_margin_account_info 返回空")

    ren = {
        '日期': 'd', '融资余额': 'rzye', '融券余额': 'rqye',
        '融资买入额': 'rzmre', '融券卖出额': 'rqmcl',
        '证券公司数量': 'n_broker', '营业部数量': 'n_dept',
        '个人投资者数量': 'n_indiv', '机构投资者数量': 'n_inst',
        '参与交易的投资者数量': 'n_active', '有融资融券负债的投资者数量': 'n_debt',
        '担保物总价值': 'collateral', '平均维持担保比例': 'ratio',
    }
    df = df.rename(columns=ren)
    keep = [c for c in ren.values() if c in df.columns]
    df = df[keep].copy()
    df['d'] = pd.to_datetime(df['d'], errors='coerce')
    df = df.dropna(subset=['d'])
    for c in keep:
        if c != 'd':
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.sort_values('d').reset_index(drop=True)

    if start:
        df = df[df['d'] >= pd.Timestamp(start)].reset_index(drop=True)
    if days and len(df) > days:
        df = df.tail(days).reset_index(drop=True)
    return df


def macd_series(s: pd.Series):
    """MACD(10,20,5)，与 chan_smc_index.macd_hist 同口径。"""
    dif = (s.ewm(span=MACD_FAST, adjust=False).mean()
           - s.ewm(span=MACD_SLOW, adjust=False).mean())
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    hist = 2 * (dif - dea)
    return dif, dea, hist


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    last, prev = df.iloc[-1], (df.iloc[-2] if len(df) > 1 else df.iloc[-1])
    dif, dea, hist = macd_series(df['rzye'])
    def pct5(col):
        v = df[col].dropna()
        if len(v) < 6 or not v.iloc[-6]:
            return None
        return round((v.iloc[-1] / v.iloc[-6] - 1) * 100, 2)
    return {
        'date': last['d'].strftime('%Y-%m-%d'),
        'rzye': round(float(last['rzye']), 2),
        'rqye': round(float(last['rqye']), 2),
        'rzrqye': round(float(last['rzye'] + last['rqye']), 2),
        'rzye_d1': round(float(last['rzye'] - prev['rzye']), 2),
        'rzye_w1_pct': pct5('rzye'),
        'rzmre': round(float(last['rzmre']), 2),
        'net_buy': round(float(last['rzye'] - prev['rzye']), 2),   # 融资净买入 = Δ融资余额
        'n_indiv': round(float(last['n_indiv']), 2),
        'n_inst': round(float(last['n_inst']), 2),
        'n_debt': round(float(last['n_debt']) / 1e4, 2),
        'n_active': round(float(last['n_active']) / 1e4, 2),
        'ratio': round(float(last['ratio']), 2),
        'macd_dif': round(float(dif.iloc[-1]), 2),
        'macd_dea': round(float(dea.iloc[-1]), 2),
        'macd_hist': round(float(hist.iloc[-1]), 2),
        'macd_hist_prev': round(float(hist.iloc[-2]), 2) if len(hist) > 1 else None,
        'bars': int(len(df)),
    }


# ============================ 出图 ============================
def plot(d: pd.DataFrame, out: str, title_extra: str = "") -> str:
    dif, dea, hist = macd_series(d['rzye'])
    x = d['d']
    fig = plt.figure(figsize=(15, 15))
    gs = fig.add_gridspec(5, 1, height_ratios=[3.0, 1.0, 1.5, 1.3, 1.4], hspace=0.10)

    UP, DN = '#c0392b', '#1e8449'

    # ---------- ① 融资余额 + MACD ----------
    ax1 = fig.add_subplot(gs[0])
    ax1.plot(x, d['rzye'], color='#c0392b', lw=2.0, label='融资余额（亿）')
    ax1.plot(x, d['rzye'].rolling(5).mean(), color='#e67e22', lw=1.1,
             ls='--', label='MA5')
    ax1.plot(x, d['rzye'].rolling(20).mean(), color='#2c3e50', lw=1.1,
             ls=':', label='MA20')
    ax1b = ax1.twinx()
    cols = [UP if v >= 0 else DN for v in hist]
    ax1b.bar(x, hist, color=cols, alpha=0.45, width=0.85, label='MACD 柱')
    ax1b.plot(x, dif, color='#e67e22', lw=1.0, label='DIF')
    ax1b.plot(x, dea, color='#34495e', lw=1.0, label='DEA')
    ax1b.set_ylabel('MACD(10,20,5)', **_CN)
    ax1.set_ylabel('融资余额（亿元）', **_CN)
    ax1.set_title('A股两融全景 · %s ~ %s%s（融资余额 + MACD(10,20,5)）'
                  % (d['d'].iloc[0].strftime('%Y-%m-%d'),
                     d['d'].iloc[-1].strftime('%Y-%m-%d'),
                     (' · ' + title_extra) if title_extra else ''),
                  fontsize=15, pad=12)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1b.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc='upper left', ncol=5, fontsize=9, framealpha=0.9)
    ax1.grid(alpha=0.25)

    last = d.iloc[-1]
    ax1.annotate('%.0f亿' % last['rzye'], xy=(last['d'], last['rzye']),
                 xytext=(-8, 10), textcoords='offset points',
                 fontsize=10, color='#c0392b', fontweight='bold')

    # ---------- ② 融券余额 ----------
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax2.fill_between(x, 0, d['rqye'], color='#8e44ad', alpha=0.40, lw=0)
    ax2.plot(x, d['rqye'], color='#6c3483', lw=1.3, label='融券余额（亿）')
    ax2.set_ylabel('融券余额\n（亿元）', **_CN)
    ax2.legend(loc='upper left', fontsize=9)
    ax2.grid(alpha=0.2)

    # ---------- ③ 融资买入 vs 偿还 + 净买入 ----------
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    repay = d['rzmre'] - d['rzye'].diff().fillna(0)     # 买入 − Δ余额 = 偿还
    net = d['rzmre'] - repay
    ax3.bar(x, net, color=[UP if v >= 0 else DN for v in net],
            alpha=0.55, width=0.85, label='单日净买入（亿）')
    ax3.plot(x, d['rzmre'], color='#d35400', lw=1.0, label='融资买入额')
    ax3.plot(x, repay, color='#16a085', lw=1.0, label='融资偿还额')
    ax3.axhline(0, color='#555', lw=0.8)
    ax3.set_ylabel('买入/偿还\n净买入（亿元）', **_CN)
    ax3.legend(loc='upper left', ncol=3, fontsize=9)
    ax3.grid(alpha=0.2)

    # ---------- ④ 两融账户 ----------
    ax4 = fig.add_subplot(gs[3], sharex=ax1)
    ax4.plot(x, d['n_indiv'], color='#2980b9', lw=1.6, label='个人投资者（万名）')
    ax4.plot(x, d['n_inst'], color='#b9770e', lw=1.6, label='机构投资者（名，右轴）')
    ax4r = ax4.twinx()
    ax4r.plot(x, d['n_debt'] / 1e4, color='#c0392b', lw=1.2, ls='--',
              label='有融资融券负债（万名，右轴2）')
    ax4r.plot(x, d['n_active'] / 1e4, color='#7f8c8d', lw=1.0, ls=':',
              label='参与交易（万名，右轴2）')
    ax4.set_ylabel('个人投资者\n（万名）', **_CN)
    ax4r.set_ylabel('机构（名）/ 负债·参与交易（万名）', **_CN)
    h4, l4 = ax4.get_legend_handles_labels()
    h4r, l4r = ax4r.get_legend_handles_labels()
    ax4.legend(h4 + h4r, l4 + l4r, loc='upper left', ncol=2, fontsize=9)
    ax4.grid(alpha=0.2)

    # ---------- ⑤ 平均维持担保比例 + 担保物 ----------
    ax5 = fig.add_subplot(gs[4], sharex=ax1)
    ax5.plot(x, d['ratio'], color='#c0392b', lw=1.8, label='平均维持担保比例（%）')
    ax5.axhline(150, color='#e67e22', ls='--', lw=1.0, label='150% 警戒')
    ax5.axhline(130, color='#c0392b', ls='--', lw=1.0, label='130% 平仓')
    ax5.set_ylabel('维持担保\n比例（%）', **_CN)
    ax5r = ax5.twinx()
    ax5r.fill_between(x, 0, d['collateral'] / 1e4, color='#5dade2', alpha=0.35, lw=0,
                      label='担保物总价值（万亿元，右轴）')
    ax5r.set_ylabel('担保物（万亿元）', **_CN)
    h5, l5 = ax5.get_legend_handles_labels()
    h5r, l5r = ax5r.get_legend_handles_labels()
    ax5.legend(h5 + h5r, l5 + l5r, loc='upper left', ncol=4, fontsize=9)
    ax5.grid(alpha=0.2)
    ax5.set_xlabel('')

    for ax in (ax1, ax2, ax3, ax4, ax5):
        ax.tick_params(axis='x', labelrotation=0, labelsize=8)

    fig.text(0.5, 0.005,
             '数据源：akshare · stock_margin_account_info（两融账户全量汇总）｜'
             'MACD 参数 10/20/5（本库统一口径）｜'
             '净买入 = 融资买入额 − 融资偿还额，偿还额 = 买入额 − Δ融资余额',
             ha='center', fontsize=8.5, color='#555')

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    fig.savefig(out, dpi=130, facecolor='white', bbox_inches='tight')
    plt.close(fig)
    return out


def render_text(s: dict) -> str:
    if not s:
        return '【两融】无数据'
    arrow = '↑' if s['rzye_d1'] > 0 else ('↓' if s['rzye_d1'] < 0 else '→')
    h = s.get('macd_hist') or 0
    hp = s.get('macd_hist_prev') or 0
    mtag = '红柱放大' if h > 0 and h > hp else ('绿柱扩大' if h < 0 and h < hp else
                                             '红柱缩量' if h > 0 else '绿柱收敛')
    L = [
        '💰 A股两融（%s 口径）' % s['date'],
        '· 融资余额 %.0f亿（日 %+.0f亿 %s｜5日 %s%%）' % (
            s['rzye'], s['rzye_d1'], arrow,
            ('%+.2f' % s['rzye_w1_pct']) if s['rzye_w1_pct'] is not None else '—'),
        '· 融券余额 %.0f亿 ｜ 两融合计 %.0f亿' % (s['rqye'], s['rzrqye']),
        '· 融资买入 %.0f亿 ｜ 当日净买入 %+.0f亿' % (s['rzmre'], s['net_buy']),
        '· 两融账户：个人 %.1f万名 / 机构 %.0f名 / 有负债 %.1f万名 / 参与交易 %.1f万名' % (
            s['n_indiv'], s['n_inst'], s['n_debt'], s['n_active']),
        '· 平均维持担保比例 %.1f%%' % s['ratio'],
        '· MACD(10,20,5)：DIF %.1f / DEA %.1f / 柱 %.1f（%s）' % (
            s['macd_dif'], s['macd_dea'], s['macd_hist'], mtag),
    ]
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='A股两融全景图（融资余额/融券/账户/担保比例 + MACD10,20,5）')
    ap.add_argument('--days', type=int, default=250, help='最近 N 个交易日（默认 250）')
    ap.add_argument('--start', default='', help='起始日 YYYY-MM-DD（给了就忽略 --days 上限）')
    ap.add_argument('--out', default='')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    d = fetch(days=(0 if a.start else a.days), start=a.start)
    if d.empty:
        raise SystemExit('[margin] 无数据')
    s = summary(d)
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return s

    out = a.out or os.path.join(WS, 'reports', 'charts', 'margin',
                                '两融全景_%s.png' % d['d'].iloc[-1].strftime('%Y%m%d'))
    p = plot(d, out)
    print(render_text(s))
    print('[margin] 图 → %s' % p)
    return s


if __name__ == '__main__':
    main()
