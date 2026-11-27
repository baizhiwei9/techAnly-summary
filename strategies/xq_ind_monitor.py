#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口（2026-11-12 目录整理）。

真实文件已移至 `apps/xq_ind_monitor.py`；本文件只为「旧路径仍可调用 / 仍可 import」而保留：
    python3 strategies/xq_ind_monitor.py          # cron、shell 脚本、外部调用照旧
    import xq_ind_monitor               # 拿到的仍是真实实现（下方 sys.modules 替换）
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---

# ---- 统一配置（DataHub config）：路径/凭据一律从 DataHub 配置取，env 仅供 loader 内部覆盖 ----
def _bootstrap_hub_root():
    """引导定位 data_hub：env DATAHUB_ROOT → ~/.config/precious-task/libroots.json → 向上探测。"""
    import json as _j, os as _o
    p = (_o.environ.get("DATAHUB_ROOT") or "").strip()
    if p and _o.path.isdir(_o.path.join(p, "data_hub")):
        return p
    try:
        with open(_o.path.expanduser("~/.config/precious-task/libroots.json"), encoding="utf-8") as _f:
            _d = _j.load(_f) or {}
        for _k in ("datahub", "datahub_root", "DATAHUB_ROOT"):
            _v = _d.get(_k)
            if _v and _o.path.isdir(_o.path.join(str(_v), "data_hub")):
                return str(_v)
    except Exception:
        pass
    _d0 = _o.path.dirname(_o.path.abspath(__file__))
    for _ in range(6):
        if _o.path.isdir(_o.path.join(_d0, "data_hub")):
            return _d0
        _d0 = _o.path.dirname(_d0)
    return ""


def _dh_cfg(_k, _d=""):
    try:
        from data_hub import cfg as _c
        return _c(_k, _d) or _d
    except Exception:
        return _d


def _dh_secret(_n, _d=""):
    try:
        from data_hub import get_secret as _g
        return _g(_n, _d) or _d
    except Exception:
        return _d
# -----------------------------------------------------------------------------------

try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, runpy, importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
# 2026-11-28：实体在 OpenClaw 工作区 apps/（本仓库无 apps/）；
# 未配 OPENCLAW_WORKSPACE 时退回迁移前默认，避免拼出相对路径 apps/xq_ind_monitor.py。
_WS = _dh_cfg("OPENCLAW_WORKSPACE") or os.path.expanduser("~/.openclaw/workspace")
_REAL = os.path.join(_WS, "apps/xq_ind_monitor.py")
if not os.path.exists(_REAL):
    sys.exit("[compat] 找不到真实文件：%s（目录结构可能已变化）" % _REAL)

_spec = importlib.util.spec_from_file_location(__name__, _REAL)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[__name__] = _mod
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    runpy.run_path(_REAL, run_name="__main__")
