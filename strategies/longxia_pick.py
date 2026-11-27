#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🦞 龙虾缠论 · 龙虎榜选股（每日早盘前选股，纯脚本 0 token）
=================================================
选股逻辑（与用户 2026-10-26 确认的口径）：
  第一层 行业：申万二级行业指数（134个）自身满足
        · 行业指数开盘价和收盘价都 > 自身 MA20
        · MA20 > MA40 > MA80 > MA160（严格多头排列）
      按当日行业涨幅降序排名。
  第二层 个股：在行业榜 1~10 名里各选 2 只，须同时满足
        · 开盘价和收盘价都站上 MA20
        · MA20 > MA40 > MA80 > MA160（严格多头排列）
        · 相邻均线乖离 20↔40 / 40↔80 / 80↔160 三段都 < 20%
          （不使用 MA20↔MA160 总乖离——大佬 2026-10-26 明确：20 和 160 没有关联要求）
        · 剔除 ST / 退市 / 上市不足 200 个交易日
  降级链（用户指定）：
        A. 行业榜 1~10 → 每行业 2 只，凑 20 只
        B. 不够 → 继续在行业榜 11~50 名里补，每行业最多 2 只
        C. 还不够（且总数 < 10）→ 完全放弃行业条件，全市场只按个股条件选 10 只
  数据：tushare 代理；均线、开盘价、收盘价统一用后复权价比较（避免除权日假信号）。

龙虎榜板块（2026-10-26 新增）：
   当日申万二级行业涨幅前十 → 每个行业列出「当日上了龙虎榜的自家成分股」，
   按龙虎榜净买额降序最多 5 只，并标注「机构专用」席位净买与游资/营业部净买。
   数据：pro.top_list（个股榜）+ pro.top_inst（席位明细），金额单位统一换算成亿元。

发送记账（去重）机制（2026-10-26 加固）：
   · 只有**正式定时任务**才写 .cache/longxia/sent_YYYYMMDD.json 记账；
     判据是环境变量 LX_FORMAL=1（由 bin/longxia_daily.sh 注入）。
   · 手动跑（测试 / 补发 / --no-send）一律不写记账，
     避免「凌晨手动跑一次 → 早上正式任务误判已发送而漏发」的坑。

用法：
  python3 strategies/longxia_pick.py                 # 正常出报告并推送
  python3 strategies/longxia_pick.py --no-send       # 只打印，不推送
  python3 strategies/longxia_pick.py --date 20261023 # 指定交易日
  python3 strategies/longxia_pick.py --qq            # 额外推 QQ 文字版
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, re, json, time, argparse, datetime

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tushare_init import get_pro

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(WS, '.cache', 'longxia')
PICKS_DIR = os.path.join(CACHE, '..', 'picks')  # 统一契约落盘目录（.cache/picks）
RAW = os.path.join(CACHE, 'raw')
SWRAW = os.path.join(CACHE, 'swraw')

pro = get_pro()

BIAS_MAX = 0.20          # 均线之间乖离率上限
# 2026-11-27（大佬反馈「A 档图天天一样」）：连续上榜剔除
#   连续 >= REPEAT_MAX 个交易日进过 A 档 → 本轮不进 A 档（仍可进 20 只名单，标「连续上榜剔除」）
#   回测 11-05~11-26（15 个交易日）：K=2 剔 9%、相邻日重合率 30%→13%；K=3 剔 6%、→19%；
#   K=1 剔 24%、直接归零（太狠）。默认 2。LX_AB_REPEAT_MAX=0 可关。
def _repeat_max():
    """LX_AB_REPEAT_MAX 容错解析：写错/为空一律回落默认 2。

    2026-11-27 复审：原来直接 int(os.environ.get(...))，环境变量写成 "abc" 会在
    模块导入期抛 ValueError，整个任务起不来。这里退化成告警 + 默认值。
    """
    raw = (os.environ.get('LX_AB_REPEAT_MAX') or '').strip()
    if not raw:
        return 2
    try:
        return max(0, int(raw))
    except ValueError:
        print('[repeat] LX_AB_REPEAT_MAX=%r 不是整数，回落默认 2' % raw, flush=True)
        return 2


REPEAT_MAX = _repeat_max()
REPEAT_LOOKBACK = max(REPEAT_MAX + 2, 10)   # 回看窗口跟随开关，避免 K>10 时永不触发
AB_HIST = os.path.join(CACHE, 'ab_history.json')
MA_WINS = (20, 40, 80, 160)
MIN_BARS = 160          # 均线最少需要的交易日数
LIST_MIN_DAYS = 200     # 上市最短交易日
PER_IND = 2
TARGET_TOP = 20
TIER1_IND = 10
TIER2_END = 50
FEISHU_DM = os.environ.get("FEISHU_CHAT_DM") or os.environ.get("FEISHU_CHAT_ID", "")
QQ_TARGET = os.environ.get("QQ_DEFAULT_TARGET", "")
# A/B 档合图额外多推一份到 bot2 的私聊（大佬 2026-10-28 在 bot2 会话里提的需求）
QQ_TARGETS_IMG = [t for t in (QQ_TARGET, os.environ.get("QQ_TARGET_IMG", "")) if t]


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 数据加载
def load_panel(dates):
    frames, adjlast = [], {}
    for d in dates:
        p = os.path.join(RAW, '%s.pkl' % d)
        if os.path.exists(p):
            try:
                frames.append(pd.read_pickle(p))
            except Exception:
                pass
    if not frames:
        raise SystemExit('面板为空，先跑 bin/longxia_panel.py')
    df = pd.concat(frames, ignore_index=True)
    df = df[df['close'].notna() & (df['close'] > 0)]
    # 个股后复权（以区间末 adj 归一，形态一致即可）
    al = df.sort_values('trade_date').groupby('ts_code')['adj_factor'].last()
    df['adj_last'] = df['ts_code'].map(al)
    df['hfq'] = df['close'] * df['adj_factor'] / df['adj_last']
    df['hfq_open'] = df['open'] * df['adj_factor'] / df['adj_last']
    # 2026-10-28 画 A/B 档合图要用高/低（同样后复权归一）
    if 'high' in df.columns:
        df['hfq_high'] = df['high'] * df['adj_factor'] / df['adj_last']
    if 'low' in df.columns:
        df['hfq_low'] = df['low'] * df['adj_factor'] / df['adj_last']

    # 近 60 个交易日涨幅（后复权，用于排序：大佬 2026-10-26 要求按 60 日涨幅排先后）
    df = df.sort_values(['ts_code', 'trade_date'])
    df['ret60'] = df.groupby('ts_code')['hfq'].transform(
        lambda s: s / s.shift(60) - 1.0)
    return df


def load_sw(level, dates):
    """拼装申万指数日线（从 swraw 单日缓存）。"""
    keep = set(sw_codes(level))
    frames = []
    for d in dates:
        p = os.path.join(SWRAW, '%s.pkl' % d)
        if os.path.exists(p):
            try:
                frames.append(pd.read_pickle(p))
            except Exception:
                pass
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df[df['ts_code'].isin(keep)]
    return df


_CLS = {}


def sw_codes(level):
    if level in _CLS:
        return _CLS[level]
    p = os.path.join(CACHE, 'sw_classify_%s.json' % level)
    m = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    _CLS[level] = m
    return m


def ma_block(df, price_col, by='ts_code'):
    """按 by 分组算均线，返回最后一日各标的均线状态。"""
    g = df.sort_values('trade_date').groupby(by)[price_col]
    out = {}
    for w in MA_WINS:
        out['ma%d' % w] = g.transform(lambda s: s.rolling(w).mean()).reset_index(drop=True) if False else None
    # 直接构造（避免 transform 索引问题）
    df = df.sort_values('trade_date').reset_index(drop=True)
    for w in MA_WINS:
        df['ma%d' % w] = df.groupby(by)[price_col].transform(
            lambda s: s.rolling(w, min_periods=w).mean())
    return df


def last_rows(df, by='ts_code'):
    """每标的最后一行（需 df 已按 trade_date 升序）。"""
    d = df.dropna(subset=['ma160'])
    if d.empty:
        return d
    idx = d.groupby(by)['trade_date'].idxmax()
    return d.loc[idx].set_index(by)


# ---------------------------------------------------------------- 连续上榜记账
def load_ab_hist():
    """{"20261126": ["601328.SH", ...], ...} —— 每日 A 档入选清单。

    2026-11-27 复审：文件损坏时不再静默清空（否则下一次 save_ab_hist 会把整段历史
    覆盖丢掉），改为先把坏文件备份成 ab_history.json.corrupt-<时间戳> 再返回空表。
    """
    if not os.path.exists(AB_HIST):
        return {}
    try:
        with open(AB_HIST, encoding='utf-8') as f:
            hist = json.load(f) or {}
        if not isinstance(hist, dict):
            raise ValueError('顶层不是 dict（%s）' % type(hist).__name__)
        clean = {}
        for d, codes in hist.items():
            if isinstance(codes, list):
                clean[str(d)] = [str(c) for c in codes]
            else:
                log('[repeat] 历史条目 %s 不是 list，已跳过' % d)
        return clean
    except Exception as e:
        bak = '%s.corrupt-%s' % (AB_HIST, datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
        try:
            os.replace(AB_HIST, bak)
            log('[repeat] 历史文件损坏（%s），已备份到 %s 后重建' % (e, bak))
        except OSError as e2:
            log('[repeat] 历史损坏且备份失败：%s / %s' % (e, e2))
        return {}


def ab_streak(hist, code, t_date, cal=None, max_back=None):
    """从 t_date 的前一交易日起往前数，连续多少天出现在 A 档（中间断一天就停）。

    2026-11-27 复审：必须沿交易日历（cal）走。原实现按「历史里最新的几天」数，
    任务漏跑一天时会把两天前甚至一周前的记录当成「昨天」，把隔了好几天的票
    误判成「连续上榜」并剔掉。改成按 cal 逐日校验后，缺记录即断（偏保守）。
    """
    max_back = max_back or REPEAT_LOOKBACK
    if cal:
        seq = [d for d in cal if d < t_date][-max_back:]
    else:
        seq = sorted([d for d in hist if d < t_date])[-max_back:]
    n = 0
    for d in reversed(seq):
        if code in (hist.get(d) or []):
            n += 1
        else:
            break
    return n


def save_ab_hist(t_date, ab_codes, keep=20):
    """写入/更新当日 A 档清单（只留最近 keep 个交易日）。"""
    try:
        hist = load_ab_hist()
        hist[t_date] = sorted(set(ab_codes))
        for d in sorted(hist, reverse=True)[keep:]:
            hist.pop(d, None)
        os.makedirs(os.path.dirname(AB_HIST), exist_ok=True)
        json.dump(hist, open(AB_HIST, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        log('[repeat] A 档历史已更新：%s → %d 只（共 %d 个交易日）'
            % (t_date, len(hist[t_date]), len(hist)))
    except Exception as e:
        log('[repeat] 历史写入失败：%s' % e)


# ---------------------------------------------------------------- 概念标签
# 大佬 2026-10-28 要求「子图后边一定要加上所属概念」，但 longxia_pick 的 detail
# 一直没塞 concepts 字段 → longxia_chart 里那段渲染从来没生效过（2026-11-27 补上）。
#
# 口径与 bin/stock_pool.py 保持一致（同一份概念表、同一套剔除规则），但**不 import**
# 它（stock_pool 会去做飞书抓取等一堆副作用），这里只复刻判定：
#   ① 覆盖股票数 > BROAD_COVER 的概念 = 宽泛（融资融券/沪股通/样本股这类）
#   ② LOW_VALUE_TAGS 里的行政/榜单/财报类噪音
#   ③ LIST_TAG 命中的榜单名（漂亮100 / TOP50 / 沪深300 之类）
CONCEPT_FILE = os.path.join(WS, '.cache', 'concepts', 'stock_concepts.json')
CONCEPT_BROAD_COVER = int(os.environ.get('LX_CONCEPT_BROAD_COVER', '400'))
CONCEPT_MAX = int(os.environ.get('LX_CONCEPT_MAX', '4'))   # 每只票最多显示几个概念
LOW_VALUE_TAGS = (
    '样本股', '成份股', '成分股', '融资融券', '转融券', '标普', 'MSCI', '富时',
    '沪股通', '深股通', '证金持股', '预盈预增', '预亏预减', '同花顺', '中特估',
    '国企改革', '回购增持', '高股息', '定增', '增发', '可转债', '股权转让', '举牌',
    '预增', '预减', '季报', '年报', '中报', '专精特新', '一带一路', '共同富裕', '乡村振兴',
    '西部大开发', '粤港澳', '美丽中国', '雄安', '长三角', '京津冀', '自贸区', '海南',
    '破净', '低价股', '壳资源', 'ST板块', '参股', '控股', '分拆上市',
    '人民币贬值受益', '融资标的', '新股与次新股', '注册制次新股', '科创次新股',
)
LIST_TAG = re.compile(r'(TOP\s*\d+|Top\s*\d+|漂亮\s*\d+|\D\s?(50|100|300)\s*$)', re.I)

# 兜底层：严格过滤后若一个不剩（银行股常见——概念全是样本股/沪股通/高股息这类），
# 只剔「纯指数成分 + 互联互通 + 两融标的」这些真的毫无信息量的，剩下的给 1~2 个。
MEANINGLESS_ONLY = ('样本股', '成份股', '成分股', '融资融券', '转融券', '融资标的',
                    '沪股通', '深股通', '标普', 'MSCI', '富时')

_CONCEPT_MAP = None


def load_concepts():
    """{ts_code: [概念...]}；文件缺失/损坏返回 {}（图里就不显示概念，不报错）。"""
    global _CONCEPT_MAP
    if _CONCEPT_MAP is not None:
        return _CONCEPT_MAP
    try:
        raw = json.load(open(CONCEPT_FILE, encoding='utf-8'))
        # 现表结构是 {"map": {...}, "__done__": [...]}，兼容裸 dict
        m = raw.get('map') if isinstance(raw, dict) and isinstance(raw.get('map'), dict) else raw
        _CONCEPT_MAP = {str(k): list(v or []) for k, v in (m or {}).items()
                        if isinstance(v, (list, tuple))}
        log('[concept] 概念表载入 %d 只（%s）' % (len(_CONCEPT_MAP), CONCEPT_FILE))
    except Exception as e:
        log('[concept] 概念表读取失败（图内不显示概念）：%s' % e)
        _CONCEPT_MAP = {}
    return _CONCEPT_MAP


def concepts_of(code, limit=None, cmap=None, broad=None):
    """单只票的可展示概念（已剔宽泛/榜单/行政噪音），按长短排序，最多 limit 个。"""
    cmap = load_concepts() if cmap is None else cmap
    if not cmap:
        return []
    tags = cmap.get(code)
    if not tags:
        # 兜底：代码后缀不一致时按 6 位数字找
        c6 = str(code)[:6]
        tags = cmap.get(c6) or []
    if not tags:
        return []
    if broad is None:
        cnt = {}
        for v in cmap.values():
            for t in set(v or []):
                cnt[t] = cnt.get(t, 0) + 1
        broad = {t for t, n in cnt.items() if n > CONCEPT_BROAD_COVER}
    keep = [t for t in tags
            if t and t not in broad
            and not any(k in t for k in LOW_VALUE_TAGS)
            and not LIST_TAG.search(t)]
    # 去重保序
    seen, out = set(), []
    for t in keep:
        if t not in seen:
            seen.add(t)
            out.append(t)
    out.sort(key=lambda t: (len(t) > 10, t))
    n = CONCEPT_MAX if limit is None else limit
    if not out:
        # 兜底：只剔纯指数成分/互联互通/两融，保住「参股保险/信托概念/高股息精选」这类
        out = [t for t in tags
               if t and not any(k in t for k in MEANINGLESS_ONLY)
               and not LIST_TAG.search(t)]
        seen, fb = set(), []
        for t in out:
            if t not in seen:
                seen.add(t)
                fb.append(t)
        out = fb
    return out[:n]


def bull(rows, close_col='close', open_col='open'):
    """多头排列 MA20>MA40>MA80>MA160，且**开盘价与收盘价都站上 MA20**。
    不再使用 MA20↔MA160 的总乖离（用户 2026-10-26 明确：20 和 160 没有关联要求）。
    """
    m20, m40, m80, m160 = rows['ma20'], rows['ma40'], rows['ma80'], rows['ma160']
    arr = (m20 > m40) & (m40 > m80) & (m80 > m160)
    above = (rows[close_col] > m20) & (rows[open_col] > m20)
    return arr & above


# ---------------------------------------------------------------- 行业层
def industry_layer(dates, basic_map):
    """申万二级行业指数均线筛选 + 涨幅排名。"""
    sw = load_sw('L2', dates)
    if sw.empty:
        raise SystemExit('申万行业缓存为空，先跑 bin/longxia_panel.py')
    sw = sw.copy()
    sw['close'] = pd.to_numeric(sw['close'], errors='coerce')
    sw['pct_change'] = pd.to_numeric(sw['pct_change'], errors='coerce')
    sw = sw[sw['close'].notna()]
    sw = sw.sort_values(['ts_code', 'trade_date'])
    sw['ret60'] = sw.groupby('ts_code')['close'].transform(lambda s: s / s.shift(60) - 1.0)
    sw = ma_block(sw, 'close')
    rows = last_rows(sw)
    if rows.empty:
        return pd.DataFrame(), pd.DataFrame()
    _ocol = 'open' if 'open' in rows.columns else 'close'
    ok_arr = bull(rows, 'close', _ocol)
    # 行业层乖离同样只看相邻均线对
    rows['b1'] = (rows['ma20'] - rows['ma40']) / rows['ma40']
    rows['b2'] = (rows['ma40'] - rows['ma80']) / rows['ma80']
    rows['b3'] = (rows['ma80'] - rows['ma160']) / rows['ma160']
    rows['pass_ma'] = ok_arr
    rows['last_date'] = rows['trade_date']
    # 行业成分股映射
    mem = load_members()
    rows['n_member'] = rows.index.map(lambda c: len(mem.get(c, [])))
    rows['ret60'] = pd.to_numeric(rows.get('ret60'), errors='coerce').fillna(-9.99)
    # 行业排名同样按近 60 日涨幅降序（不是当日涨幅）
    rows = rows.sort_values('ret60', ascending=False)
    return rows, mem


_MEM = None


def load_members():
    global _MEM
    if _MEM is not None:
        return _MEM
    p = os.path.join(CACHE, 'sw_members.pkl')
    if not os.path.exists(p):
        _MEM = {}
        return _MEM
    _MEM = pd.read_pickle(p)
    return _MEM


def industry_names():
    return sw_codes('L2')


# ---------------------------------------------------------------- 龙虎榜层
LHB_TOP_N = 10          # 取当日行业涨幅前十
LHB_PER_IND = 5         # 文本版每行业最多列 5 只
LHB_CARD_PER_IND = 3    # 卡片版每行业最多列 3 只（控体积）


def stock_industry_map():
    """个股 → 申万二级行业代码列表（反向映射，供龙虎榜板块按行业归集用）。"""
    rev = {}
    for code, mem in load_members().items():
        for ts in mem:
            rev.setdefault(ts, []).append(code)
    return rev


def fetch_lhb(trade_date):
    """当日龙虎榜：{ts_code: {...}}（金额单位：亿元）。

    · top_list 一票可能因触发多个上榜条件出现多行 → 每票取 **l_amount 最大**
      的那一条（最主要上榜原因），避免净买额重复相加。
    · top_inst 同一席位在多条件下会重复出现 → 先按 (ts_code, reason, 席位, buy, sell) 去重，
      再按票+原因汇总 net_buy。
    · exalter 含「机构专用」→ 机构；其余 → 游资/营业部。
    · 无数据 / 接口异常 → 返回 None（上层降级为「数据未入库」，脚本不崩）。
    """
    try:
        tl = pro.top_list(trade_date=trade_date)
        ti = pro.top_inst(trade_date=trade_date)
    except Exception as e:
        log('龙虎榜接口失败: %s' % e)
        return None
    if tl is None or not len(tl):
        return None
    tl = tl.copy()
    for c in ('net_amount', 'l_amount', 'pct_change'):
        if c in tl.columns:
            tl[c] = pd.to_numeric(tl[c], errors='coerce')
    tl = tl.dropna(subset=['net_amount'])
    if tl.empty:
        return None
    # 优先保留「净买额非 0」的上榜原因（避免「严重异常期间」等跨日聚合行的 net=0），
    # 同一票多条时再按 l_amount（龙虎榜成交额）取最大那条为主原因。
    tl['_nz'] = (tl['net_amount'].abs() > 1).astype(int)
    sort_cols = ['_nz'] + (['l_amount'] if 'l_amount' in tl.columns else [])
    tl = tl.sort_values(sort_cols, ascending=False)
    tl = tl.drop_duplicates(subset=['ts_code'], keep='first')

    inst_sum, hot_sum = {}, {}
    if ti is not None and len(ti):
        ti = ti.copy()
        ti['net_buy'] = pd.to_numeric(ti['net_buy'], errors='coerce').fillna(0.0)
        sub = ['ts_code', 'reason', 'exalter', 'buy', 'sell']
        ti = ti.drop_duplicates(subset=[c for c in sub if c in ti.columns])
        is_inst = ti['exalter'].astype(str).str.contains('机构专用', na=False)
        for flag, store in ((True, inst_sum), (False, hot_sum)):
            g = ti[is_inst == flag]
            if len(g):
                s = g.groupby(['ts_code', 'reason'])['net_buy'].sum()
                store.update({k: float(v) for k, v in s.items()})

    out = {}
    for _, r in tl.iterrows():
        ts = r['ts_code']
        key = (ts, r.get('reason'))
        out[ts] = {
            'ts_code': ts,
            'name': str(r.get('name') or ''),
            'pct': float(r['pct_change']) if pd.notna(r.get('pct_change')) else 0.0,
            'net': float(r['net_amount']) / 1e8,
            'inst': inst_sum.get(key, 0.0) / 1e8,
            'hot': hot_sum.get(key, 0.0) / 1e8,
        }
    return out


def lhb_section(ind, trade_date):
    """涨幅前十行业 × 当日龙虎榜上榜股。

    返回 (sections, has_data)
      sections = [{'ind','code','ind_pct','stocks':[{ts_code,name,pct,net,inst,hot}]}]
    """
    if ind is None or ind.empty:
        return [], None
    names = industry_names()
    top = ind.copy()
    top['pct_change'] = pd.to_numeric(top['pct_change'], errors='coerce')
    top = top.dropna(subset=['pct_change']).sort_values('pct_change', ascending=False)
    top = top.head(LHB_TOP_N)
    lhb = fetch_lhb(trade_date)
    rev = stock_industry_map()

    by_ind = {}
    for ts, info in (lhb or {}).items():
        for code in rev.get(ts, []):
            by_ind.setdefault(code, []).append(info)

    out = []
    for code, r in top.iterrows():
        cand = sorted(by_ind.get(code, []), key=lambda x: -x['net'])
        out.append({'ind': names.get(code, code), 'code': code,
                    'ind_pct': float(r['pct_change']),
                    'stocks': cand})
    return out, (lhb is not None)


# ---------------------------------------------------------------- 主流程
def run(date_str=None, verbose=True):
    dates = sorted(os.path.basename(x)[:8] for x in os.listdir(RAW) if x.endswith('.pkl'))
    if not dates:
        raise SystemExit('无原始面板')
    if date_str and date_str in dates:
        dates = [d for d in dates if d <= date_str]
    t_date = dates[-1]
    panel = load_panel(dates)
    box = panel[panel['trade_date'] == t_date].set_index('ts_code')
    panel = ma_block(panel, 'hfq')
    rows = last_rows(panel)                  # hfq 均线状态
    ok_arr = bull(rows, 'hfq', 'hfq_open')
    # 乖离率＝相邻均线对之间的距离（大佬 2026-10-26 明确：只看 20↔40、40↔80、80↔160）
    rows['b1'] = (rows['ma20'] - rows['ma40']) / rows['ma40']
    rows['b2'] = (rows['ma40'] - rows['ma80']) / rows['ma80']
    rows['b3'] = (rows['ma80'] - rows['ma160']) / rows['ma160']
    rows['pass_stock'] = ok_arr

    # 真实股价 + 名称 + 上市天数
    basic = pd.read_pickle(os.path.join(CACHE, 'basic.pkl'))
    bmap = basic.set_index('ts_code')
    rows['name'] = rows.index.map(lambda c: str(bmap['name'].get(c, '')))
    rows['list_date'] = rows.index.map(lambda c: str(bmap['list_date'].get(c, '')))
    rows['real_close'] = rows.index.map(lambda c: box['close'].get(c, np.nan))
    rows['real_open'] = rows.index.map(lambda c: box['open'].get(c, np.nan))
    rows['pct_chg'] = rows.index.map(lambda c: box['pct_chg'].get(c, np.nan))
    rows['amount'] = rows.index.map(lambda c: box['amount'].get(c, np.nan))
    nbars = panel.groupby('ts_code')['trade_date'].nunique()
    rows['nbars'] = rows.index.map(lambda c: nbars.get(c, 0))

    # 上市满 200 个「交易日」≈ 自然日 290 天（用上市日判，日内回放也成立）
    td = datetime.datetime.strptime(t_date, '%Y%m%d')

    def _old_enough(ld):
        try:
            d0 = datetime.datetime.strptime(ld[:8], '%Y%m%d')
        except Exception:
            return False
        return (td - d0).days >= 290

    rows['old'] = rows['list_date'].map(_old_enough)
    st = rows['name'].astype(str).str.contains('ST', na=False) | rows['name'].astype(str).str.contains('退', na=False)
    # 三条相邻均线乖离都必须 <20%
    rows['ok'] = (rows['pass_stock']
                  & (rows['b1'].abs() < BIAS_MAX)
                  & (rows['b2'].abs() < BIAS_MAX)
                  & (rows['b3'].abs() < BIAS_MAX)
                  & (~st)
                  & rows['old'])
    rows['pct_chg'] = pd.to_numeric(rows['pct_chg'], errors='coerce').fillna(-999)
    rows['ret60'] = pd.to_numeric(rows.get('ret60'), errors='coerce').fillna(-9.99)

    # ---- 股东户数否决（大佬 2026-11-04：「没有披露的就不选它，选股的时候去掉」）
    #   contracts/户数否决名单.json 由 bin/fetch_chips.py 生成，含两类：
    #     ① ❌否决(无披露数据) / ❌否决(户数未披露/过期) —— 没依据，直接剔除
    #     ② ❌否决(高位户数发散) —— 高位且环比>15%，派发风险
    #   开关 LX_CHIPS_VETO=0 可关（回滚用）
    veto = {}
    if os.environ.get('LX_CHIPS_VETO', '1') != '0':
        try:
            vp = os.path.join(WS, 'contracts', '户数否决名单.json')
            veto = (json.load(open(vp, encoding='utf-8')).get('codes') or {}) if os.path.exists(vp) else {}
        except Exception as e:
            log('[chips] 否决名单读取失败，跳过：%s' % e)
            veto = {}
    n_veto_all = 0
    if veto:
        hit = rows.index.isin(list(veto))
        n_veto_all = int((hit & rows['ok']).sum())
        rows['ok'] = rows['ok'] & (~hit)
        log('[chips] 户数否决名单 %d 只 → 剔除个股条件合格里的 %d 只' % (len(veto), n_veto_all))

    # 选股与排序一律按「近 60 日涨幅」降序（大佬 2026-10-26 明确：不用当日涨幅排）
    rows = rows.sort_values('ret60', ascending=False)

    # 大佬 2026-10-28：A/B/C 三档统一「最近一个交易日上涨」
    #   rows['pct_chg'] 缺失/停牌填的是 -999，自然被剔除
    # ⚠️ 2026-11-04 修：这一行必须在 collect() 之前算好 —— 以前放在第 446 行，
    #    而 419 行 collect() 里已经读 rows.at[c,'okup']，导致 10-29 起每天 KeyError 崩掉
    rows['okup'] = rows['ok'] & (rows['pct_chg'] > 0)

    # ---- 行业层
    ind, mem = industry_layer(dates, bmap)
    names = industry_names()
    ind_pass = ind[ind['pass_ma']].copy() if not ind.empty else pd.DataFrame()

    picks, tier_used = [], []
    ind_rank = []
    # 连续上榜剔除（2026-11-27；2026-11-27 复审加交易日历与报告口径修正）
    hist = load_ab_hist()
    cal = list(dates)            # 交易日历（已裁到 <= t_date），连续性只认它
    rep_hit = {}
    if REPEAT_MAX > 0 and hist:
        for c in rows.index:
            s = ab_streak(hist, c, t_date, cal=cal)
            if s >= REPEAT_MAX:
                rep_hit[c] = s
    cut_hit = {}                 # 真正从 A/B 档候选里被剔除的票（报告按这个计数）
    if rep_hit:
        log('[repeat] 连续上榜 >=%d 天 → 全市场命中 %d 只（实际剔除数见候选阶段）'
            % (REPEAT_MAX, len(rep_hit)))
    if not ind_pass.empty:
        ranked = ind_pass.sort_values('pct_change', ascending=False)
        order = ranked.index.tolist()
        ind_rank = order
        # 严格级联（用户口径）：
        #   A. 行业榜 1-10 名行业里选，每行业最多 2 只 → 目标 20 只
        #   B. A 一个都没选出来 → 继续看行业榜 11-50 名
        #   C. B 还是零 → 完全放弃行业条件，只按个股条件选 10 只
        def collect(codes, skip=None):
            skip = skip or set()
            got = []
            for code in codes:
                if len(got) >= TARGET_TOP:
                    break
                cand_all = [c for c in mem.get(code, [])
                            if c in rows.index and rows.at[c, 'okup']]
                for c in cand_all:          # 记下真正被「连续上榜」挡掉的候选
                    if c in skip:
                        cut_hit[c] = rep_hit.get(c, 0)
                cand = [c for c in cand_all if c not in skip]
                if not cand:
                    continue
                sub = rows.loc[cand].sort_values('ret60', ascending=False)
                for ts in sub.index[:PER_IND]:
                    if len(got) >= TARGET_TOP:
                        break
                    got.append((ts, code, names.get(code, code)))
            return got

        a = collect(order[:TIER1_IND], skip=set(rep_hit))
        picks = [(t, c, n, 'A') for t, c, n in a]
        if a:
            tier_used.append(('A', '行业榜 1-10（当日上涨）', len(a)))
        # A 不足 20 只（部分行业无合格股）→ 用 11-50 名行业补足
        if len(picks) < TARGET_TOP:
            b = collect(order[TIER1_IND:TIER2_END], skip=set(rep_hit))
            if b:
                picks += [(t, c, n, 'B') for t, c, n in b]
                tier_used.append(('B', '行业榜 11-50（补足·当日上涨）', len(b)))

    # 第三档（用户口径）：
    #   行业层完全零标的 → 放弃行业，纯个股选 10 只
    #   行业层有标的但不足 20 只 → 纯个股补齐到 20 只（标明是补充）
    pure = not picks
    allok_all = rows[rows['ok']]
    allok = rows[rows['okup']].sort_values('ret60', ascending=False)
    n_cut_down = int(len(allok_all) - len(allok))
    if pure:
        picks = [(ts, '', '全市场', 'C') for ts in allok.index[:10]]
        if picks:
            tier_used.append(('C', '放弃行业条件·纯个股（当日上涨）', len(picks)))
    elif len(picks) < TARGET_TOP:
        have = set(p[0] for p in picks)
        add = []
        for ts in allok.index:
            if len(picks) + len(add) >= TARGET_TOP:
                break
            if ts in have:
                continue
            # 连续上榜被剔出 A 档的票仍可进 20 只名单，但标清楚来源
            tag = '连续上榜剔除' if ts in rep_hit else '纯个股补充'
            add.append((ts, '', tag, 'C'))
        if add:
            picks += add
            tier_used.append(('C', '纯个股补充至%d只（当日上涨）' % TARGET_TOP, len(add)))

    # 最终名单整体按「近 60 日涨幅」降序重排（大佬 2026-10-26：不按当日涨幅排）
    picks = sorted(picks, key=lambda x: -float(rows.at[x[0], 'ret60']))

    # 报告口径（2026-11-27 复审）：说「实际从 A/B 候选被剔除」的只数，而不是全市场命中数
    if cut_hit:
        log('[repeat] A/B 档候选实际剔除 %d 只：%s'
            % (len(cut_hit), '、'.join('%s(%d天)' % (rows.at[c, 'name'], s)
                                       for c, s in list(cut_hit.items())[:12])))
    elif rep_hit:
        log('[repeat] 连续上榜 %d 只均不在本轮 A/B 候选里，未发生实际剔除' % len(rep_hit))

    # ---- A 档连续上榜记账（供下一个交易日剔除用）；C 档补充不算「连续上榜」
    save_ab_hist(t_date, [p[0] for p in picks if p[3] == 'A'])

    # ---- 组装报告
    by_ts = {p[0]: p for p in picks}
    # 概念标签（大佬 2026-10-28 要求子图必须带；2026-11-27 才真正接上 detail）
    cmap = load_concepts()
    broad = None
    if cmap:
        _cnt = {}
        for _v in cmap.values():
            for _t in set(_v or []):
                _cnt[_t] = _cnt.get(_t, 0) + 1
        broad = {_t for _t, _n in _cnt.items() if _n > CONCEPT_BROAD_COVER}
    detail = []
    for ts, icode, iname, stage in picks:
        r = rows.loc[ts]
        detail.append({
            'ts_code': ts, 'name': r['name'], 'industry': iname, 'stage': stage,
            'concepts': concepts_of(ts, cmap=cmap, broad=broad),
            'close': float(r['real_close']), 'open': float(r['real_open']),
            'pct': float(r['pct_chg']), 'ret60': float(r['ret60']),
            'ma20': float(r['ma20']), 'ma40': float(r['ma40']),
            'ma80': float(r['ma80']), 'ma160': float(r['ma160']),
            'b1': float(r['b1']), 'b2': float(r['b2']), 'b3': float(r['b3']),
            'gap20': float((r['hfq'] - r['ma20']) / r['ma20']),
            'gap20o': float((r['hfq_open'] - r['ma20']) / r['ma20']),
        })

    ind_rows = []
    for code in (ind_pass.sort_values('pct_change', ascending=False).index.tolist()[:TIER2_END]
                 if not ind_pass.empty else []):
        r = ind_pass.loc[code]
        ind_rows.append({'code': code, 'name': names.get(code, code),
                         'pct': float(r['pct_change']), 'close': float(r['close']),
                         'ret60': float(r['ret60']),
                         'b1': float(r['b1']), 'b2': float(r['b2']), 'b3': float(r['b3'])})

    # ---- 龙虎榜层（当日行业涨幅前十 × 上榜自家成分股）
    lhb_rows, lhb_ok = lhb_section(ind, t_date)

    # ---- A/B 档合图（大佬 2026-10-28：符合条件的这俩档，报告要带图，每只一个子图）
    ab_chart = None
    try:
        import longxia_chart as LC
        ab_chart = LC.build(panel, detail, date=t_date,
                            out_path=os.path.join(CACHE, 'ab_chart_%s.png' % t_date))
    except Exception as e:
        log('[chart] 跳过：%s' % e)

    return {
        'date': t_date, 'picks': detail, 'industries': ind_rows, 'lhb': lhb_rows,
        'lhb_ok': bool(lhb_ok),
        'ind_total': int(len(ind)), 'ind_pass': int(len(ind_pass)),
        'n_panel': int(len(rows)), 'n_stock_pass': int(rows['ok'].sum()),
        'n_stock_up': int(len(allok)), 'n_cut_down': n_cut_down,
        'n_chips_veto': n_veto_all,
        'n_repeat_cut': len(cut_hit),
        'repeat_cut': [{'ts_code': c, 'name': str(rows.at[c, 'name']), 'days': s}
                       for c, s in cut_hit.items()],
        'n_repeat_seen': len(rep_hit),
        'repeat_seen': [{'ts_code': c, 'name': str(rows.at[c, 'name']), 'days': s}
                        for c, s in rep_hit.items()],
        'tiers': tier_used, 'pure': pure,
        'ab': [p for p in detail if p['stage'] in ('A', 'B')],
        'ab_chart': ab_chart,
    }


# ---------------------------------------------------------------- 报告渲染
SIGN = {True: '🌙', False: '⭐'}


def ab_label(ab):
    """档位标题文案：只有 A → 「A 档」；真出现 B 才写「A/B 档」。

    2026-11-27：行业池每天只有 5~6 个能过均线，11-50 名那段永远是空，
    B 档实际从未出现（历史 88 个 A、0 个 B），标题却一直写「A/B 档」误导。
    """
    stages = {str(p.get('stage')) for p in (ab or [])}
    return 'A/B 档' if 'B' in stages else 'A 档'


def render_text(res):
    d = res['date']
    fmt = '%s-%s-%s' % (d[:4], d[4:6], d[6:])
    L = []
    L.append('**最近交易日** %s｜全市场 %d 只｜个股条件全过 %d 只 → 其中当日上涨 %d 只（全部档位都取上涨的）｜申万二级行业 %d 个·均线全过 %d 个'
             % (fmt, res['n_panel'], res['n_stock_pass'], res.get('n_stock_up', 0), res['ind_total'], res['ind_pass']))
    if res.get('n_chips_veto'):
        L.append('🧮 股东户数过滤：剔除 **%d** 只（无披露 / 披露过期 / 高位户数发散）'
                 % res['n_chips_veto'])
    if res['pure']:
        if res['ind_pass']:
            msg = ('⚠️ 行业层有 **%d** 个行业均线合格，但旗下无个股满足个股条件 → 降级为纯个股口径（选 10 只，**只取当日上涨的**）'
                   % res['ind_pass'])
        else:
            msg = '⚠️ 行业层无合格标的（全市场调整期）→ 降级为纯个股口径（选 10 只，**只取当日上涨的**）'
        L.append(msg)
    if res['tiers']:
        L.append('路径：' + '；'.join('%s %s → %d 只' % (s, n, c) for s, n, c in res['tiers']))
    L.append('')
    if res['industries']:
        L.append('**一、行业层（开、收都站上MA20 且 MA20>40>80>160，按近60日涨幅排序）**')
        for i, r in enumerate(res['industries'][:20], 1):
            tag = '🌙' if i <= TIER1_IND else ''
            L.append('%d. %s｜60日 %+.1f%%｜当日 %+.2f%%｜乖离 20/40 %.1f%% · 40/80 %.1f%% · 80/160 %.1f%% %s'
                     % (i, r['name'], r['ret60'] * 100, r['pct'],
                        r['b1'] * 100, r['b2'] * 100, r['b3'] * 100, tag))
        L.append('')

    # ---- 二、龙虎榜 · 当日涨幅前十行业上榜股
    L.append('**二、龙虎榜 · 当日涨幅前十行业上榜股**')
    if not res.get('lhb_ok'):
        L.append('（今日龙虎榜数据未入库）')
    elif not res.get('lhb'):
        L.append('（无行业数据）')
    else:
        for i, s in enumerate(res['lhb'], 1):
            L.append('%d. %s %+.2f%%' % (i, s['ind'], s['ind_pct']))
            if not s['stocks']:
                L.append('   （无上榜）')
                continue
            for x in s['stocks'][:LHB_PER_IND]:
                L.append('   · %s %s｜%+.2f%%｜龙虎榜净买%+.2f亿｜机构%+.2f亿｜游资%+.2f亿'
                         % (x['name'], x['ts_code'], x['pct'], x['net'], x['inst'], x['hot']))
    L.append('')

    ab = res.get('ab') or []
    _num = 3
    _CN = {1: '一', 2: '二', 3: '三', 4: '四', 5: '五'}
    if ab:
        L.append('**%s、%s（行业条件满足）%d 只**（K线合图见附件 / 飞书卡片内嵌图）'
                 % (_CN[_num], ab_label(ab), len(ab)))
        if res.get('n_repeat_cut'):
            L.append('　（连续上榜剔除 %d 只：%s）'
                     % (res['n_repeat_cut'],
                        '、'.join('%s%dd' % (x['name'], x['days'])
                                  for x in res['repeat_cut'][:10])))
        _num += 1
        for i, p in enumerate(ab, 1):
            L.append('%d. **%s** %s｜%s档｜%s｜60日 %+.1f%%｜当日 %+.2f%%'
                     % (i, p['name'], p['ts_code'], p['stage'], p['industry'],
                        p['ret60'] * 100, p['pct']))
        L.append('')
    # ⚠️ 2026-11-04 修：这一段原在 `if ab:` 里面，没有 A/B 档的日子整段「四、入选个股」标题
    #    会被吞掉，个股裸奔；编号也改成动态（没 A/B 档时它就是「三、」）。
    L.append('**%s、入选个股（%d 只）**' % (_CN[_num], len(res['picks'])))
    if res.get('n_chips_veto'):
        L.append('　（已剔除股东户数不合格 %d 只：无披露 / 披露过期 / 高位户数发散）' % res['n_chips_veto'])
    if not res['picks']:
        L.append('⚠️ 当日无任何个股同时满足：开盘价和收盘价都站上MA20 · MA20>MA40>MA80>MA160 · 相邻均线乖离均<20%。')
        L.append('这类行情通常出现在快速拉升高潮段（均线过度发散），宁可空仓也不硬选。')
    for i, p in enumerate(res['picks'], 1):
        L.append('%d. **%s** %s｜%s档｜60日 %+.1f%%｜当日 %+.2f%%｜开%.2f 收%.2f｜距MA20 开%+.2f%% 收%+.2f%%｜乖离 20/40 %.1f%% · 40/80 %.1f%% · 80/160 %.1f%%｜%s'
                 % (i, p['name'], p['ts_code'], p['stage'], p['ret60'] * 100, p['pct'],
                    p['open'], p['close'], p['gap20o'] * 100, p['gap20'] * 100,
                    p['b1'] * 100, p['b2'] * 100, p['b3'] * 100, p['industry']))
    L.append('')
    L.append('**名单**：' + '、'.join(p['name'] for p in res['picks']))
    return '\n'.join(L)


def render_card(res, img_key=None):
    d = res['date']
    fmt = '%s-%s-%s' % (d[:4], d[4:6], d[6:])
    title = '🦞 龙虾缠论 · 龙虎榜选股 · %s' % fmt
    els = [{'tag': 'markdown', 'content': '📎 来自 OpenClaw.Deepseek'}]
    els.append({'tag': 'markdown', 'content':
                '**最近交易日 %s**｜全市场 %d 只｜个股条件全过 **%d** 只（当日上涨 **%d** 只）\n申万二级行业 %d 个 · 行业均线全过 **%d** 个'
                % (fmt, res['n_panel'], res['n_stock_pass'], res.get('n_stock_up', 0), res['ind_total'], res['ind_pass'])})
    if res['pure']:
        if res['ind_pass']:
            txt = ('⚠️ **行业层有 %d 个行业均线合格，但旗下无个股满足个股条件**\n→ 降级为纯个股口径（选 10 只，**只取当日上涨的**）'
                   % res['ind_pass'])
        else:
            txt = '⚠️ **行业层无合格标的**（全市场调整期）\n→ 降级为纯个股口径（选 10 只，**只取当日上涨的**）'
        els.append({'tag': 'markdown', 'content': txt})
    if res['tiers']:
        els.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content':
                    '路径：' + '；'.join('%s %s → %d 只' % (t[0], t[1], t[2]) for t in res['tiers'])}]})
    els.append({'tag': 'hr'})

    if res['industries']:
        els.append({'tag': 'markdown', 'content': '**一、行业层通过名单**（开、收都站上MA20 · MA20>40>80>160 · 按近60日涨幅排序）'})
        lines = []
        for i, r in enumerate(res['industries'][:15], 1):
            mark = '🌙' if i <= TIER1_IND else ''
            lines.append('%d. %s｜60日 %+.1f%%｜当日 %+.2f%% %s'
                         % (i, r['name'], r['ret60'] * 100, r['pct'], mark))
        els.append({'tag': 'markdown', 'content': '\n'.join(lines)})
        els.append({'tag': 'hr'})

    # ---- 二、龙虎榜 · 当日涨幅前十行业上榜股
    els.append({'tag': 'markdown', 'content': '**二、龙虎榜 · 当日涨幅前十行业上榜股**'})
    if not res.get('lhb_ok'):
        els.append({'tag': 'markdown', 'content': '（今日龙虎榜数据未入库）'})
    elif not res.get('lhb'):
        els.append({'tag': 'markdown', 'content': '（无行业数据）'})
    else:
        lines = []
        for i, s in enumerate(res['lhb'], 1):
            lines.append('**%d. %s %+.2f%%**' % (i, s['ind'], s['ind_pct']))
            if not s['stocks']:
                lines.append('　（无上榜）')
                continue
            for x in s['stocks'][:LHB_CARD_PER_IND]:
                lines.append('　%s %s｜%+.2f%%｜净买%+.2f亿｜机构%+.2f｜游资%+.2f'
                             % (x['name'], x['ts_code'], x['pct'], x['net'],
                                x['inst'], x['hot']))
        els.append({'tag': 'markdown', 'content': '\n'.join(lines)})
    els.append({'tag': 'hr'})

    ab = res.get('ab') or []
    if ab:
        els.append({'tag': 'markdown', 'content':
                    '**三、%s（行业条件满足）%d 只** — 每只一个子图见下图'
                    % (ab_label(ab), len(ab))})
        if res.get('n_repeat_cut'):
            els.append({'tag': 'markdown', 'content': '　（连续上榜剔除 %d 只：%s）'
                        % (res['n_repeat_cut'],
                           '、'.join('%s%dd' % (x['name'], x['days'])
                                     for x in res['repeat_cut'][:10]))})
        lines = []
        for i, p in enumerate(ab, 1):
            lines.append('%d. **%s**（%s）｜%s档｜**60日 %+.1f%%**｜当日 %+.2f%%｜%s'
                         % (i, p['name'], p['ts_code'], p['stage'],
                            p['ret60'] * 100, p['pct'], p['industry']))
        els.append({'tag': 'markdown', 'content': '\n'.join(lines)})
        if img_key:
            els.append({'tag': 'img', 'img_key': img_key,
                        'alt': {'tag': 'plain_text', 'content': 'A档K线合图'},
                        'mode': 'fit_horizontal', 'preview': True})
        els.append({'tag': 'hr'})
    els.append({'tag': 'markdown', 'content': '**%s、入选个股 %d 只**'
                % ('四' if ab else '三', len(res['picks']))})
    if not res['picks']:
        els.append({'tag': 'markdown', 'content':
                    '⚠️ **今日无符合条件个股**（开、收都站上MA20 · MA20>MA40>MA80>MA160 · 相邻均线乖离均<20%）\n快速拉升高潮段均线过度发散，空仓等待。'})
    rows = []
    for i, p in enumerate(res['picks'], 1):
        rows.append('%d. **%s**（%s）｜%s档｜**60日 %+.1f%%**｜当日 %+.2f%%｜开 %.2f 收 **%.2f**｜距MA20 开 %+.2f%% 收 %+.2f%%｜乖离 20/40 %.1f%% · 40/80 %.1f%% · 80/160 %.1f%%｜%s'
                    % (i, p['name'], p['ts_code'], p['stage'], p['ret60'] * 100, p['pct'],
                       p['open'], p['close'], p['gap20o'] * 100, p['gap20'] * 100,
                       p['b1'] * 100, p['b2'] * 100, p['b3'] * 100, p['industry']))
    els.append({'tag': 'markdown', 'content': '\n'.join(rows)})
    els.append({'tag': 'hr'})
    els.append({'tag': 'markdown', 'content':
                '**🦞 名单**：' + '、'.join(p['name'] for p in res['picks'])})
    els.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content':
                '口径：开、收都站上MA20 · MA20>MA40>MA80>MA160 · 相邻乖离 20↔40/40↔80/80↔160 均<20% · 剔ST/次新 · 后复权 · **排序＝近60日涨幅降序**'}]})
    return {'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': title},
                       'template': 'orange'},
            'elements': els}


def upload_card_image(path):
    """上传图片到飞书拿 img_key（供卡片内嵌）。失败返回 None。"""
    try:
        import feishu_card as FC
        return FC.upload_image(FC.token(), path)
    except Exception as e:
        log('上传图片失败: %s' % e)
        return None


def send_feishu(card, chat=FEISHU_DM):
    import urllib.request
    cfg = json.load(open(os.path.expanduser("~/.openclaw/openclaw.json"), encoding='utf-8'))['channels']['feishu']
    body = json.dumps({'app_id': cfg['appId'], 'app_secret': cfg['appSecret']}).encode()
    req = urllib.request.Request(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        data=body, headers={'Content-Type': 'application/json'})
    tok = json.loads(urllib.request.urlopen(req, timeout=20).read())['tenant_access_token']
    payload = json.dumps({'receive_id': chat, 'msg_type': 'interactive',
                          'content': json.dumps(card, ensure_ascii=False)},
                         ensure_ascii=False).encode()
    req = urllib.request.Request(
        'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
        data=payload, headers={'Content-Type': 'application/json',
                               'Authorization': 'Bearer ' + tok})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    log('飞书: code=%s msg=%s id=%s' % (r.get('code'), r.get('msg'),
                                        (r.get('data') or {}).get('message_id')))
    return r.get('code') == 0


def send_qq(text, target=QQ_TARGET):
    import subprocess
    p = subprocess.run(['openclaw', 'message', 'send', '--channel', 'qqbot',
                        '--target', target, '--message', text, '--json'],
                       capture_output=True, text=True, timeout=180)
    out = (p.stdout or '')
    mid = ''
    i = out.find('{')
    if i >= 0:
        try:
            d = json.loads(out[i:])
            r = (d.get('payload') or {}).get('result') or d.get('result') or {}
            mid = r.get('messageId') or ''
        except Exception:
            pass
    log('QQ: rc=%d messageId=%s' % (p.returncode, mid or '(空!)'))


def already_sent(date, channel):
    p = os.path.join(CACHE, 'sent_%s.json' % date)
    if not os.path.exists(p):
        return False
    try:
        m = json.load(open(p, encoding='utf-8'))
    except Exception:
        return False
    return bool(m.get(channel))


def mark_sent(date, channel):
    """写发送记账。**只有正式定时任务（LX_FORMAL=1）才写**。

    手动跑（测试 / 补发）不写记账，避免第二天早上正式任务误判「已发送」而漏发。
    """
    if os.environ.get('LX_FORMAL') != '1':
        log('记账：非正式任务（LX_FORMAL!=1），跳过写 sent_%s.json' % date)
        return
    p = os.path.join(CACHE, 'sent_%s.json' % date)
    m = {}
    if os.path.exists(p):
        try:
            m = json.load(open(p, encoding='utf-8'))
        except Exception:
            m = {}
    m[channel] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    json.dump(m, open(p, 'w', encoding='utf-8'), ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--no-send', action='store_true')
    ap.add_argument('--qq', action='store_true', help='额外推 QQ 文字版')
    ap.add_argument('--qq-only', action='store_true')
    ap.add_argument('--force', action='store_true', help='忽略当日已发送记录，强制重发')
    a = ap.parse_args()
    res = run(a.date)
    txt = render_text(res)
    out = os.path.join(CACHE, 'report_%s.md' % res['date'])
    open(out, 'w', encoding='utf-8').write(txt)
    log(txt)
    log('\n存档: %s' % out)

    # ── 统一契约：写成 .cache/picks/longxia.json，供显示层「自选股」按算法分块渲染 ──
    # 大佬 2026-11-26：各算法自选股要在一个 tab 里结构整齐，不再各写各的。
    try:
        _bin = os.path.join(os.path.dirname(os.path.abspath(__file__)))
        if _bin not in sys.path:
            sys.path.insert(0, _bin)
        import pick_pool as PP
        rows = []
        for p in (res.get('picks') or []):
            rows.append(PP.pick_row(
                p.get('ts_code'), p.get('name'),
                industry=p.get('industry') or '',
                # ret60 是比例（0.97=97%），pct_chg 已经是百分数（2.77=2.77%）—— 别再乘
                reason='%s档 · 60日 %+.1f%% · 当日 %+.2f%%' % (
                    p.get('stage') or '-', (p.get('ret60') or 0) * 100,
                    p.get('pct') or 0),
                score=round((p.get('ret60') or 0) * 100, 2),
                price=p.get('close'),
                tags=['龙虾缠论', '%s档' % (p.get('stage') or '-')]))
        n_ind = res.get('ind_pass') or 0
        note = '行业过均线 %d 个｜个股条件全过 %d 只｜入选 %d 只%s' % (
            n_ind, res.get('n_stock_pass') or 0, len(rows),
            '（放弃行业条件·纯个股）' if res.get('pure') else '')
        PP.write_picks_fallback('longxia', '🦞 龙虾缠论选股',
                                res['date'].replace('-', ''), rows,
                                cadence='daily', note=note, allow_empty=True)
    except Exception as e:
        log('[picks] 写统一契约失败：%s' % str(e)[:140])

    # ── 飞书交互卡片也落一份，供私有页「自选股」子页面原样嵌入 ──
    # 放在 no_send 之前：--no-send 重建页面时也要有卡片可看
    try:
        _cd = os.path.join(PICKS_DIR, 'cards')
        os.makedirs(_cd, exist_ok=True)
        json.dump({'msg_type': 'interactive', 'card': render_card(res, img_key=None)},
                  open(os.path.join(_cd, 'longxia.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        log('[card] 落盘 %s' % os.path.join(_cd, 'longxia.json'))
    except Exception as _e:
        log('[card] 落盘失败 %s' % str(_e)[:100])

    if a.no_send:
        return
    # 去重键用「今天」（发送日），不是交易日：
    # 早盘 08:00 用的是上一交易日数据，若按交易日记账会误判成「已发」而漏发
    today_key = datetime.date.today().strftime('%Y%m%d')
    if not a.qq_only:
        if already_sent(today_key, 'feishu') and not a.force:
            log('飞书：%s 今日已发送，跳过（要重发加 --force）' % today_key)
        else:
            try:
                key = None
                if res.get('ab_chart') and os.path.exists(res['ab_chart']):
                    key = upload_card_image(res['ab_chart'])
                if send_feishu(render_card(res, img_key=key)):
                    mark_sent(today_key, 'feishu')
            except Exception as e:
                log('飞书失败: %s' % e)
    if a.qq or a.qq_only:
        if already_sent(today_key, 'qq') and not a.force:
            log('QQ：%s 今日已发送，跳过（要重发加 --force）' % today_key)
        else:
            try:
                head = '🦞 龙虾缠论 · 龙虎榜选股 · %s\n\n' % res['date']
                body = '\n'.join(l.replace('**', '') for l in txt.split('\n'))
                send_qq(head + body)
                # A/B 档合图额外推一张图（QQ 受信媒体目录由 push_image 处理）
                if res.get('ab_chart') and os.path.exists(res['ab_chart']):
                    for _tg in QQ_TARGETS_IMG:
                        try:
                            import push_image as PI
                            PI.QQ_TARGET = _tg
                            PI.send_qq(res['ab_chart'],
                                       '🦞 %s K线合图 · %s' % (ab_label(res.get('ab')), res['date']))
                        except Exception as e:
                            log('QQ 图失败(%s): %s' % (_tg, e))
                mark_sent(today_key, 'qq')
            except Exception as e:
                log('QQ 失败: %s' % e)


if __name__ == '__main__':
    main()
