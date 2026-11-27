# -*- coding: utf-8 -*-
"""
申万一级行业分层（强势/承压 五步法）
====================================
数据源：tushare 代理端点
  - sw_daily                 申万指数日线（31 个一级）
  - daily / daily_basic      个股涨跌、成交额、换手、市值
  - moneyflow                个股主力净流入（20 交易日累计）
  - limit_list_d             涨停/炸板/跌停（封板率、炸板率、封单）
  - index_member_all         个股 → 申万一级 归属（缓存 30 天）
  - index_daily              上证指数、深证成指（大盘环境 + 锚）

缓存：.cache/industry_layer/
用法：
  python3 strategies/industry_layer.py [--date 20261019] [--json]
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, time, argparse, datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tushare_init import get_pro

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(WS, '.cache', 'industry_layer')
os.makedirs(CACHE, exist_ok=True)
pro = get_pro()

WEIGHT_IND = {'银行', '非银金融', '建筑装饰', '石油石化', '交通运输'}  # 权重护盘嫌疑组


# ---------------------------------------------------------------- 基础工具
def _cached(name, fn, ttl_hours=12):
    p = os.path.join(CACHE, name)
    if os.path.exists(p):
        age = (time.time() - os.path.getmtime(p)) / 3600.0
        if age < ttl_hours:
            try:
                return pd.read_pickle(p)
            except Exception:
                pass
    df = fn()
    if df is not None and not df.empty:
        df.to_pickle(p)
    return df


def call(api, retries=2, **kw):
    last = None
    for i in range(retries + 1):
        try:
            return getattr(pro, api)(**kw)
        except Exception as e:
            last = e
            time.sleep(2 + 2 * i)
    print('[WARN] %s %s 失败: %s' % (api, kw, str(last)[:120]), file=sys.stderr)
    return None


def build_summary(res, mkt, ind):
    """复盘总结（数据驱动，≤200 字）"""
    sh_pct = mkt.get('sh_pct') or 0
    amt_ratio = mkt.get('amt_ratio') or 1
    up = mkt.get('up_ratio') or 0
    med = mkt.get('med_pct') or 0
    top_up = mkt.get('cap_top_up')
    bot_up = mkt.get('cap_bot_up')
    n_strong = len(res.get('strong') or [])
    near = res.get('strong_near') or []
    L = []
    vol = '缩量' if amt_ratio < 0.95 else ('放量' if amt_ratio > 1.05 else '平量')
    if sh_pct < -0.8:
        tone = '缩量下杀' if amt_ratio < 0.95 else '放量杀跌'
    elif sh_pct > 0 and up < 0.45:
        tone = '缩量分化'
    else:
        tone = '横盘震荡'
    L.append('今日%s：指数%+.2f%%，但仅%.0f%%个股上涨、中位%+.2f%%，'
             % (tone, sh_pct, up * 100, med))
    if top_up is not None and bot_up is not None:
        L.append('最大20%%市值组涨占比%.0f%%、最小20%%仅%.0f%%，说明上涨靠权重托底而非普涨。'
                 % (top_up * 100, bot_up * 100))
    if n_strong:
        names = '、'.join(r['name'] for r in res['strong'][:3])
        L.append('强势侧%s等%d个行业四项达标，资金与广度同时确认。' % (names, n_strong))
    else:
        if near:
            b = near[0]
            L.append('按涨幅前30%%+2pp超额的门槛，无行业合格，最接近的%s也差%.2fpp'
                     % (b['name'], max(0.0, 2.0 - b['excess'])))
            q = (res.get('quality') or {}).get(b['name']) or {}
            if q.get('seal') is not None and q['seal'] < 60:
                L.append('且封板率仅%.0f%%' % q['seal'])
            if b['mf20'] < 0:
                L.append('、20日资金%.0f亿' % b['mf20'])
            L.append('，本质是超跌反弹而非趋势启动。')
        else:
            L.append('按涨幅前30%%+2pp超额的门槛，无任何行业合格。')
    out = ind.nsmallest(1, 'mf5')
    if len(out):
        o = out.iloc[0]
        L.append('资金主线是流出而非流入：%s 5日净流出%.0f亿。'
                 % (o['name'], abs(o['mf5'])))
    dual = ind[(ind.mf1 > 0) & (ind.mf5 > 0)]
    L.append('全市场仅%d个行业当日+5日净流入双正' % len(dual))
    if len(dual) and dual['pct'].max() < 0.3:
        L.append('，且最高涨幅不足0.3%%，缺乏承接。')
    else:
        L.append('。')
    fake = [r for r in (res.get('weak') or []) if '伪强' in (r.get('tag') or '')]
    if fake:
        L.append('%s等上涨属护盘伪强（低换手+5日净流出），资金是避险不是进攻。'
                 % '、'.join(r['name'] for r in fake[:2]))
    L.append('当前属防御性存量博弈，无可以视为趋势的行业。')
    # 按句累加，硬控 200 字，保证句子完整（不切半句）
    s = ''
    for seg in L:
        if len(s + seg) > 200:
            break
        s += seg
    if not s:
        s = L[0][:200]
    return s


def trade_dates(end, n):
    """最近 n 个交易日（含 end），用上证指数日线"""
    df = _cached('idx_sh_%s_%d.pkl' % (end, n), lambda: call(
        'index_daily', ts_code='000001.SH',
        start_date=(datetime.datetime.strptime(end, '%Y%m%d') - datetime.timedelta(days=n * 2 + 20)).strftime('%Y%m%d'),
        end_date=end), ttl_hours=6)
    if df is None:
        return [end]
    df = df.sort_values('trade_date')
    ds = [d for d in df['trade_date'].tolist() if d <= end]
    return ds[-n:]


def membership():
    """ts_code -> 申万一级名称 (+ 股票简称)"""
    p = os.path.join(CACHE, 'sw_membership.pkl')
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) / 86400 < 30:
        df = pd.read_pickle(p)
    else:
        l1 = call('index_classify', level='L1', src='SW2021')
        rows = []
        for _, r in l1.iterrows():
            m = call('index_member_all', l1_code=r['index_code'], retries=2)
            if m is None or m.empty:
                continue
            m = m[m['is_new'] == 'Y'][['ts_code', 'l1_name', 'l2_name']]
            rows.append(m)
        df = pd.concat(rows, ignore_index=True).drop_duplicates('ts_code')
        df.to_pickle(p)
    # 补股票简称
    if 'stock_name' not in df.columns:
        p2 = os.path.join(CACHE, 'stock_basic.pkl')
        if os.path.exists(p2) and (time.time() - os.path.getmtime(p2)) / 86400 < 7:
            sb = pd.read_pickle(p2)
        else:
            sb = call('stock_basic', exchange='', list_status='L', fields='ts_code,name')
            if sb is not None and not sb.empty:
                sb.to_pickle(p2)
        if sb is not None and not sb.empty:
            df = df.merge(sb.rename(columns={'name': 'stock_name'}), on='ts_code', how='left')
            df.to_pickle(p)
    return df


def field(dates, api, tag, cols=None, parallel=1):
    """按日拉取并缓存，返回合并 DataFrame（parallel>1 时并行抓缺失日）"""
    out, miss = [], []
    for d in dates:
        p = os.path.join(CACHE, '%s_%s.pkl' % (tag, d))
        if os.path.exists(p) and (time.time() - os.path.getmtime(p)) / 86400 < 5:
            out.append(pd.read_pickle(p))
        else:
            miss.append(d)
    if miss:
        def one(d):
            df = call(api, trade_date=d, retries=3)
            if df is not None and not df.empty:
                df.to_pickle(os.path.join(CACHE, '%s_%s.pkl' % (tag, d)))
            return df
        if parallel > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(parallel) as ex:
                got = list(ex.map(one, miss))
        else:
            got = [one(d) for d in miss]
        out += [g for g in got if g is not None and not g.empty]
    if not out:
        return pd.DataFrame()
    df = pd.concat(out, ignore_index=True)
    if cols:
        df = df[[c for c in cols if c in df.columns]]
    return df


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    today = a.date or datetime.date.today().strftime('%Y%m%d')
    d20 = trade_dates(today, 20)
    d5 = d20[-5:]
    print('[dates] today=%s  20日窗口 %s~%s (%d 交易日)' % (today, d20[0], d20[-1], len(d20)), file=sys.stderr)

    # ---- 大盘环境
    sh = call('index_daily', ts_code='000001.SH', start_date=d20[0], end_date=today).sort_values('trade_date')
    sz = call('index_daily', ts_code='399001.SZ', start_date=d20[0], end_date=today).sort_values('trade_date')
    # 全市场成交额必须用「深证综指 399106.SZ」（含全深市）；399001 只是 500 只成分股，会把深市严重低估
    szall = call('index_daily', ts_code='399106.SZ', start_date=d20[0], end_date=today)
    if szall is not None and not szall.empty:
        szall = szall.sort_values('trade_date')
    mkt = {}
    if sh is not None and not sh.empty:
        sh_t = sh.iloc[-1]
        sh_prev = sh.iloc[:-1]
        amt_sh = sh_t['amount']
        if szall is not None and not szall.empty:
            amt_sz = szall.iloc[-1]['amount']
            hist = [(r['amount'] + szall[szall.trade_date == r['trade_date']]['amount'].sum()) / 1e5
                    for _, r in sh_prev.tail(4).iterrows()]
        else:
            amt_sz = sz.iloc[-1]['amount'] if sz is not None and not sz.empty else 0
            hist = [(r['amount'] + (sz[sz.trade_date == r['trade_date']]['amount'].iloc[0]
                                    if sz is not None and not sz.empty and (sz.trade_date == r['trade_date']).any() else 0)) / 1e5
                    for _, r in sh_prev.tail(4).iterrows()]
        tot = (amt_sh + amt_sz) / 1e5  # 千元 -> 亿元
        mkt.update(dict(sh_pct=float(sh_t['pct_chg']), sh_close=float(sh_t['close']),
                        sz_pct=float(sz.iloc[-1]['pct_chg']) if sz is not None and not sz.empty else None,
                        amt_total=float(tot), amt_prev5_avg=float(sum(hist) / len(hist)) if hist else None,
                        amt_ratio=float(tot / (sum(hist) / len(hist))) if hist else None,
                        sh_amt=float(amt_sh / 1e5), sh_amt_prev=float(sh_prev.tail(4)['amount'].mean() / 1e5)))

    # ---- 申万一级日线（20 日）
    sw = field(d20, 'sw_daily', 'sw_daily')
    l1 = call('index_classify', level='L1', src='SW2021')[['index_code', 'industry_name']]
    l1_set = set(l1['index_code'])
    sw = sw[sw['ts_code'].isin(l1_set)].copy()
    sw_t = sw[sw.trade_date == today]
    if sw_t.empty:
        print('[ERR] 今日 sw_daily 无数据', file=sys.stderr); return 1

    # ---- 个股（今日）
    daily = field([today], 'daily', 'daily')
    db = field([today], 'daily_basic', 'daily_basic')
    lim = field([today], 'limit_list_d', 'limit_list_d')
    mem = membership()

    daily = daily.merge(mem, on='ts_code', how='left')
    db = db.merge(mem, on='ts_code', how='left')
    daily['name'] = daily['stock_name'].fillna(daily['ts_code'])
    daily = daily.merge(db[['ts_code', 'turnover_rate', 'circ_mv', 'total_mv', 'free_share']],
                        on='ts_code', how='left')
    daily['free_mv'] = daily['free_share'].fillna(daily['circ_mv'] / daily['close'] / 1e4) * daily['close'] * 1e4  # 元
    daily = daily[daily['l1_name'].notna()]

    # ---- 主力净流入（东财口径 moneyflow_dc；20 日累计，单位：万元 -> 亿）
    mf_all = field(d20, 'moneyflow_dc', 'moneyflow_dc',
                   cols=['trade_date', 'ts_code', 'net_amount', 'buy_elg_amount', 'buy_lg_amount'], parallel=5)
    mf_all = mf_all.merge(mem, on='ts_code', how='left')
    mf_all = mf_all[mf_all['l1_name'].notna()]
    mf_today = mf_all[mf_all.trade_date == today].groupby('l1_name')['net_amount'].sum() / 1e4
    mf_5 = mf_all[mf_all.trade_date.isin(d5)].groupby('l1_name')['net_amount'].sum() / 1e4
    mf_20 = mf_all[mf_all.trade_date.isin(d20)].groupby('l1_name')['net_amount'].sum() / 1e4

    # ---- 上证 pct 序列（连续强势天数用）
    sh_map = dict(zip(sh['trade_date'], sh['pct_chg'])) if sh is not None else {}
    sw_piv = sw.pivot_table(index='trade_date', columns='ts_code', values='pct_change')
    sw_name = dict(zip(sw_t['ts_code'], sw_t['name']))

    # ---- 涨跌家数 / 中位涨幅 / 龙头
    rows = []
    lim_mem = lim.merge(mem, on='ts_code', how='left') if lim is not None and not lim.empty else pd.DataFrame()
    for _, r in sw_t.iterrows():
        code, name = r['ts_code'], r['name']
        g = daily[daily.l1_name == name]
        n = len(g)
        up = int((g['pct_chg'] > 0).sum())
        med = float(g['pct_chg'].median()) if n else 0.0
        amt = float(g['amount'].sum() / 1e5)  # 千元->亿元
        fsum = float(g['free_mv'].sum())
        turn = float(g['amount'].sum() * 1000 / fsum * 100) if fsum > 0 else 0.0
        lead = g.sort_values('total_mv', ascending=False).head(1)
        lead_name = lead['name'].iloc[0] if len(lead) else '-'
        lead_pct = float(lead['pct_chg'].iloc[0]) if len(lead) else 0.0
        lead_mv = float(lead['total_mv'].iloc[0] / 1e4) if len(lead) else 0.0  # 亿
        top = g.sort_values('pct_chg', ascending=False).head(1)
        top_name = top['name'].iloc[0] if len(top) else '-'
        top_pct = float(top['pct_chg'].iloc[0]) if len(top) else 0.0
        # 连续跑赢上证天数
        streak = 0
        col = sw_piv[code] if code in sw_piv.columns else pd.Series(dtype=float)
        for d in reversed(d20):
            if d not in col.index or pd.isna(col.get(d)) or d not in sh_map:
                break
            if col.get(d) > sh_map[d]:
                streak += 1
            else:
                break
        # 涨停/炸板
        zt = zb = dt = 0
        fd_amt = 0.0
        open_times = None
        if not lim_mem.empty:
            lg = lim_mem[lim_mem.l1_name == name]
            zt = int((lg['limit'] == 'U').sum()); zb = int((lg['limit'] == 'Z').sum()); dt = int((lg['limit'] == 'D').sum())
            u = lg[lg['limit'] == 'U']
            if not u.empty:
                big = u.sort_values('limit_amount', ascending=False).head(1)
                fd_amt = float(big['limit_amount'].iloc[0] / 1e8) if pd.notna(big['limit_amount'].iloc[0]) else 0.0
                open_times = int(big['open_times'].iloc[0]) if pd.notna(big['open_times'].iloc[0]) else None
        rows.append(dict(
            name=name, code=code, pct=float(r['pct_change']), amt=amt, turn=turn,
            mf1=float(mf_today.get(name, 0)), mf5=float(mf_5.get(name, 0)), mf20=float(mf_20.get(name, 0)),
            n=n, up=up, up_ratio=up / n if n else 0, med=med,
            lead=lead_name, lead_pct=lead_pct, lead_mv=lead_mv, top=top_name, top_pct=top_pct,
            streak=streak, zt=zt, zb=zb, dt=dt, fd=fd_amt, open_times=open_times,
            fcap=float(r['float_mv'] / 1e4),  # 万元->亿元
        ))
    ind = pd.DataFrame(rows)
    ind['excess'] = ind['pct'] - mkt.get('sh_pct', 0)
    ind['mf5_int'] = ind['mf5'] / ind['fcap'] * 100          # 5日净流入 / 流通市值 %
    ind['mf20_int'] = ind['mf20'] / ind['fcap'] * 100
    ind = ind.sort_values('pct', ascending=False).reset_index(drop=True)
    ind['rk'] = ind.index + 1

    # ---- 市场广度 & 市值分档（定性用）
    mkt['up_ratio'] = float((daily['pct_chg'] > 0).mean())
    mkt['med_pct'] = float(daily['pct_chg'].median())
    mkt['n_total'] = int(len(daily))
    mkt['n_up'] = int((daily['pct_chg'] > 0).sum())
    mkt['zt'] = int((lim['limit'] == 'U').sum()) if lim is not None and not lim.empty else 0
    mkt['zb'] = int((lim['limit'] == 'Z').sum()) if lim is not None and not lim.empty else 0
    mkt['dt'] = int((lim['limit'] == 'D').sum()) if lim is not None and not lim.empty else 0
    mkt['seal_rate'] = (mkt['zt'] / (mkt['zt'] + mkt['zb']) * 100) if (mkt['zt'] + mkt['zb']) else None
    mv = daily.dropna(subset=['total_mv'])
    if len(mv) > 50:
        q = mv['total_mv'].quantile([0.2, 0.8])
        mkt['cap_top_up'] = float((mv[mv.total_mv >= q.loc[0.8]]['pct_chg'] > 0).mean())
        mkt['cap_bot_up'] = float((mv[mv.total_mv <= q.loc[0.2]]['pct_chg'] > 0).mean())
        mkt['cap_top_med'] = float(mv[mv.total_mv >= q.loc[0.8]]['pct_chg'].median())
        mkt['cap_bot_med'] = float(mv[mv.total_mv <= q.loc[0.2]]['pct_chg'].median())

    res = dict(date=today, market=mkt, ind=ind.to_dict('records'))

    # ---- 第三步 强势筛选
    top30 = ind.head(max(3, int(round(len(ind) * 0.30)))).copy()
    cand = top30[(top30.excess >= 2.0) & (top30.mf1 > 0) & (top30.mf5 > 0) &
                 (top30.up_ratio >= 0.60) & (top30.turn > 1.0)].copy()
    # 权重护盘型剔除：龙头涨幅高于行业中位 + 显著高于行业整体 → 判断为拉指数
    def is_weight_led(r):
        return (r['name'] in WEIGHT_IND) and (r['lead_pct'] - r['med'] > 1.0) and (r['lead_pct'] > r['pct'])
    cand['weight_led'] = cand.apply(is_weight_led, axis=1)
    kicked = cand[cand.weight_led]
    cand = cand[~cand.weight_led].copy()
    if len(cand):
        cand['s_pct'] = cand['pct'].rank(pct=True) * 100
        cand['s_int'] = cand['mf5_int'].rank(pct=True) * 100
        cand['score'] = 0.3 * cand['s_pct'] + 0.7 * cand['s_int']
        cand = cand.sort_values('score', ascending=False)
    res['strong'] = cand.to_dict('records')
    res['kicked'] = kicked.to_dict('records')

    # ---- 候补：前 30% 里按同一公式打分（含未达标原因），即使 0 入选也能给出"最接近的"
    def _reasons(r):
        out = []
        if r['excess'] < 2.0:
            out.append('超额%+.2fpp（距2pp差%.2f）' % (r['excess'], 2.0 - r['excess']))
        if r['mf1'] <= 0:
            out.append('当日净流出%.1f亿' % r['mf1'])
        if r['mf5'] <= 0:
            out.append('5日净流出%.1f亿' % abs(r['mf5']))
        if r['up_ratio'] < 0.60:
            out.append('上涨占比%.0f%%<60%%' % (r['up_ratio'] * 100))
        if r['turn'] <= 1.0:
            out.append('换手%.2f%%<1%%' % r['turn'])
        return out

    pool = top30.copy()
    if len(pool):
        pool['s_pct'] = pool['pct'].rank(pct=True) * 100
        pool['s_int'] = pool['mf5_int'].rank(pct=True) * 100
        pool['score'] = 0.3 * pool['s_pct'] + 0.7 * pool['s_int']
        pool['reasons'] = pool.apply(_reasons, axis=1)
        pool['n_fail'] = pool['reasons'].apply(len)
        near = pool[pool['n_fail'] <= 2].sort_values(['n_fail', 'score'], ascending=[True, False]).head(3)
        res['strong_near'] = near.to_dict('records')
    else:
        res['strong_near'] = []

    # ---- 第四步 承压筛选
    bot20 = ind.tail(max(2, int(round(len(ind) * 0.20)))).copy()
    out1 = bot20[(bot20.mf1 < 0) | (bot20.mf5 < 0)]
    fake = ind[(ind.pct > 0) & (ind.mf5 < 0)]                     # 上涨但5日净流出
    guard = ind[(ind.name.isin(WEIGHT_IND)) & (ind.mf20 < 0)]       # 权重护盘但20日净流出
    weak = pd.concat([out1, fake, guard]).drop_duplicates('name')
    def tag(r):
        if r['pct'] > 0 and r['mf5'] < 0:
            return '护盘伪强(诱多)'
        if r['name'] in WEIGHT_IND and r['mf20'] < 0:
            return '护盘伪强(权重)'
        return '真下跌'
    weak = weak.copy(); weak['tag'] = weak.apply(tag, axis=1)
    # 真下跌取跌幅最深的 3 个；护盘伪强最多 2 个（两类都要出现在对照表里）
    real = weak[weak.tag == '真下跌'].sort_values('pct').head(3)
    pseudo = weak[weak.tag != '真下跌'].sort_values('pct', ascending=False).head(2)
    weak = pd.concat([real, pseudo])
    res['weak'] = weak.to_dict('records')

    # ---- 质量校验（封板率/炸板率/龙头封单），对强势入选 + 候补逐行业算
    fs = {}
    for r in (res['strong'] + res['strong_near']):
        n = r['name']
        touch = r['zt'] + r['zb']
        fd, fd_name, fd_open = None, None, None
        if not lim_mem.empty:
            lg = lim_mem[(lim_mem.l1_name == n) & (lim_mem['limit'] == 'U')]
            if len(lg):
                b = lg.sort_values('fd_amount', ascending=False).iloc[0]
                fd = float(b['fd_amount'] / 1e8) if pd.notna(b['fd_amount']) else None
                fd_name = b['name']
                fd_open = int(b['open_times']) if pd.notna(b['open_times']) else None
        fs[n] = dict(zt=r['zt'], zb=r['zb'], touch=touch,
                     seal=(r['zt'] / touch * 100) if touch else None,
                     burst=(r['zb'] / touch * 100) if touch else None,
                     fd=fd, fd_name=fd_name, fd_open=fd_open,
                     lead=r['lead'], lead_pct=r['lead_pct'])
    res['quality'] = fs

    # ---- 复盘总结（200 字内，数据驱动，可复现）
    res['summary'] = build_summary(res, mkt, ind)

    out = json.dumps(res, ensure_ascii=False, default=str)
    open(os.path.join(CACHE, 'layer_%s.json' % today), 'w').write(out)
    if a.json:
        print(out); return 0

    # ---------------- 文本输出
    print('=' * 78)
    print('沪指 %.2f%% 收 %.2f ｜ 深成 %.2f%% ｜ 两市成交 %.0f亿（前4日均 %.0f亿，%.0f%%）'
          % (mkt['sh_pct'], mkt['sh_close'], mkt.get('sz_pct') or 0, mkt['amt_total'],
             mkt['amt_prev5_avg'], mkt['amt_ratio'] * 100))
    print('窗 %s ~ %s' % (d20[0], d20[-1]))
    print('-' * 78)
    pd.set_option('display.width', 250); pd.set_option('display.max_rows', 60)
    show = ind[['rk', 'name', 'pct', 'excess', 'mf1', 'mf5', 'mf20', 'up', 'n', 'up_ratio', 'turn',
                'lead', 'lead_pct', 'top', 'top_pct', 'streak', 'zt', 'zb', 'dt']]
    print(show.round(2).to_string(index=False))
    print('-' * 78)
    print('【强势入选】')
    for r in res['strong']:
        print('  #%d %-6s 涨%.2f%% 超%.2fpp 5日净流入%.1f亿(强度%.2f%%) 上涨%d/%d 换手%.2f%% 连续%d日 分%.1f'
              % (r['rk'], r['name'], r['pct'], r['excess'], r['mf5'], r['mf5_int'],
                 r['up'], r['n'], r['turn'], r['streak'], r['score']))
    if res['kicked']:
        print('  [剔除权重护盘]', ', '.join('%s(涨%.2f 龙头%s %.2f)' % (k['name'], k['pct'], k['lead'], k['lead_pct']) for k in res['kicked']))
    print('【承压】')
    for r in res['weak']:
        print('  %-6s 涨%.2f%% 当日%.1f亿 5日%.1f亿 20日%.1f亿 [%s]'
              % (r['name'], r['pct'], r['mf1'], r['mf5'], r['mf20'], r['tag']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
