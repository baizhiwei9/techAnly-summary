#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""今日上涨行业/概念 · **同花顺口径**（东财已移除，2026-11-12）

历史：本文件曾是全库**唯一**直连东财（push2his.eastmoney.com）的模块。
2026-11-12 大佬定「不要用东财了」→ 板块列表/板块 K 线全部改走 misc/core/ths_hub.py。
2026-11-14 再次收口：默认（TECH_ALLOW_EM=0）连东财域名都发不出去（net_guard 拦截），
         本文件已无任何东财取数代码，`_get()` 保留为抛错垫片（见下方）。

现状：
  · 板块列表：同花顺 `q.10jqka.com.cn/thshy`（行业 90）/ `.../gn`（概念 294）
  · 板块快照：同花顺 `d.10jqka.com.cn/v6/realhead/48_<code>/defer/last.js`
  · 板块日K：同花顺 `d.10jqka.com.cn/v6/line/48_<code>/01/<YYYY>.js`（升序，全量）
  · 不复存在：HOSTS / _get() / FIELDS 等东财专用代码

向后兼容（重要）：
  · `board_list(fs)` 签名不变，fs 里含 `t:3` → 概念，否则行业
  · 返回值除统一键 code/name 外，同时带东财老键 f12/f14（以及 f2/f3/f5/f6/f15/f16/f17）
    —— 老调用方按 f12/f14 取值的代码不用改
  · `kline(code, lmt)` 结构不变：[{date,open,high,low,close,vol,amt}]（升序，date=YYYY-MM-DD）

用法：python3 bin/wyckoff_today_fetch.py [--date YYYYMMDD]
"""
import os, sys, json, time, argparse

# ── 东财开关（2026-11-14 大佬定：默认一个东财请求都不发，它那边一直限流）──
# 本文件现已无任何东财直连代码（2026-11-12 已全量改同花顺）；这里保留开关只为
# 让老调用方（如 misc/core/datafeed.py 的 `F._get(...)`）拿到明确的失败原因，
# 而不是莫名其妙的 AttributeError。
ALLOW_EM = os.environ.get("TECH_ALLOW_EM", "0").strip().lower() in ("1", "true", "yes", "on")


def _get(path, params=None, tries=2, timeout=10):
    """【已废弃】东财 clist 直连垫片。

    2026-11-12 起板块数据全部走同花顺（ths_hub），东财直连代码已删除。
    本垫片只为：调用方立刻拿到明确失败，不再向 push2his/push2.eastmoney.com 发任何请求。
    """
    if ALLOW_EM:
        raise RuntimeError("东财直连代码已于 2026-11-12 下线（仅保留开关壳）；"
                           "板块数据请走 ths_hub / data_hub")
    raise RuntimeError("东财已默认关闭（TECH_ALLOW_EM=0）；板块数据请走 ths_hub / data_hub")


def _repo_root(start):
    """向上探测仓库根：同时含 bin/ 与 chanlun/ 的那一层（2026-11-12 目录整理后可任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "chanlun")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
CACHE = os.path.join(WS, '.cache', 'wyckoff_today')
KCACHE = os.path.join(CACHE, 'k')

# 同花顺模块（misc/core/ths_hub.py）
_CORE = os.path.join(WS, "misc", "core")
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
try:
    import ths_hub as THS
except Exception as _e:      # pragma: no cover
    THS = None
    print("[wyckoff_today] ths_hub 不可用: %s" % _e, file=sys.stderr, flush=True)

# data_hub 取数桥（纯适配层，失败一律返回空 → 自动降级 ths_hub 老链路）
try:
    import dh_bridge as DHB
except Exception as _e:      # pragma: no cover
    DHB = None
    print("[wyckoff_today] dh_bridge 不可用: %s" % _e, file=sys.stderr, flush=True)

# 同花顺 realhead 快照（板块实时/收盘快照，替代东财 clist）
_REALHEAD = "https://d.10jqka.com.cn/v6/realhead/48_%s/defer/last.js"
_H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
      "Referer": "https://q.10jqka.com.cn/"}
QUOTE_GAP = float(os.environ.get("WF_QUOTE_GAP", "0.05"))   # 连续快照请求间隔
QUOTE_TRIES = int(os.environ.get("WF_QUOTE_TRIES", "3"))


def _num(v):
    try:
        f = float(v)
    except Exception:
        return None
    return f


def _quote(code, name=""):
    """单板块快照 → 东财同义字段

    同花顺 realhead 字段（实测 881121 半导体）：
        10=现价 6=昨收 7=今开 8=最高 9=最低 13=成交量 19=成交额 199112=涨跌幅%
    ⚠️ 主力净流入(netmain)、上涨/下跌/平家数(up/dn/n) 同花顺该接口没有 →
       置 0 并在注释写明；当前无调用方消费这些字段（只有 f12/f14 被用到）。

    取数（2026-11-14 补齐）：**data_hub `sector_spot`** 优先（同花顺板块页单请求
        拿全表快照，含 name/涨跌幅/成交额/净流入/涨跌家数/领涨股），
        取不到降级同花顺 realhead 逐板块直连（原链路）。
    ⚠️ 板块快照统一用同花顺口径（881xxx/885xxx），与本脚本板块码同源，无需编码映射。
    """
    # ⓪ data_hub sector_spot（同花顺板块页，单请求拿全表），按 name → code 匹配
    try:
        if DHB is not None:
            row = None
            if name:
                row = DHB.sector_spot_one(name, kind="行业")
            if row is None and code:
                row = DHB.sector_spot_one(str(code), kind="行业")
            if row:
                # 同花顺行业表：vol 单位万手、amt 单位亿元；realhead 口径 vol=手、amt=元
                vol = row.get("vol")
                amt = row.get("amt")
                pct = row.get("pct_chg")
                # ⚠️ 同花顺行业首页只给前 50 个快照，其余 40 个是「有码无值」占位行
                #    （pct_chg 为 NaN）→ 这种情况必须落回 realhead，否则会把空值当快照
                if pct is not None and pct == pct:
                    return dict(price=row.get("price"), pct1d=pct,
                                high=None, low=None, open=None,
                                vol=(None if vol is None or vol != vol else vol * 1e4),
                                amt=(None if amt is None or amt != amt else amt * 1e8))
    except Exception as e:                                      # noqa: BLE001
        print("[wyckoff_today] data_hub sector_spot 失败 %s → 降级同花顺"
              % str(e)[:60], file=sys.stderr, flush=True)

    import requests
    import re
    last = None
    for i in range(QUOTE_TRIES):
        try:
            r = requests.get(_REALHEAD % code, headers=_H, timeout=10)
            m = re.search(r"\((\{.*\})\)\s*;?\s*$", r.text.strip(), re.S)
            if not m:
                raise RuntimeError("realhead 格式无法解析")
            it = (json.loads(m.group(1)) or {}).get("items") or {}
            px = _num(it.get("10"))
            prev = _num(it.get("6"))
            pct = _num(it.get("199112"))
            if pct is None and px and prev:
                pct = (px - prev) / prev * 100.0
            return dict(price=px, pct1d=pct,
                        high=_num(it.get("8")), low=_num(it.get("9")), open=_num(it.get("7")),
                        vol=_num(it.get("13")), amt=_num(it.get("19")))
        except Exception as e:
            last = e
        time.sleep(0.6 * (i + 1))
    print("[wyckoff_today] 快照失败 %s: %s" % (code, str(last)[:60]), file=sys.stderr, flush=True)
    return None


def board_list(fs, with_quotes=False):
    """板块列表（同花顺）

    fs: 兼容老东财的筛选串 —— 含 `t:3` 视为概念，否则行业
    with_quotes: 是否逐板块拉实时快照（pct1d/price/amt…）。默认 False 只给代码+名称。
                 拉快照会多 90/294 次请求，只有 main() 的快照模式需要。

    返回：[{code,name,src='ths', f12,f14, pct1d,price,amt,vol,high,low,open,
            netmain,up,dn,n, f2,f3,f5,f6,f15,f16,f17}]
      · 无快照时 pct1d/price… 为 None，f2/f3/f5/f6/f15/f16/f17 不出现（与东财「缺字段」一致）
    """
    kind = "概念" if "t:3" in str(fs).replace(" ", "") else "行业"
    # ① data_hub sector_list（同花顺板块列表）
    rows = []
    if DHB is not None:
        try:
            rows = DHB.sector_list(kind)
        except Exception as _e:
            print("[wyckoff_today] data_hub sector_list(%s) 失败 %s → 降级 ths_hub"
                  % (kind, str(_e)[:60]), file=sys.stderr, flush=True)
            rows = []
    # ② 老链路兜底：ths_hub
    if not rows:
        if THS is None:
            raise RuntimeError("板块列表取不到（data_hub + ths_hub 都不可用）")
        rows = THS.board_list(kind)
    out = []
    for b in rows:
        code, name = b.get("code"), b.get("name")
        if not code or not name:
            continue
        r = {"code": code, "name": name, "src": "ths",
             # 东财老键（向后兼容：老调用方只读 f12/f14）
             "f12": code, "f14": name,
             # 同花顺该接口拿不到的字段 → 0（无调用方消费）
             "netmain": 0.0, "up": 0, "dn": 0, "n": 0}
        if with_quotes:
            q = _quote(code, name=name)
            if q:
                amt_yi = (q["amt"] or 0.0) / 1e8
                r.update(pct1d=q["pct1d"], price=q["price"], amt=amt_yi, vol=q["vol"],
                         high=q["high"], low=q["low"], open=q["open"],
                         f2=q["price"], f3=q["pct1d"], f5=q["vol"], f6=q["amt"],
                         f15=q["high"], f16=q["low"], f17=q["open"])
            else:
                r.update(pct1d=None, price=None, amt=0.0, vol=0.0,
                         high=None, low=None, open=None)
            time.sleep(QUOTE_GAP)
        out.append(r)
    return out


def kline(code, lmt=140):
    """板块日线（同花顺 48_<code>），升序，取最后 lmt 根

    code: 同花顺板块码（881xxx 行业 / 885xxx 概念）
    ⚠️ 防护：东财 BKxxxx 这类非 6 位数字码直接返回 []（不抛异常，避免中断整条链）

    取数（2026-11-13 统一）：**data_hub `sector_kline`** 优先，取不到降级 ths_hub 老链路。
    """
    code = str(code or "").strip()
    if not (len(code) == 6 and code.isdigit()):
        print("[wyckoff_today] 跳过非板块码 %r（东财 BK 码同花顺不支持）" % code,
              file=sys.stderr, flush=True)
        return []
    # 交易日 ≈ 244/年；多取一年保证足够根数
    years_back = max(2, int((lmt or 140) / 244) + 2)
    # ① data_hub 板块日线
    if DHB is not None:
        try:
            rows = DHB.sector_kline_rows(code=code, years_back=years_back, lmt=lmt)
            if rows:
                return rows
        except Exception as _e:
            print("[wyckoff_today] data_hub sector_kline(%s) 失败 %s → 降级 ths_hub"
                  % (code, str(_e)[:60]), file=sys.stderr, flush=True)
    # ② 老链路兜底：ths_hub
    if THS is None:
        raise RuntimeError("ths_hub 不可用，无法取板块 K 线")
    ks = THS.board_kline(code, years_back=years_back)
    if not ks:
        return []
    return ks[-int(lmt):] if lmt else ks


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
        rows = board_list(fs, with_quotes=True)
        keep = []
        for r in rows:
            pct = r.get('pct1d')
            if not isinstance(pct, (int, float)):
                continue
            f = lambda k: float(r.get(k)) if isinstance(r.get(k), (int, float)) else 0.0
            keep.append(dict(code=r['code'], name=r['name'], pct1d=float(pct),
                             price=f('price'), amt=f('amt'), vol=f('vol'),
                             high=f('high'), low=f('low'), open=f('open'),
                             netmain=f('netmain'), up=int(f('up')), dn=int(f('dn')), n=int(f('n'))))
        keep.sort(key=lambda x: -x['pct1d'])
        uprows = [x for x in keep if x['pct1d'] > 0]
        print('[%s] 共 %d ｜ 上涨 %d ｜ %.0fs' % (kind, len(keep), len(uprows), time.time() - t0), flush=True)
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
                    # 快照模式只要 ~140 根（半年），够判今日结构
                    ks = kline(b['code'], lmt=140)
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
