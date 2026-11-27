#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【兼容转发 shim】缠论分型版+VWAP 单子图已迁至 charts/chan_vwap.py，本文件仅转发。"""
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

from charts.chan_vwap import draw_stock  # noqa: E402,F401
