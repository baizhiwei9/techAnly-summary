#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持仓量 PCR · 分指数版（0 token 纯脚本）
=====================================================================
大佬 2026-11-16 23:52：「我主要需要的是持仓量的 PCR。而且是包离近月近月 5 天
以内的。比如说我离近月只有 5 天了，那我就不要近月了。」

口径（本模块，与 `opt_pcr_idx.py` 的**成交量 PCR** 并列，互不影响）
---------------------------------------------------------------------
    PCR_OI = Σ 认沽持仓量(oi) / Σ 认购持仓量(oi)

**到期剔除规则 = dte ≤ 5（不是无条件剔当月）**
    dte = 该到期月到期日 − 该交易日（自然日）
    · dte ≤ 5 的到期月 → 剔除（临到期 OI 塌陷/换月把指标打歪）
    · dte > 5 的到期月 → **全部保留**（含当月、次月、季月）
    对照口径 `pcr_oi_near`：无条件剔除「最近到期月」（与成交量 PCR 一致），
    用于看两种剔除方式差多少。

指数 → 期权标的（沿用 opt_pcr_idx.GROUPS；另补中证500 供五大宽基画图用）
    沪深300 : OP510300.SH / OP159919.SZ / OP000300.SH(IO)
    中证500 : OP510500.SH / OP159922.SZ          ← 本模块补充
    中证1000: OP000852.SH(MO)
    科创50  : OP588000.SH / OP588080.SH
    创业板  : OP159915.SZ

数据 / 缓存
    tushare `opt_daily`(SSE/SZSE/CFFEX，逐日抓，代理走 bin/tushare_init.ts_call)
    + `opt_basic`(call_put / maturity_date / opt_code)
    缓存 .cache/opt_pcr/oi_hist.json（增量补齐），合约表 .cache/opt_pcr/opt_basic_oi.json

用法
    python3 strategies/opt_pcr_oi.py                     # 报告用中文段
    python3 strategies/opt_pcr_oi.py --date 2026-11-16
    python3 strategies/opt_pcr_oi.py --json
    python3 strategies/opt_pcr_oi.py --compare           # pcr_oi(dte≤5) vs pcr_oi_near 对照表
    python3 strategies/opt_pcr_oi.py --backfill 75
    python3 strategies/opt_pcr_oi.py --smooth 5          # N 日 Σput_oi/Σcall_oi（默认 1=单日）
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import json
import argparse
import datetime

WS = '/root/.openclaw/workspace'
sys.path.insert(0, os.path.join(WS, 'bin'))

CACHE = os.path.join(WS, '.cache', 'opt_pcr')
os.makedirs(CACHE, exist_ok=True)
HIST = os.path.join(CACHE, 'oi_hist.json')
BASIC = os.path.join(CACHE, 'opt_basic_oi.json')
LOG = os.path.join(CACHE, 'oi.log')

WINDOW = int(os.environ.get('OPT_PCR_OI_WINDOW', '60'))
BACKFILL = int(os.environ.get('OPT_PCR_OI_BACKFILL', '75'))
SMOOTH = int(os.environ.get('OPT_PCR_OI_SMOOTH', '1'))
DTE_MAX = int(os.environ.get('OPT_PCR_OI_DTE', '5'))   # dte≤5 剔除

GROUPS = [
    ('沪深300', ['OP510300.SH', 'OP159919.SZ', 'OP000300.SH']),
    ('中证500', ['OP510500.SH', 'OP159922.SZ']),
    ('中证1000', ['OP000852.SH']),
    ('科创50', ['OP588000.SH', 'OP588080.SH']),
    ('创业板', ['OP159915.SZ']),
]
# 画图/报告里的别名（charts 用「创业板指」）
ALIAS = {'创业板指': '创业板', '沪深300指': '沪深300', '中证1000指': '中证1000',
         '上证50': '上证50'}
ALL_CODES = sorted({c for _l, cs in GROUPS for c in cs})
EXCHANGES = ('SSE', 'SZSE', 'CFFEX')

try:
    import trade_day as TD
except Exception:
    TD = None
try:
    import tushare_init as TI
except Exception:
    TI = None


def log(m):
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write('[%s] oi %s\n' % (datetime.datetime.now().strftime('%F %T'), m))
    except Exception:
        pass


def d8(d):
    return str(d).replace('-', '').replace('/', '')[:8]


def d10(s):
    s = d8(s)
    return '%s-%s-%s' % (s[:4], s[4:6], s[6:8])


def dshow(s):
    s = d8(s)
    return '%s-%s' % (s[4:6], s[6:8])


def _cal():
    if TD is None:
        return []
    try:
        return sorted(k for k, v in TD.cal_days().items() if v == 1)
    except Exception:
        return []


def trade_days_before(date, n):
    s = d8(date)
    ds = [k for k in _cal() if k <= s]
    if not ds:
        cur = datetime.datetime.strptime(s, '%Y%m%d')
        out = []
        while len(out) < n:
            if cur.weekday() < 5:
                out.append(cur.strftime('%Y%m%d'))
            cur -= datetime.timedelta(days=1)
        return sorted(out)
    return ds[-n:]


def resolve_target(date=None):
    if date is None:
        date = datetime.date.today().strftime('%Y%m%d')
    if TD is None:
        return d8(date)
    try:
        return d8(TD.resolve(date)[0])
    except Exception:
        return d8(date)


def prev_trade_strict(date=None):
    s = d8(date)
    ds = [k for k in _cal() if k < s]
    if ds:
        return ds[-1]
    cur = datetime.datetime.strptime(s, '%Y%m%d') - datetime.timedelta(days=1)
    while cur.weekday() >= 5:
        cur -= datetime.timedelta(days=1)
    return cur.strftime('%Y%m%d')


def anchor_date(snap_date, session):
    s = d8(snap_date or datetime.date.today().strftime('%Y%m%d'))
    return prev_trade_strict(s) if session == 'morning' else s


# ------------------------------------------------------------ opt_basic
def load_basic(force=False):
    """返回 {ts_code: {'cp': 'C'/'P', 'mat': 'YYYYMMDD', 'oc': 'OPxxxxxx.SH'}}"""
    if not force and os.path.exists(BASIC):
        try:
            c = json.load(open(BASIC, encoding='utf-8'))
            age = (datetime.datetime.now() -
                   datetime.datetime.strptime(c['ts'], '%Y-%m-%d %H:%M:%S')).days
            if age <= 3:
                return c['map']
        except Exception:
            pass
    m = {}
    for ex in EXCHANGES:
        try:
            b = TI.ts_call('opt_basic', exchange=ex)
        except Exception as e:
            log('opt_basic %s err %s' % (ex, str(e)[:80]))
            b = None
        if b is None:
            continue
        for _, r in b.iterrows():
            oc = str(r.get('opt_code', '') or '')
            if oc not in ALL_CODES:
                continue
            m[str(r['ts_code'])] = {'cp': str(r['call_put']).upper(),
                                    'mat': str(r['maturity_date'])[:8],
                                    'oc': oc}
    if m:
        json.dump({'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'map': m},
                  open(BASIC, 'w', encoding='utf-8'), ensure_ascii=False)
    return m


# ------------------------------------------------------------ 单日抓取
def fetch_day(date, basic):
    """抓某交易日 → {opt_code: {'mats': {mm: mat8}, 'oi': {'C|mm': v, 'P|mm': v},
                                  'vol': {...}}}

    只按 opt_code 聚合，凡是该标的的合约（不论到期月）都收，剔除留给 compute 层。
    """
    s = d8(date)
    per = {}
    got = False
    for ex in EXCHANGES:
        try:
            df = TI.ts_call('opt_daily', trade_date=s, exchange=ex)
        except Exception as e:
            log('opt_daily %s %s err %s' % (ex, s, str(e)[:80]))
            df = None
        if df is None or len(df) == 0:
            continue
        got = True
        for _, r in df.iterrows():
            b = basic.get(str(r['ts_code']))
            if not b or not b.get('cp') or not b.get('mat'):
                continue
            oc, mat, cp = b['oc'], b['mat'], b['cp']
            mm = mat[:6]
            d = per.setdefault(oc, {'mats': {}, 'oi': {}, 'vol': {}})
            d['mats'][mm] = mat
            k = '%s|%s' % ('C' if cp == 'C' else 'P', mm)
            d['oi'][k] = d['oi'].get(k, 0.0) + float(r.get('oi') or 0)
            d['vol'][k] = d['vol'].get(k, 0.0) + float(r.get('vol') or 0)
    return per if got else None


def load_hist():
    if os.path.exists(HIST):
        try:
            return json.load(open(HIST, encoding='utf-8'))
        except Exception:
            return {}
    return {}


def save_hist(h):
    try:
        json.dump(h, open(HIST, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass


def ensure_hist(date, need=None, force_last=False):
    need = need or max(WINDOW + 5, BACKFILL)
    h = load_hist()
    days = trade_days_before(date, need)
    basic = load_basic()
    miss = [d for d in days if d not in h or (force_last and d == days[-1])]
    if not miss:
        return h, days
    for d in miss:
        rec = fetch_day(d, basic)
        if rec:
            h[d] = rec
        else:
            log('no data %s' % d)
    save_hist(h)
    log('fetched %d day(s)' % len(miss))
    return h, days


# ------------------------------------------------------------ 核心口径
def _mats_of(rec, oc):
    """该标的当日全部到期月（mm → mat8）"""
    r = rec.get(oc) or {}
    return (r.get('mats') or {}), (r.get('oi') or {}), (r.get('vol') or {})


def day_sums(rec, codes, date, dte_max=DTE_MAX, mode='dte'):
    """把某日多标的聚合成 (call_oi, put_oi, near, kept_mms, dropped_mms)

    mode='dte'  → 剔除 dte ≤ dte_max 的到期月，其余全留（默认）
    mode='near' → 无条件剔除「最近到期月」（对照口径）
    mode='all'  → 不剔除
    """
    s = d8(date)
    base = datetime.datetime.strptime(s, '%Y%m%d').date()
    c = p = 0.0
    kept, dropped, near_all = set(), set(), []
    for oc in codes:
        mats, oi, _vol = _mats_of(rec, oc)
        if not mats:
            continue
        live = []
        for mm, mat in mats.items():
            try:
                md = datetime.datetime.strptime(mat, '%Y%m%d').date()
            except Exception:
                continue
            dte = (md - base).days
            if dte < 0:
                continue                      # 已到期，忽略
            live.append((dte, mm, mat))
        if not live:
            continue
        live.sort()
        near_all.append(live[0])              # 该标的最近到期月
        near_mm = live[0][1]
        for dte, mm, mat in live:
            if mode == 'near':
                drop = (mm == near_mm)
            elif mode == 'all':
                drop = False
            else:
                drop = (dte <= dte_max)
            if drop:
                dropped.add(mm)
                continue
            kept.add(mm)
            c += oi.get('C|%s' % mm, 0.0)
            p += oi.get('P|%s' % mm, 0.0)
    near = near_all[0] if near_all else None
    return c, p, near, sorted(kept), sorted(dropped)


def _ratio(c, p):
    return (p / c) if c else None


def series_for_codes(h, days, codes, mode='dte', smooth=1):
    """[[date, pcr, call_oi, put_oi], ...]（smooth>1 时按 Σput/Σcall 跨日累加）"""
    seq = []
    for d in days:
        rec = h.get(d)
        if not rec:
            continue
        c, p, _near, _k, _dr = day_sums(rec, codes, d, mode=mode)
        if c > 0:
            seq.append((d, c, p))
    out = []
    n = max(1, int(smooth))
    for i in range(len(seq)):
        j = max(0, i - n + 1)
        if i - j + 1 < n:
            continue
        c = sum(x[1] for x in seq[j:i + 1])
        p = sum(x[2] for x in seq[j:i + 1])
        if c:
            out.append([seq[i][0], p / c, c, p])
    return out


def pcr_series_for_index(name, start=None, end=None, smooth=1, mode='dte',
                         fetch_new=True):
    """给画图用：返回 [(YYYYMMDD, pcr_oi), ...]（读取本地缓存，不重复抓历史）。

    name 支持 '创业板指' 之类别名；未知指数返回 []。
    只有「end 那天不在缓存里」（新交易日）才触发抓数；历史天数靠 CLI
    `--backfill N` 补。这样画图调用是毫秒级，不会因为抓数把出图拖死。
    """
    key = ALIAS.get(name, name)
    codes = None
    for lab, cs in GROUPS:
        if lab == key:
            codes = cs
            break
    if not codes:
        return []
    end_s = d8(end) if end else d8(datetime.date.today().strftime('%Y%m%d'))
    end_s = resolve_target(end_s)
    h = load_hist()
    if end_s not in h and fetch_new:
        h, _d = ensure_hist(end_s, need=max(WINDOW + 5, BACKFILL))
    days = sorted(h)
    if start:
        s0 = d8(start)
        days = [d for d in days if d >= s0]
    if end:
        e0 = d8(end)
        days = [d for d in days if d <= e0]
    seq = series_for_codes(h, days, codes, mode=mode, smooth=smooth)
    return [(x[0], x[1]) for x in seq]


# ------------------------------------------------------------ 计算
def compute(date=None, force=False, window=None, smooth=None):
    win = window or WINDOW
    sm = int(smooth if smooth is not None else SMOOTH)
    target = resolve_target(date)
    h, days = ensure_hist(target, need=max(win + 5, BACKFILL), force_last=force)
    rows = []
    for label, codes in GROUPS:
        seq = series_for_codes(h, days, codes, mode='dte', smooth=sm)
        if not seq:
            rows.append({'label': label, 'ok': False, 'codes': list(codes)})
            continue
        seq = seq[-win:]
        vals = [x[1] for x in seq]
        cur = seq[-1]
        cur_pcr = vals[-1]
        rank = sum(1 for v in vals if v <= cur_pcr)
        pct = round(rank / len(vals) * 100) if vals else None
        prev_pcr = vals[-2] if len(vals) >= 2 else None
        c, p, near, kept, dropped = day_sums(h.get(cur[0]) or {}, codes, cur[0])
        ndays = None
        if near:
            mm = near[1]
            mat = near[2]
            ndays = (datetime.datetime.strptime(mat, '%Y%m%d').date() -
                     datetime.datetime.strptime(d8(cur[0]), '%Y%m%d').date()).days
        rows.append({
            'label': label, 'ok': True, 'codes': list(codes),
            'date': cur[0], 'date_show': dshow(cur[0]),
            'pcr_oi': round(cur_pcr, 4), 'pct': pct, 'samples': len(vals),
            'window': win, 'smooth': sm,
            'prev_show': dshow(seq[-2][0]) if len(seq) >= 2 else None,
            'prev_pcr_oi': round(prev_pcr, 4) if prev_pcr is not None else None,
            'diff': round(cur_pcr - prev_pcr, 4) if prev_pcr is not None else None,
            'min': round(min(vals), 4), 'max': round(max(vals), 4),
            'call_oi': round(c), 'put_oi': round(p),
            'near_my': near[1] if near else None,
            'near_mat': near[2] if near else None,
            'near_dte': ndays,
            'kept_mm': kept, 'dropped_mm': dropped,
            'series': [[x[0], round(x[1], 4)] for x in seq],
        })
    # 全市场对照
    seq = series_for_codes(h, days, ALL_CODES, mode='dte', smooth=sm)[-win:]
    mkt = None
    if seq:
        vals = [x[1] for x in seq]
        rank = sum(1 for v in vals if v <= vals[-1])
        mkt = {'label': '全市场', 'pcr_oi': round(vals[-1], 4),
               'pct': round(rank / len(vals) * 100), 'samples': len(vals),
               'diff': round(vals[-1] - vals[-2], 4) if len(vals) > 1 else None,
               'date_show': dshow(seq[-1][0])}
    return {'ok': any(r.get('ok') for r in rows), 'target': target,
            'is_target': bool(rows and rows[0].get('ok') and rows[0].get('date') == target),
            'window': win, 'smooth': sm, 'dte_max': DTE_MAX,
            'rows': rows, 'market': mkt,
            'date_show': next((r['date_show'] for r in rows if r.get('ok')), '')}


def compute_near(date=None, window=None, smooth=None):
    """对照口径：无条件剔除最近到期月"""
    win = window or WINDOW
    sm = int(smooth if smooth is not None else SMOOTH)
    target = resolve_target(date)
    h, days = ensure_hist(target, need=max(win + 5, BACKFILL))
    out = {}
    for label, codes in GROUPS:
        seq = series_for_codes(h, days, codes, mode='near', smooth=sm)[-win:]
        if not seq:
            out[label] = None
            continue
        vals = [x[1] for x in seq]
        rank = sum(1 for v in vals if v <= vals[-1])
        out[label] = {'pcr_oi_near': round(vals[-1], 4),
                      'pct': round(rank / len(vals) * 100),
                      'diff': round(vals[-1] - vals[-2], 4) if len(vals) > 1 else None,
                      'date_show': dshow(seq[-1][0])}
    return out


def _pct_word(pct, win=None):
    win = win or WINDOW
    if pct is None:
        return ''
    if pct <= 10:
        return '近%d日最低区间' % win
    if pct <= 30:
        return '偏低'
    if pct <= 70:
        return '中性'
    if pct <= 90:
        return '偏高'
    return '近%d日最高区间' % win


def _dir(diff):
    if diff is None:
        return ''
    if diff > 0.0005:
        return '↑'
    if diff < -0.0005:
        return '↓'
    return '→'


def section_text(date=None, force=False, smooth=None):
    d = compute(date, force=force, smooth=smooth)
    sm = d['smooth']
    L = ['【📊 持仓量 PCR · 分指数 · 剔除到期≤%d天合约%s】' % (
        d['dte_max'], '·%d日累计' % sm if sm > 1 else '')]
    if not d.get('ok'):
        L.append('  数据暂不可用')
        return '\n'.join(L) + '\n'
    for r in d['rows']:
        if not r.get('ok'):
            L.append('  %-6s ：暂无期权数据' % r['label'])
            continue
        s = '  %-6s：%.4f ｜ 近%d日分位 %s%%（%s）' % (
            r['label'], r['pcr_oi'], d['window'], r['pct'], _pct_word(r['pct'], d['window']))
        if r.get('diff') is not None:
            s += ' ｜ 较前日 %+.4f %s' % (r['diff'], _dir(r['diff']))
        L.append(s)
    if d.get('market'):
        m = d['market']
        s = '  %-6s：%.4f ｜ 近%d日分位 %s%%（%s）' % (
            '全市场', m['pcr_oi'], d['window'], m['pct'], _pct_word(m['pct'], d['window']))
        if m.get('diff') is not None:
            s += ' ｜ 较前日 %+.4f %s' % (m['diff'], _dir(m['diff']))
        L.append(s)
    # 到期剔除明细（让大佬一眼看到「为什么这么剔」）
    for r in d['rows'][:1]:
        if r.get('ok') and r.get('near_mat'):
            L.append('  到期明细（%s）：最近到期 %s（%s）dte=%d → %s；保留月份 %s' % (
                r['label'], r['near_my'], d10(r['near_mat']), r['near_dte'],
                '剔除' if r['near_dte'] is not None and r['near_dte'] <= d['dte_max'] else '保留',
                ','.join(r['kept_mm'])))
    L.append('  （口径：认沽持仓量之和 ÷ 认购持仓量之和；到期日距当日 ≤%d 自然日的月份剔除，'
             '其余月份全留；默认单日，不做平滑）' % d['dte_max'])
    if not d.get('is_target'):
        L.append('  （注：%s 数据尚未入库，为最近交易日 %s 口径）' % (
            d10(d['target']), d.get('date_show', '')))
    return '\n'.join(L) + '\n'


def compare_text(date=None, smooth=None):
    d = compute(date, smooth=smooth)
    near = compute_near(date, smooth=smooth)
    L = ['【持仓量 PCR · dte≤%d 口径 vs 无条件剔近月 对照 · %s】' % (d['dte_max'], d.get('date_show', ''))]
    L.append('  %-8s %10s %10s %8s   %s' % ('指数', 'dte≤5', '剔近月', '差值', '到期/剔除'))
    for r in d['rows']:
        if not r.get('ok'):
            continue
        nv = (near.get(r['label']) or {}).get('pcr_oi_near')
        diff = (r['pcr_oi'] - nv) if nv else None
        L.append('  %-8s %10.4f %10s %8s   near=%s(%s) dte=%s →%s' % (
            r['label'], r['pcr_oi'], ('%.4f' % nv) if nv else '-',
            ('%+.4f' % diff) if diff is not None else '-',
            r.get('near_my'), r.get('near_mat'), r.get('near_dte'),
            '剔除' if (r.get('near_dte') is not None and r['near_dte'] <= d['dte_max']) else '保留'))
    return '\n'.join(L) + '\n'


def card_md(date=None, smooth=None):
    d = compute(date, smooth=smooth)
    if not d.get('ok'):
        return '📊 **持仓量 PCR**：数据暂不可用'
    parts = ['%s %.4f(%s%%)' % (r['label'], r['pcr_oi'], r['pct'])
             for r in d['rows'] if r.get('ok')]
    return '📊 **持仓量 PCR（分指数·剔除到期≤%d天）** ｜ ' % d['dte_max'] + ' ｜ '.join(parts)


def backfill(n):
    end = resolve_target(datetime.date.today().strftime('%Y%m%d'))
    days = trade_days_before(end, int(n))
    h = load_hist()
    basic = load_basic(force=True)
    got = 0
    for d in days:
        if d in h:
            continue
        rec = fetch_day(d, basic)
        if rec:
            h[d] = rec
            got += 1
    save_hist(h)
    print('backfill: +%d 天，历史共 %d 天' % (got, len(h)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--card', action='store_true')
    ap.add_argument('--compare', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--smooth', type=int, default=None)
    ap.add_argument('--backfill', type=int, default=0)
    ap.add_argument('--anchor', default='')
    a = ap.parse_args()
    if a.backfill:
        backfill(a.backfill)
        return
    if a.anchor:
        a.date = anchor_date(a.date, a.anchor)
    if a.json:
        print(json.dumps(compute(a.date, force=a.force, smooth=a.smooth),
                         ensure_ascii=False, indent=2))
    elif a.compare:
        print(compare_text(a.date, a.smooth))
    elif a.card:
        print(card_md(a.date, a.smooth))
    else:
        print(section_text(a.date, force=a.force, smooth=a.smooth))


if __name__ == '__main__':
    main()
