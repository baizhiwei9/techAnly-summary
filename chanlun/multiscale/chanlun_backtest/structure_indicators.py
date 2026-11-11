"""
缠论结构版指标模块（MACD 10,20,5 — 对齐远程 techAnly-summary 定稿）
替代原 indicators.py 中的均线版
模块A: K线包含 -> 模块B: 分型 -> 模块C: 笔 -> 模块E: 中枢 -> MACD

MACD 参数可用环境变量覆盖（与远程 bin/chan_smc_index.py 一致）：
  CHAN_MACD_FAST / CHAN_MACD_SLOW / CHAN_MACD_SIGNAL
"""
import os
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# 2026-11-11：跟随远程「大佬定稿」由 20/40/5 → 10/20/5
MACD_FAST = _env_int("CHAN_MACD_FAST", 10)
MACD_SLOW = _env_int("CHAN_MACD_SLOW", 20)
MACD_SIGNAL = _env_int("CHAN_MACD_SIGNAL", 5)

# === 数据结构 ===
@dataclass
class MergedK:
    high: float; low: float; start_idx: int; end_idx: int; direction: int

@dataclass
class Fractal:
    idx: int; kind: str; price: float; kline_idx: int

@dataclass
class BiEnd:
    idx: int; kind: str; price: float; kline_idx: int; merged_pos: int

@dataclass
class Zhongshu:
    start_bi: int; end_bi: int; zd: float; zg: float

# === 模块A: K线包含处理 ===
def merge_klines(highs, lows):
    n = len(highs)
    if n == 0: return []
    stack = [MergedK(high=highs[0], low=lows[0], start_idx=0, end_idx=0, direction=0)]
    for i in range(1, n):
        mk = MergedK(high=highs[i], low=lows[i], start_idx=i, end_idx=i, direction=0)
        last = stack[-1]
        contains = (mk.high >= last.high and mk.low <= last.low) or \
                   (mk.high <= last.high and mk.low >= last.low)
        if contains:
            if len(stack) >= 2:
                prev = stack[-2]
                d = 1 if last.high > prev.high else -1
            else:
                d = 1 if mk.high >= last.high else -1
            if d == 1:
                last.high = max(last.high, mk.high); last.low = max(last.low, mk.low)
            else:
                last.high = min(last.high, mk.high); last.low = min(last.low, mk.low)
            last.end_idx = mk.end_idx; last.direction = d
        else:
            stack.append(mk)
    return stack

# === 模块B: 分型检测 ===
def find_fractals(merged):
    fractals = []
    for i in range(1, len(merged) - 1):
        p, c, n = merged[i-1], merged[i], merged[i+1]
        if c.high > p.high and c.high > n.high:
            fractals.append(Fractal(idx=i, kind='top', price=c.high,
                                    kline_idx=(c.start_idx+c.end_idx)//2))
        elif c.low < p.low and c.low < n.low:
            fractals.append(Fractal(idx=i, kind='bot', price=c.low,
                                    kline_idx=(c.start_idx+c.end_idx)//2))
    return fractals

# === 模块C: 笔构建 ===
MIN_BI_GAP = 4  # 两端点间至少5根合并K线

def build_bi(fractals):
    if not fractals: return []
    unique = [fractals[0]]
    for f in fractals[1:]:
        if f.kind == unique[-1].kind:
            if (f.kind == 'top' and f.price > unique[-1].price) or \
               (f.kind == 'bot' and f.price < unique[-1].price):
                unique[-1] = f
        else:
            unique.append(f)
    fractals = unique
    ends = [BiEnd(idx=0, kind=fractals[0].kind, price=fractals[0].price,
                  kline_idx=fractals[0].kline_idx, merged_pos=fractals[0].idx)]
    for f in fractals[1:]:
        last = ends[-1]
        if f.kind == last.kind:
            if (f.kind == 'top' and f.price > last.price) or \
               (f.kind == 'bot' and f.price < last.price):
                last.price = f.price; last.kline_idx = f.kline_idx; last.merged_pos = f.idx
            continue
        if abs(f.idx - last.merged_pos) < MIN_BI_GAP + 1: continue
        if (f.kind == 'top' and f.price <= last.price) or \
           (f.kind == 'bot' and f.price >= last.price): continue
        ends.append(BiEnd(idx=len(ends), kind=f.kind, price=f.price,
                          kline_idx=f.kline_idx, merged_pos=f.idx))
    return ends

# === 模块E: 中枢 ===
def detect_zhongshu(bi_ends):
    """
    中枢识别：前三笔重叠区间确定 ZG/ZD，后续笔逐笔判断延伸/结束。

    延伸规则（修正版）：
      - 一笔与中枢有重叠 → 仍在中枢内震荡，延伸
      - 一笔完全离开中枢（向上脱离或向下脱离） → 标记脱离方向
        - 下一笔又回到中枢区间内 → 延伸（中枢延伸/扩张）
        - 下一笔继续朝同一方向脱离 → 真突破，本中枢结束
      - 避免把"大幅突破后再跌回来"误判为同一个中枢的延伸

    这样做的核心：中枢是"震荡区间"，价格脱离后又回来才算延伸；
    脱离后继续沿原方向走，就是新的走势段了。
    """
    zs_list = []
    if len(bi_ends) < 4: return zs_list
    i = 0
    while i <= len(bi_ends) - 4:
        segs = []
        for j in range(3):
            a, b = bi_ends[i+j], bi_ends[i+j+1]
            segs.append((max(a.price,b.price), min(a.price,b.price)))
        zd = max(s[1] for s in segs); zg = min(s[0] for s in segs)
        if zg > zd:
            end_bi = i + 3  # 中枢由前4个端点（3笔）确定
            k = i + 3       # 从第4笔开始检查延伸
            last_left_dir = None  # None=未脱离, 'up'=向上脱离过, 'down'=向下脱离过
            while k < len(bi_ends):
                seg_h = max(bi_ends[k-1].price, bi_ends[k].price)
                seg_l = min(bi_ends[k-1].price, bi_ends[k].price)
                overlap = min(zg, seg_h) > max(zd, seg_l)

                if overlap:
                    # 这笔与中枢有重叠 → 震荡延续，延伸
                    end_bi = k
                    last_left_dir = None
                    k += 1
                else:
                    # 这笔与中枢无重叠 → 可能是脱离
                    if seg_l >= zg:
                        cur_dir = 'up'
                    elif seg_h <= zd:
                        cur_dir = 'down'
                    else:
                        # 不可能情况（无重叠且不在两侧），安全起见退出
                        break

                    if last_left_dir is None:
                        # 首次脱离：先标记，继续看下一笔能不能回来
                        # 延伸的最后端点仍然是离开的那笔（end_bi 已 = k-1）
                        # 但我们把这笔也算进中枢范围内（中枢延伸的最后一笔是离开笔）
                        end_bi = k
                        last_left_dir = cur_dir
                        k += 1
                    elif last_left_dir == cur_dir:
                        # 连续两笔朝同一方向脱离 → 真突破，中枢结束
                        # end_bi 保留为上一笔（首次脱离的那笔）
                        break
                    else:
                        # 方向相反 → 从另一侧又回来了（即离开后再回来）
                        # 这属于"中枢延伸 / 扩张"，继续延伸并重置脱离标记
                        end_bi = k
                        last_left_dir = None
                        k += 1
            zs_list.append(Zhongshu(start_bi=i, end_bi=end_bi, zd=zd, zg=zg))
            i = end_bi
        else:
            i += 1
    return zs_list

# === MACD(10,20,5) 定版（可用 CHAN_MACD_* 覆盖）===
def calc_macd(close, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    n = len(close)
    ema_f = np.zeros(n); ema_s = np.zeros(n)
    ema_f[0] = close[0]; ema_s[0] = close[0]
    af = 2.0/(fast+1); a_s = 2.0/(slow+1)
    for t in range(1, n):
        ema_f[t] = af*close[t] + (1-af)*ema_f[t-1]
        ema_s[t] = a_s*close[t] + (1-a_s)*ema_s[t-1]
    dif = ema_f - ema_s
    dea = np.zeros(n); dea[0] = dif[0]
    a_sig = 2.0/(signal+1)
    for t in range(1, n):
        dea[t] = a_sig*dif[t] + (1-a_sig)*dea[t-1]
    return dif, dea, 2.0*(dif-dea)

# === 在DataFrame上计算结构指标 ===
def calculate_structure_indicators(df):
    """在DataFrame上计算缠论结构版指标"""
    highs = df['high'].values; lows = df['low'].values; closes = df['close'].values
    n = len(df)
    # MACD
    dif, dea, bar = calc_macd(closes)
    df['DIF1'] = dif; df['DEA1'] = dea; df['MB1'] = bar
    # 包含+分型+笔
    merged = merge_klines(highs, lows)
    fractals = find_fractals(merged)
    bi_ends = build_bi(fractals)
    # 中枢
    zhongshus = detect_zhongshu(bi_ends)
    # 标记每根K线的分型/笔端点/中枢
    df['is_top'] = False; df['is_bot'] = False
    df['bi_idx'] = -1; df['zhongshu_zd'] = np.nan; df['zhongshu_zg'] = np.nan
    for f in fractals:
        if f.kline_idx < n:
            if f.kind == 'top': df.loc[f.kline_idx, 'is_top'] = True
            else: df.loc[f.kline_idx, 'is_bot'] = True
    for bi in bi_ends:
        if bi.kline_idx < n:
            df.loc[bi.kline_idx, 'bi_idx'] = bi.idx
    for zs in zhongshus:
        for k in range(zs.start_bi, min(zs.end_bi+1, n)):
            if k < n:
                df.loc[k, 'zhongshu_zd'] = zs.zd
                df.loc[k, 'zhongshu_zg'] = zs.zg
    # 存储笔端点和中枢供信号模块使用
    df.attrs['bi_ends'] = bi_ends
    df.attrs['zhongshus'] = zhongshus
    # 合并K线（v2 对比模块用：远程背驰口径在合并K线上算 MACD 力度）
    df.attrs['merged'] = merged
    return df
