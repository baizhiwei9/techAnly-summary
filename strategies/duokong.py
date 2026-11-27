#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多空标识公共库 —— 大佬 2026-10-23 定下的硬性规范

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

================================================================
原话：「往飞书发的消息，你要记住，看多和看空要有，后边有标识，特别是指数，
        宽基指数那。比如看空要标星星，然后看多标月亮。多空平衡我就不用管」

规则（所有飞书推送统一遵守）：
    看空 / 偏空  →  ⭐   （1=偏空  2=看空  3=强看空）
    看多 / 偏多  →  🌙   （1=偏多  2=看多  3=强看多）
    多空平衡 / 中性 / 观望  →  **不打任何标识**

用法：
    from duokong import tag, tag_by_score, tag_by_z, mark

    tag('bull')                  -> '🌙🌙'
    tag('bear', 3)               -> '⭐⭐⭐'
    tag('flat')                  -> ''
    tag_by_score(0.42)           -> '🌙🌙'      # 正=bull，负=bear，看绝对值定强弱
    tag_by_z(1.7, 'bear')        -> '⭐⭐'      # 明确方向 + 偏离强度(z / 中枢半宽)
    mark('沪深300', 'bull', 2)   -> '沪深300 🌙🌙'
    line('沪深300', 4036.3, 'bull', 2, extra='Abs=+1.2σ')
                                 -> '沪深300 4036.30 🌙🌙 ｜Abs=+1.2σ'

⚠️ 不要在各个脚本里各写一套图标，改口径只改这里。
"""
from __future__ import annotations

BULL = "🌙"
BEAR = "⭐"

# tag_by_score 的分档（按 |score|）
#   < NEUT : 多空平衡 → 不打标
#   < W1   : 偏多/偏空（1 个）
#   < W2   : 看多/看空（2 个）
#   >= W2  : 强多/强空（3 个）
NEUT = 0.05
W1 = 0.15
W2 = 0.50

# tag_by_z 的分档（|z| 以「中枢半宽」为单位）
Z_NEUT = 0.5
Z_W1 = 1.0
Z_W2 = 2.0

_DIR_BUll_ALIASES = {"bull", "long", "看多", "偏多", "多", "多", "up", "多单", "多头"}
_DIR_BEAR_ALIASES = {"bear", "short", "看空", "偏空", "空", "down", "空单", "空头"}
_FLAT_ALIASES = {"flat", "neutral", "neut", "中性", "平衡", "多空平衡", "观望", "无", ""}


def _norm(direction) -> str:
    """把各种写法归一成 bull / bear / flat"""
    if direction is None:
        return "flat"
    if isinstance(direction, (int, float)):
        # 数字当分数用
        return "bull" if direction > 0 else ("bear" if direction < 0 else "flat")
    s = str(direction).strip().lower()
    if s in _FLAT_ALIASES:
        return "flat"
    if s in _DIR_BUll_ALIASES or any(k in s for k in ("看多", "偏多", "bull", "long")):
        return "bull"
    if s in _DIR_BEAR_ALIASES or any(k in s for k in ("看空", "偏空", "bear", "short")):
        return "bear"
    return "flat"


def _clamp(n) -> int:
    try:
        n = int(n)
    except Exception:
        n = 2
    return max(1, min(3, n))


def tag(direction, strength: int = 2) -> str:
    """方向 + 强弱(1~3) → 图标串；中性返回空串。"""
    d = _norm(direction)
    if d == "flat":
        return ""
    return (BULL if d == "bull" else BEAR) * _clamp(strength)


def tag_by_score(score, neut: float = NEUT, w1: float = W1, w2: float = W2) -> str:
    """按分数自动定方向 + 强弱（模型预测分、因子值、涨跌幅等都能用）。"""
    try:
        v = float(score)
    except Exception:
        return ""
    if v != v:  # NaN
        return ""
    a = abs(v)
    if a < neut:
        return ""            # 多空平衡，不打标
    n = 1 if a < w1 else (2 if a < w2 else 3)
    return tag("bull" if v > 0 else "bear", n)


def strength_by_score(score, neut: float = NEUT, w1: float = W1, w2: float = W2) -> int:
    """0=平衡, 1~3=强弱"""
    try:
        a = abs(float(score))
    except Exception:
        return 0
    if a != a or a < neut:
        return 0
    return 1 if a < w1 else (2 if a < w2 else 3)


def tag_by_z(z, direction, z_neut: float = Z_NEUT, z_w1: float = Z_W1, z_w2: float = Z_W2) -> str:
    """已知方向、用偏离强度 |z| 定图标个数。z 常以「中枢半宽」为单位。"""
    d = _norm(direction)
    if d == "flat":
        return ""
    try:
        a = abs(float(z))
    except Exception:
        return tag(d, 2)
    if a != a:
        return tag(d, 2)
    if a < z_neut:
        return ""            # 贴着中轴 → 多空平衡，不打标
    n = 1 if a < z_w1 else (2 if a < z_w2 else 3)
    return tag(d, n)


def mark(name: str, direction, strength: int = 2, sep: str = " ") -> str:
    """'沪深300' + bull + 2 → '沪深300 🌙🌙'（中性则原样返回名字）"""
    t = tag(direction, strength)
    return f"{name}{sep}{t}" if t else f"{name}"


def line(name: str, price=None, direction="flat", strength: int = 2,
         extra: str = "", sep: str = " ", price_fmt: str = "%.2f") -> str:
    """一行式：'沪深300 4036.30 🌙🌙 ｜Abs=+1.2σ'"""
    head = name
    if price is not None:
        try:
            head += sep + (price_fmt % float(price))
        except Exception:
            head += sep + str(price)
    t = tag(direction, strength)
    if t:
        head += sep + t
    if extra:
        head += " ｜" + extra
    return head


def legend() -> str:
    """口径说明行，放报告开头/结尾。"""
    return "标识：🌙=看多/偏多（越多越强）｜⭐=看空/偏空（越多越强）｜无标识=多空平衡（不用管）"


if __name__ == "__main__":
    for d in ("bull", "bear", "flat", "看多", "看空", "中性", None, 0.3, -0.9):
        print(repr(d), "->", repr(tag(d, 2)))
    print("score 0.02 ->", repr(tag_by_score(0.02)))
    print("score -0.42 ->", repr(tag_by_score(-0.42)))
    print("score -1.07 ->", repr(tag_by_score(-1.07)))
    print("z 1.7 bear ->", repr(tag_by_z(1.7, "bear")))
    print(line("沪深300", 4036.3, "bull", 2, "Abs=+1.20σ"))
    print(legend())
