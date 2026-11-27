#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙虾缠论 · 龙虎榜选股 · 数据底座（增量版）
====================================
缓存结构：
  .cache/longxia/raw/<date>.pkl        单日全市场 daily + adj_factor
  .cache/longxia/swraw/<date>.pkl      单日申万全级指数（L1+L2+L3，一次请求全拿）
  .cache/longxia/sw_classify_L1.json   申万一级代码→名称
  .cache/longxia/sw_classify_L2.json   申万二级代码→名称
  .cache/longxia/sw_members.pkl        申万二级 → 成分股（TTL 30天）
  .cache/longxia/basic.pkl             stock_basic（TTL 24h）

用法：
  python3 strategies/longxia_panel.py --days 260            # 增量更新（日常跑这个，几秒到几十秒）
  python3 strategies/longxia_panel.py --days 260 --backfill # 强制补齐区间内所有缺失日
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, time, argparse, datetime, json
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tushare_init import get_pro

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(WS, '.cache', 'longxia')
RAW = os.path.join(CACHE, 'raw')
SWRAW = os.path.join(CACHE, 'swraw')
for p in (CACHE, RAW, SWRAW):
    os.makedirs(p, exist_ok=True)

pro = get_pro()


def log(*a):
    print(*a, flush=True)


def call(fn, retries=3, **kw):
    last = None
    for i in range(retries + 1):
        try:
            return fn(**kw)
        except Exception as e:
            last = e
            msg = str(e)
            if '每分钟' in msg or 'ip' in msg.lower() or 'limit' in msg.lower():
                time.sleep(20 + 10 * i)
            else:
                time.sleep(2 + 2 * i)
    log('[WARN] 失败 %s: %s' % (kw, str(last)[:150]))
    return None


def trade_dates(days):
    today = datetime.date.today().strftime('%Y%m%d')
    start = (datetime.date.today() - datetime.timedelta(days=int(days * 1.9) + 40)).strftime('%Y%m%d')
    df = call(pro.trade_cal, exchange='SSE', start_date=start, end_date=today, is_open='1')
    if df is None or df.empty:
        raise SystemExit('trade_cal 失败')
    d = sorted(df['cal_date'].tolist())
    now = datetime.datetime.now()
    if d and d[-1] == today and now.hour * 60 + now.minute < 15 * 60 + 30:
        d = d[:-1]
    return d[-days:]


def build_raw(dates, sleep=0.1):
    miss = [d for d in dates if not os.path.exists(os.path.join(RAW, '%s.pkl' % d))]
    log('个股面板：需补 %d 天 / 共 %d 天' % (len(miss), len(dates)))
    for i, d in enumerate(miss, 1):
        daily = call(pro.daily, trade_date=d)
        if daily is None or daily.empty:
            continue
        adj = call(pro.adj_factor, trade_date=d)
        if adj is not None and not adj.empty:
            daily = daily.merge(adj[['ts_code', 'adj_factor']], on='ts_code', how='left')
        else:
            daily['adj_factor'] = 1.0
        daily['adj_factor'] = daily['adj_factor'].fillna(1.0)
        daily[['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close',
               'pct_chg', 'vol', 'amount', 'adj_factor']].to_pickle(
            os.path.join(RAW, '%s.pkl' % d))
        if i % 20 == 0 or i == len(miss):
            log('  [%d/%d] %s' % (i, len(miss), d))
        time.sleep(sleep)


def build_sw_raw(dates, sleep=0.08):
    """单日一次请求拿全部申万指数（L1+L2+L3 共 439 条）。"""
    miss = [d for d in dates if not os.path.exists(os.path.join(SWRAW, '%s.pkl' % d))]
    log('申万指数：需补 %d 天 / 共 %d 天' % (len(miss), len(dates)))
    for i, d in enumerate(miss, 1):
        df = call(pro.sw_daily, trade_date=d)
        if df is None or df.empty:
            continue
        df['trade_date'] = df['trade_date'].astype(str)
        df.to_pickle(os.path.join(SWRAW, '%s.pkl' % d))
        if i % 40 == 0 or i == len(miss):
            log('  [%d/%d] %s' % (i, len(miss), d))
        time.sleep(sleep)


CLASSIFY = {}


def classify(level):
    if level in CLASSIFY:
        return CLASSIFY[level]
    p = os.path.join(CACHE, 'sw_classify_%s.json' % level)
    if os.path.exists(p):
        age = (time.time() - os.path.getmtime(p)) / 86400.0
        if age < 30:
            m = json.load(open(p, encoding='utf-8'))
            CLASSIFY[level] = m
            return m
    df = call(pro.index_classify, level=level, src='SW2021')
    if df is None or df.empty:
        raise SystemExit('index_classify %s 失败' % level)
    m = {str(r['index_code']): r['industry_name'] for _, r in df.iterrows()}
    json.dump(m, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    CLASSIFY[level] = m
    return m


def build_members(force=False):
    p = os.path.join(CACHE, 'sw_members.pkl')
    if os.path.exists(p) and not force:
        age = (time.time() - os.path.getmtime(p)) / 86400.0
        if age < 30:
            return pd.read_pickle(p)
    cls = classify('L2')
    mem = {}
    codes = list(cls.keys())
    log('申万成分股：%d 个二级行业' % len(codes))
    for i, code in enumerate(codes, 1):
        df = call(pro.index_member_all, l2_code=code)
        if df is not None and not df.empty:
            if 'is_new' in df.columns:
                df = df[df['is_new'].astype(str).str.upper().eq('Y')]
            mem[code] = [c for c in df['ts_code'].tolist() if isinstance(c, str)]
        if i % 30 == 0 or i == len(codes):
            log('  [%d/%d]' % (i, len(codes)))
        time.sleep(0.05)
    pd.to_pickle(mem, p)
    return mem


def build_basic(force=False):
    p = os.path.join(CACHE, 'basic.pkl')
    if os.path.exists(p) and not force:
        age = (time.time() - os.path.getmtime(p)) / 3600.0
        if age < 24:
            return pd.read_pickle(p)
    df = call(pro.stock_basic, exchange='', list_status='L',
              fields='ts_code,symbol,name,area,industry,market,list_date')
    if df is not None and not df.empty:
        df.to_pickle(p)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=260)
    ap.add_argument('--force-members', action='store_true')
    ap.add_argument('--skip-members', action='store_true')
    a = ap.parse_args()

    dates = trade_dates(a.days)
    log('交易日区间：%s ~ %s（%d 天）' % (dates[0], dates[-1], len(dates)))
    build_raw(dates)
    build_sw_raw(dates)
    b = build_basic()
    log('stock_basic：%d 只' % (0 if b is None else len(b)))
    classify('L1')
    classify('L2')
    if not a.skip_members:
        m = build_members(force=a.force_members)
        log('成分股：%d 个行业 / %d 只' % (len(m), sum(len(v) for v in m.values())))
    # 清理超出窗口的旧文件（保留 400 天）
    keep = set(dates)
    cut = dates[0]
    for d in (RAW, SWRAW):
        n = 0
        for f in os.listdir(d):
            if f.endswith('.pkl') and f[:8] < cut:
                os.remove(os.path.join(d, f))
                n += 1
        if n:
            log('清理过期缓存 %s：%d 个' % (os.path.basename(d), n))
    log('面板就绪 · 最新交易日 %s' % dates[-1])


if __name__ == '__main__':
    main()
