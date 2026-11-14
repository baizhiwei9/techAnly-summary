#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""registry.py · 画图登记表（唯一一份）
=========================================
本库所有「会出图」的脚本都登记在这里，`misc/charts/run.py` 只认这张表。
新增一张图 = 在这里加一条 dict，别的地方不用动。

每条的字段：
    name        统一入口用的名字（命令行第一个参数）
    desc        一句话说明（`--list` 直接打印）
    module      脚本相对**仓库根**的路径
    default_out 未指定 --out 时的默认输出位置（相对仓库根）
    out_kind    "file" = --out 是 png 文件路径 / "dir" = --out 是目录 / None = 该脚本没有 --out
    kind        "script" = 有可执行入口；"api" = 只有可调用的函数（没有独立 CLI）
    entry       kind="api" 时的可调用入口签名（给 --list 和直接执行时提示用）
"""

CHARTS = [
    dict(
        name="chan-fractal",
        desc="缠论·分型版单标的出图（包含→分型→笔→中枢→背驰，K线+MACD）",
        module="chanlun/fractal/chan_chart.py",
        default_out="reports/charts/chan-fractal",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="chan-ma",
        desc="缠论·均线版单标的出图（MA5/10 双穿 MA20 → 笔/线段/走势类型）",
        module="chanlun/ma/chan_ma_chart.py",
        default_out="reports/charts/chan-ma",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="buypoint",
        desc="买点个股三联图（日线 + 小时线 + 缠论均线版）。"
             "⚠️ 没有独立 CLI/main，只能当库调用："
             "draw_stock_grid(picks, out_path, n_show=120, ...) / "
             "draw_buypoint_chart(disp, day, h1=None, out=None, ...)，"
             "日常由 misc/engine/tech_buypoint.py 调起",
        module="misc/engine/buypoint_chart.py",
        default_out="reports/charts/buypoint",
        out_kind=None,
        kind="api",
        entry="draw_stock_grid(picks, out_path, ...) / draw_buypoint_chart(disp, day, h1=None, out=None, ...)",
    ),
    dict(
        name="simple",
        desc="单图速览（缠论分型版笔/中枢 + VWAP 六轨，K线一屏）。"
             "⚠️ 无 CLI 参数：__main__ 是写死的样板（极米科技）；"
             "要换标的请 import：draw_stock(code, name, d, n_show=120, out=None)",
        module="misc/engine/simple_chart.py",
        default_out="reports/charts/simple",   # 该脚本自己写死 .cache/charts，不改它的行为
        out_kind=None,
        kind="script",
        entry="draw_stock(code, name, d, n_show=120, out=None)",
    ),
    dict(
        name="topology-spd",
        desc="拓扑·SPD 流形四合一图 + 多标的二维相位图（H1 环寿命 × 平均相关性）",
        module="topology/run.py",
        default_out="reports/charts/topology-spd",
        out_kind="dir",
        kind="script",
        entry=None,
    ),
    dict(
        name="topology-terrain",
        desc="拓扑·黎曼流形地形图（左：地形 + 持续同调条码 / 右：相关矩阵曲面）",
        module="topology/terrain.py",
        default_out="reports/charts/topology-terrain",
        out_kind="dir",
        kind="script",
        entry=None,
    ),
    dict(
        name="topology-manifold",
        desc="拓扑·流形图（左：UMAP 结构位置 / 右：里奇曲率网络）。"
             "标题分两块：结构研判（不含方向）+ 趋势层（含方向）",
        module="topology/manifold_topo.py",
        default_out="reports/charts/topology-manifold",
        out_kind="file",
        kind="script",
        entry=None,
    ),
]


def names():
    return [c["name"] for c in CHARTS]


def get(name):
    for c in CHARTS:
        if c["name"] == name:
            return c
    return None
