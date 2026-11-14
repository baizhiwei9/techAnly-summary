#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【兼容转发 shim】买点图已迁至 charts/buypoint.py，本文件仅转发，旧 import 照常可用。"""
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
for _ in range(6):
    if _os.path.isdir(_os.path.join(_d, "chanlun")) and _os.path.isdir(_os.path.join(_d, "misc")):
        if _d not in _sys.path:
            _sys.path.insert(0, _d)
        break
    _nd = _os.path.dirname(_d)
    if _nd == _d:
        break
    _d = _nd

from charts.buypoint import (  # noqa: E402,F401
    draw_buypoint_chart, draw_stock_grid, find_sells_for_chart,
    CHART_DPI, GRID_DPI, CHART_DIR,
)
