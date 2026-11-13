#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""targets.py · 标的解析与类型识别
====================================
把用户输入（中文名 / 代码 / 显式类型前缀）解析成统一的 Target 对象。

输入协议（--target 支持逗号分隔或多次传入，可混合类型）：
    沪深300                        # 自动识别 → 指数
    000300.SH                      # 指数代码
    指数:沪深300                   # 显式指数
    行业:电子                      # 显式行业
    概念:人工智能                  # 显式概念
    板块:半导体                    # 板块（先在行业里找，再在概念里找）
    个股:600519                    # 显式个股（SPD 流形需要成分股，个股不支持，报错）

自动识别顺序（无前缀时）：
    指数代码/名称 → 行业名 → 概念名 → 板块名
"""
from __future__ import annotations

import re

# 常用指数映射（简称 / 代码 → tushare 格式 ts_code）
INDEX_ALIASES = {
    # 简称
    "沪深300": "000300.SH", "hs300": "000300.SH", "000300": "000300.SH",
    "中证500": "000905.SH", "zz500": "000905.SH", "000905": "000905.SH",
    "中证1000": "000852.SH", "zz1000": "000852.SH", "000852": "000852.SH",
    "上证50": "000016.SH", "sz50": "000016.SH", "000016": "000016.SH",
    "科创50": "000688.SH", "kcb50": "000688.SH", "000688": "000688.SH",
    "创业板指": "399006.SZ", "cyb": "399006.SZ", "399006": "399006.SZ",
    "创业板50": "399673.SZ", "cyb50": "399673.SZ", "399673": "399673.SZ",
    "上证指数": "000001.SH", "sh": "000001.SH", "000001": "000001.SH",
    "深证成指": "399001.SZ", "szcz": "399001.SZ", "399001": "399001.SZ",
    "北证50": "899050.BJ", "bj50": "899050.BJ",
    "中证2000": "932000.CSI",
}

# 指数 ts_code → 展示名
INDEX_NAMES = {
    "000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000",
    "000016.SH": "上证50", "000688.SH": "科创50", "399006.SZ": "创业板指",
    "399673.SZ": "创业板50", "000001.SH": "上证指数", "399001.SZ": "深证成指",
    "899050.BJ": "北证50",
}

_TYPE_PREFIX = ("指数:", "行业:", "概念:", "板块:", "个股:")


class Target:
    """统一标的对象。

    kind 取值：
        index    指数（code = tushare ts_code）
        industry 行业板块（name = 行业名）
        concept  概念板块（name = 概念名）
        board    板块（行业或概念，kind2 记录实际命中）
        stock    个股（SPD 流形不支持）
        unknown  无法识别
    """

    __slots__ = ("kind", "name", "code", "kind2", "raw")

    def __init__(self, kind, name="", code="", kind2="", raw=""):
        self.kind = kind
        self.name = name
        self.code = code
        self.kind2 = kind2
        self.raw = raw

    def __repr__(self):  # pragma: no cover
        return ("<Target %s name=%r code=%r kind2=%r>"
                % (self.kind, self.name, self.code, self.kind2))


def _is_index_code(s):
    return bool(re.fullmatch(r"\d{6}\.(SH|SZ|BJ|CSI)", (s or "").strip().upper()))


def resolve_index(s):
    """把各种指数写法 → (ts_code, display_name) 或 (None, None)"""
    t = (s or "").strip()
    low = t.lower()
    if t in INDEX_ALIASES:
        code = INDEX_ALIASES[t]
        return code, INDEX_NAMES.get(code, t)
    if low in INDEX_ALIASES:
        code = INDEX_ALIASES[low]
        return code, INDEX_NAMES.get(code, t)
    if _is_index_code(t):
        up = t.upper()
        return up, INDEX_NAMES.get(up, up)
    return None, None


def resolve(raw, board_indexes=None, board_concepts=None):
    """把用户输入解析成 Target。

    board_indexes / board_concepts：可选，[(name, code)...]，用于无前缀时
    按名称识别行业/概念/板块。调用方（run.py）从取数层预取，避免每次解析都拉列表。
    """
    raw = (raw or "").strip()
    if not raw:
        return Target("unknown", raw=raw)

    # 1) 显式类型前缀
    for p in _TYPE_PREFIX:
        if raw.startswith(p):
            body = raw[len(p):].strip()
            if not body:
                return Target("unknown", raw=raw)
            return _typed(p.rstrip(":"), body)

    # 2) 指数（代码/简称）
    code, name = resolve_index(raw)
    if code:
        return Target("index", name=name, code=code, raw=raw)

    # 3) 个股（6 位数字，SPD 流形不支持）
    if re.fullmatch(r"\d{6}", raw):
        return Target("stock", name=raw, code=raw, raw=raw)

    # 4) 行业 / 概念 / 板块（按名称匹配）
    hit = _match_board(raw, board_indexes, "行业")
    if hit:
        return Target("industry", name=hit[0], code=hit[1], raw=raw)
    hit = _match_board(raw, board_concepts, "概念")
    if hit:
        return Target("concept", name=hit[0], code=hit[1], raw=raw)
    # 板块：行业+概念都找（同名优先行业）
    hit = _match_board(raw, board_indexes, "行业") or _match_board(raw, board_concepts, "概念")
    if hit:
        return Target("board", name=hit[0], code=hit[1], kind2="行业" if hit[0] in _names(board_indexes) else "概念", raw=raw)

    return Target("unknown", name=raw, raw=raw)


def _typed(kind, body):
    """显式类型 → Target"""
    if kind == "指数":
        code, name = resolve_index(body)
        if code:
            return Target("index", name=name, code=code, raw=body)
        return Target("unknown", name=body, raw=body)
    if kind == "个股":
        if re.fullmatch(r"\d{6}", body):
            return Target("stock", name=body, code=body, raw=body)
        return Target("unknown", name=body, raw=body)
    if kind == "行业":
        return Target("industry", name=body, raw=body)
    if kind == "概念":
        return Target("concept", name=body, raw=body)
    if kind == "板块":
        return Target("board", name=body, raw=body)
    return Target("unknown", name=body, raw=body)


def _names(boards):
    return {n for n, _ in (boards or [])}


def _match_board(name, boards, _kind):
    """板块名匹配：精确 > 包含。boards=[(name, code), ...]"""
    if not boards:
        return None
    name = name.strip()
    for n, c in boards:
        if n == name:
            return (n, c)
    for n, c in boards:
        if name in n or n in name:
            return (n, c)
    return None


def parse_all(args):
    """解析 --target 参数列表 → [(raw, Target)...]（保留原始输入用于报错）"""
    out = []
    for a in args:
        for seg in str(a).split(","):
            seg = seg.strip()
            if not seg:
                continue
            out.append((seg, None))  # 占位，解析延迟到取数层（需板块列表）
    return out
