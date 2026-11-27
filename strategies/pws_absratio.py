#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相变异常检测系统 · 指标6：吸收比率 AR = λ1/N（与本机 pool_topo / struct_tag 同源）

为什么要单独一个指标
--------------------
现有 M1 的 AR 是 **AR(1) 时间自相关**（单序列记忆），与 Kritzman 吸收比率完全是两件事。
本机 `bin/struct_tag.py` 的口径（大佬 2026-11-02 定）是：
    先去掉当日截面共同因子 eps = R - mean_cs(R)，再算相关矩阵第一特征值占比 λ1/N

实测定论（2026-11-03，386 日 × 3 指数）
  · 原始收益口径（pool_topo）：10月峰 72~86 分位，7月峰 14~35 分位
  · 去共同因子口径（struct_tag）：10月峰 **96~98 分位**，7月峰 74~82 分位
  → 去因子口径能把「抱团到极限」抓得非常干净，是 10 月型顶的专属探测器
  → 但它对 7 月型（广谱顶）无效：7 月宽度正常、指数还在涨，没有抱团特征

规则：AR_dm > 过去 120 日 P90 → 告警（本机口径，阈值为滚动分位而非固定值）

⚠️ 方向约束（2026-11-03 补）：纯 AR 破 P90 在**暴跌途中也会亮**——市场同步下跌时
   个股相关性同样飙升（λ1/N 上去），这不是"抱团冲顶"。实测创业板 7/28 起暴跌，
   AR 从 0.347 一路升到 0.45，全程误报。加"指数须在 MA60 上方"后：
   7月命中完全不变（5/29~6/01），亮灯天数减半（68→32 / 71→39）。
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys

import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WS = os.environ.get("OPENCLAW_WORKSPACE") or __import__("ocsecrets").workspace()
XS = os.path.join(WS, '.cache', 'pws', 'xsec')
os.makedirs(XS, exist_ok=True)

TAG = {'000300.SH': 'hs300', '000905.SH': 'zz500', '000852.SH': 'zz1000',
       '000688.SH': 'kc_all', '399006.SZ': 'cyb'}

AR_WIN = 20
AR_REF = 120
AR_PCT = 90
AR_CAP = 300          # 池子抽样上限（速度）
DEDEMEAN = True       # True = struct_tag 口径（去共同因子）
MA_FILTER = 60        # 方向约束：收盘须在 MA60 之上（0 = 关闭）


def _ar_one(R):
    """R: DataFrame(窗口 × 成员)，返回 去因子后的 λ1/N"""
    n = R.shape[1]
    if n < 2 or len(R) < 8:
        return float('nan')
    X = R.sub(R.mean(axis=1), axis=0) if DEDEMEAN else R
    with np.errstate(invalid='ignore', divide='ignore'):
        C = np.corrcoef(X.values.T)
    C = np.nan_to_num(np.asarray(C, float), nan=0.0)
    C = np.clip((C + C.T) / 2, -1, 1)
    np.fill_diagonal(C, 1.0)
    return float(np.linalg.eigvalsh(C)[-1] / n)


def build(index_code, start='20250401', end=None, win=AR_WIN, cap=AR_CAP, seed=7):
    """算并缓存 AR_dm 序列 → .cache/pws/xsec/ar_<tag>.json"""
    end = end or pd.Timestamp.now().strftime('%Y%m%d')
    import pws_data as D
    panel = D.market_ret(start, end)
    if panel is None or panel.empty:
        return None
    codes = [c for c in D.pool(index_code, end) if c in panel.columns]
    if len(codes) < 30:
        return None
    if len(codes) > cap:
        codes = sorted(np.random.default_rng(seed).choice(codes, cap, replace=False))
    sub = panel[codes]
    idx = list(sub.index)
    out = {}
    for i in range(win - 1, len(idx), 1):
        w = sub.iloc[i - win + 1:i + 1].dropna(axis=1, thresh=int(win * .95)).dropna()
        if w.shape[1] < 30:
            continue
        v = _ar_one(w)
        if np.isfinite(v):
            out[idx[i]] = round(v, 6)
    if not out:
        return None
    s = pd.Series(out).sort_index()
    s.to_json(os.path.join(XS, f'ar_{TAG[index_code]}.json'))
    return s


def load_ar(index_code, end=None):
    fp = os.path.join(XS, f'ar_{TAG[index_code]}.json')
    if not os.path.exists(fp):
        return None
    s = pd.read_json(fp, typ='series')
    s.index = s.index.astype(str)
    s = s.sort_index()
    if end:
        s = s[s.index <= end]
    return s if len(s) else None


def alert(s_ar, dates, ref=AR_REF, pct=AR_PCT, close=None, ma_win=MA_FILTER):
    """对齐到每个交易日 + 滚动分位告警（可选方向约束：close > MA(ma_win)）"""
    if s_ar is None or len(s_ar) == 0:
        return np.zeros(len(dates), bool), np.full(len(dates), np.nan), np.full(len(dates), np.nan)
    v = s_ar.reindex(dates).ffill()
    x = v.to_numpy(float)
    a = np.zeros(len(dates), bool)
    thr = np.full(len(dates), np.nan)
    for t in range(ref, len(dates)):
        w = x[max(0, t - ref):t]
        w = w[np.isfinite(w)]
        if len(w) >= 60 and np.isfinite(x[t]):
            thr[t] = np.percentile(w, pct)
            a[t] = x[t] > thr[t]
    if close is not None and ma_win:
        ma = pd.Series(np.asarray(close, float)).rolling(ma_win).mean().to_numpy()
        a &= np.isfinite(ma) & (np.asarray(close, float) > ma)
    return a, x, thr


def build_all(start='20250401', end=None):
    for code in TAG:
        s = build(code, start, end)
        print(f'  AR {TAG[code]:<8}', 'skip' if s is None else
              f'{len(s)} 点 {s.index[0]}~{s.index[-1]} 最新 {s.iloc[-1]:.4f}')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20250401')
    ap.add_argument('--end', default=None)
    a = ap.parse_args()
    build_all(a.start, a.end)
