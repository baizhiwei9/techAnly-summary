# -*- coding: utf-8 -*-
"""_paths —— techAnly-summary 的路径引导（2026-11-27 建立）

背景
----
2026-11-27 大佬要求把 workspace 的 bin/ 目录彻底删掉，技术分析脚本整体迁入
本仓库 `strategies/`。这些脚本原本扁平躺在 bin/ 里，靠 `sys.path.insert(bin)`
就能互相 import；搬到子目录后仍保持扁平，所以本文件只做两件事：

    1. 把「本目录」加进 sys.path（脚本互相 import 用）
    2. 把外部依赖（openclaw 工作区的 scripts/、smc/、misc/ 等）按需挂上，
       全部从环境变量 / ocsecrets 推导，**不写死绝对路径**

用法（在每个策略脚本顶部）：
    from _paths import ensure
    ensure()

注意：本模块自身不依赖 ocsecrets（那玩意可能没装），全部 try/except 兜底。
"""
from __future__ import annotations

import os
import sys

__all__ = ["ensure", "workspace", "here", "add_openclaw_paths"]

_HERE = os.path.dirname(os.path.abspath(__file__))


def here() -> str:
    return _HERE


def workspace() -> str:
    """OpenClaw 工作区根目录（数据、缓存都在那里）。"""
    for key in ("OPENCLAW_WORKSPACE", "WS", "WORKSPACE"):
        v = os.environ.get(key, "").strip()
        if v and os.path.isdir(v):
            return v
    try:  # 有 ocsecrets 就用它（它认得 secrets.env）
        import ocsecrets
        ws = ocsecrets.workspace()
        if ws and os.path.isdir(ws):
            return ws
    except Exception:
        pass
    return "/root/.openclaw/workspace"


def _add(path: str) -> None:
    if path and os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)


def add_openclaw_paths() -> str:
    """把工作区里仍被技术脚本依赖的目录挂上 sys.path。

    这些目录是「还没搬进本仓库」的残留依赖，搬完后可以逐步删掉对应条目。
    """
    ws = workspace()
    for rel in ("", "scripts", "scripts/cri_topology", "scripts/sector_rotation",
                "smc", "misc/core", "misc/engine", "misc/tools",
                "optstack", "chanlun/fractal", "chanlun/ma", "wavelet", "apps"):
        _add(os.path.join(ws, rel))
    return ws


def ensure() -> str:
    """策略脚本的统一初始化：本目录 + 仓库根（smc/charts 包）+ 工作区依赖目录。

    幂等，可重复调用。同时把统一配置的非敏感参数注入环境变量，
    让沿用 `os.environ.get(...)` 的老代码也能读到 config.json 的值。
    """
    _add(_HERE)
    # 仓库根：让 `import smc` / `import charts` / `import chanlun` 生效
    repo_root = os.path.dirname(_HERE)
    _add(repo_root)
    _add(os.path.join(repo_root, "chanlun", "multiscale"))
    ws = add_openclaw_paths()
    _inject_config_env(ws)
    return ws


_SECRET_PAT = (
    "TOKEN", "SECRET", "PASS", "PASSWD", "_KEY", "WEBHOOK", "HOOK",
    "SESSION", "COOKIE", "CREDENTIAL",
)


def _inject_config_env(ws: str) -> int:
    """把 DataHub 统一配置的非敏感参数灌进 os.environ（凭证不注入）。"""
    cfg_dir = os.path.join(ws, "optstack")
    if not os.path.isdir(cfg_dir):
        return 0
    if cfg_dir not in sys.path:
        sys.path.insert(0, cfg_dir)
    try:
        from config.loader import _load as _load_cfg  # noqa: PLC0415
        data = _load_cfg()
    except Exception:
        return 0
    n = 0
    for k, v in data.items():
        if not k or k.startswith("_") or v in ("", None):
            continue
        ku = k.upper()
        if any(s in ku for s in _SECRET_PAT):
            continue
        if os.environ.get(ku):
            continue
        os.environ[ku] = str(v)
        n += 1
    return n
