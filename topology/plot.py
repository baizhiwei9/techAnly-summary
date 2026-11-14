#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【兼容转发 shim】SPD 流形画图已迁至 charts/topology.py，本文件仅转发（topology/run.py 仍走这里）。"""
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

from charts.topology import (  # noqa: E402,F401
    plot_spd, plot_phase, plot_multi, color_for,
    ZONE_LABEL, ZONE_COLORS,
    BG, PANEL, CYAN, BLUE, ORANGE, RED, PURPLE, GOLD, GREEN, GRIDC, TEXTC, MUTED,
)
