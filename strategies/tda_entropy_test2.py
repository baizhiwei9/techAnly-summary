#!/usr/bin/env python3
"""更细口径: 用(λ-1)+超特征值解释率做香农熵"""

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

results = {}

# A: (λ-1)+ 解释的"超额信息"
excess = eigvals - 1
excess[excess<0] = 0
pa = excess/excess.sum() if excess.sum()>0 else eigvals
results['A_(λ-1)+超额熵'] = -np.sum(pa*np.log(pa+1e-12)) if excess.sum()>0 else np.nan

# B: λ/Σ 但只取>1的
gt = eigvals[eigvals>1]
pb = gt/gt.sum()
results['B_λ>1占比熵'] = -np.sum(pb*np.log(pb+1e-12))

# C: 把 (λ-1) 当"新信息"，加上均匀残差
# D: 香农熵 用 p=λ/(Σλ)，乘以 log2 还是 ln? 百度可能是 ln。前面用了ln。
# E: log2 版本 (信息论比特)
p1 = eigvals/eigvals.sum()
results['E_全占比log2熵'] = -np.sum(p1*np.log2(p1+1e-12))

# F: 熵/位 用有效维度 e^H
results['F_有效维_e^H'] = np.exp(-np.sum(p1*np.log(p1+1e-12)))

# G: 曲率尝试: 百度 0.3635。 尝试 香农熵归一化 log2(n)
results['G_熵/log2(8)'] = (-np.sum(p1*np.log2(p1+1e-12)))/np.log2(n)

# H: 特征值 归一化 到 [0,1] 的 方差解释 曲率 = 1 - 香农熵/最大熵
results['H_1-H/Hmax'] = 1 - (-np.sum(p1*np.log(p1+1e-12)))/np.log(n)

# I: 前k累计解释率到0.85需要的k 相关
# J: 参与率: 1-λ1/Σλ (分散度)
results['J_1-λmax/Σλ'] = 1 - eigvals[0]/eigvals.sum()

for k,v in results.items():
    print(f"{k}: {v:.4f}")

print(f"\n百度目标 β₁=2.4750, 曲率=0.3635")
print(f"A_e^H = {np.exp(-np.sum(pa*np.log(pa+1e-12))) if results['A_(λ-1)+超额熵']==results['A_(λ-1)+超额熵'] else 'nan'}")
