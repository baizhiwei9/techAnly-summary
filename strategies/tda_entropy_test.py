#!/usr/bin/env python3
"""穷举香农熵口径，找出能复现百度 β₁=2.4750 的定义"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, 'bin')
import numpy as np
import pandas as pd
import tda_rotation as t

etf_prices = t.get_etf_prices(t.ETF_CODES, t.WINDOW)
ret = np.log(etf_prices / etf_prices.shift(1)).dropna()
corr = ret.corr().values
eigvals = np.linalg.eigvalsh(corr)[::-1]
n = len(eigvals)
sum_l = eigvals.sum()
print(f"特征值: {np.round(eigvals,4)}")
print(f"Σλ = {sum_l:.4f}, n={n}")
print(f"目标 β₁=2.4750, 曲率=0.3635\n")

candidates = {}

# 口径1: 所有特征值占比 香农熵
p1 = eigvals / sum_l
candidates['1_全特征值占比'] = (-np.sum(p1*np.log(p1+1e-12)))

# 口径2: 只用正特征值占比
pos = eigvals[eigvals>0]
p2 = pos/pos.sum()
candidates['2_正特征值占比'] = (-np.sum(p2*np.log(p2+1e-12)))

# 口径3: 特征值/最大特征值 归一化熵
p3 = eigvals/np.max(eigvals) if np.max(eigvals)>0 else eigvals
candidates['3_除以最大λ_熵'] = (-np.sum(p3*np.log(p3+1e-12)))

# 口径4: λ/(λ+1) 参与率熵
q4 = eigvals/(eigvals+1)
p4 = q4/q4.sum()
candidates['4_λ/(λ+1)参与率'] = (-np.sum(p4*np.log(p4+1e-12)))

# 口径5: 用协方差(未归一化相关)特征值
cov = ret.cov().values
ev5 = np.linalg.eigvalsh(cov)[::-1]
p5 = ev5/ev5.sum()
candidates['5_协方差特征值熵'] = (-np.sum(p5*np.log(p5+1e-12)))

# 口径6: 香农熵用 p=λ/(Σλ)，但只对 特征值>1 的
gt = eigvals[eigvals>1]
p6 = gt/gt.sum() if gt.sum()>0 else eigvals
candidates['6_仅λ>1熵'] = (-np.sum(p6*np.log(p6+1e-12)))

# 口径7: H=log(有效维度)=log(Σλ/λmax)
candidates['7_log(Σλ/λmax)'] = np.log(sum_l/np.max(eigvals))

# 口径8: H = -Σp·log(p), p=每个特征值解释方差占比(即λ/Σλ)，但含常数c=1的偏移
# 口径9: 香农熵 + 基尼式
candidates['9_ln(参与特征数均值)'] = np.log(n/(n- ( np.sum(eigvals>1) ) +1))

for k,v in candidates.items():
    print(f"{k}: {v:.4f}")

# 尝试比例系数: 百度2.4750 / 我的1.3682
print(f"\n比例: 2.4750/1.3682 = {2.4750/1.3682:.3f}")
print(f"我的口径1 = {candidates['1_全特征值占比']:.4f}, ×{2.4750/candidates['1_全特征值占比']:.3f}")
