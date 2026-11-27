#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股债利差 EY - BY = 1/PE(TTM) - 10Y国债收益率
=============================================
经典股债利差指标：
  EY = 1 / 沪深300 PE_TTM（股票盈利收益率）
  BY = 中债国债收益率曲线 10年
  利差 = EY - BY（百分点）

■ PE 数据源（三级降级）
  1. Tushare index_dailybasic（2014-至今，日频 PE_TTM）
  2. 乐咕月频 PE + 指数价格反推（2005-2014，月频骨架日频插值）
  3. 乐咕月频 PE 直接用（月频，降级标注）

■ 10Y国债数据源
  akshare 中国债券信息网 国债收益率曲线

■ 分区规则（2.5年滚动布林带，约 625 交易日）
  - 上轨以上 = 股票罕见便宜 → 底部区
  - 中轴附近 = 中性
  - 下轨以下 = 股票相对昂贵 → 顶部区

用法：
  python3 strategies/eyby_spread.py                 # 打印日报
  python3 strategies/eyby_spread.py --json          # 只输出 JSON
  python3 strategies/eyby_spread.py --chart         # 另存 PNG
  python3 strategies/eyby_spread.py --out xxx.md    # 写报告
"""
import os
import sys
import json
import socket
import argparse
import datetime as dt

# ---------- 强制 IPv4 ----------
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k)
                                      if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(WS, '.cache', 'eyby')
os.makedirs(CACHE, exist_ok=True)

SIGN = '📎 来自 OpenClaw.EYBY'

# ======================= 缓存工具 =======================
def _cache_path(name):
    return os.path.join(CACHE, f'{name}.csv')

def _cached_load(name, max_age_days=3):
    fp = _cache_path(name)
    if not os.path.exists(fp):
        return None
    try:
        df = pd.read_csv(fp, parse_dates=['date'])
        if (dt.date.today() - df['date'].iloc[-1].date()).days <= max_age_days:
            return df
    except Exception:
        pass
    return None

# ======================= 沪深300 PE =======================
def get_hs300_pe_tushare():
    """Tushare 日频 PE_TTM（2014-至今）。"""
    try:
        sys.path.insert(0, WS)
        sys.path.insert(0, os.path.join(WS, 'bin'))
        from tushare_init import get_pro
        pro = get_pro()
        frames = []
        # tushare 单次最多 6000 条，分段拉
        years = [('20140101', '20191231'),
                 ('20200101', '20241231'),
                 ('20250101', dt.date.today().strftime('%Y%m%d'))]
        for s, e in years:
            d = pro.index_dailybasic(ts_code='000300.SH', start_date=s, end_date=e,
                                     fields='trade_date,pe_ttm,pb,turnover_rate')
            if d is not None and len(d):
                frames.append(d)
        if not frames:
            return None
        df = pd.concat(frames)
        df['date'] = pd.to_datetime(df['trade_date'])
        df = df[['date', 'pe_ttm', 'pb', 'turnover_rate']].dropna(subset=['pe_ttm'])
        df = df.sort_values('date').reset_index(drop=True)
        # 去重
        df = df.drop_duplicates(subset=['date']).reset_index(drop=True)
        df.to_csv(_cache_path('hs300_pe_ts'), index=False)
        return df
    except Exception as e:
        print(f'[eyby] tushare PE 失败: {e!r}', file=sys.stderr)
        return None


def get_hs300_pe_lg():
    """乐咕月频 PE（2005-至今，月频）。"""
    try:
        from data_hub import ak as ak
        df = ak.stock_index_pe_lg(symbol="沪深300")
        df['date'] = pd.to_datetime(df['日期'])
        df = df.rename(columns={'滚动市盈率': 'pe_ttm', '静态市盈率': 'pe'})
        df = df[['date', 'pe_ttm', 'pe']].dropna()
        df = df.sort_values('date').reset_index(drop=True)
        df.to_csv(_cache_path('hs300_pe_lg'), index=False)
        return df
    except Exception as e:
        print(f'[eyby] 乐咕 PE 失败: {e!r}', file=sys.stderr)
        return None


def get_hs300_price_daily():
    """沪深300 日频收盘价（用于月频→日频反推 PE）。"""
    try:
        from data_hub import ak as ak
        df = ak.stock_zh_index_hist_csindex(symbol='000300',
                                            start_date='20040101',
                                            end_date=dt.date.today().strftime('%Y%m%d'))
        df['date'] = pd.to_datetime(df['日期'])
        df = df.rename(columns={'收盘': 'close'})
        df = df[['date', 'close']].dropna()
        df = df.sort_values('date').reset_index(drop=True)
        df.to_csv(_cache_path('hs300_price'), index=False)
        return df
    except Exception as e:
        print(f'[eyby] 沪深300价格失败: {e!r}', file=sys.stderr)
        return None


def build_pe_series(force=False):
    """构建完整日频 PE 序列（2005-至今）。
    
    策略：
    - 优先用 Tushare 日频（2014-至今）
    - 2005-2014 用乐咕月频 + 价格日频反推（盈利=价格/PE，月内盈利不变）
    - 合并后做平滑衔接
    """
    if not force:
        cached = _cached_load('hs300_pe_combined', max_age_days=3)
        if cached is not None:
            return cached

    pe_ts = get_hs300_pe_tushare()
    pe_lg = get_hs300_pe_lg()
    price = get_hs300_price_daily()

    if pe_ts is None and pe_lg is None:
        raise RuntimeError('所有 PE 数据源均失败')

    # --- 日频部分：Tushare ---
    daily_part = None
    if pe_ts is not None and len(pe_ts) > 0:
        daily_part = pe_ts[['date', 'pe_ttm', 'turnover_rate']].copy()
        daily_part = daily_part.rename(columns={'turnover_rate': 'turnover'})
        daily_part['src'] = 'tushare'

    # --- 远期部分：乐咕月频反推日频 ---
    early_part = None
    if pe_lg is not None and price is not None:
        # 月频 PE → 月度盈利（盈利 = 价格 / PE）
        # 取每月最后一个交易日的价格作为月价格
        price_m = price.copy()
        price_m['ym'] = price_m['date'].dt.to_period('M')
        price_m = price_m.groupby('ym').last().reset_index()
        price_m = price_m[['date', 'close']].rename(columns={'date': 'month_end', 'close': 'month_price'})
        price_m['ym'] = price_m['month_end'].dt.to_period('M')

        pe_lg['ym'] = pe_lg['date'].dt.to_period('M')
        merged = pe_lg.merge(price_m[['ym', 'month_price']], on='ym', how='left')
        # 盈利 E = P / PE
        merged['earnings'] = merged['month_price'] / merged['pe_ttm']

        # 日频价格 × 月度盈利 → 日频 PE
        price_d = price.copy()
        price_d['ym'] = price_d['date'].dt.to_period('M')
        early = price_d.merge(merged[['ym', 'earnings', 'pe_ttm']], on='ym', how='left')
        early['pe_ttm_daily'] = early['close'] / early['earnings']
        early = early.rename(columns={'pe_ttm': 'pe_ttm_monthly'})
        early['pe_ttm'] = early['pe_ttm_daily']
        early['src'] = 'lg_reconstruct'
        early = early[['date', 'pe_ttm', 'src']].dropna()

        # 只保留 tushare 开始日之前的
        if daily_part is not None and len(daily_part):
            cut = daily_part['date'].iloc[0]
            early_part = early[early['date'] < cut].copy()
        else:
            early_part = early.copy()

    # --- 合并 ---
    parts = []
    if early_part is not None and len(early_part):
        parts.append(early_part)
    if daily_part is not None and len(daily_part):
        parts.append(daily_part)

    if not parts:
        raise RuntimeError('无法构建 PE 序列')

    df = pd.concat(parts).sort_values('date').reset_index(drop=True)
    df = df.drop_duplicates(subset=['date']).reset_index(drop=True)
    df.to_csv(_cache_path('hs300_pe_combined'), index=False)
    return df


# ======================= 10年国债收益率 =======================
def get_bond_10y(force=False):
    """10年期国债收益率（akshare bond_china_yield）。"""
    if not force:
        cached = _cached_load('bond_10y')
        if cached is not None:
            return cached
    from data_hub import ak as ak
    try:
        # 用 bond_zh_us_rate 接口（数据完整 1990-至今）
        df = ak.bond_zh_us_rate(start_date='20040101')
        df['日期'] = pd.to_datetime(df['日期'])
        df10 = df.rename(columns={'日期': 'date', '中国国债收益率10年': 'yield_10y'})
        df10 = df10[['date', 'yield_10y']].dropna()
        df10 = df10.sort_values('date').reset_index(drop=True)
    except Exception as e:
        print(f'[eyby] 国债收益率主接口失败: {e!r}', file=sys.stderr)
        # 先退回本地缓存（过期也用，避免整张报表中断）
        cached = _cached_load('bond_10y', max_age_days=3650) if not force else None
        if cached is not None:
            print('[eyby] ⚠️ 改用缓存中的 10Y 国债收益率', file=sys.stderr)
            return cached
        raise

    df10.to_csv(_cache_path('bond_10y'), index=False)
    return df10


# ======================= 计算 =======================
def build_series(force=False):
    pe = build_pe_series(force=force)
    bd = get_bond_10y(force=force)

    pe_s = pe[['date', 'pe_ttm', 'src']].copy()
    bd_s = bd[['date', 'yield_10y']].copy()

    df = pe_s.merge(bd_s, on='date', how='inner').sort_values('date').reset_index(drop=True)
    df['ey'] = 1.0 / df['pe_ttm'] * 100    # 盈利收益率，百分点
    df['by'] = df['yield_10y']             # 国债收益率，百分点
    df['spread'] = df['ey'] - df['by']     # 股债利差，百分点
    return df


def bollinger_bands(df, window=625):
    """2.5年滚动布林线（约 625 个交易日）。"""
    df = df.copy()
    mid = df['spread'].rolling(window, min_periods=window // 2).mean()
    std = df['spread'].rolling(window, min_periods=window // 2).std()
    df['mid'] = mid
    df['upper'] = mid + 2 * std
    df['lower'] = mid - 2 * std
    return df


def classify(df):
    """基于布林带的分区。"""
    row = df.iloc[-1]
    s = float(row['spread'])
    u = float(row['upper'])
    l = float(row['lower'])

    if pd.isna(u) or pd.isna(l):
        return '数据不足', '⚪', '布林带窗口不足，无法分区', 0.5

    if abs(u - l) < 1e-6:
        pos = 0.5
    else:
        pos = (s - l) / (u - l)
    pos = max(0.0, min(1.0, pos))

    if pos >= 1.0:
        zone, emoji, action = '上轨以上（极端便宜）', '🟢', '股票相对债券极端便宜，历史级底部区'
    elif pos >= 0.75:
        zone, emoji, action = '上半区（偏便宜）', '🟡', '股票估值偏低，可分批布局'
    elif pos >= 0.25:
        zone, emoji, action = '中轴附近（中性）', '⚪', '无明显信号'
    elif pos > 0.0:
        zone, emoji, action = '下半区（偏贵）', '🟠', '股票估值偏贵，注意控制仓位'
    else:
        zone, emoji, action = '下轨以下（极端昂贵）', '🔴', '股票相对债券极端昂贵，历史级顶部区'
    return zone, emoji, action, pos


# ======================= 报告 =======================
def build_report(df_):
    df = bollinger_bands(df_)
    row = df.iloc[-1]
    d = pd.Timestamp(row['date'])
    s = float(row['spread'])
    pe = float(row['pe_ttm'])
    ey = float(row['ey'])
    by = float(row['by'])
    mid = float(row['mid']) if pd.notna(row['mid']) else float('nan')
    upper = float(row['upper']) if pd.notna(row['upper']) else float('nan')
    lower = float(row['lower']) if pd.notna(row['lower']) else float('nan')

    zone, emoji, action, pos = classify(df)

    # 与上周变化（5 个交易日）
    idx = df.index[df['date'] == row['date']][0]
    prev5 = df.iloc[max(idx - 5, 0)]
    chg = s - float(prev5['spread'])
    prev5_date = pd.Timestamp(prev5['date'])

    # 历史分位
    valid = df['spread'].dropna()
    pct = float((valid <= s).mean() * 100) if len(valid) else float('nan')

    bb_pos_pct = pos * 100

    L = []
    L.append(f'# 股债利差 EY-BY 日报 · {d.strftime("%Y-%m-%d")}')
    L.append('')
    L.append(f'**{SIGN}**')
    L.append('')
    L.append('## 一句话')
    L.append('')
    L.append(f'{emoji} **{zone}** — {action}')
    L.append('')
    L.append('## 核心数据')
    L.append('')
    L.append('| 项目 | 数值 |')
    L.append('|---|---|')
    L.append(f'| 日期 | {d.strftime("%Y-%m-%d")} |')
    L.append(f'| **股债利差** | **{s:.2f}%** |')
    L.append(f'| 沪深300 PE(TTM) | {pe:.2f} |')
    L.append(f'| 盈利收益率 EY | {ey:.2f}% |')
    L.append(f'| 10年期国债收益率 BY | {by:.2f}% |')
    L.append(f'| 布林带位置 | {bb_pos_pct:.0f}%（0%=下轨，100%=上轨） |')
    L.append(f'| 与上周变化 | {chg:+.2f}pp（对比 {prev5_date.strftime("%Y-%m-%d")}） |')
    L.append(f'| 历史分位 | {pct:.0f}%（{len(valid)} 个交易日） |')
    L.append('')
    L.append('## 布林带参考线（2.5年滚动）')
    L.append('')
    if pd.notna(upper):
        L.append('| 线 | 值 |')
        L.append('|---|---|')
        L.append(f'| 上轨（+2σ） | {upper:.2f}% |')
        L.append(f'| 中轴（MA） | {mid:.2f}% |')
        L.append(f'| 下轨（-2σ） | {lower:.2f}% |')
    else:
        L.append('（数据窗口不足，布林带待形成）')
    L.append('')
    L.append('## 数据来源')
    L.append('')
    src = row.get('src', 'unknown')
    src_txt = {'tushare': 'Tushare index_dailybasic 日频',
               'lg_reconstruct': '⚠️ 乐咕月频反推（价格×月度盈利）'}.get(str(src), str(src))
    L.append(f'- 沪深300 PE(TTM)：{src_txt}')
    L.append('- 10年期国债收益率：中国债券信息网 / akshare')
    L.append('- 布林带参数：2.5年滚动（约625交易日），2倍标准差')
    L.append('')
    return '\n'.join(L), {
        'date': d.strftime('%Y-%m-%d'),
        'spread': round(s, 3),
        'pe': round(pe, 3),
        'ey': round(ey, 3),
        'by': round(by, 3),
        'zone': zone,
        'emoji': emoji,
        'action': action,
        'bb_pos_pct': round(bb_pos_pct, 1),
        'chg_pp': round(chg, 3),
        'pct': round(pct, 1) if not pd.isna(pct) else None,
        'upper': round(upper, 3) if pd.notna(upper) else None,
        'mid': round(mid, 3) if pd.notna(mid) else None,
        'lower': round(lower, 3) if pd.notna(lower) else None,
        'prev5_date': prev5_date.strftime('%Y-%m-%d'),
        'src': str(src),
    }


# ======================= 图表 =======================
def make_chart(df, out_png):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for cand in ('Noto Sans CJK JP', 'Noto Sans CJK SC', 'WenQuanYi Zen Hei', 'DejaVu Sans'):
        if any(cand in f.name for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.sans-serif'] = [cand]
            break
    plt.rcParams['axes.unicode_minus'] = False

    df = bollinger_bands(df)
    fig, ax1 = plt.subplots(figsize=(14, 6))

    ax1.plot(df['date'], df['spread'], lw=1.3, color='#1f3f7a', label='股债利差 EY-BY (%)')
    mask = df['upper'].notna()
    if mask.any():
        ax1.plot(df.loc[mask, 'date'], df.loc[mask, 'upper'], lw=0.8, ls='--', color='#1a9850', label='上轨 (+2σ)')
        ax1.plot(df.loc[mask, 'date'], df.loc[mask, 'mid'], lw=0.8, ls='-', color='#888', alpha=0.6, label='中轴 (MA)')
        ax1.plot(df.loc[mask, 'date'], df.loc[mask, 'lower'], lw=0.8, ls='--', color='#d73027', label='下轨 (-2σ)')

        # 填充
        up_val = df.loc[mask, 'upper']
        lo_val = df.loc[mask, 'lower']
        ax1.fill_between(df.loc[mask, 'date'], up_val, df['spread'].max() * 1.05,
                         color='#1a9850', alpha=0.1, lw=0)
        ax1.fill_between(df.loc[mask, 'date'], lo_val, df['spread'].min() * 0.95,
                         color='#d73027', alpha=0.1, lw=0)

    ax1.scatter([df['date'].iloc[-1]], [df['spread'].iloc[-1]], s=55, zorder=5,
                color='#d73027', edgecolor='white', linewidth=1.2)
    ax1.annotate(f'{df["spread"].iloc[-1]:.2f}%\n{df["date"].iloc[-1].date()}',
                 xy=(df['date'].iloc[-1], df['spread'].iloc[-1]), xytext=(-70, 22),
                 textcoords='offset points', fontsize=9,
                 arrowprops=dict(arrowstyle='->', color='#d73027'))

    ax1.set_ylabel('股债利差（百分点）')
    ax1.set_title('股债利差 EY-BY · 沪深300盈利收益率 - 10年国债收益率（2.5年布林带）', fontsize=12)
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(alpha=0.25)

    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return out_png


# ======================= main =======================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--chart', action='store_true')
    ap.add_argument('--out', help='报告输出路径 md')
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    df = build_series(force=args.no_cache)
    if not len(df):
        print('数据为空', file=sys.stderr)
        return 2

    md, meta = build_report(df)

    if args.chart:
        png = os.path.join(WS, 'reports', f'eyby_{meta["date"]}.png')
        os.makedirs(os.path.dirname(png), exist_ok=True)
        make_chart(df, png)
        meta['chart'] = png

    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(md)

    if args.json:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    elif not args.quiet:
        print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
