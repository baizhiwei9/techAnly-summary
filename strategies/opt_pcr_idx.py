#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成交量 PCR · 分指数版（0 token 纯脚本）
=====================================================
给股票早晚报用：**分指数**的成交量 PCR + 近 60 日分位数 + 较前一交易日的升降。
全部程序化计算，不调用任何大模型。

口径
----
成交量 PCR = 该指数全部挂钩期权合约的「认沽成交量 / 认购成交量」，**剔除当月合约**。

指数 → 期权标的（可多标的合并）
  * 沪深300 : 300ETF期权(华泰柏瑞 510300 + 嘉实 159919) + 沪深300股指期权 IO(000300)
  * 中证1000: 中证1000股指期权 MO(000852)；若无数据 → 退中证2000（无则跳过）
  * 科创50  : 科创50ETF期权(华夏 588000 + 易方达 588080)
  * 创业板  : 创业板ETF期权(易方达 159915)
  * 全市场  : 上交所 + 深交所 全部 ETF 期权（对照用）

「当月合约」= 该标的当日仍在交易的最近到期月份（跨月自动滚动），避免临到期
时间价值塌陷与换月把指标打歪。

数据
----
tushare `opt_daily`(SSE/SZSE/CFFEX) + `opt_basic`，走 bin/tushare_init.ts_call（代理优先）。
逐日缓存 .cache/opt_pcr/idx_hist.json，只增量抓新交易日。

用法
----
    python3 strategies/opt_pcr_idx.py                 # 报告用中文段
    python3 strategies/opt_pcr_idx.py --date 2026-10-15
    python3 strategies/opt_pcr_idx.py --json
    python3 strategies/opt_pcr_idx.py --backfill 75   # 首次部署回补历史
    python3 strategies/opt_pcr_idx.py --anchor morning --date 2026-10-15
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
HIST = os.path.join(CACHE, 'idx_hist.json')
BASIC = os.path.join(CACHE, 'opt_basic_all.json')

WINDOW = int(os.environ.get('OPT_PCR_WINDOW', '60'))
BACKFILL = int(os.environ.get('OPT_PCR_BACKFILL', '75'))
EXCL = os.environ.get('OPT_PCR_EXCL', 'near')   # near(默认) | cal
# 平滑窗口：PCR = 近 N 个交易日「认沽成交量之和 / 认购成交量之和」（用户 2026-10-15 指定 5 日）
SMOOTH = int(os.environ.get('OPT_PCR_SMOOTH', '5'))

# 指数 → 期权标的 opt_code（顺序即展示顺序）
GROUPS = [
    ('沪深300', ['OP510300.SH', 'OP159919.SZ', 'OP000300.SH'],
     ['300ETF(华泰柏瑞)', '300ETF(嘉实)', '沪深300股指IO']),
    ('中证1000', ['OP000852.SH'], ['中证1000股指MO']),
    ('科创50', ['OP588000.SH', 'OP588080.SH'], ['科创50ETF(华夏)', '科创50ETF(易方达)']),
    ('创业板', ['OP159915.SZ'], ['创业板ETF(易方达)']),
]
# 中证1000 缺失时的退路（用户：中证1000 没有就中证2000，中证2000 没有就算了）
FALLBACK_1000 = ('中证2000', [])

ALL_CODES = sorted({c for _l, cs, _n in GROUPS for c in cs} | set(FALLBACK_1000[1]))
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
        with open(os.path.join(WS, '.cache/opt_pcr.log'), 'a', encoding='utf-8') as f:
            f.write('[%s] idx %s\n' % (datetime.datetime.now().strftime('%F %T'), m))
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
    if not force and os.path.exists(BASIC):
        try:
            c = json.load(open(BASIC, encoding='utf-8'))
            if (datetime.datetime.now() -
                    datetime.datetime.strptime(c['ts'], '%Y-%m-%d %H:%M:%S')).days <= 3:
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
            m[str(r['ts_code'])] = {'cp': str(r['call_put']),
                                    'mm': str(r['maturity_date'])[:6],
                                    'oc': str(r.get('opt_code', ''))}
    if m:
        json.dump({'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'map': m},
                  open(BASIC, 'w', encoding='utf-8'), ensure_ascii=False)
    return m


# ------------------------------------------------------------ 单日 → 每标的聚合
def fetch_day(date, basic):
    """返回 {opt_code: {'c','p','c_nm','p_nm','c_all','p_all','near'}}（不含无期权标的）"""
    s = d8(date)
    cm = s[:6]
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
            if not b or not b.get('cp') or not b.get('oc'):
                continue
            oc = b['oc']
            if oc not in ALL_CODES:
                continue
            d = per.setdefault(oc, {'c': 0.0, 'p': 0.0, 'c_nm': 0.0, 'p_nm': 0.0,
                                    'c_all': 0.0, 'p_all': 0.0, 'mms': set()})
            v = float(r['vol'] or 0)
            d['mms'].add(b['mm'])
            key = 'c' if b['cp'] == 'C' else 'p'
            d[key + '_all'] += v
            if b['mm'] != cm:
                d[key + '_nm'] += v
            d['_v'] = d.get('_v', [])
            d['_v'].append((b['cp'], b['mm'], v))
    if not got:
        return None
    # 每个标的的「当月」= 当日仍在交易的最近到期月（rolling）
    out = {}
    for oc, d in per.items():
        live = [mm for mm in d['mms'] if mm >= cm]
        near = min(live) if live else cm
        for cp, mm, v in d['_v']:
            if mm == near:
                continue
            if cp == 'C':
                d['c'] += v
            else:
                d['p'] += v
        out[oc] = {'c': d['c'], 'p': d['p'], 'c_nm': d['c_nm'], 'p_nm': d['p_nm'],
                   'c_all': d['c_all'], 'p_all': d['p_all'], 'near': near}
    return out


def _ratio(c, p):
    return (p / c) if c else None


def group_pcr(daymap, codes, mode=None):
    """把某日多个标的聚合成指数级 PCR"""
    mode = mode or EXCL
    c = p = 0.0
    for oc in codes:
        r = daymap.get(oc)
        if not r:
            continue
        if mode == 'cal':
            c += r['c_nm']; p += r['p_nm']
        else:
            c += r['c']; p += r['p']
    return _ratio(c, p)


# ------------------------------------------------------------ 缓存
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
    for d in miss:
        rec = fetch_day(d, basic)
        if rec:
            h[d] = rec
    if miss:
        save_hist(h)
        log('fetched %d day(s)' % len(miss))
    return h, days


# ------------------------------------------------------------ 计算
def _daily_seq(h, days, codes):
    """某指数逐交易日的（认购量, 认沽量）——已是剔除当月口径"""
    out = []
    for d in days:
        rec = h.get(d)
        if not rec:
            continue
        c = p = 0.0
        ok = False
        for oc in codes:
            r = rec.get(oc)
            if not r:
                continue
            c += r['c']
            p += r['p']
            ok = True
        if ok and c > 0:
            out.append((d, c, p))
    return out


def _smoothed(h, days, codes, n=None):
    """5 日（N 日）平滑 PCR 序列：[date, pcr]；PCR = Σ认沽量 / Σ认购量（跨日累加）"""
    n = n or SMOOTH
    seq = _daily_seq(h, days, codes)
    out = []
    for i in range(len(seq)):
        j = max(0, i - n + 1)
        if i - j + 1 < n:
            continue
        c = sum(x[1] for x in seq[j:i + 1])
        p = sum(x[2] for x in seq[j:i + 1])
        if c:
            out.append((seq[i][0], p / c, c, p))
    return out


def _series(h, days, codes, win):
    seq = _smoothed(h, days, codes)
    seq = seq[-win:]
    return [x[0] for x in seq], [x[1] for x in seq]


def compute(date=None, force=False, window=None):
    win = window or WINDOW
    target = resolve_target(date)
    h, days = ensure_hist(target, need=max(win + 5, BACKFILL))
    rows = []
    for label, codes, _names in GROUPS:
        codes = list(codes)
        if label == '中证1000' and FALLBACK_1000[1]:
            seq, vals = _series(h, days, codes, win)
            if not seq:
                label, codes = FALLBACK_1000[0], list(FALLBACK_1000[1])
        seq, vals = _series(h, days, codes, win)
        if not seq:
            rows.append({'label': label, 'ok': False, 'codes': codes})
            continue
        cur = seq[-1]
        cur_pcr = vals[-1]
        rank = sum(1 for v in vals if v <= cur_pcr)
        pct = round(rank / len(vals) * 100) if vals else None
        prev_pcr = vals[-2] if len(vals) >= 2 else None
        diff = (cur_pcr - prev_pcr) if prev_pcr is not None else None
        rows.append({
            'label': label, 'ok': True, 'codes': codes,
            'date': cur, 'date_show': dshow(cur),
            'pcr': round(cur_pcr, 4),
            'pct': pct, 'samples': len(vals), 'window': win,
            'prev_show': dshow(seq[-2]) if len(seq) >= 2 else None,
            'prev_pcr': round(prev_pcr, 4) if prev_pcr is not None else None,
            'diff': round(diff, 4) if diff is not None else None,
            'min': round(min(vals), 4), 'max': round(max(vals), 4),
        })
    # 全市场（对照）
    ALL = sorted({oc for oc in {oc for l, cs, n in GROUPS for oc in cs}})
    seq, vals = _series(h, days, ALL, win)
    mkt = None
    if seq:
        cur_pcr, prev_pcr = vals[-1], (vals[-2] if len(vals) >= 2 else None)
        rank = sum(1 for v in vals if v <= cur_pcr)
        mkt = {'label': '全市场', 'ok': True, 'pcr': round(cur_pcr, 4),
               'pct': round(rank / len(vals) * 100),
               'diff': round(cur_pcr - prev_pcr, 4) if prev_pcr is not None else None,
               'prev_pcr': round(prev_pcr, 4) if prev_pcr is not None else None,
               'date_show': dshow(seq[-1]), 'window': win, 'samples': len(vals)}
    return {'ok': any(r.get('ok') for r in rows), 'target': target,
            'is_target': bool(rows[0].get('date') == target) if rows and rows[0].get('ok') else False,
            'window': win, 'rows': rows, 'market': mkt,
            'date_show': next((r['date_show'] for r in rows if r.get('ok')), '')}


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


def section_text(date=None, force=False):
    d = compute(date, force=force)
    L = ['【📊 成交量 PCR · 分指数 · %d日均 · 剔除当月合约】' % SMOOTH]
    if not d.get('ok'):
        L.append('  数据暂不可用')
        return '\n'.join(L) + '\n'
    for r in d['rows']:
        if not r.get('ok'):
            L.append('  %-6s ：暂无期权数据' % r['label'])
            continue
        diff = r.get('diff')
        s = '  %-6s：%.4f ｜ 近%d日分位 %s%%（%s）' % (
            r['label'], r['pcr'], d['window'], r['pct'], _pct_word(r['pct'], d['window']))
        if diff is not None:
            s += ' ｜ 较前日 %+.4f %s' % (diff, _dir(diff))
        L.append(s)
    if d.get('market'):
        m = d['market']
        s = '  %-6s：%.4f ｜ 近%d日分位 %s%%（%s）' % (
            '全市场', m['pcr'], d['window'], m['pct'], _pct_word(m['pct'], d['window']))
        if m.get('diff') is not None:
            s += ' ｜ 较前日 %+.4f %s' % (m['diff'], _dir(m['diff']))
        L.append(s)
    L.append('  （%d日均：近%d个交易日认沽量之和 ÷ 认购量之和，剔除当月合约；数据=上/深交所 ETF 期权 + 中金所股指期权）' % (
        SMOOTH, SMOOTH))
    if not d.get('is_target'):
        L.append('  （注：%s 数据尚未入库，为最近交易日 %s 口径）' % (d10(d['target']), d.get('date_show', '')))
    return '\n'.join(L) + '\n'


def card_md(date=None, force=False):
    d = compute(date, force=force)
    if not d.get('ok'):
        return '📊 **成交量 PCR（分指数）**：数据暂不可用'
    parts = []
    for r in d['rows']:
        if not r.get('ok'):
            continue
        parts.append('%s %.4f(%s%%)' % (r['label'], r['pcr'], r['pct']))
    return '📊 **成交量 PCR（分指数·%d日均·剔除当月）** ｜ ' % SMOOTH + ' ｜ '.join(parts)


def backfill(n):
    days = trade_days_before(datetime.date.today().strftime('%Y%m%d'), int(n))
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
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--backfill', type=int, default=0)
    ap.add_argument('--anchor', default='')
    args = ap.parse_args()
    if args.backfill:
        backfill(args.backfill)
        return
    if args.anchor:
        args.date = anchor_date(args.date, args.anchor)
    if args.json:
        print(json.dumps(compute(args.date, force=args.force), ensure_ascii=False, indent=2))
    elif args.card:
        print(card_md(args.date, force=args.force))
    else:
        print(section_text(args.date, force=args.force))


if __name__ == '__main__':
    main()
