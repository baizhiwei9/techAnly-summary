#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【兼容转发 shim】笔/中枢对比图已迁至 charts/compare.py，本文件仅转发。"""
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

from charts.compare import (  # noqa: E402,F401
    main, load, candles, draw_pens, draw_zones, remote_geometry, local_geometry,
)


if __name__ == "__main__":
    _sys.exit(main())
