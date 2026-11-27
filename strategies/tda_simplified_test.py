#!/usr/bin/env python3
"""
简化版 TDA 验证脚本：复现百度/Wind 的"相关系数矩阵特征值"法
β₀_proxy = #{ λᵢ > 1 }
β₁_proxy = H(λ) = -Σ pᵢ·log(pᵢ)   (香农熵)
对比目标: 百度 β₁=2.4750, 曲率=0.3635, 脆弱性=63.9%, 震荡偏强
"""

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
from tushare_init import get_pro, get_bar

import tda_rotation as t

# 获取 ETF 价格
etf_prices = t.get_etf_prices(t.ETF_CODES, t.WINDOW)
print("ETF 数据 shape:", etf_prices.shape)
print("列(因子):", list(etf_prices.columns))

# 收益率矩阵 (60日)
ret = np.log(etf_prices / etf_prices.shift(1)).dropna()
print("收益率矩阵 shape:", ret.shape)

# 相关系数矩阵 (8x8)
corr = ret.corr().values
print("\n=== 相关系数矩阵 ===")
print(np.round(corr, 3))

# 特征值分解
eigvals = np.linalg.eigvalsh(corr)
eigvals = eigvals[::-1]  # 降序
print("\n特征值(降序):", np.round(eigvals, 4))

# β₀ = 特征值>1 的数量
beta_0 = int(np.sum(eigvals > 1))
print(f"β₀ (特征值>1个数) = {beta_0}")

# β₁ = 香农熵，用正特征值归一化
pos = eigvals[eigvals > 0]
if len(pos) == 0:
    pos = eigvals
p = pos / pos.sum()
H = -np.sum(p * np.log(p))
print(f"β₁ 香农熵 H(λ) = {H:.4f}")

# 曲率：尝试几种口径
# 口径A: 前2个特征值累计贡献率
cum2 = eigvals[:2].sum() / eigvals.sum()
# 口径B: 用香农熵归一化到[0,1]
# 口径C: 最大特征值占比
max_ratio = eigvals[0] / eigvals.sum()
print(f"曲率候选 - 前2特征值贡献率={cum2:.4f}, 最大特征值占比={max_ratio:.4f}")
