#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""今日上涨行业/概念 · 东方财富板块数据（稳健串行 + 增量缓存）

- 板块列表：push2his clist（含今日开高低/量额）
- 板块日K：push2his kline（盘中常限流，串行 + 指数退避 + 单只落盘缓存）
用法：python3 bin/wyckoff_today_fetch.py [--date YYYYMMDD]
"""
import os, sys, json, time, argparse

def _repo_root(start):
    """向上探测仓库根：同时含 bin/ 与 core/ 的那一层（2026-11-12 目录整理后可任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "core")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
CACHE = os.path.join(WS, '.cache', 'wyckoff_today')
KCACHE = os.path.join(CACHE, 'k')
HOSTS = ['push2his.eastmoney.com', '7.push2.eastmoney.com']
H = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}
FIELDS = 'f2,f3,f5,f6,f12,f14,f15,f16,f17,f62,f104,f105,f106'


def _get(path, params, tries=6, timeout=12):
    import requests
    last = None
    for i in range(tries):
        h = HOSTS[i % len(HOSTS)]
        try:
            r = requests.get('https://%s%s' % (h, path), params=params, headers=H, timeout=timeout)
            j = r.json()
            if j and j.get('data'):
                return j['data']
        except Exception as e:
            last = e
        time.sleep(1.2 * (i + 1))
    raise RuntimeError('fetch fail %s: %s' % (path, type(last).__name__))


def board_list(fs):
    d = _get('/api/qt/clist/get', dict(pn=1, pz=800, po=1, np=1, fltt=2, invt=2, fid='f3', fs=fs, fields=FIELDS))
    return d.get('diff') or []


def kline(code, lmt=140):
    p = dict(secid='90.%s' % code, klt=101, fqt=1, lmt=lmt, end='20500101',
             fields1='f1,f2,f3,f4,f5,f6', fields2='f51,f52,f53,f54,f55,f56,f57')
    d = _get('/api/qt/stock/kline/get', p, tries=4)
    ks = []
    for ln in (d.get('klines') or []):
        a = ln.split(',')
        ks.append(dict(date=a[0], open=float(a[1]), close=float(a[2]), high=float(a[3]),
                       low=float(a[4]), vol=float(a[5]), amt=float(a[6])))
    return ks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    ap.add_argument('--delay', type=float, default=0.45)
    ap.add_argument('--kinds', default='行业,概念')
    a = ap.parse_args()
    os.makedirs(KCACHE, exist_ok=True)
    kinds = [x for x in a.kinds.split(',') if x]
    out = {}
    for kind, fs in (('行业', 'm:90 t:2'), ('概念', 'm:90 t:3')):
        if kind not in kinds:
            continue
        t0 = time.time()
        rows = board_list(fs)
        keep = []
        for r in rows:
            pct = r.get('f3')
            if not isinstance(pct, (int, float)):
                continue
            f = lambda k: float(r[k]) if isinstance(r.get(k), (int, float)) else 0.0
            keep.append(dict(code=r['f12'], name=r['f14'], pct1d=float(pct),
                             price=f('f2'), amt=f('f6') / 1e8, vol=f('f5'),
                             high=f('f15'), low=f('f16'), open=f('f17'),
                             netmain=f('f62') / 1e8, up=int(f('f104')), dn=int(f('f105')), n=int(f('f106'))))
        keep.sort(key=lambda x: -x['pct1d'])
        uprows = [x for x in keep if x['pct1d'] > 0]
        print('[%s] 共 %d ｜ 上涨 %d' % (kind, len(keep), len(uprows)), flush=True)
        miss = 0
        for i, b in enumerate(uprows):
            fp = os.path.join(KCACHE, '%s.json' % b['code'])
            ks = None
            if os.path.exists(fp):
                try:
                    ks = json.load(open(fp))
                except Exception:
                    ks = None
            if not ks or len(ks) < 70:
                try:
                    ks = kline(b['code'])
                    json.dump(ks, open(fp, 'w'))
                    time.sleep(a.delay)
                except Exception as e:
                    miss += 1
                    print('  kline fail %s %s' % (b['name'], str(e)[:50]), file=sys.stderr, flush=True)
                    ks = []
            b['k'] = ks
            if i and i % 25 == 0:
                print('  ... %d/%d ｜ %.0fs ｜ miss=%d' % (i, len(uprows), time.time() - t0, miss), flush=True)
        ok = sum(1 for x in uprows if len(x.get('k') or []) > 60)
        print('[%s] K线就绪 %d / %d ｜ %.0fs' % (kind, ok, len(uprows), time.time() - t0), flush=True)
        out[kind] = keep
    ds = ''
    for b in out.get('行业', []):
        if b.get('k'):
            ds = b['k'][-1]['date']
            break
    d8 = a.date or ds.replace('-', '')
    path = os.path.join(CACHE, 'snap_%s.json' % (d8 or 'today'))
    json.dump(out, open(path, 'w'), ensure_ascii=False)
    print('saved', path, 'hist_last=', ds, 'today_snapshot_price_ok=', bool(out.get('行业')))


if __name__ == '__main__':
    main()
