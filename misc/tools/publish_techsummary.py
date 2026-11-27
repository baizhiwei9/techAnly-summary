#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_techsummary.py —— 【兼容别名 / 已废弃 2026-11-11】

历史：这个脚本原先推的是私有库 `baizhiwei9/tech-summary`。
2026-11-11 大佬定稿：技术分析只保留 `baizhiwei9/techAnly-summary` 一个库，
私有库全部内容（代码 / 文档 / 发布工具 / 历史记录）已合并进 techAnly-summary 后删除。

本文件保留只是为了**兼容老命令**：谁还写着 `publish_techsummary.py`，
跑起来会自动转到 `publish_techanly.py`，不会推错库。

正确用法：
    python3 tools/publish_techanly.py --msg "feat: xxx" --dry-run
    python3 tools/publish_techanly.py --msg "feat: xxx" --notes "..."
"""
from __future__ import annotations

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TARGET = os.path.join(_HERE, "publish_techanly.py")

print("[废弃] publish_techsummary.py 已改为转发到 publish_techanly.py",
      file=sys.stderr)

if not os.path.exists(_TARGET):
    print("找不到 %s（发布工具应该和本文件在同一目录）" % _TARGET, file=sys.stderr)
    sys.exit(2)

_spec = importlib.util.spec_from_file_location("publish_techanly", _TARGET)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    sys.exit(_mod.main())
