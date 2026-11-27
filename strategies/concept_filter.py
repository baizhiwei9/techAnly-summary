#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""概念板块过滤器 —— 剔掉「不是产业概念」的伪概念

大佬 2026-11-09 反馈：「那个日报，那里边那个概念那一堆…你要去掉宽泛的概念，比如说什么首板啊」

问题根源：东财概念池（m:90+t:3，531 个）里混了四类**不是产业逻辑**的东西，
按涨幅排序时它们天天霸榜：
  ① 涨停/情绪统计标签：昨日首板、昨日涨停、昨日高换手、最近多板 …
  ② 风格/市值/股价属性：题材股、微盘股、百元股、次新股、高市盈率、超跌股 …
  ③ 财报/机构持仓统计：2026三季报预增、社保重仓、QFII新进、基金增仓 …
  ④ 宽基指数成分/交易制度：上证50_、HS300_、融资融券、转债标的 …

另外还有一批**过宽的主题词**（人工智能、新能源车、半导体概念…），基本等于
「整个市场」，而且日报里已经有「行业板块」一节，重复且没有信息量。

用法：
    from concept_filter import filter_concepts, is_pseudo
    df2 = filter_concepts(df)          # df 需有 name 列

可扩展：设环境变量 CONCEPT_KEEP="苹果概念,特斯拉概念" 强制保留；
       设 CONCEPT_DROP="某某概念" 追加剔除。
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import re

# ---------- ① 涨停 / 情绪统计标签 ----------
EMO_RE = re.compile(
    r'昨日|首板|连板|涨停|炸板|触板|打板|封板|多板|高换手|高振幅|竞价|情绪|赚钱效应|'
    r'热股|人气|活跃股|强势股|龙头股|妖股')

# ---------- ② 风格 / 市值 / 股价属性 ----------
STYLE_RE = re.compile(
    r'微盘|小盘|中盘|大盘|百元股|低价股|高价股|次新股|权重股|'
    r'价值股|周期股|趋势股|反转股|超跌股|高成长股|微利股|'
    r'破净|破发|破增发价|红利股|红利破净|历史新高|近期新高|百日新高|近期摘帽|'
    r'^ST股$|连续亏损|股权分散|股权集中|举牌|增持计划|股权激励|股权转让|'
    r'风格$|散户|游资|庄股|^题材股$|'
    r'市盈率|市净率|外资背景|参股|控股|^高市|^低市')

# ---------- ③ 财报 / 机构持仓统计 ----------
FUND_RE = re.compile(
    r'\d{4}(一季报|中报|三季报|年报)|预增|预减|扭亏|首亏|'
    r'重仓|增仓|减仓|新进|持股|金股|做市|融资融券|沪股通|深股通|'
    r'转债标的|GDR|AB股|AH股|^B股$|MSCI|富时罗素|标准普尔|独角兽|专精特新')

# ---------- ④ 宽基指数成分 / 交易所板块 ----------
INDEX_RE = re.compile(
    r'_$|HS300|上证50|上证180|上证380|中证500|央视50|深证100|深成500|'
    r'创业板综|创业成份|科创板|北交所概念|^沪企改革$')

# ---------- ⑤ 过宽主题词（≈整个市场 / 与行业板块重复）----------
BROAD = {
    '人工智能', 'AI应用', 'AI智能体', 'AI语料', '算力概念', '大数据', '云计算',
    '数字经济', '数据中心', '国产软件', '信创', '网络安全', '物联网', '工业互联网',
    '区块链', '元宇宙概念', '虚拟现实', '增强现实', '超清视频',
    '新能源', '新能源车', '光伏概念', '锂电池概念', '储能概念', '氢能源',
    '军工', '军民融合', '芯片', '半导体概念', '消费电子概念', '机器人概念',
    '智能驾驶', '车联网(车路云)', '5G概念', '通信技术', '华为概念', '苹果概念',
    '特斯拉概念', '比亚迪概念', '小米概念', '低空经济', '新型工业化',
    '国企改革', '央国企改革', '一带一路', '西部大开发', '长江三角',
}

# 强制保留 / 追加剔除（环境变量覆盖）
KEEP = {x.strip() for x in os.environ.get('CONCEPT_KEEP', '').split(',') if x.strip()}
DROP = {x.strip() for x in os.environ.get('CONCEPT_DROP', '').split(',') if x.strip()}


def reason(name):
    """返回剔除原因（'' = 保留）"""
    n = str(name or '').strip()
    if not n:
        return '空名'
    if n in KEEP:
        return ''
    if n in DROP:
        return '手动剔除'
    if n in BROAD:
        return '过宽主题'
    if EMO_RE.search(n):
        return '情绪标签'
    if STYLE_RE.search(n):
        return '风格/市值'
    if FUND_RE.search(n):
        return '财报/持仓统计'
    if INDEX_RE.search(n):
        return '指数成分/交易制度'
    return ''


def is_pseudo(name):
    return bool(reason(name))


def filter_concepts(df, name_col='name', verbose=False, stats=None):
    """过滤概念池。stats 传入 dict 可就地累计剔除原因（便于打日志）"""
    if df is None or not len(df):
        return df
    if name_col not in df.columns:
        return df
    rs = df[name_col].map(reason)
    if stats is not None:
        for r in rs:
            if r:
                stats[r] = stats.get(r, 0) + 1
    keep = rs == ''
    if verbose:
        print(f'[concept_filter] {int(keep.sum())}/{len(df)} 保留，剔除 {int((~keep).sum())} 个')
    return df[keep]


def filter_names(names):
    return [n for n in names if not is_pseudo(n)]


if __name__ == '__main__':
    import json
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else '/tmp/concepts_all.json'
    try:
        names = json.load(open(p, encoding='utf-8'))
    except Exception:
        names = []
    st = {}
    kept = filter_names(names) if names else []
    for n in names:
        r = reason(n)
        if r:
            st[r] = st.get(r, 0) + 1
    print(f'总 {len(names)} → 保留 {len(kept)}，剔除 {len(names) - len(kept)}')
    for k, v in sorted(st.items(), key=lambda kv: -kv[1]):
        print(f'  {k}: {v}')
    if names:
        dropped = [n for n in names if is_pseudo(n)]
        print('\n被剔除示例：' + '、'.join(dropped[:25]))
