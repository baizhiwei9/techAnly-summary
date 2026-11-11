"""
缠论结构版信号 v2 候选（第二批，2026-11-11）
⚠️ 仅用于对比/评估，**未接入实盘**；实盘仍走 structure_signals.detect_structure_signals。

相对 v1 的两处修正（对齐远程 bin/tech_buypoint.py 2026-11-11 定稿）：

  1) 三买/三卖：遍历**所有中枢**（v1 只看最后一个中枢）
     · 向上离开中枢 → 回踩低点不破 ZG = 三买
     · 中枢递进校验：离开段（i..k）之间若又形成新的有效中枢，则旧中枢失效，
       该回踩不算三买（修「巨人网络」式假三买）
     · 现价必须仍在 ZG 上方（跌回中枢内 = 失效）
     · 回踩之后结构未破（未跌破回踩低点 0.5%）
     · 年龄 ≤ 90 根、距买点 +10%/-3% 内

  2) 二买/二卖：锚定一买/一卖（v1 是「任意更高的低点」都算二买，过滥）
     · 二买 = 一买之后的**第一个**回踩低点，且不破一买低点
     · 二卖 = 一卖之后的第一个反抽高点，且不破一卖高点
     · 一买/一卖本身沿用 v1 的 MACD 背驰判定

返回: list[dict] — {idx, kind, level, age, zg, zd, why}
  idx 为**原始K线索引**（与 v1 一致，可直接画图）
"""
from typing import List, Dict, Any

import numpy as np

from .structure_indicators import calc_macd
from .structure_signals import detect_structure_signals

TB_MAX_AGE = 90       # 三买/三卖最大年龄（对齐远程 TB_MAX_AGE）
TB_NEAR_UP = 10.0     # 现价高于买点 ≤ 10%
NEAR_DN = 3.0         # 跌破买点 ≤ 3%
SELL_NEAR = 10.0      # 卖点对称
BREAK_TOL = 0.995     # 结构未破容差


def _find_bi_pos(bi_ends, kline_idx, kind):
    for pos, e in enumerate(bi_ends):
        if e.kline_idx == kline_idx and e.kind == kind:
            return pos
    return None


def _remote_divergences(df):
    """
    远程口径的一买/一卖（bin/chan_smc_index.py chan_read）：
      段价格创新低/新高 + （段幅度更小 或 该段 MACD 力度更小）= 背驰
    MACD 在**合并K线**的中点上计算（不是原始K线），10/20/5。
    返回每个背驰端点；调用方自行决定取最新一个还是全部。
    """
    bi_ends = df.attrs.get('bi_ends') or []
    merged = df.attrs.get('merged') or []
    if len(bi_ends) < 4 or len(merged) < 12:
        return []
    approx = np.array([(m.high + m.low) / 2.0 for m in merged], float)
    _dif, _dea, hist = calc_macd(approx)

    segs = []
    for k in range(len(bi_ends) - 1):
        a, b = bi_ends[k], bi_ends[k + 1]
        segs.append({
            'up': b.kind == 'top',
            'from': float(a.price), 'to': float(b.price),
            'i0': int(a.merged_pos), 'i1': int(b.merged_pos),
            'idx': int(b.kline_idx),
        })
    if len(segs) < 3:
        return []

    def amp(s):
        return abs(s['to'] - s['from'])

    def power(s, up):
        a, b = max(0, s['i0'] - 1), min(len(hist) - 1, s['i1'])
        if b <= a:
            return 0.0
        seg = hist[a:b]
        return float(seg.max() if up else -seg.min())

    n = len(df)
    out = []
    for k, s in enumerate(segs):
        if s['up']:
            continue
        same = [j for j in range(k) if not segs[j]['up']]
        if not same:
            continue
        p = segs[same[-1]]
        if s['to'] < p['to'] and (amp(s) < amp(p) or power(s, False) < power(p, False)):
            out.append({'idx': s['idx'], 'kind': 'B1', 'level': s['to'],
                        'age': int(n - 1 - s['idx']), 'zg': None, 'zd': None,
                        'why': '段背驰（新低 + 幅度或MACD力度衰减）'})
    for k, s in enumerate(segs):
        if not s['up']:
            continue
        same = [j for j in range(k) if segs[j]['up']]
        if not same:
            continue
        p = segs[same[-1]]
        if s['to'] > p['to'] and (amp(s) < amp(p) or power(s, True) < power(p, True)):
            out.append({'idx': s['idx'], 'kind': 'S1', 'level': s['to'],
                        'age': int(n - 1 - s['idx']), 'zg': None, 'zd': None,
                        'why': '段背驰（新高 + 幅度或MACD力度衰减）'})
    out.sort(key=lambda x: x['idx'])
    return out


def _anchored_second_points(df, base_signals):
    """二买/二卖：锚定一买/一卖后的第一个回踩（不破一买低点/一卖高点）"""
    bi_ends = df.attrs.get('bi_ends') or []
    out = []
    if len(bi_ends) < 3:
        return out

    for idx, kind, stop_loss, _lvl in base_signals:
        if kind not in ('B1', 'S1'):
            continue
        want = 'bot' if kind == 'B1' else 'top'
        pos = _find_bi_pos(bi_ends, idx, want)
        if pos is None:
            continue
        # 一买后第一个「不破一买低点」的回踩低点 / 一卖后第一个「不破一卖高点」的反抽
        # （中间若创新低则跳过，继续找 —— 对齐远程 chan_read 的 B2 扫描口径）
        first = None
        for j in range(pos + 1, len(bi_ends)):
            if bi_ends[j].kind != want:
                continue
            if kind == 'B1' and bi_ends[j].price > float(stop_loss):
                first = bi_ends[j]
                break
            if kind == 'S1' and bi_ends[j].price < float(stop_loss):
                first = bi_ends[j]
                break
        if first is None:
            continue
        if kind == 'B1':
            out.append({'idx': int(first.kline_idx), 'kind': 'B2',
                        'level': float(first.price), 'age': None,
                        'zg': None, 'zd': None,
                        'why': '一买后首个不破一买低点的回踩'})
        else:
            out.append({'idx': int(first.kline_idx), 'kind': 'S2',
                        'level': float(first.price), 'age': None,
                        'zg': None, 'zd': None,
                        'why': '一卖后首个不破一卖高点的反抽'})
    return out


def _all_zhongshu_breaks(df, close, lows, highs, n):
    """三买/三卖：遍历所有中枢 + 中枢递进校验 + 结构未破 + 现价位置"""
    bi_ends = df.attrs.get('bi_ends') or []
    if len(bi_ends) < 6:
        return []

    # 笔端点 → 段（与远程 segs_from_bi 同口径）
    segs = []
    for j in range(len(bi_ends) - 1):
        a, b = bi_ends[j], bi_ends[j + 1]
        segs.append({
            'up': b.kind == 'top' and a.kind == 'bot',
            'hi': float(max(a.price, b.price)),
            'lo': float(min(a.price, b.price)),
            'i0': int(a.kline_idx),
            'i1': int(b.kline_idx),
        })
    ns = len(segs)
    if ns < 6:
        return []

    out = []
    seen = set()

    # ── 三买 ──
    for i in range(ns - 3, -1, -1):
        w = segs[i:i + 3]
        zg = min(x['hi'] for x in w)
        zd = max(x['lo'] for x in w)
        if not (zg > zd):
            continue
        for k in range(ns - 2, i + 2, -1):
            if not (segs[k]['up'] and segs[k]['hi'] > zg):
                continue
            r = segs[k + 1]
            if r['up'] or r['lo'] <= zg:
                continue
            # 中枢递进校验：离开途中不得再形成新中枢
            mid = False
            for j in range(i + 3, k - 1):
                w2 = segs[j:j + 3]
                if len(w2) < 3:
                    continue
                if min(x['hi'] for x in w2) > max(x['lo'] for x in w2):
                    mid = True
                    break
            if mid:
                continue
            lvl = r['lo']
            idx = r['i1']
            age = n - 1 - idx
            key = ('B3', round(lvl, 3))
            if key in seen:
                break
            if age > TB_MAX_AGE:
                break
            gap = (close[-1] / lvl - 1) * 100 if lvl > 0 else 0.0
            if gap > TB_NEAR_UP or gap < -NEAR_DN:
                break
            after = lows[idx:]
            if len(after) and float(after.min()) < lvl * BREAK_TOL:
                break
            if close[-1] <= zg:
                break
            seen.add(key)
            out.append({'idx': int(idx), 'kind': 'B3', 'level': lvl, 'age': int(age),
                        'zg': round(float(zg), 4), 'zd': round(float(zd), 4),
                        'why': '遍历中枢：离开后回踩不破 ZG'})
            break

    # ── 三卖（对称）──
    for i in range(ns - 3, -1, -1):
        w = segs[i:i + 3]
        zg = min(x['hi'] for x in w)
        zd = max(x['lo'] for x in w)
        if not (zg > zd):
            continue
        for k in range(ns - 2, i + 2, -1):
            if segs[k]['up'] or not (segs[k]['lo'] < zd):
                continue
            r = segs[k + 1]
            if not r['up'] or r['hi'] >= zd:
                continue
            mid = False
            for j in range(i + 3, k - 1):
                w2 = segs[j:j + 3]
                if len(w2) < 3:
                    continue
                if min(x['hi'] for x in w2) > max(x['lo'] for x in w2):
                    mid = True
                    break
            if mid:
                continue
            lvl = r['hi']
            idx = r['i1']
            age = n - 1 - idx
            key = ('S3', round(lvl, 3))
            if key in seen:
                break
            if age > TB_MAX_AGE:
                break
            gap = (close[-1] / lvl - 1) * 100 if lvl > 0 else 0.0
            if gap < -SELL_NEAR or gap > NEAR_DN:
                break
            after = highs[idx:]
            if len(after) and float(after.max()) > lvl / BREAK_TOL:
                break
            if close[-1] >= zd:
                break
            seen.add(key)
            out.append({'idx': int(idx), 'kind': 'S3', 'level': lvl, 'age': int(age),
                        'zg': round(float(zg), 4), 'zd': round(float(zd), 4),
                        'why': '遍历中枢：向下离开后反抽不破 ZD'})
            break

    return out


def detect_structure_signals_v2(df, b1_mode="v1") -> List[Dict[str, Any]]:
    """
    v2 候选信号。
    b1_mode:
      · "v1"     — 一买/一卖沿用 local v1（MACD 面积背驰），用于隔离第二批改动
      · "remote" — 一买/一卖改用远程口径（段幅度或 MACD 力度背驰），
                   这样「二买锚定一买」才有足够的一买锚点
    """
    if 'bi_ends' not in df.attrs:
        return []
    n = len(df)
    close = df['close'].values.astype(float)
    lows = df['low'].values.astype(float)
    highs = df['high'].values.astype(float)

    if b1_mode == "remote":
        first_points = _remote_divergences(df)
        b1_src = [(s['idx'], s['kind'], s['level'], s['level'])
                  for s in first_points]
    else:
        base = detect_structure_signals(df)
        first_points = [
            {'idx': int(i), 'kind': k, 'level': float(sl),
             'age': int(n - 1 - i), 'zg': None, 'zd': None,
             'why': 'v1 MACD 面积背驰'}
            for i, k, sl, _l in base if k in ('B1', 'S1')
        ]
        b1_src = [(i, k, sl, l) for i, k, sl, l in base if k in ('B1', 'S1')]

    out = []
    seen_key = set()

    # 一买/一卖
    for s in first_points:
        k = (s['kind'], s['idx'])
        if k in seen_key:
            continue
        seen_key.add(k)
        out.append(s)

    # 二买/二卖：锚定所选口径的一买/一卖
    for s in _anchored_second_points(df, b1_src):
        k = (s['kind'], s['idx'])
        if k in seen_key:
            continue
        seen_key.add(k)
        s['age'] = int(n - 1 - s['idx'])
        out.append(s)

    # 三买/三卖：全中枢扫描
    for s in _all_zhongshu_breaks(df, close, lows, highs, n):
        k = (s['kind'], s['idx'])
        if k in seen_key:
            continue
        seen_key.add(k)
        out.append(s)

    out.sort(key=lambda x: x['idx'])
    return out
