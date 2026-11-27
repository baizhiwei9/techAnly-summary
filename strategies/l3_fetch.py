#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L3 双轨（国债回购链 / 股市资金池）指标采集 → market.db
==========================================================
对应大佬 2026-10-18 改版的国内流动性提示词：L0–L3 拆「国债链 vs 股市链」。

新增 series（domain=liquidity）：
  —— L3-A 国债回购链 ——
  cn.repo.vol_total         银行间质押式回购日成交额（亿元，R001+R007+…）
  cn.repo.vol_on_share      隔夜回购占比（%）
  cn.repo.gc007             GC007 交易所回购（%，204007.SH）
  cn.repo.gc_dr_spread      GC007 − DR007（bp）
  cn.repo.r_dr_spread       R007 − DR007（bp）

  —— L3-B 股市资金池 ——
  cn.eq.north_flow          北向资金净流入（亿元，日频）
  cn.eq.new_fund_week       偏股基金周新发（亿元，周频，用成立规模代理）

数据源：tushare（直连优先，失败切代理）+ akshare
用法：
  python3 strategies/l3_fetch.py              # 增量（近 400 自然日）
  python3 strategies/l3_fetch.py --days 700
  python3 strategies/l3_fetch.py --dry
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import datetime as dt
import os
import sys
import warnings

warnings.filterwarnings('ignore')

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import marketdb as M  # noqa: E402

TS_TOKEN_DIRECT = __import__("ocsecrets").tushare_token()
TS_TOKEN_PROXY = __import__("ocsecrets").tushare_proxy_token()
TS_PROXY_URL = 'https://ttx.dailyfetch.top/'


def _force_ipv4():
    import socket
    if getattr(socket, '_ipv4_forced', False):
        return
    _orig = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET]
    socket._ipv4_forced = True


def _pro(direct=True):
    """Tushare pro 实例 —— 统一走 DataHub（同一出口 IP + 统一限流）。"""
    from data_hub import ts_get_pro
    if direct:
        from data_hub.core.tushare_dual import _make_pro
        return _make_pro('direct')
    return ts_get_pro()


def ts_call(api, **kw):
    for direct in (True, False):
        try:
            return getattr(_pro(direct), api)(**kw)
        except Exception as e:
            if direct:
                continue
            print(f'  ! tushare {api} 双通道失败: {repr(e)[:90]}')
    return None


# ─────────────────────────────────────── L3-A 回购链
REPO_CODES = {
    'R001': 'R001.IB', 'R007': 'R007.IB', 'R014': 'R014.IB',
    'R021': 'R021.IB', 'R1M': 'R1M.IB',
}


def fetch_repo_volumes(start, end):
    """银行间质押式回购成交额（tushare repo_daily.amount，单位千元 → 亿元）"""
    vol = {}          # date -> {code: 亿元}
    close = {}        # date -> {code: %}
    for tag, code in REPO_CODES.items():
        df = ts_call('repo_daily', ts_code=code, start_date=start, end_date=end,
                     fields='ts_code,trade_date,close,amount')
        if df is None or not len(df):
            continue
        for _, r in df.iterrows():
            d = str(r['trade_date'])
            d = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
            try:
                vol.setdefault(d, {})[tag] = float(r['amount']) / 1e5   # 千元 → 亿元
            except (TypeError, ValueError):
                pass
            try:
                close.setdefault(d, {})[tag] = float(r['close'])
            except (TypeError, ValueError):
                pass

    rows_total, rows_on = [], []
    for d in sorted(vol):
        v = vol[d]
        tot = sum(v.values())
        if tot <= 0:
            continue
        rows_total.append((d, round(tot, 2)))
        rows_on.append((d, round(v.get('R001', 0) / tot * 100, 2)))
    return rows_total, rows_on, close


def fetch_gc_dr(start, end):
    """GC007（204007.SH）− DR007（bp）"""
    gc = ts_call('repo_daily', ts_code='204007.SH', start_date=start, end_date=end,
                 fields='ts_code,trade_date,close')
    dr = ts_call('repo_daily', ts_code='DR007.IB', start_date=start, end_date=end,
                 fields='ts_code,trade_date,close')
    r7 = ts_call('repo_daily', ts_code='R007.IB', start_date=start, end_date=end,
                 fields='ts_code,trade_date,close')

    def to_map(df):
        m = {}
        if df is None or not len(df):
            return m
        for _, r in df.iterrows():
            d = str(r['trade_date'])
            try:
                m[f'{d[:4]}-{d[4:6]}-{d[6:8]}'] = float(r['close'])
            except (TypeError, ValueError):
                pass
        return m

    gcm, drm, r7m = to_map(gc), to_map(dr), to_map(r7)
    gc_rows, gcspr, rspr = [], [], []
    for d in sorted(gcm):
        gc_rows.append((d, gcm[d]))
        if d in drm:
            gcspr.append((d, round((gcm[d] - drm[d]) * 100, 1)))
    for d in sorted(r7m):
        if d in drm:
            rspr.append((d, round((r7m[d] - drm[d]) * 100, 1)))
    return gc_rows, gcspr, rspr


# ─────────────────────────────────────── L3-B 股市资金池
def fetch_north(start, end):
    """北向资金净流入（tushare moneyflow_hsgt.north_money，单位万元 → 亿元）"""
    df = ts_call('moneyflow_hsgt', start_date=start, end_date=end,
                 fields='trade_date,north_money')
    rows = []
    if df is None or not len(df):
        return rows
    for _, r in df.iterrows():
        d = str(r['trade_date'])
        try:
            rows.append((f'{d[:4]}-{d[4:6]}-{d[6:8]}', round(float(r['north_money']) / 1e4, 2)))
        except (TypeError, ValueError):
            pass
    rows.sort()
    return rows


def fetch_new_fund_week():
    """偏股基金新发（周，亿元）：akshare fund_new_found_em 的「募集份额」按成立日聚合"""
    rows = []
    try:
        from data_hub import ak as ak
        df = ak.fund_new_found_em()
    except Exception as e:
        print(f'  ! 基金新发失败: {repr(e)[:90]}')
        return rows
    if df is None or not len(df):
        return rows
    wk = {}
    for _, r in df.iterrows():
        t = str(r.get('基金类型') or '')
        if not any(k in t for k in ('股票', '混合')):
            continue
        d = str(r.get('成立日期') or '')[:10]
        if len(d) != 10:
            continue
        try:
            sz = float(r.get('募集份额'))
        except (TypeError, ValueError):
            continue
        # 自然周（周一为锚）
        dd = dt.date.fromisoformat(d)
        mon = dd - dt.timedelta(days=dd.weekday())
        wk[mon.isoformat()] = wk.get(mon.isoformat(), 0.0) + sz
    rows = sorted((k, round(v, 2)) for k, v in wk.items())
    return rows


META = {
    'cn.repo.vol_total': ('银行间质押式回购成交额', '亿元', 'D'),
    'cn.repo.vol_on_share': ('隔夜回购占比', 'pct', 'D'),
    'cn.repo.gc007': ('GC007 交易所回购', 'pct', 'D'),
    'cn.repo.gc_dr_spread': ('GC007−DR007', 'bp', 'D'),
    'cn.repo.r_dr_spread': ('R007−DR007', 'bp', 'D'),
    'cn.eq.north_flow': ('北向资金净流入', '亿元', 'D'),
    'cn.eq.new_fund_week': ('偏股基金周新发(成立口径)', '亿元', 'W'),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=430)
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()

    _force_ipv4()
    end = dt.date.today()
    start = end - dt.timedelta(days=a.days)
    s8, e8 = start.strftime('%Y%m%d'), end.strftime('%Y%m%d')
    print(f'▶ 区间 {s8} ~ {e8}')

    items = []

    print('▶ L3-A 回购成交量 / 隔夜占比')
    total, on_share, _ = fetch_repo_volumes(s8, e8)
    print(f'  回购成交额 {len(total)} 天，最新 {total[-1] if total else "-"}')

    print('▶ L3-A GC007 / 利差')
    gc, gcspr, rspr = fetch_gc_dr(s8, e8)
    print(f'  GC007 {len(gc)} 天，GC−DR {len(gcspr)} 天，R−DR {len(rspr)} 天')

    print('▶ L3-B 北向资金')
    north = fetch_north(s8, e8)
    print(f'  {len(north)} 天，最新 {north[-1] if north else "-"}')

    print('▶ L3-B 偏股基金新发（周）')
    nf = fetch_new_fund_week()
    print(f'  {len(nf)} 周，最新 {nf[-1] if nf else "-"}')

    series = {
        'cn.repo.vol_total': total,
        'cn.repo.vol_on_share': on_share,
        'cn.repo.gc007': gc,
        'cn.repo.gc_dr_spread': gcspr,
        'cn.repo.r_dr_spread': rspr,
        'cn.eq.north_flow': north,
        'cn.eq.new_fund_week': nf,
    }

    for sid, rows in series.items():
        if not rows:
            continue
        name, unit, freq = META[sid]
        items.append({'series_id': sid, 'rows': rows, 'domain': 'liquidity',
                      'source': 'tushare/akshare', 'code': sid.split('.')[-1],
                      'name': name, 'unit': unit, 'freq': freq})

    print('\n── 最近 8 天 ──')
    for sid in ('cn.repo.vol_total', 'cn.repo.vol_on_share', 'cn.repo.gc_dr_spread',
                'cn.repo.r_dr_spread', 'cn.eq.north_flow'):
        rows = series.get(sid) or []
        if rows:
            print(f'  {sid:<26} ' + '  '.join(f'{d}:{v}' for d, v in rows[-3:]))

    if a.dry:
        print('\n[dry] 不写库')
        return

    r = M.save_batch(items, task='l3_fetch')
    print(f"\n✅ 写入 {r['series']} 指标 / {r['rows']} 行")


if __name__ == '__main__':
    M.init_db()
    main()
