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

__all__ = ["ensure", "workspace", "here", "add_openclaw_paths", "secret", "feishu_chat"]

_HERE = os.path.dirname(os.path.abspath(__file__))


def here() -> str:
    return _HERE


def workspace() -> str:
    """数据根目录（历史数据、缓存都在那里）。

    2026-11-28：不读环境变量（env 覆盖由 DataHub loader 内部统一处理），
    也不硬编码 ``~/.openclaw/workspace``。解析顺序：
    DataHub 统一配置（secrets / paths 段的 openclaw_workspace）
    → ocsecrets（认得 secrets.env）→ 空串（调用方自行决定跳过或报错）。
    """
    try:  # DataHub 统一配置（config.local.json：secrets 段优先）
        from data_hub import get_secret as _gs
        v = (_gs("OPENCLAW_WORKSPACE", "") or "").strip()
        if v and os.path.isdir(v):
            return v
    except Exception:
        pass
    try:  # 兼容放在 paths 段的老配置
        from data_hub import cfg as _c
        v = (_c("OPENCLAW_WORKSPACE", "") or "").strip()
        if v and os.path.isdir(v):
            return v
    except Exception:
        pass
    try:  # 有 ocsecrets 就用它（它认得 secrets.env）
        import ocsecrets
        ws = ocsecrets.workspace()
        if ws and os.path.isdir(ws):
            return ws
    except Exception:
        pass
    return ""


def _add(path: str) -> None:
    if path and os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)


def add_openclaw_paths() -> str:
    """把工作区里仍被技术脚本依赖的目录挂上 sys.path。

    这些目录是「还没搬进本仓库」的残留依赖，搬完后可以逐步删掉对应条目。
    """
    ws = workspace()
    if not ws:
        return ""
    for rel in ("", "scripts", "scripts/cri_topology", "scripts/sector_rotation",
                "smc", "misc/core", "misc/engine", "misc/tools",
                "optstack", "chanlun/fractal", "chanlun/ma", "wavelet", "apps"):
        _add(os.path.join(ws, rel))
    return ws


def secret(name: str, env_key: str = "", default: str = "") -> str:
    """取一个内标识/地址：环境变量 → ocsecrets → default（不写死在仓库里）。

    例：``feishu_chat("CHAN_FEISHU_CHAT")``。
    """
    for key in (env_key, name):
        if key:
            v = os.environ.get(key, "").strip()
            if v:
                return v
    try:
        import ocsecrets
        v = ocsecrets.get(name, (env_key,) if env_key else (), default)
        if v:
            return str(v).strip()
    except Exception:
        pass
    return (default or "").strip()


def feishu_chat(env_key: str = "", default: str = "") -> str:
    """飞书 chat_id：优先本脚本专用环境变量，其次通用 FEISHU_CHAT_ID / ocsecrets。"""
    return secret("FEISHU_CHAT_ID", env_key, default)


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
    if not ws:
        return 0
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
