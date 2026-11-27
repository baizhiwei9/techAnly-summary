#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🏷 行业/概念「聚集 or 扩散」标识（纯程序，0 token）
=====================================================================
大佬口径（2026-11-02）：「图表后边加个标识，这个行业和概念到底是聚集还是扩散」

判定逻辑（和 pool_topo 同源，但作用在**行业/概念组**上）
------------------------------------------------------------
两组可测的量：
  · 吸收比率 AR = λ₁/N        —— 资金是否往一个方向挤（Kritzman）
  · 嵌入离散度 spread         —— 同一相关距离矩阵做 PCoA 二维投影后的平均两两距离

每 5 个交易日对比一次：ΔAR、Δspread
  上涨组：ΔAR>0 或 Δspread<0  → **聚集**（资金抱团往上）
  上涨组：否则              → **扩散**
  下跌组：ΔAR<0 或 Δspread>0  → **扩散**（资金四散）
  下跌组：否则              → **聚集**（一起跌，说明还在抱团）

输出给 stock_pool 画图用：
  {label: {'dir':'涨/跌', 'state':'聚集/扩散', 'ok':bool, 'd_ar':.., 'd_sp':.., 'n':..}}

数据：tushare 直连 token 按日拉全市场（历史日缓存到 .cache/struct_tag/，一日一文件）
用法：
  python3 strategies/struct_tag.py --date 2026-10-30 --ind 白酒,银行,通信设备
  python3 strategies/struct_tag.py --date 2026-10-30 --con 白酒概念,共封装光学(CPO)
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, time, argparse
import numpy as np
import pandas as pd

BASE = os.path.expanduser("~/.openclaw/workspace")
CACHE = os.path.join(BASE, '.cache/struct_tag')
WIN = 20        # 相关矩阵窗口
LAG = 5         # 与几个交易日前对比
P = 5           # 涨跌幅口径（5 日）
MAX_MEM = 300   # 大组抽样上限（保证速度）


def _log(m):
    print(m, file=sys.stderr)


# ---------------- 数据 ----------------
def _pro():
    sys.path.insert(0, os.path.join(BASE, 'bin'))
    import tushare_init as T
    return T.get_pro(token=T.DIRECT_TOKEN, http_url='')   # 直连（代理常 ip超限）


def trade_dates(n=WIN + LAG + 8, end=None):
    """最近 n 个交易日（含 end）；优先读本地缓存清单"""
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, '_dates.json')
    today = time.strftime('%Y%m%d')
    if os.path.exists(fp):
        try:
            d = json.load(open(fp, encoding='utf-8'))
            if d.get('asof') == today and len(d.get('dates', [])) >= n:
                ds = d['dates']
                if end:
                    ds = [x for x in ds if x <= end]
                return ds[-n:]
        except Exception:
            pass
    p = _pro()
    cal = p.trade_cal(exchange='SSE', start_date='20250101', end_date=today)
    ds = sorted(cal[cal['is_open'] == 1]['cal_date'].astype(str).tolist())
    json.dump({'asof': today, 'dates': ds}, open(fp, 'w'))
    if end:
        ds = [x for x in ds if x <= end]
    return ds[-n:]


def day_df(d8):
    """单日全市场日线（本地缓存，历史日不再变）"""
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, 'd_%s.csv.gz' % d8)
    if os.path.exists(fp):
        try:
            return pd.read_csv(fp, dtype={'ts_code': str})
        except Exception:
            pass
    p = _pro()
    for i in range(3):
        try:
            df = p.daily(trade_date=d8)
            if df is not None and len(df):
                df = df[['ts_code', 'close', 'pct_chg']].copy()
                df.to_csv(fp, index=False, compression='gzip')
                return df
        except Exception as e:
            _log('[day] %s 第%d次失败 %s' % (d8, i + 1, str(e)[:60]))
            time.sleep(1.5 + 2 * i)
    return None


def price_panel(dates):
    """宽表：index=date, columns=ts_code, values=close"""
    frames = []
    for d in dates:
        df = day_df(d)
        if df is None or not len(df):
            continue
        s = df.set_index('ts_code')['close'].astype(float).rename(d)
        frames.append(s)
    if not frames:
        return None
    px = pd.concat(frames, axis=1).T
    px.index = pd.to_datetime(px.index, format='%Y%m%d')
    return px.sort_index()


# ---------------- 组成员 ----------------
def load_basic():
    import glob, csv
    cands = sorted(glob.glob(os.path.join(BASE, '.cache/market/*/stock_basic.csv')))
    if not cands:
        return {}
    out = {}
    with open(cands[-1], encoding='utf-8') as f:
        for row in csv.DictReader(f):
            ts = (row.get('ts_code') or '').strip()
            if len(ts) >= 6:
                out[ts[:6]] = (row.get('name') or '').strip(), (row.get('industry') or '').strip()
    return out


def load_concepts():
    fp = os.path.join(BASE, '.cache/concepts/stock_concepts.json')
    if not os.path.exists(fp):
        return {}
    try:
        return json.load(open(fp, encoding='utf-8')).get('map', {})
    except Exception:
        return {}


# ---------------- 核心指标 ----------------
def _ar_disp(R):
    """R: DataFrame(窗口 × 成员) → (AR, PCoA spread)"""
    n = R.shape[1]
    if n < 2 or len(R) < 8:
        return float('nan'), float('nan')
    eps = R.sub(R.mean(axis=1), axis=0)          # 去掉当日市场共同因子
    with np.errstate(invalid='ignore', divide='ignore'):
        C = np.corrcoef(eps.values.T)
    C = np.nan_to_num(np.asarray(C, float), nan=0.0)
    C = np.clip((C + C.T) / 2, -1, 1)
    np.fill_diagonal(C, 1.0)
    w = np.linalg.eigvalsh(C)
    ar = float(w[-1] / n)
    D = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - C)))
    np.fill_diagonal(D, 0.0)
    J = np.eye(n) - np.ones((n, n)) / n
    try:
        B = -0.5 * J @ (D ** 2) @ J
        ev, evec = np.linalg.eigh(B)
        k = np.argsort(ev)[::-1][:2]
        emb = evec[:, k] * np.sqrt(np.maximum(ev[k], 0))
        dm = np.linalg.norm(emb[:, None, :] - emb[None, :, :], axis=-1)
        iu = np.triu_indices(n, 1)
        sp = float(dm[iu].mean() / np.sqrt(n)) if len(iu[0]) else float('nan')
    except Exception:
        sp = float('nan')
    return ar, sp


def metrics(px, members, name=''):
    """members: ts_code 列表；返回 dict 或 None"""
    cols = [m for m in members if m in px.columns]
    if len(cols) > MAX_MEM:
        cols = sorted(cols)[::max(1, len(cols) // MAX_MEM)][:MAX_MEM]
    if len(cols) < 2:
        return None
    sub = px[cols]
    sub = sub.loc[:, sub.notna().sum() > WIN + LAG]      # 剔除停牌多的
    n = sub.shape[1]
    if n < 2 or len(sub) < WIN + LAG + 2:
        return None
    R = sub.pct_change().iloc[1:]
    if len(R) < WIN + LAG:
        return None
    a_now, s_now = _ar_disp(R.iloc[-WIN:])
    a_ref, s_ref = _ar_disp(R.iloc[-WIN - LAG:-LAG])
    if not np.isfinite(a_now) or not np.isfinite(a_ref):
        return None
    r5 = float((sub.iloc[-1] / sub.iloc[-P - 1] - 1).mean() * 100)
    r1 = float((sub.iloc[-1] / sub.iloc[-2] - 1).mean() * 100)
    d_ar = a_now - a_ref
    d_sp = (s_now - s_ref) if (np.isfinite(s_now) and np.isfinite(s_ref)) else float('nan')
    up = r5 > 0
    if up:
        ok = (d_ar > 0) or (np.isfinite(d_sp) and d_sp < 0)
        state = '聚集' if ok else '扩散'
    else:
        ok = (d_ar < 0) or (np.isfinite(d_sp) and d_sp > 0)
        state = '扩散' if ok else '聚集'
    return {'name': name, 'n': n, 'r5': round(r5, 2), 'r1': round(r1, 2),
            'ar': round(a_now, 4), 'd_ar': round(d_ar, 4),
            'spread': round(s_now, 4) if np.isfinite(s_now) else None,
            'd_sp': round(d_sp, 4) if np.isfinite(d_sp) else None,
            'dir': '涨' if up else '跌', 'state': state, 'ok': bool(ok)}


def run(date, industries=None, concepts=None, basic=None, cmap=None, px=None):
    """批量算：industries=[行业名], concepts=[概念名] → {label: metrics}"""
    basic = basic if basic is not None else load_basic()
    cmap = cmap if cmap is not None else load_concepts()
    if px is None:
        end = str(date).replace('-', '')
        dates = trade_dates(WIN + LAG + 8, end=end)
        px = price_panel(dates)
    if px is None or not len(px):
        return {}
    code2ts = {}
    for ts in px.columns:
        code2ts[ts[:6]] = ts
    out = {}
    if industries:
        by = {}
        for c6, (_nm, ind) in basic.items():
            if ind:
                by.setdefault(ind, []).append(c6)
        for ind in industries:
            mem = [code2ts[c] for c in by.get(ind, []) if c in code2ts]
            m = metrics(px, mem, ind)
            if m:
                out[ind] = m
    if concepts:
        by = {}
        for ts, tags in cmap.items():
            for t in (tags or []):
                by.setdefault(t, []).append(ts)
        for cp in concepts:
            mem = [t for t in by.get(cp, []) if t in px.columns]
            m = metrics(px, mem, cp)
            if m:
                out[cp] = m
    return out


def label_of(m):
    """图上用的短标识，如 '涨·聚集'"""
    if not m:
        return ''
    return '%s·%s' % (m['dir'], m['state'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--ind', default='')
    ap.add_argument('--con', default='')
    args = ap.parse_args()
    inds = [x for x in args.ind.split(',') if x.strip()]
    cons = [x for x in args.con.split(',') if x.strip()]
    r = run(args.date, inds, cons)
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
