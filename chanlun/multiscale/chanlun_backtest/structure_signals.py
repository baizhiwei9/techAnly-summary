"""
缠论结构版信号模块（MACD 10,20,5 — 对齐远程定稿）
替代原 signals.py 中的均线版
B1/S1/B2/S2/B3/S3 六个买卖点
"""
import numpy as np
from typing import List, Tuple

def detect_structure_signals(df) -> List[Tuple[int, str, float, float]]:
    """
    扫描结构版买卖点信号
    返回: list of (idx, signal_type, stop_loss, structure_level)

    修正说明：
      - B3/S3 只与最近一个中枢比对，不再遍历所有历史中枢
      - 避免远古中枢的 ZG/ZD 被用作当前价位的止损，导致信号失真
    """
    if 'bi_ends' not in df.attrs or 'zhongshus' not in df.attrs:
        return []
    
    bi_ends = df.attrs['bi_ends']
    zhongshus = df.attrs['zhongshus']
    bar = df['MB1'].values
    close = df['close'].values
    n = len(df)
    
    if len(bi_ends) < 3:
        return []
    
    signals = []
    seen = set()
    
    # === B1/S1 背驰 ===
    for i in range(2, len(bi_ends)):
        e0, e1, e2 = bi_ends[i-2], bi_ends[i-1], bi_ends[i]
        s1s = min(e0.kline_idx, e1.kline_idx)
        s1e = max(e0.kline_idx, e1.kline_idx)
        s2s = min(e1.kline_idx, e2.kline_idx)
        s2e = max(e1.kline_idx, e2.kline_idx)
        if s1e-s1s < 1 or s2e-s2s < 1:
            continue
        area1 = np.sum(np.abs(bar[s1s:s1e+1]))
        area2 = np.sum(np.abs(bar[s2s:s2e+1]))
        if area1 == 0:
            continue
        if e2.kline_idx in seen:
            continue
        if e2.kind == 'bot' and e0.kind == 'bot':
            seg1 = close[s1s:s1e+1]; seg2 = close[s2s:s2e+1]
            if len(seg2)>0 and len(seg1)>0:
                if np.min(seg2) <= np.min(seg1) and area2 < area1*0.9:
                    signals.append((e2.kline_idx, 'B1', e2.price, e0.price))
                    seen.add(e2.kline_idx)
        elif e2.kind == 'top' and e0.kind == 'top':
            seg1 = close[s1s:s1e+1]; seg2 = close[s2s:s2e+1]
            if len(seg2)>0 and len(seg1)>0:
                if np.max(seg2) >= np.max(seg1) and area2 < area1*0.9:
                    signals.append((e2.kline_idx, 'S1', e2.price, e0.price))
                    seen.add(e2.kline_idx)
    
    # === B2/S2 回调确认 ===
    for i, bot in enumerate(bi_ends):
        if bot.kind != 'bot' or bot.kline_idx in seen:
            continue
        found_top = False
        for j in range(i+1, len(bi_ends)):
            e = bi_ends[j]
            if e.kind == 'top':
                found_top = True
            elif e.kind == 'bot' and found_top and e.price > bot.price:
                if e.kline_idx not in seen:
                    signals.append((e.kline_idx, 'B2', bot.price, bot.price))
                    seen.add(e.kline_idx)
                break
    for i, top in enumerate(bi_ends):
        if top.kind != 'top' or top.kline_idx in seen:
            continue
        found_bot = False
        for j in range(i+1, len(bi_ends)):
            e = bi_ends[j]
            if e.kind == 'bot':
                found_bot = True
            elif e.kind == 'top' and found_bot and e.price < top.price:
                if e.kline_idx not in seen:
                    signals.append((e.kline_idx, 'S2', top.price, top.price))
                    seen.add(e.kline_idx)
                break
    
    # === B3/S3 中枢突破回抽 ===
    # 修正：只使用最近一个中枢，不再遍历所有历史中枢
    # B3 = 向上突破中枢ZG后回抽不破ZG；S3 = 向下跌破中枢ZD后反弹不破ZD
    if zhongshus:
        zs = zhongshus[-1]  # 只取最近一个中枢
        post = [e for e in bi_ends if e.idx > zs.end_bi]
        if post:
            broke_up = False
            for e in post:
                if e.kind == 'top' and e.price > zs.zg:
                    broke_up = True
                elif e.kind == 'bot' and broke_up and e.price > zs.zg:
                    if e.kline_idx not in seen:
                        signals.append((e.kline_idx, 'B3', zs.zg, zs.zg))
                        seen.add(e.kline_idx)
                    broke_up = False
            broke_down = False
            for e in post:
                if e.kind == 'bot' and e.price < zs.zd:
                    broke_down = True
                elif e.kind == 'top' and broke_down and e.price < zs.zd:
                    if e.kline_idx not in seen:
                        signals.append((e.kline_idx, 'S3', zs.zd, zs.zd))
                        seen.add(e.kline_idx)
                    broke_down = False
    
    # 按时间排序
    signals.sort(key=lambda x: x[0])
    return signals
