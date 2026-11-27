#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行业横截面排名引擎（机会提示）

用途：给「收盘/开盘」报告加一段「行业排名上升」——
  ① 当天涨得好的行业；
  ② 这些行业最近 2-3 天的横截面动量名次是不是在往前走；
  ③ 名次连续上升的行业单独挑出来（= 机会提示，让用户关注）。

数据：
  * 全部同花顺行业板块的日线（90 个）——优先复用 `.cache/sector_rotation/<date>/hist/*.json`，
    缺失时用 akshare `stock_board_industry_index_ths` 现抓并缓存；
  * 当日板块涨跌幅——优先复用统一数据层 `.cache/market/<date>/industry`，否则读
    `.cache/sector_rotation/<date>/summary.json`。

名次口径：
  动量窗口 MOM_WIN（默认 5）个交易日的累计涨幅，在全部行业里做横截面排名（1=最强）。

用法：
  python3 strategies/industry_rank.py --date 2026-10-13            # markdown 到 stdout
  python3 strategies/industry_rank.py --date 2026-10-13 --since     # 早报口径（到上一交易日）
  from industry_rank import section_md; print(section_md('2026-10-13'))
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, socket, argparse, datetime, time

_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

WS = '/root/.openclaw/workspace'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MOM_WIN = int(os.environ.get('IND_RANK_WIN', '5'))     # 名次用的动量窗口（交易日）
LOOKBACK = int(os.environ.get('IND_RANK_LOOKBACK', '4'))  # 回看/展示多少天名次
TOP_GAIN = int(os.environ.get('IND_RANK_TOP', '10'))   # 今日领涨取前几
TOP_RISE = int(os.environ.get('IND_RANK_RISE', '8'))   # 名次上升取前几
HIST_DIR_DAYS = 120                                    # 现抓日线回看天数


def _d8(date):
    return str(date).replace('-', '')[:8]


def _d10(d8):
    d8 = _d8(d8)
    return f'{d8[:4]}-{d8[4:6]}-{d8[6:]}'


def _resolve(date):
    """非交易日回退到最近交易日，返回 (d8, 是否当日)"""
    try:
        import trade_day
        r, ok, _ = trade_day.resolve(_d8(date))
        return _d8(r), ok
    except Exception:
        return _d8(date), True


# ───────── 数据加载 ─────────
def _hist_from_sector_cache(date):
    """从 .cache/sector_rotation/<Y-M-D>/hist/*.json 读全部行业日线"""
    d = _d10(date)
    hp = os.path.join(WS, '.cache/sector_rotation', d, 'hist')
    if not os.path.isdir(hp):
        return None
    out = {}
    for f in os.listdir(hp):
        if not f.endswith('.json'):
            continue
        name = f[:-5].replace('_', '/')
        try:
            rows = json.load(open(os.path.join(hp, f), encoding='utf-8'))
        except Exception:
            continue
        seq = []
        for r in rows:
            ds = r.get('日期')
            if isinstance(ds, (int, float)):
                ds = datetime.datetime.utcfromtimestamp(ds / 1000).strftime('%Y%m%d')
            else:
                ds = _d8(str(ds))
            try:
                seq.append((ds, float(r['收盘价'])))
            except Exception:
                pass
        if len(seq) >= MOM_WIN + LOOKBACK + 2:
            seq.sort()
            out[name] = seq
    return out or None


def _hist_from_ak(date):
    """akshare 现抓全部同花顺行业日线，缓存到 .cache/xq_industry"""
    from data_hub import ak as ak
    cache = os.path.join(WS, '.cache/xq_industry')
    os.makedirs(cache, exist_ok=True)
    names_p = os.path.join(cache, 'all_names.json')
    names = None
    if os.path.exists(names_p) and os.path.getsize(names_p) > 50:
        try:
            names = json.load(open(names_p, encoding='utf-8'))
        except Exception:
            names = None
    if not names:
        df = ak.stock_board_industry_summary_ths()
        names = [str(x) for x in df['板块'].tolist()]
        json.dump(names, open(names_p, 'w', encoding='utf-8'), ensure_ascii=False)
    beg = (datetime.date.fromisoformat(_d10(date)) - datetime.timedelta(days=HIST_DIR_DAYS)).strftime('%Y%m%d')
    end = _d8(date)
    out = {}
    for n in names:
        p = os.path.join(cache, f'{n.replace("/", "_")}_{end}.json')
        seq = None
        if os.path.exists(p) and os.path.getsize(p) > 200:
            try:
                seq = json.load(open(p, encoding='utf-8'))
            except Exception:
                seq = None
        if not seq:
            for _ in range(3):
                try:
                    df = ak.stock_board_industry_index_ths(symbol=n, start_date=beg, end_date=end)
                    seq = []
                    for _, r in df.iterrows():
                        ds = r['日期']
                        ds = ds.strftime('%Y%m%d') if hasattr(ds, 'strftime') else _d8(str(ds))
                        seq.append((ds, float(r['收盘价'])))
                    seq.sort()
                    if seq:
                        json.dump(seq, open(p, 'w', encoding='utf-8'))
                    break
                except Exception:
                    time.sleep(1.2)
        if seq and len(seq) >= MOM_WIN + LOOKBACK + 2:
            out[n] = seq
    return out or None


def load_hist(date):
    return _hist_from_sector_cache(date) or _hist_from_ak(date) or {}


def today_pct(date):
    """当日板块涨跌幅 {name: pct}"""
    d = _d10(date)
    try:
        import market_hub
        h = market_hub.load_hub(_d8(date))
        if h and h.get('industry') is not None:
            ind = h['industry']
            return {str(r['name']): float(r['pct']) for _, r in ind.iterrows()}
    except Exception:
        pass
    p = os.path.join(WS, '.cache/sector_rotation', d, 'summary.json')
    if os.path.exists(p):
        try:
            return {str(r['板块']): float(r['涨跌幅']) for r in json.load(open(p, encoding='utf-8'))}
        except Exception:
            pass
    return {}


# ───────── 名次计算 ─────────
def build(date, win=MOM_WIN, look=LOOKBACK):
    """返回 dict: dates(近 look+1 个交易日的 d8，旧→新)、rank{date:{board:名次}}、mom{date:{board:窗口涨幅}}、pct(今日)"""
    hist = load_hist(date)
    if not hist:
        return None
    # 构造 close[board][date]
    cl = {b: {d: c for d, c in seq} for b, seq in hist.items()}
    alld = sorted({d for seq in hist.values() for d, _ in seq})

    # 历史日线通常缺“今天”（指数日线盘中/盘后未落地）→ 用当日板块涨跌幅合成今天收盘
    today = _d8(date)
    if today not in alld:
        pc = today_pct(date)
        lastd = alld[-1]
        added = False
        for b in hist:
            if b in pc and cl[b].get(lastd):
                cl[b][today] = cl[b][lastd] * (1 + pc[b] / 100.0)
                added = True
        if added:
            alld = alld + [today]

    order = list(alld)
    pos = {d: i for i, d in enumerate(order)}
    show = order[-(look + 1):]

    rank, mom = {}, {}
    for d in show:
        i = pos[d]
        if i < win:
            continue
        prev_d = order[i - win]
        vals = {}
        for b in hist:
            c1, c0 = cl[b].get(d), cl[b].get(prev_d)
            if c1 and c0:
                vals[b] = (c1 / c0 - 1) * 100
        if not vals:
            continue
        srt = sorted(vals, key=lambda x: -vals[x])
        rank[d] = {b: k + 1 for k, b in enumerate(srt)}
        mom[d] = vals
    show = [d for d in show if d in rank]
    if not show:
        return None
    return dict(dates=show, rank=rank, mom=mom, pct=today_pct(date),
                win=win, n=len(hist))


def _traj(rank, dates, b):
    return [rank.get(d, {}).get(b) for d in dates]


def _rising(rank, dates, b, need=2):
    """名次连续上升：后一天名次 < 前一天名次，连续 need 次"""
    seq = [r for r in _traj(rank, dates, b) if r is not None]
    if len(seq) < need + 1:
        return 0
    cnt = 0
    for i in range(1, len(seq)):
        if seq[i] < seq[i - 1]:
            cnt += 1
        else:
            cnt = 0
    return cnt


def section_md(date, title_prefix='📈 行业排名上升'):
    """生成 markdown 段落；无数据返回 ''"""
    d8, _ = _resolve(date)
    R = build(d8)
    if not R or not R['rank']:
        return ''
    dates = R['dates']
    rank, mom, pct = R['rank'], R['mom'], R['pct']
    last = dates[-1]
    win = R['win']
    n = len(dates)
    L = [f'### {title_prefix}（{win}日动量横截面名次，1=最强；近{n}个交易日）']

    def _arrow(seq):
        s = [x for x in seq if x is not None]
        if len(s) < 2:
            return '—'
        d = s[-1] - s[0]
        if d <= -1:
            return f'↑走强{abs(d)}名'
        if d >= 1:
            return f'↓回落{d}名'
        return '—'

    top = sorted(pct, key=lambda x: -pct[x])[:TOP_GAIN] if pct else []

    # ① 今日领涨 · 名次轨迹
    L.append(f'**① 今日领涨行业 · 动量名次轨迹**（名次越小越强）')
    for b in top:
        seq = _traj(rank, dates, b)
        tr = '→'.join(str(s) if s else '·' for s in seq)
        L.append(f"- {b} {pct.get(b, 0):+.2f}% ｜ {tr} ｜ {_arrow(seq)}")
    L.append('')

    # ② 名次连续上升（机会提示）
    rise = []
    for b in rank.get(last, {}):
        c = _rising(rank, dates, b)
        if c >= 2:
            rise.append((b, c, _traj(rank, dates, b), pct.get(b)))
    rise.sort(key=lambda x: (-x[1], (x[2][0] - x[2][-1]) if x[2][0] and x[2][-1] else 0))
    if rise:
        L.append(f'**② 名次连续上升 · 机会提示**（近{n}日名次持续往前走）')
        for b, c, seq, p in rise[:TOP_RISE]:
            tr = '→'.join(str(s) if s else '·' for s in seq)
            px = f'｜今日{p:+.2f}%' if p is not None else ''
            L.append(f"- {b}：{tr}（连续{c}日上升）{px}　← 🔭 关注")
        L.append('')

    # ③ 今日领涨 + 名次上升 的交集（最强信号）
    both = [x for x in rise if x[0] in top]
    if both:
        L.append('**③ 今日领涨 ∩ 名次上升**（当天强 + 排名往前走，最值得盯）')
        for b, c, seq, p in both:
            L.append(f"- {b} {p:+.2f}% ｜ 名次" + '→'.join(str(s) if s else '·' for s in seq))
        L.append('')
    return '\n'.join(L).rstrip()


def prev_trade(d8):
    """上一个交易日（YYYYMMDD）"""
    try:
        import trade_day
        return trade_day.prev_trade(_d8(d8))
    except Exception:
        return None


def prev_trade_strict(d8):
    """严格早于 d8 的最近交易日（早报用：看上一个交易日的收盘排名）"""
    d8 = _d8(d8)
    try:
        import trade_day
        days = trade_day.cal_days()
        cands = [k for k, v in days.items() if str(v) == '1' and k < d8]
        if cands:
            return max(cands)
    except Exception:
        pass
    return None


def section_text(date, title='📈 行业排名 · 机会提示', compact=False):
    """报告用：纯文本（带【】标题，匹配 xq_report 的 split_sections 分段）

    早报（到上一交易日）：名次以「上一交易日」为最新一天；
    晚报（当日）：名次包含当日（用当日板块涨跌幅合成）。
    compact=True（交易员模式）：只保留「名次连升」和「领涨∩连升」两段，条数减半，
                               不列涨幅前十的完整轨迹。
    """
    d8 = _d8(date)
    R = build(d8)
    if not R or not R['rank']:
        return ''
    dates, rank, pct, win = R['dates'], R['rank'], R['pct'], R['win']
    last = dates[-1]
    n = len(dates)
    pctsrc = _d10(last)
    L = ['【%s】' % title]

    def tr(b):
        return '→'.join(str(x) if x else '·' for x in _traj(rank, dates, b))

    top = sorted(pct, key=lambda x: -pct[x])[:TOP_GAIN] if pct else []
    if top and not compact:
        L.append('  ▪️ %s 涨幅前十（动量名次：近%d日，越小越强）' % (pctsrc, n))
        for b in top:
            seq = [x for x in _traj(rank, dates, b) if x]
            dlt = (seq[0] - seq[-1]) if len(seq) >= 2 else 0
            tag = ('↑走强%d名' % dlt) if dlt >= 1 else (('↓回落%d名' % -dlt) if dlt <= -1 else '—')
            L.append('    %s %+.2f%% ｜ %s ｜ %s' % (b, pct.get(b, 0), tr(b), tag))
        L.append('')

    rise = []
    for b in rank.get(last, {}):
        c = _rising(rank, dates, b)
        if c >= 2:
            rise.append((b, c, _traj(rank, dates, b), pct.get(b)))
    rise.sort(key=lambda x: (-x[1], (x[2][0] - x[2][-1]) if x[2][0] and x[2][-1] else 0))
    if rise:
        L.append('  ▪️ 名次连续上升（机会提示：排名持续往前走）')
        for b, c, seq, p in rise[:(4 if compact else TOP_RISE)]:
            px = (' ｜收 %+.2f%%' % p) if p is not None else ''
            L.append('    🔭 %s：%s（连续%d日上升）%s　← 关注' % (b, tr(b), c, px))
        L.append('')

    both = [x for x in rise if x[0] in top]
    if both:
        L.append('  ▪️ %s 领涨 ∩ 名次上升（最强信号）' % pctsrc)
        for b, c, seq, p in both[:(3 if compact else len(both))]:
            L.append('    🌙 %s %+.2f%% ｜ %s' % (b, p, tr(b)))
        L.append('')
    if len(L) <= 2:
        return ''
    return '\n'.join(L).rstrip()


def card_md(date):
    """飞书卡片用：紧凑 markdown（不含【】标题）"""
    d8 = _d8(date)
    R = build(d8)
    if not R or not R['rank']:
        return ''
    dates, rank, pct, win = R['dates'], R['rank'], R['pct'], R['win']
    n = len(dates)
    L = ['**📈 行业排名 · 机会提示**（%d日动量横截面名次，1=最强）' % win]
    rise = []
    for b in rank.get(dates[-1], {}):
        c = _rising(rank, dates, b)
        if c >= 2:
            rise.append((b, c, _traj(rank, dates, b), pct.get(b)))
    rise.sort(key=lambda x: (-x[1], (x[2][0] - x[2][-1]) if x[2][0] and x[2][-1] else 0))
    if rise:
        L.append('**🔭 名次连续上升（关注）**')
        for b, c, seq, p in rise[:TOP_RISE]:
            tr = '→'.join(str(x) if x else '·' for x in seq)
            px = (' ｜今日%+.2f%%' % p) if p is not None else ''
            L.append('- %s：%s（连续%d日）%s' % (b, tr, c, px))
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=datetime.date.today().strftime('%Y%m%d'))
    ap.add_argument('--out', default='')
    ap.add_argument('--card', action='store_true', help='飞书卡片紧凑版')
    args = ap.parse_args()
    md = card_md(args.date) if args.card else section_md(args.date)
    if not md:
        print('no data', file=sys.stderr)
        sys.exit(1)
    if args.out:
        open(args.out, 'w', encoding='utf-8').write(md)
        print('written:', args.out, len(md), '字')
    else:
        print(md)


if __name__ == '__main__':
    main()
