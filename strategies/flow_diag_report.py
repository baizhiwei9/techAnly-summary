#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行业资金 + 产业链传导 合并诊断报告

两条流水线合并成一份报告：
  A. 大成交簇资金诊断（吸筹/派发/换筹/无法判断 + 长钱短钱）
  B. 产业链传导监控（自动找发动机 → β/跟涨跟跌比 → 🟢🔴⚪🟡 判定 → 验证日历）

用法：
  python3 strategies/flow_diag_report.py                 # 打印
  python3 strategies/flow_diag_report.py --push feishu   # 推飞书卡片
  python3 strategies/flow_diag_report.py --chain AI算力链 新能源车链
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import json, os, sys, statistics, math, argparse, datetime

WS = os.path.expanduser("~/.openclaw/workspace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ⚠️ 2026-11-09：本文件是 flow_diag_lib.py 的早期副本，数据层已落后（会有
#   龙虎榜/席位超时→FileNotFoundError、CODE_MAP KeyError、ETF份额残缺缓存三个坑）。
#   数据层统一委托给 flow_diag_lib，避免同样的 bug 在这里再犯一次。
import flow_diag_lib as _L

import socket as _socket
_orig_gai = _socket.getaddrinfo
_socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == _socket.AF_INET]


def _resolve_date(d=None):
    """默认取最近交易日（非交易日自动回退），并确保 sector_rotation 缓存存在"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import trade_day
        if d:
            r, _ok, _n = trade_day.resolve(d.replace('-', ''))
            return r.replace('-', '')
        return trade_day.resolve().replace('-', '')
    except Exception:
        return (d or datetime.datetime.now().strftime('%Y%m%d')).replace('-', '')


DATE = _resolve_date(os.environ.get('FLOW_DIAG_DATE'))
D8 = DATE
CACHE = os.path.join(WS, '.cache/fund_diag')
SR = os.path.join(WS, '.cache/sector_rotation', f'{DATE[:4]}-{DATE[4:6]}-{DATE[6:]}')
HIST = os.path.join(SR, 'hist')


def ensure_sector_cache():
    """若当日 sector_rotation 缓存缺失，自动跑一次抓取（akshare，约 90 秒）"""
    need = ['summary.json', 'metrics.json']
    if all(os.path.exists(os.path.join(SR, f)) for f in need) and os.path.isdir(HIST):
        n = len([f for f in os.listdir(HIST) if f.endswith('.json')])
        if n >= 80:
            return True
    import subprocess
    dd = f'{DATE[:4]}-{DATE[4:6]}-{DATE[6:]}'
    print(f'[init] 缺少 {dd} 板块缓存，正在抓取…', file=sys.stderr)
    r = subprocess.run([sys.executable, os.path.join(WS, 'scripts/sector_rotation/fetch_sector_data.py'), dd],
                       capture_output=True, text=True, timeout=1800)
    print((r.stdout or '')[-800:], file=sys.stderr)
    rb = os.path.join(WS, 'scripts/sector_rotation/build_report.py')
    if os.path.exists(rb):
        subprocess.run([sys.executable, rb, dd], capture_output=True, text=True, timeout=1200)
    return all(os.path.exists(os.path.join(SR, f)) for f in need)
os.makedirs(CACHE, exist_ok=True)

# 同花顺一级行业代码（.TI）
CODE_MAP = {'半导体': '881121.TI', '通信设备': '881129.TI', '元件': '881270.TI', '电池': '881281.TI',
            '通用设备': '881117.TI', '汽车零部件': '881126.TI', '文化传媒': '881164.TI',
            'IT服务': '881271.TI', '消费电子': '881124.TI', '化学制药': '881140.TI'}

CHAINS = {
    'AI算力链': ['半导体', '元件', '光学光电子', '通信设备', '消费电子', '计算机设备', '软件开发', 'IT服务'],
    '新能源车链': ['电池', '能源金属', '汽车零部件', '汽车整车', '电机', '电网设备'],
    '大消费链': ['白酒', '食品加工制造', '饮料制造', '白色家电', '小家电', '美容护理'],
    '医药链': ['化学制药', '生物制品', '医疗器械', '医疗服务', '中药', '医药商业'],
    '地产链': ['房地产', '建筑材料', '建筑装饰', '钢铁', '家居用品', '厨卫电器'],
    '猪产业': ['养殖业', '农产品加工', '种植业与林业', '食品加工制造', '化学制品'],
}

CFG = dict(min_move=2.0, lookback=60, engine_days=20, beta_win=60, amp_ratio=1.5, share_rise=30.0,
           pos_lo=35.0, pos_hi=65.0, follow_edge=0.5, rel_gap=0.20, ex_gap=1.0, elast=0.60,
           beta_low=0.6, drop_share=-10.0)


# ---------------- 数据层 ----------------
def _pro():
    import tushare_init as ti
    return ti.get_pro()


def _retry(fn, n=3, w=3, tag=''):
    import time
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            print(f'    [{tag}] fail#{i}: {repr(e)[:80]}', file=sys.stderr)
            time.sleep(w)
    return None


def ensure_market():
    """⚠️ 2026-11-09：委托 flow_diag_lib（本文件的旧实现会让龙虎榜超时把整份报告打挂）"""
    return _L.ensure_market()


def ensure_members(boards):
    """⚠️ 2026-11-09：委托 flow_diag_lib（旧实现 CODE_MAP 只有 10 个行业 → KeyError）"""
    return _L.ensure_members(boards)


def ensure_margin():
    """⚠️ 2026-11-09：委托 flow_diag_lib（直连优先）"""
    return _L.ensure_margin()


def ensure_fund_share():
    """⚠️ 2026-11-09：委托 flow_diag_lib（含残缺缓存重取）"""
    return _L.ensure_fund_share()


def load(board):
    p = os.path.join(HIST, board + '.json')
    h = sorted(json.load(open(p)), key=lambda r: r['日期'])
    return dict(close=[float(r['收盘价']) for r in h], high=[float(r['最高价']) for r in h],
                low=[float(r['最低价']) for r in h], amt=[float(r['成交额']) for r in h])


def rets(s):
    c = s['close']
    return [(c[i] / c[i - 1] - 1) * 100 for i in range(1, len(c))]


def cum(s, n):
    c = s['close']; n = min(n, len(c) - 1)
    return (c[-1] / c[-n - 1] - 1) * 100


def pos(s, n=60):
    c = s['close'][-n:]; lo, hi = min(c), max(c)
    return (s['close'][-1] - lo) / (hi - lo) * 100 if hi > lo else 50.0


def amp5(s):
    return statistics.mean([(s['high'][i] - s['low'][i]) / s['close'][i - 1] * 100 for i in range(-5, 0)])


def beta_corr(ra, rb):
    n = min(len(ra), len(rb)); a, b = ra[-n:], rb[-n:]
    mb, ma = statistics.mean(b), statistics.mean(a)
    cov = sum((b[i] - mb) * (a[i] - ma) for i in range(n))
    vb = sum((b[i] - mb) ** 2 for i in range(n))
    va = sum((a[i] - ma) ** 2 for i in range(n))
    if vb == 0 or va == 0:
        return 0.0, 0.0
    return cov / vb, cov / math.sqrt(va * vb)


def follow(ra, rb, thr):
    """三重跟动指标
    fu/fd : 同向比（只看符号）
    su/sd : 强度比（跟涨需 ≥ 发动机涨幅×0.5；跟跌需 ≤ 发动机跌幅×0.5）
    """
    n = min(len(ra), len(rb)); a, b = ra[-n:], rb[-n:]
    up = [i for i in range(n) if b[i] > thr]
    dn = [i for i in range(n) if b[i] < -thr]
    fu = sum(1 for i in up if a[i] > 0) / len(up) if up else None
    fd = sum(1 for i in dn if a[i] < 0) / len(dn) if dn else None
    su = sum(1 for i in up if a[i] > 0 and a[i] >= b[i] * 0.5) / len(up) if up else None
    sd = sum(1 for i in dn if a[i] < 0 and a[i] <= b[i] * 0.5) / len(dn) if dn else None
    # 跟涨超额：发动机大涨日环节相对发动机的平均收益差(pp)
    ex_u = statistics.mean([a[i] - b[i] for i in up]) if up else None
    ex_d = statistics.mean([a[i] - b[i] for i in dn]) if dn else None
    # 跟随弹性 = 环节在发动机大动日的平均涨跌幅 ÷ 发动机平均涨跌幅
    eu = (statistics.mean([a[i] for i in up]) / statistics.mean([b[i] for i in up])) if up else None
    ed = (statistics.mean([a[i] for i in dn]) / statistics.mean([b[i] for i in dn])) if dn else None
    return fu, fd, su, sd, eu, ed, len(up), len(dn)


# ---------------- A. 大成交簇 ----------------
def stage_a(topn=10):
    import pandas as pd
    m = json.load(open(os.path.join(SR, 'metrics.json')))
    met, ds = m['meta'], m['dates']
    cur = ds[-1]
    summ = {r['板块']: r for r in json.load(open(os.path.join(SR, 'summary.json')))}

    def tot(d):
        return sum(v['amt'] for v in met[d]['m'].values())

    t_today = tot(cur)
    rows = sorted(met[cur]['m'].items(), key=lambda kv: -kv[1]['amt'])[:topn]
    names = [b for b, _ in rows]

    files = ensure_market()
    mem = ensure_members(names)
    daily = pd.read_json(files['daily']).merge(pd.read_json(files['basic']), on='ts_code', how='left')
    daily['amt_yi'] = daily.amount / 1e5
    daily['mv'] = daily.circ_mv / 1e4
    tl = pd.read_json(files['toplist'])
    ti = pd.read_json(files['topinst'])
    mg = ensure_margin()
    fsh = ensure_fund_share()

    KW = {'半导体': ['半导体', '芯片'], '通信设备': ['通信', '5G', '光通信'], '元件': ['电子', 'PCB'],
          '电池': ['电池', '新能源车', '锂电'], '通用设备': ['装备', '机械', '机器人'], '汽车零部件': ['汽车'],
          '文化传媒': ['传媒', '游戏', '影视'], 'IT服务': ['信息技术', '计算机', '软件', '云计算', '大数据'],
          '消费电子': ['消费电子'], '化学制药': ['创新药', '医药', '生物医药', '医疗'],
          '光学光电子': ['光电', '面板', 'LED'], '计算机设备': ['计算机'], '软件开发': ['软件'],
          '能源金属': ['锂', '钴', '稀有'], '汽车整车': ['汽车'], '电机': ['电机', '电气'],
          '电网设备': ['电网', '电力设备'], '白酒': ['白酒', '酒'], '食品加工制造': ['食品'],
          '饮料制造': ['饮料', '乳'], '白色家电': ['家电'], '小家电': ['家电'], '美容护理': ['美容', '化妆品'],
          '生物制品': ['生物', '疫苗'], '医疗器械': ['医疗器械', '医疗'], '医疗服务': ['医疗'],
          '中药': ['中药'], '医药商业': ['医药'], '房地产': ['地产', '房地产'], '建筑材料': ['建材'],
          '建筑装饰': ['建筑'], '钢铁': ['钢铁'], '家居用品': ['家居'], '厨卫电器': ['家电']}

    out = []
    for b in names:
        v = met[cur]['m'][b]
        d5 = ds[-6]
        share = v['amt'] / t_today * 100
        share5 = met[d5]['m'][b]['amt'] / tot(d5) * 100
        hist20 = [met[d]['m'][b]['amt'] for d in ds[-21:-1]]
        r20 = v['amt'] / statistics.mean(hist20)
        s = load(b)
        row = dict(board=b, amt=v['amt'], share=share, d_share=share - share5, amt_ratio=r20,
                   pct1d=v['pct1d'], pct5d=v['pct5d'], pos=pos(s), mem_n=len(mem.get(b, [])),
                   netmain=float(summ[b].get('净流入', 0) or 0))
        # 量价健康度
        c, am = s['close'], s['amt']
        last5 = [((c[-i] / c[-i - 1] - 1) * 100, am[-i] / statistics.mean(am[-21:-1])) for i in range(1, 6)]
        up = [a for p, a in last5 if p > 0]; dn = [a for p, a in last5 if p <= 0]
        row['vp'] = '健康' if (up and dn and statistics.mean(up) > statistics.mean(dn)) else '背离'
        # 个股结构
        codes = set(mem.get(b, []))
        g = daily[daily.ts_code.isin(codes)]
        if len(g):
            row['up'] = int((g.pct_chg > 0).sum()); row['dn'] = int((g.pct_chg < 0).sum())
            row['zt'] = int((g.pct_chg >= 9.8).sum())
            row['turn'] = float((g.turnover_rate * g.amt_yi).sum() / g.amt_yi.sum())
            lead = g.nlargest(1, 'pct_chg').iloc[0]
            row['lead_mv'] = float(lead.mv); row['lead_pct'] = float(lead.pct_chg)
            mid = g.nlargest(20, 'amt_yi').nlargest(3, 'mv')
            row['mid'] = [(r.ts_code[:6], float(r.mv), float(r.pct_chg)) for r in mid.itertuples()]
        # 长钱：ETF 份额
        etf = None
        if fsh is not None and b in KW:
            sub = fsh[fsh.name.str.contains('|'.join(KW[b]), na=False)]
            if len(sub):
                etf = float(sub.d.sum() / 1e4)
        row['etf'] = etf
        # 融资
        if mg is not None and len(codes):
            sub = mg[mg.ts_code.isin(codes)].copy()
            sub['net'] = sub.rzmre - sub.rzche
            row['margin'] = float(sub.net.sum() / 1e8)
            row['marg_bal'] = float(sub.rzye.sum() / 1e8)
        else:
            row['margin'] = None
            row['marg_bal'] = 1.0
        # 龙虎榜/机构游资
        if len(tl) and len(codes):
            t = tl[tl.ts_code.isin(codes)]
            row['lb_n'] = len(t); row['lb_net'] = float(t.net_amount.sum() / 1e8)
        else:
            row['lb_n'] = 0; row['lb_net'] = 0.0
        if len(ti) and len(codes):
            tt = ti[ti.ts_code.isin(codes)].copy()
            tt['inst'] = tt.exalter.str.contains('机构专用')
            row['inst'] = float(tt[tt.inst].net_buy.sum() / 1e8)
            row['youzi'] = float(tt[~tt.inst].net_buy.sum() / 1e8)
        else:
            row['inst'] = 0.0; row['youzi'] = 0.0
        out.append(row)
    return out


def verdict_a(r):
    """吸筹 / 派发 / 换筹 / 无法判断 + 资金性质

    判定优先级（先派发后吸筹，避免高位被算成吸筹）：
      P1 派发: 放量下跌且跌家数碾压   ar>1.05 且 p<-2 且 dn>2×up
      P2 派发: 高位游资小票换手       pos>=70 且 换手>=10% 且 游资>机构
      P3 派发: 中位放量杀跌           ar>=1.0 且 p<0 且 dn>2×up 且 主力<-10亿
      P4 吸筹: 长钱确认+缩量          ETF净申购>1亿份 且 ar<1.05 且 pos<60
      P5 吸筹: 低位放量+主力流入      p>0 且 ar>1.1 且 主力>0 且 pos<40
      P6 换筹: 低位缩量下跌(洗盘)      ar<0.9 且 pos<40
      P7 换筹: 缩量横盘                ar<1.0 且 |p|<1.5
      否则 无法判断
    """
    p, ar, posp = r['pct1d'], r['amt_ratio'], r['pos']
    up, dn = r.get('up', 0), r.get('dn', 0)
    netm = r.get('netmain', 0)
    etf = r.get('etf') or 0
    mm = r.get('margin') or 0
    inst, yz = r.get('inst', 0), r.get('youzi', 0)
    turn = r.get('turn', 0)

    if ar > 1.05 and p < -2 and dn > up * 2:
        tag = '派发'
    elif posp >= 70 and turn >= 10 and yz > inst:
        tag = '派发'
    elif ar >= 1.0 and p < 0 and dn > up * 2 and netm < -10:
        tag = '派发'
    elif etf > 1.0 and ar < 1.05 and posp < 60:
        tag = '吸筹'
    elif p > 0 and ar > 1.1 and netm > 0 and posp < 40:
        tag = '吸筹'
    elif ar < 0.9 and posp < 40:
        tag = '换筹'
    elif ar < 1.0 and abs(p) < 1.5:
        tag = '换筹'
    else:
        tag = '无法判断'

    long_ok = etf > 1.0 or (mm > 0 and abs(mm) / max(r.get('marg_bal', 1), 1) > 0.005)
    short_ok = yz > 0 and yz > inst + 0.5
    if long_ok and short_ok:
        nature = '混合·长钱占上风'
    elif long_ok:
        nature = '长钱介入'
    elif short_ok:
        nature = '短钱主导'
    else:
        nature = '混合'
    return tag, nature


# ---------------- A2. 行业流动性变化 ----------------
def stage_liquidity(topn=10):
    """行业流动性变化追踪：成交额占比迁移 + 量比 + 自身额比比 + 换手

    liquidity score = 今日额占比 / 20日均额占比  （>1 = 资金相对流入）
    """
    m = json.load(open(os.path.join(SR, 'metrics.json')))
    met, ds = m['meta'], m['dates']
    cur = ds[-1]

    def tot(d):
        return sum(v['amt'] for v in met[d]['m'].values())

    t_today = tot(cur)
    rows = sorted(met[cur]['m'].items(), key=lambda kv: -kv[1]['amt'])[:topn]
    out = []
    for b, v in rows:
        h20 = [met[d]['m'][b]['amt'] for d in ds[-21:-1]]
        sh_now = v['amt'] / t_today * 100
        sh_20 = sum(h20) / sum(tot(d) for d in ds[-21:-1]) * 100
        sh_5 = met[ds[-6]]['m'][b]['amt'] / tot(ds[-6]) * 100
        s = load(b)
        c, am = s['close'], s['amt']
        turn_proxy = am[-1] / statistics.mean(am[-21:-1])
        out.append(dict(board=b, amt=v['amt'], sh_now=sh_now, sh_20=sh_20, sh_5=sh_5,
                        d5=sh_now - sh_5, d20=sh_now - sh_20,
                        liq=sh_now / sh_20 if sh_20 else 1.0,
                        vol_ratio=turn_proxy, pct1d=v['pct1d'],
                        streak=_amt_streak(met, ds, b)))
    return out


def _amt_streak(met, ds, b, n=5):
    """连续放量/缩量天数（相对20日均额）"""
    k = 0
    sign = None
    for i in range(1, n + 1):
        d = ds[-i]
        h = [met[x]['m'][b]['amt'] for x in ds[-i - 20:-i]]
        if not h:
            break
        up = met[d]['m'][b]['amt'] > statistics.mean(h)
        if sign is None:
            sign = up
            k = 1
        elif up == sign:
            k += 1
        else:
            break
    return k if sign else -k


# ---------------- B. 产业链传导 ----------------
def stage_b(chain):
    """产业链传导：双发动机（主涨侧 + 主跌侧）分别作基准

    对每个环节同时算两套跟动：
      vs 主涨侧 → 跟涨比 fu / 强度比 su / 超额 exu  → 不跟涨 = 🔴掉队 or 🟡蓄势
      vs 主跌侧 → 跟跌比 fd / 强度比 sd / 超额 exd  → 不跟跌 = 🟢机会 / 🔴埋雷 / ⚪抱团
    """
    boards = [b for b in CHAINS[chain] if os.path.exists(os.path.join(HIST, b + '.json'))]
    data = {b: load(b) for b in boards}
    r = {b: rets(v) for b, v in data.items()}
    t5 = sum(sum(v['amt'][-5:]) for v in data.values())
    t20 = sum(sum(v['amt'][-20:]) for v in data.values())

    st = {}
    for b, v in data.items():
        sh5 = sum(v['amt'][-5:]) / t5 * 100
        sh20 = sum(v['amt'][-20:]) / t20 * 100
        st[b] = dict(cum=cum(v, CFG['lookback']), cum20=cum(v, 20), cum5=cum(v, 5),
                     cum10=cum(v, 10), amp=amp5(v),
                     amt_ratio=(sum(v['amt'][-5:]) / 5) / (sum(v['amt'][-21:-1]) / 20),
                     share5=sh5, share20=sh20,
                     share_rise=(sh5 - sh20) / sh20 * 100 if sh20 else 0,
                     pos=pos(v), ma20=(v['close'][-1] / statistics.mean(v['close'][-20:]) - 1) * 100,
                     last=rets(v)[-1])

    avg_amp = statistics.mean([s['amp'] for s in st.values()])
    for s in st.values():
        s['emotion'] = s['amp'] > avg_amp * CFG['amp_ratio'] and s['share_rise'] > CFG['share_rise']

    ED = CFG['engine_days']
    key = f'cum{ED}'
    order = sorted(st.items(), key=lambda kv: -kv[1][key])
    n_up = sum(1 for s in st.values() if s[key] > 0)
    regime = '涨' if n_up * 2 > len(st) else '跌'
    up_eng, dn_eng = order[0][0], order[-1][0]

    # 规则2：情绪主导升级（只对当前 regime 的同向侧生效）
    cands = [k for k, s in st.items() if s['emotion']]
    if cands:
        c = max(cands, key=lambda k: abs(st[k]['amp']))
        if regime == '涨' and st[c][key] > 0 and abs(st[c][key]) >= abs(st[up_eng][key]) * 0.6:
            up_eng = c
        elif regime == '跌' and st[c][key] < 0 and abs(st[c][key]) >= abs(st[dn_eng][key]) * 0.6:
            dn_eng = c

    engine = up_eng if regime == '涨' else dn_eng

    for b in data:
        st[b].update(tag={}, beta={}, fu={}, fd={}, su={}, sd={}, exu={}, exd={})

    def _pair(ref, direction):
        rb = r[ref]
        for b in data:
            if b == ref:
                continue
            be, co = beta_corr(r[b][-CFG['beta_win']:], rb[-CFG['beta_win']:])
            fu, fd, su, sd, eu, ed, nu, nd = follow(r[b], rb, CFG['min_move'])
            st[b]['beta'][ref] = be
            if direction == '涨':
                st[b]['fu'][ref], st[b]['su'][ref], st[b]['exu'][ref] = fu, su, eu
            else:
                st[b]['fd'][ref], st[b]['sd'][ref], st[b]['exd'][ref] = fd, sd, ed

    _pair(up_eng, '涨')
    _pair(dn_eng, '跌')

    # ---- 判定：不跟涨（弹性 su 为主，命中率 fu 为辅）----
    for b, s in st.items():
        if b == up_eng:
            s['tag'][up_eng] = '🔧 主涨侧'
            s['why_up'] = f'{ED}日累计第一({s[key]:+.1f}%)'
            continue
        fu = s['fu'].get(up_eng); eu = s['exu'].get(up_eng); be = s['beta'].get(up_eng, 1)
        # 不跟涨：弹性 < ELAST 且 命中率 < 0.85（或弹性极低 <0.35）
        nofollow = (eu is not None) and ((eu < CFG['elast'] and (fu is None or fu < 0.85)) or eu < 0.35)
        if nofollow:
            if s['amt_ratio'] < 1.0 and s['pos'] <= CFG['pos_hi']:
                s['tag'][up_eng] = '🟡 蓄势'
                s['why_up'] = f'弹性{eu:.2f}不跟涨+缩量(额比{s["amt_ratio"]:.2f})+位置{s["pos"]:.0f}%'
            elif be < CFG['beta_low'] and s['share_rise'] < CFG['drop_share']:
                s['tag'][up_eng] = '🔴 掉队'
                s['why_up'] = f'弹性{eu:.2f}+β{be:.2f}低+额占比{s["share_rise"]:+.0f}%'
            else:
                s['tag'][up_eng] = '🔴 掉队'
                s['why_up'] = f'弹性{eu:.2f}不跟涨+放量(额比{s["amt_ratio"]:.2f})'
        else:
            s['tag'][up_eng] = '—'
            s['why_up'] = (f'正常跟涨 弹性{eu:.2f}/命中{fu:.0%}' if eu is not None else '无大波动样本')

    # ---- 判定：不跟跌（弹性 sd 为主）----
    for b, s in st.items():
        if b == dn_eng:
            s['tag'][dn_eng] = '🔧 主跌侧'
            s['why_dn'] = f'{ED}日累计最差({s[key]:+.1f}%)'
            continue
        if b == up_eng:
            # ⚠️ 2026-10-28 同步修复：主涨侧引擎不做「跌侧埋雷」判定
            # （它是全链唯一逆势上涨的一环，「不跟跌」= 强势，不是存量抱团接不住）
            s['tag'][dn_eng] = '🔧 主涨侧(不做跌侧判定)'
            s['why_dn'] = f'主涨侧引擎（{ED}日{s[key]:+.1f}%），不跟跌=强势而非埋雷'
            continue
        fd = s['fd'].get(dn_eng); ed = s['exd'].get(dn_eng)
        band = s.get('dn_band', '')
        nofollow = (ed is not None) and ((ed < CFG['elast'] and (fd is None or fd < 0.85)) or ed < 0.35)
        weak = (ed is not None) and band == '弱跟'
        if nofollow:
            if s['pos'] <= CFG['pos_lo'] and s['amt_ratio'] < 1.15:
                s['tag'][dn_eng] = '🟢 机会'
                s['why_dn'] = f'低位{s["pos"]:.0f}%+不跟跌(弹性{ed:.2f})+缩量'
            elif s['pos'] >= CFG['pos_hi'] and s['amt_ratio'] >= 1.0:
                s['tag'][dn_eng] = '🔴 埋雷'
                s['why_dn'] = f'高位{s["pos"]:.0f}%+放量+不跟跌(弹性{ed:.2f})→补跌风险'
            else:
                s['tag'][dn_eng] = '⚪ 抱团'
                s['why_dn'] = f'中位{s["pos"]:.0f}%+不跟跌(弹性{ed:.2f})存量抱团'
        elif weak:
            if s['pos'] <= CFG['pos_lo'] and s['amt_ratio'] < 1.0:
                s['tag'][dn_eng] = '🟢 机会(弱)'
                s['why_dn'] = f'低位{s["pos"]:.0f}%+弱跟(弹性{ed:.2f},第{s["dn_rank"]}/{s["dn_rank_n"]}抗跌)+缩量'
            elif s['pos'] >= CFG['pos_hi']:
                s['tag'][dn_eng] = '🔴 埋雷(弱)'
                s['why_dn'] = f'高位{s["pos"]:.0f}%+弱跟(弹性{ed:.2f})→补跌风险'
            else:
                s['tag'][dn_eng] = '⚪ 抱团(弱)'
                s['why_dn'] = f'中位{s["pos"]:.0f}%+弱跟(弹性{ed:.2f},第{s["dn_rank"]}/{s["dn_rank_n"]})'
        else:
            s['tag'][dn_eng] = '—'
            s['why_dn'] = (f'正常跟跌 弹性{ed:.2f}/命中{fd:.0%}' if ed is not None else '无大波动样本')

    # ---- 主跌侧跟跌排行（相对强弱）：跌的日子里谁最抗跌、谁跌最凶 ----
    rank = []
    for b, sv in st.items():
        if b == dn_eng:
            continue
        ed = sv['exd'].get(dn_eng)
        if ed is None:
            continue
        rank.append((b, ed, sv))
    rank.sort(key=lambda x: x[1])           # 升序：弹性越小越抗跌，排最前
    eds = [x[1] for x in rank]
    med_ed = statistics.median(eds) if eds else None
    for i, (b, ed, sv) in enumerate(rank):
        sv['dn_rank'] = i + 1
        sv['dn_rank_n'] = len(rank)
        sv['dn_med'] = med_ed
        if ed < 0.35:
            sv['dn_band'] = '明显不跟'
        elif med_ed is not None and ed < med_ed:
            sv['dn_band'] = '弱跟'
        else:
            sv['dn_band'] = '正常/领跌'
    # 传导终点 = 弹性最大（跌得最凶 = 主跌侧情绪的最下游承接者）
    if rank:
        st[rank[-1][0]]['dn_end'] = True

    return dict(chain=chain, engine=engine, eng_dir=regime, up_eng=up_eng, dn_eng=dn_eng,
                dn_rank=[(b, ed) for b, ed, _ in rank], dn_med=med_ed,
                rule=f'{ED}日累计排序 + 情绪升级校验', avg_amp=avg_amp, st=st, eng_days=ED)


STATE = os.path.join(WS, 'memory/flow_diag_state.json')


def diff_signals(Bs, A):
    """与上次运行对比，输出信号变化"""
    cur = {}
    for B in Bs:
        for b, sv in B['st'].items():
            for ref, tg in sv['tag'].items():
                if tg and tg != '—' and not tg.startswith('🔧'):
                    cur[f"{B['chain']}|{b}|{ref}"] = tg
        # 主跌侧排行也进状态，次日比对换防
        for i, (b, ed) in enumerate(B.get('dn_rank') or [], 1):
            cur[f"{B['chain']}|dnrank|{b}"] = f'第{i}抗跌({ed:.2f})'
        cur[f"{B['chain']}|dnmed"] = f"{B.get('dn_med',0):.2f}"
    for r in A:
        cur[f"簇|{r['board']}"] = r['tag_a']
    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE)).get('sig', {})
        except Exception:
            prev = {}
    changes = []
    for k, v in cur.items():
        old = prev.get(k)
        if old and old != v:
            parts = k.split('|')
            if len(parts) >= 3 and parts[1] == 'dnrank':
                changes.append(f"[{parts[0]}跌侧] {parts[2]}：{old} → {v}")
            elif k.endswith('|dnmed'):
                changes.append(f"[{parts[0]}] 跌侧中位弹性：{old} → {v}")
            else:
                changes.append(f"{k.split('|',1)[1]}：{old} → {v}")
    json.dump({'date': DATE, 'sig': cur}, open(STATE, 'w'), ensure_ascii=False, indent=1)
    return changes, len(cur)


def validate_calendar(Bs, A, days, prev_state=None):
    """未来验证日历：T+1 逐条给可量化验证点（含主跌侧传导），T+2/T+3 延续检查"""
    out = []
    out.append(f'▸ {days[0][4:6]}/{days[0][6:]}（T+1）逐条验证')
    for r in A:
        if r['tag_a'] == '派发':
            out.append(f"   派发·{r['board']}：缩量(额比<0.8)或跌家数收敛→减弱；再放量下跌→确认")
        elif r['tag_a'] == '吸筹':
            out.append(f"   吸筹·{r['board']}：中军站稳MA20+额比>1→成立；缩量破20日低→失败")
    for B in Bs:
        ue, de, st = B['up_eng'], B['dn_eng'], B['st']
        out.append(f"   ▸{B['chain']}")
        # 主涨侧验证
        xu = [b for b, s in st.items() if b != ue and '🟡' in s['tag'].get(ue, '')]
        dq = [b for b, s in st.items() if b != ue and '🔴' in s['tag'].get(ue, '')]
        out.append(f"     涨侧·发动机 {ue}：额占比Δ>0 且 跟涨弹性≥0.6 → 主线延续；若弹性<0.4 且额占比转负 → 主线熄火换人")
        if xu:
            out.append(f"     🟡蓄势 ({'、'.join(xu)})：弹性升破0.6→补涨启动；跌破0.35→降🔴掉队")
        if dq:
            out.append(f"     🔴掉队 ({'、'.join(dq)})：需见额占比回升+弹性>0.5 才可翻案")
        # 主跌侧验证（重点）
        rk = B.get('dn_rank') or []
        if rk:
            hold = rk[0][0]
            drop = rk[-1][0]
            out.append(f"     跌侧·发动机 {de}：是否再创新低（决定风险偏好）")
            out.append(f"     跌侧·最抗跌 {hold}(弹性{rk[0][1]:.2f})：若开始补跌(弹性>0.6) → 🟢机会失效，转⚪抱团瓦解")
            out.append(f"     跌侧·最跟跌 {drop}(弹性{rk[-1][1]:.2f})：若弹性回落<0.4 → 杀跌收敛，全链见底信号")
            out.append(f"     跌侧排行迁移：今 抗跌 {hold} → 领跌 {drop}；若排行前2名互换 = 资金换防")
            out.append(f"     传导是否扩散：今日弹性≥0.8 环节={len([1 for _,e in rk if e>=0.8])}/{len(rk)}；次日≥半数为1 → 杀跌向下游扩散")
    for i, d in enumerate(days[1:], 2):
        out.append(f"▸ {d[4:6]}/{d[6:]}（T+{i}）延续检查：双发动机是否换人 / 🟡🟢 是否证伪 / 跌侧排行是否重排 / 派发是否转放量破位")
    out.append('   翻转阈值：跟随弹性 ±0.20 ｜ 额占比 ±3pp ｜ 命中率 ±10pp ｜ 排行变动 ≥2 位为换防')
    return out


def next_trading_days(n=3):
    try:
        import trade_day
        cal = trade_day.cal_days()
        ds = sorted([d for d, ok in cal.items() if ok == 1 and d > DATE])[:n]
        return ds
    except Exception:
        return []


# ---------------- 报告 ----------------
def build_compact(A, Bs, days):
    """QQ 精简版：只留结论"""
    L = [f'📊 资金×传导 {DATE[4:6]}/{DATE[6:]}']
    L.append('【大成交簇】')
    for r in A:
        L.append(f"{r['board']} {r['share']:.1f}% liq{r['amt_ratio']:.2f} {r['tag_a']}/{r['nature']}")
    L.append('【产业链·双发动机】')
    for B in Bs:
        ue, de, st = B['up_eng'], B['dn_eng'], B['st']
        L.append(f"{B['chain']}：涨侧{ue} 跌侧{de}")
        bits = []
        for b, sv in st.items():
            for ref, tg in sv['tag'].items():
                if tg and tg != '—' and not tg.startswith('🔧'):
                    bits.append(f'{b}{tg}')
        L.append('  判定 ' + (' ｜ '.join(bits) if bits else '全部正常跟动'))
        rk = B.get('dn_rank') or []
        if rk and len(rk) >= 2:
            L.append(f"  跌侧传导 {rk[0][0]}(抗跌{rk[0][1]:.2f})→{rk[-1][0]}(跟跌{rk[-1][1]:.2f}) 中位{B['dn_med']:.2f}")
    L.append('【验证】' + days[0][4:6] + '/' + days[0][6:] + ' 看发动机延续+🟡🟢是否证伪')
    return '\n'.join(L)


def build(chain_names=None, topn=10):
    chain_names = chain_names or list(CHAINS.keys())
    A = stage_a(topn)
    for r in A:
        r['tag_a'], r['nature'] = verdict_a(r)
    Bs = [stage_b(c) for c in chain_names]
    days = next_trading_days(3)

    L = []
    L.append(f'📊 行业资金 × 产业链传导 合并诊断 · {DATE[:4]}-{DATE[4:6]}-{DATE[6:]}')
    L.append('')
    L.append('══ 一、大成交簇资金诊断 ══')
    L.append('（占比 / 5日Δpp / 额÷20均额；全社会额÷20均 = 0.85 属缩量环境）')
    L.append('')
    for r in A:
        L.append(f"·{r['board']}：占比{r['share']:.1f}% Δ{r['d_share']:+.1f}pp 额比{r['amt_ratio']:.2f} "
                 f"涨跌{r['pct1d']:+.1f}% 位置{r['pos']:.0f}% 量价{r['vp']} → 【{r['tag_a']}】{r['nature']}")
        det = []
        if 'up' in r:
            det.append(f"涨{r['up']}/跌{r['dn']} 涨停{r['zt']} 换手{r['turn']:.1f}%")
        if r.get('lead_mv'):
            det.append(f"领涨{r['lead_pct']:+.0f}%@{r['lead_mv']:.0f}亿")
        det.append(f"主力{r.get('lb_net',0):+.1f}亿")
        if r.get('inst'):
            det.append(f"机构{r['inst']:+.1f}亿 游资{r['youzi']:+.1f}亿")
        if r.get('margin') is not None:
            det.append(f"融资{r['margin']:+.1f}亿")
        if r.get('etf') is not None:
            det.append(f"ETF{r['etf']:+.2f}亿份")
        L.append('   ' + ' | '.join(det))
        if r.get('mid'):
            L.append('   中军：' + ' '.join(f'{c}({mv:.0f}亿){p:+.1f}%' for c, mv, p in r['mid']))
    L.append('')
    L.append('══ 二、行业流动性变化 ══')
    L.append('（liq = 今日额占比÷20日均额占比，>1 资金相对流入；连放/连缩=相对20日均额）')
    LQ = stage_liquidity(10)
    L.append('板块       额亿   占比%  5日Δpp 20日Δpp  liq   量比  连放/缩  涨跌%')
    for r in LQ:
        L.append(f"{r['board']:<9s}{r['amt']:>7.0f}{r['sh_now']:>7.2f}{r['d5']:>+7.2f}{r['d20']:>+7.2f}"
                 f"{r['liq']:>6.2f}{r['vol_ratio']:>6.2f}{r['streak']:>+6d}{r['pct1d']:>+7.2f}")
    top_liq = sorted(LQ, key=lambda x: -x['liq'])[:3]
    bot_liq = sorted(LQ, key=lambda x: x['liq'])[:3]
    L.append('流动性净流入前三：' + '、'.join(f"{r['board']}(liq {r['liq']:.2f})" for r in top_liq))
    L.append('流动性净流出前三：' + '、'.join(f"{r['board']}(liq {r['liq']:.2f})" for r in bot_liq))
    L.append('')
    L.append('══ 三、产业链传导监控 ══')
    for B in Bs:
        st, ue, de = B['st'], B['up_eng'], B['dn_eng']
        ck = 'cum%d' % B['eng_days']
        L.append('')
        L.append(f"【{B['chain']}】主涨侧(发动机)={ue}({st[ue][ck]:+.1f}%/20日) ｜ "
                 f"主跌侧={de}({st[de][ck]:+.1f}%) ｜ 全链多数{B['eng_dir']} ｜ 振幅基准{B['avg_amp']:.2f}%")
        L.append('环节        β对涨 β对跌  跟涨% 跟跌%  位置%  额比  占比Δ%  不跟涨→      不跟跌→')
        for b, sv in sorted(st.items(), key=lambda kv: -kv[1][ck]):
            fu = f"{sv['fu'].get(ue):.0%}" if sv['fu'].get(ue) is not None else ' —'
            fd = f"{sv['fd'].get(de):.0%}" if sv['fd'].get(de) is not None else ' —'
            b1 = f"{sv['beta'].get(ue,1):.2f}" if b != ue else '基准'
            b2 = f"{sv['beta'].get(de,1):.2f}" if b != de else '基准'
            t1 = sv['tag'].get(ue, '—')
            t2 = sv['tag'].get(de, '—')
            L.append(f"{b:<9s}{b1:>5s}{b2:>6s}{fu:>6s}{fd:>6s}{sv['pos']:>6.0f}{sv['amt_ratio']:>6.2f}"
                     f"{sv['share_rise']:>+8.1f}  {t1:<12s} {t2}")
        # 主跌侧跟跌排行（传导顺序）
        if B.get('dn_rank'):
            rk = B['dn_rank']
            most_hold, most_drop = rk[0][0], rk[-1][0]
            L.append(f"  ▸ 主跌侧跟跌排行（{de}基准；弹性小=抗跌，大=跌得一样凶）")
            L.append(f"    【最抗跌】{most_hold}({rk[0][1]:.2f})  →  【最跟跌】{most_drop}({rk[-1][1]:.2f})")
            L.append(f"    {'＞'.join(f'{b}{ed:.2f}' for b, ed in rk)}")
            L.append(f"    中位弹性 {B['dn_med']:.2f}；<0.35 视为明显不跟")
            # 传导终点：发动机能不能传到下游
            endb = [b for b, ed in rk if ed >= 0.8]
            if endb:
                L.append(f"    传导终点(仍跟跌充分 ≥0.8)：{'、'.join(endb)}")
            else:
                L.append(f"    传导终点：无环节弹性≥0.8 → 主跌侧杀跌未能扩散到下游")
        L.append('  判定依据：')
        for b, sv in sorted(st.items(), key=lambda kv: -kv[1][ck]):
            if b == ue and b == de:
                continue
            if b == ue:
                L.append(f"   ·{b}: 🔧主涨侧 — {sv['why_up']}")
            elif b == de:
                L.append(f"   ·{b}: 🔧主跌侧 — {sv['why_dn']}（基准）")
            else:
                L.append(f"   ·{b}: 涨侧{sv['tag'][ue]} {sv['why_up']} ｜ 跌侧{sv['tag'][de]} {sv['why_dn']}")
    L.append('')
    L.append('══ 四、四类池 ══')
    acc = [r['board'] for r in A if r['tag_a'] == '吸筹']
    dist = [r['board'] for r in A if r['tag_a'] == '派发']
    shortp = [r['board'] for r in A if '短钱' in r['nature']]
    longp = [r['board'] for r in A if '长钱' in r['nature']]
    L.append('吸筹(可回踩跟踪)：' + ('、'.join(acc) if acc else '无'))
    L.append('派发(只卖不追)：' + ('、'.join(dist) if dist else '无'))
    L.append('短钱(隔日交易)：' + ('、'.join(shortp) if shortp else '无'))
    L.append('长钱(中线观察)：' + ('、'.join(longp) if longp else '无'))
    for B in Bs:
        ue, de = B['up_eng'], B['dn_eng']
        op = [b for b, s in B['st'].items() if '🟢' in s['tag'].get(de, '')]
        mi = [b for b, s in B['st'].items() if '🔴' in s['tag'].get(de, '')]
        ho = [b for b, s in B['st'].items() if '⚪' in s['tag'].get(de, '')]
        xu = [b for b, s in B['st'].items() if '🟡' in s['tag'].get(ue, '')]
        dq = [b for b, s in B['st'].items() if '🔴' in s['tag'].get(ue, '')]
        L.append(f"{B['chain']}：🟢机会({ue}涨侧/跌侧{de})={('、'.join(op) or '无')} ｜ "
                 f"🔴埋雷={('、'.join(mi) or '无')} ｜ ⚪抱团={('、'.join(ho) or '无')}")
        L.append(f"           🟡蓄势={('、'.join(xu) or '无')} ｜ 🔴掉队={('、'.join(dq) or '无')}")
    L.append('')
    L.append('══ 五、未来验证日历 & 信号变化 ══')
    changes, n_sig = diff_signals(Bs, A)
    L.append(f'本次跟踪信号 {n_sig} 条；与上次对比变化 {len(changes)} 条')
    if changes:
        for c in changes[:12]:
            L.append('  · ' + c)
    else:
        L.append('  · 无变化（首次运行或状态未变）')
    L.extend(validate_calendar(Bs, A, days))
    L.append('数据缺口：北向个股持仓、股东户数 = 无数据（接口当日无返回）')
    return '\n'.join(L), A, Bs, days


QQ_TARGET = os.environ.get('FD_QQ_TARGET') or os.environ.get("QQ_DEFAULT_TARGET", "")
FEISHU_CHAT = os.environ.get('FD_FEISHU_CHAT') or os.environ.get("FEISHU_CHAT_STOCK") or os.environ.get("FEISHU_CHAT_ID", "")


def push_qq(text, target=None):
    import subprocess
    # QQ 纯文本无 markdown，卡片分隔符换成短横线
    t = text.replace('══', '—').replace('▸', '·')
    if len(t) > 3500:
        t = t[:3500] + '\n…（完整版见飞书）'
    p = subprocess.run(['openclaw', 'message', 'send', '--channel', 'qqbot',
                        '--target', target or QQ_TARGET, '--message', t],
                       cwd=WS, capture_output=True, text=True, timeout=180)
    return p.returncode, ((p.stdout or '') + (p.stderr or '')).strip()[-200:]


def push_feishu_card(text, title):
    """走官方应用机器人（可发群，正文长也不受 webhook 50KB 限制）"""
    import subprocess
    card = {'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': 'blue'},
            'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': text[:18000]}}]}
    cf = os.path.join(CACHE, f'card_{D8}.json')
    json.dump(card, open(cf, 'w', encoding='utf-8'), ensure_ascii=False)
    p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feishu_push_card.py'),
                        '--card-file', cf, '--chat', FEISHU_CHAT, '--signature', ''],
                       cwd=WS, capture_output=True, text=True, timeout=180)
    out = ((p.stdout or '') + (p.stderr or '')).strip()
    norm = out.replace(' ', '').replace('"', '')
    ok = p.returncode == 0 and ('code=0' in norm or 'code:0' in norm)
    return ok, out[-200:]


def push_card(text, title):
    """飞书推送：**优先走统一通知库（data_hub.notify）**，未就绪才回落 webhook。"""
    try:
        import _notify
        res = _notify.send_feishu(title, text)
        if res.get("ok"):
            return {"code": 0, "msg": "success（data_hub.notify）"}
        print("[flow_diag] 统一库未就绪，回落 webhook：%s" % res.get("reason"))
    except Exception as exc:
        print("[flow_diag] 统一库异常，回落 webhook：%s" % exc)
    import urllib.request
    hook = __import__("ocsecrets").feishu_hook()
    body = {'msg_type': 'interactive', 'card': {
        'config': {'wide_screen_mode': True},
        'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': 'blue'},
        'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': text[:12000]}}]}}
    req = urllib.request.Request(hook, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--chain', nargs='*', default=list(CHAINS.keys()))
    ap.add_argument('--topn', type=int, default=10)
    ap.add_argument('--push', default='', help='feishu | qq | both | 空=不推')
    ap.add_argument('--qq-target', default=QQ_TARGET)
    ap.add_argument('--chat', default=FEISHU_CHAT)
    ap.add_argument('--out', default='')
    ap.add_argument('--no-init', action='store_true')
    a = ap.parse_args()
    if not a.no_init:
        ensure_sector_cache()
    txt, A, Bs, days = build(a.chain, a.topn)
    title = f'📊 行业资金 × 产业链传导 · {DATE[4:6]}/{DATE[6:]}'
    print(txt)
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(txt)
    if a.push in ('qq', 'both'):
        rc, msg = push_qq(build_compact(A, Bs, days), a.qq_target)
        print(f'[qq] rc={rc} {msg}')
    if a.push in ('feishu', 'both'):
        ok, msg = push_feishu_card(txt, title)
        print(f'[feishu] ok={ok} {msg}')
