#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口（2026-11-12 目录整理）。

真实文件已移至 `misc/xq_ind_monitor.py`；本文件只为「旧路径仍可调用 / 仍可 import」而保留：
    python3 bin/xq_ind_monitor.py          # cron、shell 脚本、外部调用照旧
    import xq_ind_monitor               # 拿到的仍是真实实现（下方 sys.modules 替换）
"""
import os, sys, runpy, importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
_REAL = os.path.join(os.path.dirname(_HERE), "misc/core/xq_ind_monitor.py")
if not os.path.exists(_REAL):
    sys.exit("[compat] 找不到真实文件：%s（目录结构可能已变化）" % _REAL)

_spec = importlib.util.spec_from_file_location(__name__, _REAL)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[__name__] = _mod
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    runpy.run_path(_REAL, run_name="__main__")
