#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📌 夜间日报 · 个股备选池 / 弃选池（大佬 2026-11-02 需求）
=====================================================================
规则（大佬口径）：
  · 汇总当日群里所有报告提到的个股，逐票计「看好 / 不看好」
  · 同一只股票，看好占比 ≥ 60% → 进 **备选池**
  · 同一只股票，不看好占比 ≥ 60% → 进 **弃选池**
  · 两个池各取前 50 名
  · **重点统计两池的行业 + 概念分布**，程序画图发 QQ 私聊

实现：
  1. 拉飞书群当日 15:00 后消息（复用 strategies/jobs/fetch-feishu-group-msgs.py）
  2. 按消息块切分 → 每块=一份报告=若干票；股票代码必须在 stock_basic 里（自动过滤 ETF/指数）
  3. 每行独立判多空（关键词），无行级信号则继承最近的章节标题极性
  4. 同一报告内同一股票多行 → 净票（+1/-1/0），一份报告只投一票
  5. 行业取 stock_basic.industry；概念取 .cache/concepts/stock_concepts.json（剔除样本股/融资融券类噪音）
  6. matplotlib 画 2×2：备选行业 / 备选概念 / 弃选行业 / 弃选概念
  7. 推送：push_image.py（QQ 私聊 + 飞书私聊）+ 文字清单

用法：
  python3 strategies/stock_pool.py [--date 2026-10-30] [--top 50] [--no-send] [--qq-only]
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---

# ---- 统一配置（DataHub config）：路径/凭据一律从 DataHub 配置取，env 仅供 loader 内部覆盖 ----
def _bootstrap_hub_root():
    """引导定位 data_hub：env DATAHUB_ROOT → ~/.config/precious-task/libroots.json → 向上探测。"""
    import json as _j, os as _o
    p = (_o.environ.get("DATAHUB_ROOT") or "").strip()
    if p and _o.path.isdir(_o.path.join(p, "data_hub")):
        return p
    try:
        with open(_o.path.expanduser("~/.config/precious-task/libroots.json"), encoding="utf-8") as _f:
            _d = _j.load(_f) or {}
        for _k in ("datahub", "datahub_root", "DATAHUB_ROOT"):
            _v = _d.get(_k)
            if _v and _o.path.isdir(_o.path.join(str(_v), "data_hub")):
                return str(_v)
    except Exception:
        pass
    _d0 = _o.path.dirname(_o.path.abspath(__file__))
    for _ in range(6):
        if _o.path.isdir(_o.path.join(_d0, "data_hub")):
            return _d0
        _d0 = _o.path.dirname(_d0)
    return ""


def _dh_cfg(_k, _d=""):
    try:
        from data_hub import cfg as _c
        return _c(_k, _d) or _d
    except Exception:
        return _d


def _dh_secret(_n, _d=""):
    try:
        from data_hub import get_secret as _g
        return _g(_n, _d) or _d
    except Exception:
        return _d
# -----------------------------------------------------------------------------------

try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, re, sys, json, subprocess, argparse, datetime
from collections import defaultdict, Counter

def _resolve_base():
    """数据根（2026-11-28 去 OpenClaw 硬编码路径）。

    顺序：DataHub paths.stock_pool_ws（loader 内部处理 env 覆盖）
    > 含 .cache/market/ 的最近祖先 > 统一数据根（_paths.workspace）。
    都没有就报错，避免拼出相对路径静默读错目录。
    """
    env = str(_dh_cfg('STOCK_POOL_WS') or '').strip()
    if env and os.path.isdir(env):
        return env
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, '.cache', 'market')):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    try:
        from _paths import workspace as _ws_fn
        w = _ws_fn()
        if w and os.path.isdir(w):
            return w
    except Exception:
        pass
    raise RuntimeError('未配置数据根：请设环境变量 STOCK_POOL_WS，'
                       '或在 config.local.json 配置 paths.stock_pool_ws')


BASE = _resolve_base()
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# 2026-11-28：fetch 脚本已随仓库提供，不再从 OpenClaw 工作区 optstack/jobs 取。
FETCH = _dh_cfg('SP_FETCH') or os.path.join(_HERE, 'jobs', 'fetch-feishu-group-msgs.py')
# 图片推送脚本随仓库提供（misc/core/push_image.py），不再指向 OpenClaw 工作区。
PUSH_IMAGE_PY = _dh_cfg('PUSH_IMAGE_PY') or os.path.join(
    os.path.dirname(_HERE), 'misc', 'core', 'push_image.py')
CHAT = _dh_secret('SP_CHAT_ID', '') or _dh_secret('FEISHU_CHAT_STOCK', '') or _dh_secret('FEISHU_CHAT_ID', '')
QQ_TARGET = _dh_secret('SP_QQ_TARGET', '') or _dh_secret('QQ_DEFAULT_TARGET', '')

# ---------------- 多空关键词 ----------------
BULL_KW = ['看多', '看好', '推荐', '买入', '增持', '机会', '领涨', '净流入', '流入',
           '买点', '可以跟', '突破', '强势', '乐观', '上调', '超配', '加分',
           '评分卡top', '共振', '高股息', '涨停', '领涨半区', '流入龙头']
BEAR_KW = ['看空', '看淡', '不看好', '回避', '减仓', '卖出', '领跌', '净流出', '流出', '风险',
           '剔除', '谨慎', '弱势', '跌破', '低配', '抛售', '止损', '杀跌', '下跌超', '跌超',
           '风险概念', '风险top', '领跌半区']
# 中性标题（含这些词的标题不要改变章节极性）
NEUTRAL_HDR = ('龙头股', '指标解释', '数据状态', '基线组合统计')

# 这些概念的标签信息量太低（行政/股东/榜单/财报类），统计时直接剔除
# 2026-11-02 大佬：「宽泛的概念去掉，比如定增、TOP100、美丽中国这些」
LOW_VALUE_TAGS = (
    '样本股', '成份股', '成分股', '融资融券', '转融券', '标普', 'MSCI', '富时',
    '沪股通', '深股通', '证金持股', '预盈预增', '预亏预减', '同花顺', '中特估',
    '国企改革', '回购增持', '高股息', '定增', '增发', '可转债', '股权转让', '举牌',
    '预增', '预减', '季报', '年报', '中报', '专精特新', '一带一路', '共同富裕', '乡村振兴',
    '西部大开发', '粤港澳', '美丽中国', '雄安', '长三角', '京津冀', '自贸区', '海南',
    '破净', '低价股', '壳资源', 'ST板块', '参股', '控股', '分拆上市',
    '人民币贬值受益', '融资标的',
)
# 覆盖股票数超过这个值（≈全市场 7%）→ 视为「宽泛概念」不入统计
BROAD_COVER = int(os.environ.get('SP_BROAD_COVER', '400'))
# 榜单类概念（中国AI 50 / TOP100 / 龙头50 …）一律不算概念
LIST_TAG = re.compile(r'(TOP\s*\d+|Top\s*\d+|漂亮\s*\d+|\D\s?(50|100|300)\s*$)', re.I)
_BROAD = None


# ---------------- 基础数据 ----------------
def load_stock_basic():
    """code6 -> (name, industry)"""
    import glob
    cands = sorted(glob.glob(os.path.join(BASE, '.cache/market/*/stock_basic.csv')))
    if not cands:
        return {}
    out = {}
    import csv
    with open(cands[-1], encoding='utf-8') as f:
        for row in csv.DictReader(f):
            ts = (row.get('ts_code') or '').strip()
            if len(ts) < 6:
                continue
            out[ts[:6]] = ((row.get('name') or '').strip(), (row.get('industry') or '').strip())
    return out


def load_concepts():
    fp = os.path.join(BASE, '.cache/concepts/stock_concepts.json')
    if not os.path.exists(fp):
        return {}
    try:
        return json.load(open(fp, encoding='utf-8')).get('map', {})
    except Exception:
        return {}


def _broad_set(cmap):
    """覆盖股票数 > BROAD_COVER 的概念 = 宽泛概念，统计时剔除"""
    global _BROAD
    if _BROAD is not None:
        return _BROAD
    cnt = Counter()
    for tags in cmap.values():
        for t in set(tags or []):
            cnt[t] += 1
    _BROAD = {t for t, n in cnt.items() if n > BROAD_COVER}
    print(f'[broad] 宽泛概念 {len(_BROAD)} 个（覆盖>{BROAD_COVER}只）', file=sys.stderr)
    return _BROAD


def concepts_for(code6, cmap, limit=2):
    cands = []
    for suf in ('.SH', '.SZ', '.BJ'):
        v = cmap.get(code6 + suf)
        if v:
            cands = v
            break
    broad = _broad_set(cmap)
    keep = [t for t in cands
            if t and t not in broad and not any(k in t for k in LOW_VALUE_TAGS)
            and not LIST_TAG.search(t)]
    keep.sort(key=lambda t: (len(t) > 12, t))
    return keep[:limit]


def concept_tags_all(code6, cmap):
    """该股票所有有效概念（已剔宽泛/榜单，不截断）"""
    cands = []
    for suf in ('.SH', '.SZ', '.BJ'):
        v = cmap.get(code6 + suf)
        if v:
            cands = v
            break
    broad = _broad_set(cmap)
    seen, out = set(), []
    for t in cands:
        if not t or t in seen:
            continue
        if t in broad or LIST_TAG.search(t) or any(k in t for k in LOW_VALUE_TAGS):
            continue
        seen.add(t)
        out.append(t)
    return out


def weighted_concepts(rows, cmap, min_stocks=2):
    """大佬口径（2026-11-02）：概念按「池内股票数」加权。
    一只股有很多概念时，优先选「池子里最多股票共有」的那个；只有单股才有的概念被自然压下去。
    返回 Counter(tag -> 池内提及它的股票数)。默认只保留 ≥min_stocks 只股票共有的（去掉噪声）；
    若一个都没有，才退回全部。
    """
    cnt = Counter()
    for r in rows:
        for t in concept_tags_all(r['code'], cmap):
            cnt[t] += 1
    # 只保留池内 ≥min_stocks 只股票共有的概念（这就是「按股票数加权」的含义）
    return Counter({t: n for t, n in cnt.items() if n >= min_stocks})


# ---------------- 消息解析 ----------------
def split_blocks(raw_text):
    """返回 [(time, sender, mtype, body), ...]"""
    txt = raw_text
    parts = re.split(r'\n--- \[(\d\d:\d\d)\] ([^ ]+) \(([a-z]+)\) ---\n', txt)
    out = []
    i = 1
    while i + 3 <= len(parts):
        out.append((parts[i], parts[i + 1], parts[i + 2], parts[i + 3]))
        i += 4
    return out


def is_header(line):
    s = line.strip()
    if not s or len(s) > 60:
        return False
    if any(k in s for k in NEUTRAL_HDR):
        return True
    if re.match(r'^[一二三四五六七八九十]+[、\.]', s):
        return True
    if s.startswith(('TOP', 'Top', '📋', '📊', '💰', '🔥', '⚠️', '✅', '🔴', '🟢', '📈', '📉', '⭐', '🎯')):
        return True
    return False


def polarity(line, polar_only=False):
    """polar_only=True → 只看明确多空词（不含 领涨/流入 这类中性偏强的词）"""
    s = line.lower()
    bull = BULL_KW if not polar_only else ('看多', '看好', '推荐', '买入', '增持', '机会')
    bear = BEAR_KW if not polar_only else ('看空', '看淡', '不看好', '回避', '减仓', '卖出', '风险')
    b = sum(1 for k in bull if k.lower() in s)
    r = sum(1 for k in bear if k.lower() in s)
    if b > r:
        return 1
    if r > b:
        return -1
    return 0


# 「概念名: 名字(代码)」行 → 概念名
CONCEPT_LINE = re.compile(r'^\s*([^\s:：|]{2,20})\s*[:：]\s*.*\(\d{6}\)')
# 「1.概念名」这类概念清单条目
CONCEPT_ENTRY = re.compile(r'^\s*\d{1,2}[\.、]\s*([^\s|]{2,20})\s*$')


def extract_votes(raw_text, basic):
    """→ votes[code6] = {'bull': set(block_idx), 'bear': set(block_idx), 'bmen'/'rmen': int, ...}

    两个粒度同时统计（大佬 2026-11-02）：
      · bull/bear  = 提及该票的**报告数**（一报告一票，算占比用）
      · bmen/rmen  = **提及次数**（同一报告里出现多行也算多次，算门槛用）
    """
    votes = defaultdict(lambda: {'bull': set(), 'bear': set(), 'lines': [], 'names': Counter(),
                                 'bmen': 0, 'rmen': 0})
    for bi, (t, sender, mtype, body) in enumerate(split_blocks(raw_text)):
        if mtype in ('image', 'file'):
            continue
        sec_pol = 0
        cpt = {}            # 概念名 -> 极性（本报告内）
        risk_hd = opp_hd = False   # 当前所处清单是「风险概念」还是「机会概念」
        per_stock = defaultdict(int)
        per_name = defaultdict(Counter)
        mcnt = Counter()    # 本报告内每票的提及次数（含极性）
        for line in body.split('\n'):
            s = line.strip()
            if not s or len(s) < 4:
                continue
            if is_header(s):
                # header 极性别序：明确看多/推荐优先，再看风险/剔除
                if any(k in s for k in ('推荐', '看多', '看好', '机会', '买点')):
                    sec_pol = 1
                else:
                    lp = polarity(s)
                    if lp:
                        sec_pol = lp
                risk_hd = ('风险' in s and ('概念' in s or 'Top' in s or '板块' in s))
                opp_hd = (('机会' in s or '看多' in s) and ('概念' in s or 'Top' in s or '板块' in s))
                m_ent = None
            else:
                # 概念清单条目（在风险/机会清单下）→ 记概念极性
                m_ent = CONCEPT_ENTRY.match(s)
                if m_ent and (risk_hd or opp_hd):
                    cpt[m_ent.group(1)] = -1 if risk_hd else 1
                    continue
            # 排除 000985.SH 这类指数代码（后缀带点）与长数字串
            codes = set(re.findall(r'(?<!\d)(\d{6})(?![\d.])', s))
            codes = {c for c in codes if c in basic}
            if not codes:
                continue
            # 优先级：行内明确多空词 > 概念清单极性 > 章节极性
            p = polarity(s, polar_only=True) or polarity(s)
            m2 = CONCEPT_LINE.match(s)
            if not p and m2 and m2.group(1) in cpt:
                p = cpt[m2.group(1)]
            if not p:
                p = sec_pol
            if not p:
                continue
            for c in codes:
                per_stock[c] += p
                per_name[c][basic[c][0]] += 1
                mcnt[c] += 1
        for c, net in per_stock.items():
            st = 1 if net > 0 else (-1 if net < 0 else 0)
            if st > 0:
                votes[c]['bull'].add(bi)
                votes[c]['bmen'] += mcnt[c]
            elif st < 0:
                votes[c]['bear'].add(bi)
                votes[c]['rmen'] += mcnt[c]
            votes[c]['lines'].append((bi, t, st, s[:60]))
            for nm, n in per_name[c].items():
                votes[c]['names'][nm] += n
    return votes


def build_pools(votes, basic, min_bull=4, min_bear=3, thr=0.6):
    """大佬口径（2026-11-02）：
      门槛看「提及次数」：看好提及 ≥ min_bull(4) → 备选；看空提及 ≥ min_bear(3) → 弃选
      占比看「报告数」：看好报告数 / 总报告数 ≥ thr(0.6)
      额外保险：至少来自 2 份不同报告（防止单份报告里重复列行把次数刷高）
      某池为空时自动降一级门槛重试（打印 note，图表也会标注）
    """
    rows = []
    for c, v in votes.items():
        nb, nr = len(v['bull']), len(v['bear'])
        mb, mr = v['bmen'], v['rmen']
        tot = nb + nr
        if tot == 0:
            continue
        name = basic[c][0]
        if v['names']:
            name = v['names'].most_common(1)[0][0] or name
        bull_ratio = nb / tot
        rows.append({
            'code': c, 'name': name, 'industry': basic[c][1] or '未分类',
            'bull': nb, 'bear': nr, 'total': tot, 'bmen': mb, 'rmen': mr,
            'bull_ratio': round(bull_ratio, 3), 'bear_ratio': round(1 - bull_ratio, 3),
        })

    def _sel(min_b, min_r):
        # 备选：提及≥min_b 且 至少来自 2 份不同报告（防单份报告重复列行刷次数）
        # 弃选：提及≥min_r（看空类清单本来就稀疏，同一份报告在不同风险概念里重复点名 = 多个独立信号，计数不重复去重）
        pk = [r for r in rows if r['bmen'] >= min_b and r['bull'] >= 2 and r['bull_ratio'] >= thr]
        av = [r for r in rows if r['rmen'] >= min_r and r['bear'] >= 1 and r['bear_ratio'] >= thr]
        pk.sort(key=lambda r: (r['bull_ratio'], r['bmen'], r['bull']), reverse=True)
        av.sort(key=lambda r: (r['bear_ratio'], r['rmen'], r['bear']), reverse=True)
        return pk, av

    pick, avoid = _sel(min_bull, min_bear)
    note = ''
    if not avoid and min_bear > 1:
        for k in range(min_bear - 1, 0, -1):
            _, avoid = _sel(min_bull, k)
            if avoid:
                note = f'看空提及≥{min_bear}次今日无票，已放宽到≥{k}次'
                break
    if not pick and min_bull > 1:
        for k in range(min_bull - 1, 0, -1):
            pick, _ = _sel(k, min_bear)
            if pick:
                note = (note + '；' if note else '') + f'看好提及≥{min_bull}次今日无票，已放宽到≥{k}次'
                break
    return pick[:50], avoid[:50], rows, note


# ---------------- 画图 ----------------
def make_chart(pick, avoid, cmap, out_png, date, topo_pick=None, topo_avoid=None, struct=None):
    """struct: {label: metrics}（来自 bin/struct_tag.py，行业/概念组的聚集-扩散判定）"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.font_manager import FontProperties
    FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
    FONT_B = '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
    for f in (FONT, FONT_B):
        try:
            font_manager.fontManager.addfont(f)
        except Exception:
            pass
    FP = FontProperties(fname=FONT)
    FPB = FontProperties(fname=FONT_B)
    plt.rcParams['axes.unicode_minus'] = False

    def agg(rows, key):
        if key == 'industry':
            return Counter(r['industry'] for r in rows)
        # 概念：按池内股票数加权排序（多股共有的自然排前面，单股的在最后）
        return weighted_concepts(rows, cmap, min_stocks=1)

    fig = plt.figure(figsize=(19, 13), dpi=130)
    gs = fig.add_gridspec(2, 3, width_ratios=[3, 3, 2.1], wspace=0.30, hspace=0.26)
    axes = [[fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[0, 2])],
            [fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[1, 1]), fig.add_subplot(gs[1, 2])]]
    specs = [
        (axes[0][0], pick, 'industry', f'看多·备选池 — 行业分布（{len(pick)} 只）', '#2e8b57'),
        (axes[0][1], pick, 'concept', f'看多·备选池 — 概念分布（{len(pick)} 只）', '#3aa76d'),
        (axes[1][0], avoid, 'industry', f'看空·弃选池 — 行业分布（{len(avoid)} 只）', '#b03a2e'),
        (axes[1][1], avoid, 'concept', f'看空·弃选池 — 概念分布（{len(avoid)} 只）', '#c05a4e'),
    ]
    for ax, rows, key, title, color in specs:
        cnt = agg(rows, key)
        ranked = cnt.most_common(20)
        if key == 'concept':
            # 概念：≥2 只股票共有的优先（最多 12 根），单股概念最多附 3 根
            shared = [(k, v) for k, v in ranked if v >= 2][:12]
            singles = [(k, v) for k, v in ranked if v < 2][:3]
            top = shared + singles if shared else ranked[:6]
        else:
            top = ranked[:12]
        ax.set_title(title, fontproperties=FPB, fontsize=17, pad=12)
        if not top:
            ax.text(0.5, 0.5, '（无数据）', ha='center', va='center',
                    fontproperties=FP, fontsize=18, color='#888')
            ax.axis('off')
            continue
        labels = [k for k, _ in top][::-1]
        vals = [v for _, v in top][::-1]
        # 只有 1 只股票持有的概念用浅色，一眼区分「共有」与「单股」
        cols = [color if v >= 2 else '#c9c9c9' for v in vals]
        ax.barh(range(len(vals)), vals, color=cols, alpha=0.9, height=0.62)
        ax.set_yticks(range(len(vals)))
        # 标签后缀：涨·聚集 / 跌·扩散（程序判定，0 token）
        lab2 = []
        for k in labels:
            m = (struct or {}).get(k)
            lab2.append(f"{k}  {m['dir']}·{m['state']}" if m else k)
        ax.set_yticklabels(lab2, fontproperties=FP, fontsize=12.5)
        ax.set_xlabel('只数', fontproperties=FP, fontsize=12)
        ax.tick_params(axis='x', labelsize=11)
        ax.grid(axis='x', ls='--', alpha=0.3)
        mx = max(vals)
        for i, v in enumerate(vals):
            ax.text(v + mx * 0.015, i, str(v), va='center', fontproperties=FP, fontsize=12)
        ax.set_xlim(0, mx * 1.16)
        if key in ('industry', 'concept'):
            # 右侧标识列：↑涨/↓跌 + 聚集/扩散（聚集=橙红、扩散=蓝）
            xmax = mx * 1.16
            for i, k in enumerate(labels):
                m = (struct or {}).get(k)
                if not m:
                    continue
                arrow = '↑' if m['dir'] == '涨' else '↓'
                cc = '#d9534f' if m['state'] == '聚集' else '#3f7fbf'
                ax.text(xmax * 0.98, i, f"{arrow}{m['state']}", ha='right', va='center',
                        fontproperties=FPB, fontsize=12, color=cc)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)

    # ── 右列：拓扑结构（TDA 吸收比率/H0 + UMAP 离散度）──
    def draw_topo(ax, t, label, upcolor, dncolor):
        ax.set_title(f'{label} — 资金结构（TDA/UMAP）', fontproperties=FPB, fontsize=16, pad=12)
        if not t or not t.get('ok'):
            ax.text(0.5, 0.5, '拓扑不可算\n' + ((t or {}).get('why') or '（无数据）'),
                    ha='center', va='center', fontproperties=FP, fontsize=14, color='#888')
            ax.axis('off')
            return
        p = t['pool']
        ss = p['series']
        xs = [s['d'][5:] for s in ss]
        ar = [s['ar'] * 100 for s in ss]
        sp = [s['spread'] * 100 for s in ss]
        ax.plot(xs, ar, 'o-', color='#1f77b4', lw=2.4, ms=6, label='吸收比率 AR(%)')
        ax.plot(xs, sp, 's--', color='#ff7f0e', lw=2.0, ms=5, label='UMAP 离散度')
        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels(xs, fontproperties=FP, fontsize=10, rotation=30)
        ax.tick_params(axis='y', labelsize=10)
        ax.legend(prop=FP, fontsize=11, loc='best', framealpha=0.9)
        ax.grid(ls='--', alpha=0.25)
        for s_ in ('top', 'right'):
            ax.spines[s_].set_visible(False)
        ok = p['votes'] >= 1
        color = upcolor if ok else dncolor
        txt = (f"当日 {p['ret_today']:+.2f}% / 超额 {p['resid_today']:+.2f}%  "
               f"胜率 {p['pct_pos']:.0f}%\n"
               f"ΔAR {p['d_ar']*100:+.2f}pct ｜ ΔH0 {p['d_h0']:+.2f} ｜ Δ离散 {p['d_spread']*100:+.2f}\n"
               f"TDA {'过' if p['tda_ok'] else '否'} ｜ UMAP {'过' if p['umap_ok'] else '否'} ｜ "
               f"{p['verdict']}·{p['strength']}")
        ax.text(0.02, -0.30, txt, transform=ax.transAxes, fontproperties=FP, fontsize=11.5,
                color=color, va='top', bbox=dict(boxstyle='round,pad=0.4', fc='#f7f7f7', ec=color, lw=1.4))

    draw_topo(axes[0][2], topo_pick, '看多·备选池', '#1d7a45', '#a03a2e')
    draw_topo(axes[1][2], topo_avoid, '看空·弃选池', '#1d7a45', '#a03a2e')

    fig.suptitle(f'个股备选池 / 弃选池 — 行业概念分布 + 拓扑结构校验 — {date}',
                 fontproperties=FPB, fontsize=21, y=0.985)
    fig.text(0.5, 0.015,
             '口径：备选=看好提及≥4次且占比≥60%；弃选=看空提及≥3次且占比≥60%；概念按池内股票数加权。'
             '标签后缀 = 该行业/概念组结构状态（↑涨/↓跌 + 聚集/扩散），程序按 AR 与嵌入离散度 5 日变化判定',
             ha='center', fontproperties=FP, fontsize=10.5, color='#555')
    fig.tight_layout(rect=(0, 0.03, 1, 0.965))
    fig.savefig(out_png, facecolor='white')
    plt.close(fig)
    return out_png


# ---------------- 推送 ----------------
def _qq_send(text):
    try:                              # 统一通知（data_hub.notify）优先
        import _notify
        _r = _notify.send_card(((text or "").strip().splitlines() or [""])[0][:60], text)
        if _r.get("ok"):
            return True
        print("[notify] 统一库未就绪，回落原通道：%s" % _r.get("reason"))
    except Exception as _exc:
        print("[notify] 统一库异常，回落原通道：%s" % _exc)
    if not QQ_TARGET:
        print('[QQ text] 跳过：未配置 SP_QQ_TARGET')
        return False
    try:
        r = subprocess.run([_dh_cfg('OPENCLAW_BIN', 'openclaw'),
                            'message', 'send', '--channel', 'qqbot',
                            '--target', QQ_TARGET, '--message', text, '--json'],
                           capture_output=True, text=True, timeout=180)
        ok = r.returncode == 0
        mid = ''
        try:
            mid = ((json.loads(r.stdout or '{}').get('payload') or {}).get('result') or {}).get('messageId') or ''
        except Exception:
            pass
        print('[QQ text]', 'ok' if ok else 'fail', mid[:20], (r.stderr or '')[-160:] if not ok else '')
        return ok
    except Exception as e:
        print('[QQ text] exc', e)
        return False


def fmt_topo(t, label):
    if not t or not t.get('ok'):
        return f'🧬 {label}拓扑：不可算（{(t or {}).get("why") or "无数据"}）'
    p = t['pool']
    icon = '✅' if p['votes'] >= 1 else '❌'
    return (f'🧬 {label}拓扑：{icon} {p["verdict"]}·{p["strength"]}'
            f'（TDA {"✓" if p["tda_ok"] else "✗"} / UMAP {"✓" if p["umap_ok"] else "✗"}）\n'
            f'   当日 {p["ret_today"]:+.2f}% 超额 {p["resid_today"]:+.2f}% 胜率 {p["pct_pos"]:.0f}%'
            f' ｜ ΔAR {p["d_ar"]*100:+.2f}pct ΔH0 {p["d_h0"]:+.2f} Δ离散 {p["d_spread"]*100:+.2f}\n'
            f'   {p["want"]}')


def fmt_list(rows, title, icon):
    if not rows:
        return f'{icon} {title}：今日无（票数不足或无一致性）'
    out = [f'{icon} {title}（{len(rows)} 只，按看好/不看好占比排序）']
    for i, r in enumerate(rows, 1):
        tag = r['industry']
        tt = r.get('topo_tag')
        tts = f" [{tt}]" if tt else ''
        out.append(f"{i:>2}. {r['name']}({r['code']}) {tag}{tts} "
                   f"{'看好' if icon == '🌙' else '看空'}{r['bull'] if icon == '🌙' else r['bear']}/"
                   f"{r['total']} = {(r['bull_ratio'] if icon == '🌙' else r['bear_ratio']) * 100:.0f}%")
    return '\n'.join(out)


def fmt_stats(rows, cmap, label):
    ind = Counter(r['industry'] for r in rows)
    con = weighted_concepts(rows, cmap)
    s = [f'【{label}】行业 Top：' + '、'.join(f'{k}{v}' for k, v in ind.most_common(8))]
    if con:
        s.append(f'【{label}】概念 Top：' + '、'.join(f'{k}{v}' for k, v in con.most_common(8)))
    return '\n'.join(s)


def scan_report_stats(raw_text, basic):
    """数当日群消息里「有多少条报告、其中多少条真提到个股」——空池归因用。

    大佬 2026-11-26 要求：池子选不出股票时，必须**明确告诉他是数据不够**，
    而不是让他看图里「（无数据）」猜是程序坏了。图里不写原因，只写这句推送文字。
    """
    n_msg = n_stock_msg = 0
    for _t, _sender, mtype, body in split_blocks(raw_text):
        if mtype in ('image', 'file'):
            continue
        n_msg += 1
        codes = {c for c in re.findall(r'(?<!\d)(\d{6})(?![\d.])', body) if c in basic}
        if codes:
            n_stock_msg += 1
    return n_msg, n_stock_msg


def empty_reason(raw_text, basic, rows, min_bull, min_bear, thr):
    """两池都空时，生成「是数据原因」的归因说明（不含代码细节，只讲数据）。"""
    n_msg, n_stock_msg = scan_report_stats(raw_text, basic)
    max_bmen = max([r['bmen'] for r in rows], default=0)
    max_rmen = max([r['rmen'] for r in rows], default=0)
    L = ['⚠️ 今日两池皆空 —— 这是**数据不够**，不是程序故障',
         f'· 群内报告 {n_msg} 条，其中真正点名个股的只有 {n_stock_msg} 条',
         f'· 有效提及个股 {len(rows)} 只，单票最高被看好提及 {max_bmen} 次、看空 {max_rmen} 次']
    if max_bmen < min_bull:
        L.append(f'· 入备选需：看好提及 ≥{min_bull} 次 且 看好占比 ≥{int(thr * 100)}% '
                 f'→ 今天最高只有 {max_bmen} 次，不够')
    if max_rmen < min_bear:
        L.append(f'· 入弃选需：看空提及 ≥{min_bear} 次 且 看空占比 ≥{int(thr * 100)}% '
                 f'→ 今天最高只有 {max_rmen} 次，不够')
    L.append('· 原因：今天群里没有「多份报告同时点名同一只票」的情况，'
             '每只票基本只被一份报告提到 1 次')
    return '\n'.join(L)


def _feishu_card(text, title):
    """飞书私聊卡片（feishu_push_card.py 的 --chat 实为 chat_id，私聊用 open_id 会 400，
    所以这里不传 chat，直接走脚本默认的私聊 chat_id）"""
    import tempfile
    fp = tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8')
    fp.write(text)
    fp.close()
    try:
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feishu_push_card.py'),
                            '--title', title, '--body-file', fp.name],
                           capture_output=True, text=True, timeout=180)
        print('[Feishu card]', (r.stdout or '').strip()[-160:], (r.stderr or '')[-160:])
        return 'code=0' in (r.stdout or '') or 'success' in (r.stdout or '')
    finally:
        try:
            os.unlink(fp.name)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=datetime.date.today().isoformat())
    ap.add_argument('--after', default='15:00')
    ap.add_argument('--top', type=int, default=50)
    ap.add_argument('--min-votes', type=int, default=1)
    ap.add_argument('--min-bull', type=int, default=4, help='备选池：至少几家报告看好')
    ap.add_argument('--min-bear', type=int, default=3, help='弃选池：至少几家报告看空')
    ap.add_argument('--thr', type=float, default=0.6)
    ap.add_argument('--raw', default='')
    ap.add_argument('--no-send', action='store_true')
    ap.add_argument('--no-image', action='store_true')
    ap.add_argument('--qq-only', action='store_true')
    ap.add_argument('--no-feishu', action='store_true')
    ap.add_argument('--no-topo', action='store_true', help='跳过 TDA/UMAP 拓扑校验')
    ap.add_argument('--no-struct', action='store_true', help='跳过行业/概念的聚集-扩散标识')
    ap.add_argument('--with-text', action='store_true',
                    help='额外发文字清单（默认只发图，大佬 2026-11-02 要求「发图就行」）')
    ap.add_argument('--skip-if-same', action='store_true',
                    help='补跑用：结果与上次已推送的相同则静默退出，不重复推送')
    ap.add_argument('--out-dir', default='',
                    help='报告产物目录（框架传 CASE_OUT_DIR；默认 .cache/stock_pool/）')
    args = ap.parse_args()

    raw = args.raw
    # 非交易日（周末/节假日）自动回退到最近交易日
    if not raw:
        try:
            import trade_day
            r, is_open, note = trade_day.resolve(args.date)
            if not is_open:
                print('[trade_day]', note or '非交易日', '→', r, file=sys.stderr)
                args.date = f'{r[:4]}-{r[4:6]}-{r[6:]}'
        except Exception as e:
            print('[trade_day] skip', str(e)[:80], file=sys.stderr)
    if not raw:
        # ⚠️ 必须每次重拉：21:50 跑时不能复用下午拉过的旧缓存（那时报告还没发完）
        raw = f'/tmp/feishu_raw_{args.date}.txt'
        if not CHAT:
            print('缺少 SP_CHAT_ID（要读的飞书群 chat_id）—— 凭据不入库，需部署时配置', file=sys.stderr)
            sys.exit(3)
        r = subprocess.run([sys.executable, FETCH, '--chat', CHAT, '--date', args.date,
                            '--after', args.after], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            print('fetch fail', r.stderr[-400:]); sys.exit(1)
        with open(raw, 'w', encoding='utf-8') as f:
            f.write(r.stdout)
    if not os.path.exists(raw):
        print('raw 不存在', raw); sys.exit(1)
    text = open(raw, encoding='utf-8').read()
    if len(text.strip()) < 200:
        print('当日消息过少', len(text)); sys.exit(2)

    basic = load_stock_basic()
    cmap = load_concepts()
    votes = extract_votes(text, basic)
    pick, avoid, allrows, note = build_pools(votes, basic, args.min_bull, args.min_bear, args.thr)
    pick, avoid = pick[:args.top], avoid[:args.top]
    if note:
        print('[note]', note, file=sys.stderr)

    # ── 补跑幂等：结果没变就不重发（避免一天推两次同样的图）──
    stamp_pre = args.date.replace('-', '')
    sent_state = os.path.join(BASE, f'.cache/stock_pool/sent_{stamp_pre}.json')
    if args.skip_if_same:
        cur = {'pick': sorted(r['code'] for r in pick),
               'avoid': sorted(r['code'] for r in avoid)}
        prev = None
        if os.path.exists(sent_state):
            try:
                prev = json.load(open(sent_state, encoding='utf-8'))
            except Exception:
                prev = None
        if prev and prev.get('pick') == cur['pick'] and prev.get('avoid') == cur['avoid']:
            print('结果与上次一致（备选 %d / 弃选 %d），补跑静默退出，不重复推送'
                  % (len(pick), len(avoid)))
            return
        print('补跑：结果有变化或首次推送 → 正常推送')

    ind_cnt_p = Counter(r['industry'] for r in pick)
    ind_cnt_a = Counter(r['industry'] for r in avoid)
    print(f'=== {args.date} 个股池 ===')
    print(f'有效提及个股 {len(allrows)} 只 | 备选 {len(pick)} | 弃选 {len(avoid)}')
    print('备选行业:', ind_cnt_p.most_common(8))
    print('弃选行业:', ind_cnt_a.most_common(8))
    print('备选前5:', [(r['name'], r['bull'], r['total']) for r in pick[:5]])
    print('弃选前5:', [(r['name'], r['bear'], r['total']) for r in avoid[:5]])

    os.makedirs(os.path.join(BASE, '.cache/stock_pool'), exist_ok=True)
    stamp = args.date.replace('-', '')

    # ── 拓扑结构校验（大佬 2026-11-02：上涨需资金聚集，下跌需资金发散）──
    topo_pick = topo_avoid = None
    if not args.no_topo:
        try:
            import pool_topo as PT
            if len(pick) >= 2:
                topo_pick = PT.analyze([r['code'] for r in pick], args.date)
            else:
                topo_pick = {'ok': False, 'why': '池内不足 2 只', 'pool': {}, 'stocks': {}}
            if len(avoid) >= 2:
                topo_avoid = PT.analyze([r['code'] for r in avoid], args.date)
            else:
                topo_avoid = {'ok': False, 'why': '池内不足 2 只', 'pool': {}, 'stocks': {}}
            for tag, t in (('备选', topo_pick), ('弃选', topo_avoid)):
                if t.get('ok'):
                    p_ = t['pool']
                    print(f'[topo {tag}] {p_["verdict"]}·{p_["strength"]} '
                          f'(TDA {p_["tda_ok"]}/UMAP {p_["umap_ok"]}) '
                          f'ΔAR {p_["d_ar"]*100:+.2f}pct ΔH0 {p_["d_h0"]:+.2f} Δ离散 {p_["d_spread"]*100:+.2f}',
                          file=sys.stderr)
                else:
                    print(f'[topo {tag}] 跳过：{t.get("why")}', file=sys.stderr)
            # 池级不配合 → 全部标「结构未验证」，不删股只降级
            for tag, t, pool_rows in (('pick', topo_pick, pick), ('avoid', topo_avoid, avoid)):
                if not t or not t.get('ok'):
                    continue
                ok = t['pool']['votes'] >= 1
                for r in pool_rows:
                    ts = PT.to_ts(r['code'])
                    st = t['stocks'].get(ts, {})
                    r['topo_tag'] = ('核心' if st.get('core') else '边缘') if ok else '结构未验证'
                    r['topo_ok'] = ok
                    r['topo_strength'] = t['pool']['strength']
        except Exception as e:
            print('[topo] fail', str(e)[:160], file=sys.stderr)

    payload = {'date': args.date, 'pick': pick, 'avoid': avoid, 'all': allrows,
               'ind_pick': ind_cnt_p.most_common(20), 'ind_avoid': ind_cnt_a.most_common(20),
               'topo_pick': (topo_pick or {}), 'topo_avoid': (topo_avoid or {})}
    json.dump(payload, open(os.path.join(BASE, f'.cache/stock_pool/pool_{stamp}.json'), 'w'),
              ensure_ascii=False, indent=1)

    OUT_DIR = args.out_dir or os.path.join(BASE, '.cache/stock_pool')
    os.makedirs(OUT_DIR, exist_ok=True)

    png = ''
    if not args.no_image:
        # ── 行业/概念组结构标识（聚集 or 扩散，纯程序）──
        struct = {}
        if not args.no_struct:
            try:
                import struct_tag as ST
                ind_set = {r['industry'] for r in (pick + avoid) if r.get('industry')}
                inds, cons = set(), set()
                for rows_ in (pick, avoid):
                    for k, _ in Counter(r['industry'] for r in rows_).most_common(16):
                        inds.add(k)
                    for k, _ in weighted_concepts(rows_, cmap, min_stocks=1).most_common(20):
                        if any(k in k2 for k2 in LOW_VALUE_TAGS) or LIST_TAG.search(k):
                            continue
                        if k in ind_set:
                            inds.add(k)
                        else:
                            cons.add(k)
                struct = ST.run(args.date, sorted(inds), sorted(cons))
                for k, v in struct.items():
                    print(f'[struct] {k}: {v["dir"]}·{v["state"]} '
                          f'(ΔAR {v["d_ar"]*100:+.2f}pct Δsp {v["d_sp"]*100:+.2f} n={v["n"]})',
                          file=sys.stderr)
            except Exception as e:
                print('[struct] fail', str(e)[:160], file=sys.stderr)
        png = os.path.join(BASE, f'.cache/stock_pool/pool_{stamp}.png')
        make_chart(pick, avoid, cmap, png, args.date, topo_pick, topo_avoid, struct)
        print('chart:', png, os.path.getsize(png) // 1024, 'KB')

        # 2026-11-03 22:09 大佬要求「把这几张图依次合一下子」→ 默认改发合并大图
        #   （行业+概念+资金结构 + 申万三级下钻 ⑦⑧⑨），原 2x2 图保留存档。
        #   关掉合并：SP_MERGE=0
        if os.environ.get('SP_MERGE', '1') != '0':
            try:
                import pool_chart_merged as M
                mp = os.path.join(BASE, f'.cache/stock_pool/pool_merged_{stamp}.png')
                sw = json.load(open(os.path.join(BASE, '.cache/sw_member_all.json'), encoding='utf-8'))
                rows_all = M.build_rows({'all': allrows}, sw)
                M.make(rows_all, pick, avoid, struct, topo_pick, topo_avoid,
                       args.date, mp, cmap, sys.modules[__name__])
                print('merged:', mp, os.path.getsize(mp) // 1024, 'KB')
                png = mp
            except Exception as e:
                print('[merge] fail, 用原图', str(e)[:160], file=sys.stderr)

    head = (f'📌 个股备选池 / 弃选池 · {args.date}\n'
            f'口径：看好提及≥{args.min_bull}次入备选、看空提及≥{args.min_bear}次入弃选（占比≥{int(args.thr*100)}%，各取前{args.top}）\n'
            f'有效提及个股 {len(allrows)} 只 → 备选 {len(pick)} / 弃选 {len(avoid)}')
    msg1 = head + '\n\n' + fmt_stats(pick, cmap, '备选') + '\n' + fmt_stats(avoid, cmap, '弃选')
    msg1 += '\n\n' + fmt_topo(topo_pick, '备选池') + '\n' + fmt_topo(topo_avoid, '弃选池')
    msg2 = fmt_list(pick[:args.top], '备选池', '🌙')
    msg3 = fmt_list(avoid[:args.top], '弃选池', '⭐')

    # 大佬 2026-11-26：池子空了必须主动说「是数据不够」。
    # 提示只进推送文字，**不进图**（图里保持简洁，不暴露内部逻辑）。
    empty_tip = ''
    if not pick and not avoid:
        empty_tip = empty_reason(text, basic, allrows, args.min_bull, args.min_bear, args.thr)
        print('\n[空池归因]\n' + empty_tip)

    # ── 产物先落盘（--no-send 也要落，框架的 fetch 阶段就是用来产出的）──
    lists_txt = os.path.join(OUT_DIR, f'pool_{stamp}.txt')
    with open(lists_txt, 'w', encoding='utf-8') as f:
        f.write(msg1 + '\n\n' + msg2 + '\n\n' + msg3)
    try:
        import shutil as _sh
        if png and os.path.exists(png) and os.path.abspath(os.path.dirname(png)) != os.path.abspath(OUT_DIR):
            _sh.copy2(png, os.path.join(OUT_DIR, os.path.basename(png)))
        src_json = os.path.join(BASE, f'.cache/stock_pool/pool_{stamp}.json')
        if os.path.exists(src_json) and os.path.abspath(OUT_DIR) != os.path.abspath(os.path.dirname(src_json)):
            _sh.copy2(src_json, os.path.join(OUT_DIR, os.path.basename(src_json)))
    except Exception as e:
        print('[out-dir] 拷贝产物失败', str(e)[:100], file=sys.stderr)
    print('out-dir ->', OUT_DIR)

    # ── 统一契约：两池写成 picks，供显示层「自选股」按算法分块渲染 ──
    # 大佬 2026-11-26：各算法自选股要在一个 tab 里结构整齐，不再各写各的。
    try:
        import pick_pool as PP
        rows = []
        for r in pick[:args.top]:
            rows.append(PP.pick_row(
                r['code'], r['name'], industry=r.get('industry', ''),
                reason='看好 %d/%d 次（%d%%）' % (r['bull'], r['total'],
                                                 round(r['bull_ratio'] * 100)),
                score=round(r.get('bull_ratio', 0) * 100, 2),
                tags=['备选'] + ([r['topo_tag']] if r.get('topo_tag') else [])))
        for r in avoid[:args.top]:
            rows.append(PP.pick_row(
                r['code'], r['name'], industry=r.get('industry', ''),
                reason='看空 %d/%d 次（%d%%）' % (r['bear'], r['total'],
                                                 round(r['bear_ratio'] * 100)),
                score=-round(r.get('bear_ratio', 0) * 100, 2), direction='short',
                tags=['弃选'] + ([r['topo_tag']] if r.get('topo_tag') else [])))
        _note = ''
        if not rows:
            _n_msg, _n_stk = scan_report_stats(text, basic)
            _note = ('今日两池皆空：群内报告 %d 条、真正点名个股 %d 条，'
                     '单票最高被看好 %d 次（门槛 %d 次）—— 数据不够，非程序故障'
                     % (_n_msg, _n_stk, max([r['bmen'] for r in allrows], default=0),
                        args.min_bull))
        PP.write_picks('stock_pool', '个股备选池 / 弃选池', args.date, rows,
                       cadence='daily', note=_note, out_dir=OUT_DIR, allow_empty=True)
        PP.write_picks_fallback('stock_pool', '个股备选池 / 弃选池', args.date,
                                rows, cadence='daily', note=_note, allow_empty=True)
    except Exception as e:
        print('[picks] 写统一契约失败', str(e)[:140], file=sys.stderr)

    if args.no_send:
        print('\n===== 概览 =====\n' + msg1)
        print('\n===== 备选 =====\n' + msg2)
        print('\n===== 弃选 =====\n' + msg3)
        return

    if args.with_text:
        _qq_send(msg1)
        if not args.no_feishu:
            _feishu_card(msg1, f'📌 个股备选池 / 弃选池 · {args.date}')
    if png:
        cmd = [sys.executable, PUSH_IMAGE_PY, png]
        caption = ''
        if empty_tip:
            # 图配文说清楚原因（QQ 和飞书都会带上这句）
            caption = '📌 个股备选池 / 弃选池 · %s\n\n%s' % (args.date, empty_tip)
            cmd += ['--text', caption]
        if args.qq_only or args.no_feishu:
            cmd.append('--qq-only')
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        print('push image:', r.stdout.strip()[-200:], r.stderr[-200:])
    elif empty_tip:
        # 没出图（--no-image）也要把原因发出去
        _qq_send('📌 个股备选池 / 弃选池 · %s\n\n%s' % (args.date, empty_tip))
        if not args.no_feishu:
            _feishu_card(empty_tip, f'📌 个股备选池 / 弃选池 · {args.date}')
    if args.with_text:
        _qq_send(msg2)
        _qq_send(msg3)
    # 记下本次已推送内容，供补跑（--skip-if-same）比对
    try:
        json.dump({'pick': sorted(r['code'] for r in pick),
                   'avoid': sorted(r['code'] for r in avoid),
                   'pushed_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
                  open(sent_state, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception as e:
        print('[state] 写 sent 状态失败', str(e)[:80], file=sys.stderr)
    print('lists ->', lists_txt)
    print('done')


if __name__ == '__main__':
    main()
