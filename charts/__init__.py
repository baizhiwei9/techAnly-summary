#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""charts · 技术分析画图统一模块
===================================
把散落在 chanlun/、misc/、topology/ 的画图功能收拢到这一个目录，按口径分子模块：

  charts.chan_fractal   缠论·分型版 K线图（K线 + 笔 + 中枢 + 买卖点 + MACD）
  charts.chan_vwap      缠论·分型版 + VWAP 单子图（K线 + 笔 + 中枢 + VWAP ±1σ/±2σ）
  charts.chan_ma        缠论·均线版 K线图（MA + 笔 + 线段 + 中枢 + 走势类型底色）
  charts.buypoint       买点三面板大图 + 成分股网格（日线/小时线/均线版）
  charts.topology       SPD 流形图（plot_spd / plot_phase / plot_multi）
  charts.terrain        黎曼流形地形图（build）
  charts.compare        笔/分型 引擎对比图（candles / draw_pens / draw_zones）

统一入口（惰性导入，避免 import charts 时把 mplfinance/ripser/chan_smc_index 全拉起来）：

    from charts import draw_chan_fractal, draw_chan_ma, draw_chan_vwap
    from charts import draw_buypoint_chart, draw_stock_grid
    from charts import plot_spd, plot_phase, plot_multi, draw_terrain

原有入口文件（chanlun/fractal/chan_chart.py 等）保留为转发 shim，旧 import 全部照常可用。

几何/指标计算不在这里：chan_geometry 的底层在 chanlun/fractal/chan_smc_index.py，
SPD 数值在 topology/spd.py；本目录只收「画图」。
"""

__version__ = "0.1.0"

# 惰性导入表：charts.<attr> / from charts import <attr> 都走这里
_LAZY = {
    # chan_fractal
    "chan_geometry": ("chan_fractal", "chan_geometry"),
    "draw_chan_fractal": ("chan_fractal", "draw"),
    # chan_vwap
    "draw_chan_vwap": ("chan_vwap", "draw_stock"),
    # chan_ma
    "build_chan_ma": ("chan_ma", "build"),
    "draw_chan_ma": ("chan_ma", "draw"),
    # buypoint
    "draw_buypoint_chart": ("buypoint", "draw_buypoint_chart"),
    "draw_stock_grid": ("buypoint", "draw_stock_grid"),
    # topology
    "plot_spd": ("topology", "plot_spd"),
    "plot_phase": ("topology", "plot_phase"),
    "plot_multi": ("topology", "plot_multi"),
    "color_for": ("topology", "color_for"),
    # terrain
    "draw_terrain": ("terrain", "build"),
    # compare
    "draw_compare": ("compare", "main"),
}

__all__ = list(_LAZY.keys()) + [
    "chan_fractal", "chan_vwap", "chan_ma", "buypoint",
    "topology", "terrain", "compare", "common",
]


def __getattr__(name):
    if name in _LAZY:
        import importlib
        mod_name, attr = _LAZY[name]
        mod = importlib.import_module("charts." + mod_name)
        return getattr(mod, attr)
    raise AttributeError("module 'charts' has no attribute %r" % name)
