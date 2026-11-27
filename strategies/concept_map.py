#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🏷 个股 → 所属概念（同花顺概念，tushare ths_member）
=====================================================================
大佬 2026-10-28：「子图后边一定要加上所属概念……画出股票的图的时候，一定要加上所属的概念」

缓存：.cache/concepts/stock_concepts.json
  {"map": {"600036.SH": ["银行","金融科技",...]}, "__done__": ["885xxx.TI", ...]}

用法（库）：
    import concept_map as CM
    d = CM.get_map()            # 全量 {"600036.SH": [...]}
    tags = CM.tags_for("600036.SH", limit=3)   # ["银行","沪股通","+2"]
命令行：
    python3 strategies/concept_map.py --build          # 增量补齐概念成分股
    python3 strategies/concept_map.py --show 600036.SH
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
import time

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(WS, ".cache", "concepts")
STOCK_MAP = os.path.join(CACHE, "stock_concepts.json")
IDX_MAP = os.path.join(WS, ".cache", "ths_index_map.json")

# 这些「样本股/成分股」类伪概念信息量低，排在后面（不剔除，只降权）
_LOW_VALUE = ("样本股", "成份股", "成分股", "融资融券", "转融券", "标普", "MSCI",
              "富时", "沪股通", "深股通", "预盈预增", "预亏预减")

_MAP = None


def get_map(rebuild=False):
    global _MAP
    if _MAP is not None and not rebuild:
        return _MAP
    if os.path.exists(STOCK_MAP):
        try:
            d = json.load(open(STOCK_MAP, encoding='utf-8'))
            _MAP = d.get('map', {})
        except Exception:
            _MAP = {}
    else:
        _MAP = {}
    return _MAP


def tags_for(ts_code, limit=3, fallback=""):
    """返回展示用概念标签列表：优先低价值的排后面，最多 limit 个，超出用 '+N'。"""
    m = get_map().get(ts_code) or []
    if not m:
        return [fallback] if fallback else []
    # 去重保序
    seen, lst = set(), []
    for x in m:
        if x and x not in seen:
            seen.add(x)
            lst.append(x)
    hi = [x for x in lst if not any(k in x for k in _LOW_VALUE)]
    lo = [x for x in lst if any(k in x for k in _LOW_VALUE)]
    ordered = hi + lo
    if len(ordered) <= limit:
        return ordered
    return ordered[:limit] + ["+%d" % (len(ordered) - limit)]


def concept_lines(ts_code, limit=4, per_line=3):
    """给报告用：概念名拼成一行字符串（不超过 limit 个）。"""
    t = tags_for(ts_code, limit=limit)
    return " · ".join(t) if t else "—"


# ---------------------------------------------------------------- 构建
def build(force=False, sleep=0.0):
    import tushare_init  # noqa
    from tushare_init import get_pro
    pro = get_pro()
    os.makedirs(CACHE, exist_ok=True)
    idx = json.load(open(IDX_MAP, encoding='utf-8')) if os.path.exists(IDX_MAP) else {}
    if not idx:
        print('缺少 .cache/ths_index_map.json')
        return
    cur = {'map': {}, '__done__': []}
    if os.path.exists(STOCK_MAP) and not force:
        try:
            cur = json.load(open(STOCK_MAP, encoding='utf-8'))
        except Exception:
            pass
    data, done = cur.get('map', {}), set(cur.get('__done__', []))
    todo = [(n, c) for n, c in idx.items() if c not in done]
    print('概念总数 %d，待补 %d' % (len(idx), len(todo)), flush=True)
    for i, (name, code) in enumerate(todo, 1):
        for k in range(4):
            try:
                df = pro.ths_member(ts_code=code)
                if df is not None and len(df):
                    for ts in df['con_code'].tolist():
                        data.setdefault(ts, [])
                        if name not in data[ts]:
                            data[ts].append(name)
                    done.add(code)
                break
            except Exception as e:
                msg = str(e)
                wait = 20 if ('ip' in msg or '每分钟' in msg) else 3
                print('  重试 %s %s' % (code, msg[:50]), flush=True)
                time.sleep(wait)
        if i % 30 == 0:
            _dump(data, done)
            print('  [%d/%d] 覆盖个股 %d' % (i, len(todo), len(data)), flush=True)
        if sleep:
            time.sleep(sleep)
    _dump(data, done)
    print('完成：覆盖个股 %d，概念 %d' % (len(data), len(done)), flush=True)


def _dump(data, done):
    os.makedirs(CACHE, exist_ok=True)
    tmp = STOCK_MAP + '.tmp'
    json.dump({'map': data, '__done__': sorted(done)}, open(tmp, 'w', encoding='utf-8'),
              ensure_ascii=False)
    os.replace(tmp, STOCK_MAP)


def main():
    args = sys.argv[1:]
    if '--build' in args:
        build(force='--force' in args)
    elif '--show' in args:
        i = args.index('--show')
        code = args[i + 1] if len(args) > i + 1 else ''
        print(json.dumps(tags_for(code, limit=20), ensure_ascii=False))
    else:
        print(__doc__)


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
