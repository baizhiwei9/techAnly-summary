#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""charts · 公共基础设施
=================================
所有画图模块共享的启动代码：仓库根探测 / sys.path / 网络出口策略 / 中文字体 / Agg 后端。

每个 charts/*.py 顶部统一：
    from charts.common import *   # 先跑完 sys.path + net_guard + 字体 + Agg，再 import 计算口径

为什么集中到这里：chan_chart / chan_ma_chart / buypoint_chart / simple_chart / topology
之前各自复制了一份「仓库根探测 + sys.path + 中文字体 + net_guard」样板，改一处要改六处。
2026-11-14 大佬要求「画图功能整到一个目录」，顺手把这份重复样板也收敛成唯一一份。
"""
import os
import sys


def _repo_root(start):
    """向上探测仓库根：同时含 chanlun/ 与 misc/ 的那一层（可在任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "chanlun")) and os.path.isdir(os.path.join(d, "misc")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


# 仓库根：优先 TECH_WS 环境变量（与原各画图文件一致）
WS = os.environ.get("TECH_WS") or _repo_root(__file__)

# 顺序有讲究：最后插入的优先级最高 → bin/ 放最前 = 最低优先级，
# 保证 import 命中真实目录，而不是 bin/ 里的兼容 shim。
for _sd in ("bin", "", "misc/core", "chanlun/fractal", "chanlun/ma", "chanlun/multiscale",
            "chanlun/multiscale/vendor", "misc/engine", "misc", "wavelet", "smc",
            "misc/strategies", "misc/tools"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# 网络出口策略：默认 IPv4；东财行情族走 IPv6（2026-11-12 东财 K 线仅 IPv6 可达）。
# ⚠️ 必须放在 sys.path 建好之后，否则 import net_guard 会失败、退回一刀切禁 IPv6。
try:
    from net_guard import install as _net_install  # noqa: E402
    _net_install()
except Exception:
    import socket
    _orig_gai = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

# Agg 后端（服务器无显示器）
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402,F401
from matplotlib import font_manager  # noqa: E402,F401

# 中文字体：显式注册字体文件（.ttc 按 family 名查找会失败，直接给文件最稳）
_FONT_FILES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
]
_FAM_OK = []
for _f in _FONT_FILES:
    if os.path.exists(_f):
        try:
            font_manager.fontManager.addfont(_f)
            _FAM_OK.append(font_manager.FontProperties(fname=_f).get_name())
        except Exception:
            pass
matplotlib.rcParams["font.sans-serif"] = _FAM_OK + [
    "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Noto Sans CJK SC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
