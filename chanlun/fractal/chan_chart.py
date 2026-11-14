#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【兼容转发 shim】缠论分型版画图已迁至 charts/chan_fractal.py，本文件仅转发，旧 import 照常可用。"""
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

from charts.chan_fractal import (  # noqa: E402,F401
    chan_geometry, draw, main, OUT_DIR, LV_TXT, VER,
)


if __name__ == "__main__":
    _sys.exit(main())
