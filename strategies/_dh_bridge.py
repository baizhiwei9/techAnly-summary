# -*- coding: utf-8 -*-
"""_dh_bridge —— 技术脚本访问 DataHub 私有库模块的桥（2026-11-27）

背景：技术脚本（如 l3_fetch、tda_60_daily_v2）需要 marketdb / market_hub 这类
数据层模块。这些模块已归入 DataHub 私有库（datahub-multi-tier），不在本仓库。
本文件把它们注册成顶层模块名，让 `import marketdb` 继续可用。
"""
import os, sys, importlib

_MODS = ("marketdb", "market_hub")
_registered = False


def register() -> bool:
    global _registered
    if _registered:
        return True
    try:
        import ocsecrets
        ws = ocsecrets.workspace()
    except Exception:
        ws = os.environ.get("OPENCLAW_WORKSPACE") or os.path.expanduser("~/.openclaw/workspace")
    opt = os.path.join(ws, "optstack")
    if opt not in sys.path:
        sys.path.insert(0, opt)
    ok = False
    for name in _MODS:
        if name in sys.modules:
            ok = True
            continue
        try:
            if name == "marketdb":
                m = importlib.import_module("data_hub.marketdb")
            else:
                m = importlib.import_module("data_hub.snapshot.hub")
            sys.modules[name] = m
            ok = True
        except Exception:
            continue
    _registered = ok
    return ok
