#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py · 本库唯一画图入口
================================
以后所有出图都从这里走，不要再去记每个脚本的路径和参数：

    python3 misc/charts/run.py --list                                  # 列出全部图
    python3 misc/charts/run.py topology-manifold --target 科创50        # 跑一张
    python3 misc/charts/run.py chan-ma --target 沪深300 --level day
    python3 misc/charts/run.py topology-spd --target 沪深300,科创50

规则：
  · 第一个参数是图名（见 --list），后面**原样透传**给真实脚本，本层不解析不篡改；
  · 没给 --out 时自动补 `--out reports/charts/<图名>/...`，统一出口；
    你显式给了 --out 就完全听你的；
  · 脚本自己的代码一行不改，只做 sys.path 引导 + 参数转发。

登记表在 registry.py —— 新加一张图，只在那里加一条 dict。
"""
from __future__ import annotations

import os
import runpy
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))          # …/misc/charts
_REPO = os.path.dirname(os.path.dirname(_HERE))             # …/（仓库根）

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import registry  # noqa: E402


def _bootstrap_path():
    """把仓库内各代码目录按优先级塞进 sys.path（仓库根最高，保证 import 命中真实模块）。"""
    dirs = [
        _REPO,                       # 仓库根（topology 等包从这里 import）
        os.path.join(_REPO, "misc", "core"),
        os.path.join(_REPO, "smc"),
        os.path.join(_REPO, "chanlun", "fractal"),
        os.path.join(_REPO, "chanlun", "ma"),
    ]
    add = [d for d in dirs if os.path.isdir(d) and d not in sys.path]
    sys.path[0:0] = add              # 保持上面列出的先后顺序


def _print_list():
    import textwrap
    rows = registry.CHARTS
    w = max(len(r["name"]) for r in rows)
    pad = " " * (w + 2)
    print("本库画图入口（统一走 misc/charts/run.py <name> [原脚本参数...]）")
    print("=" * 76)
    for r in rows:
        tag = "" if r.get("kind") == "script" else "   [库调用·无 CLI]"
        body = textwrap.wrap(r["desc"], width=76 - (w + 2), subsequent_indent="") or [""]
        print("%-*s  %s%s" % (w, r["name"], body[0], tag))
        for line in body[1:]:
            print("%s%s" % (pad, line))
        print("%s├ 脚本：%s" % (pad, r["module"]))
        print("%s└ 默认输出：%s%s" % (
            pad, r["default_out"],
            "" if r.get("out_kind") else "（该脚本自带固定输出目录，--out 不一定生效）"))
        if r.get("keywords"):
            print("%s话题词：%s" % (pad, " / ".join(r["keywords"])))
    print("=" * 76)
    print("用法：python3 misc/charts/run.py --list")
    print("      python3 misc/charts/run.py <name> [原脚本参数...]")
    print("      python3 misc/charts/run.py \"画流形图\" --target 沪深300   # 也可以直接说人话")
    print("新增图：编辑 misc/charts/registry.py 加一条 dict（name/desc/module/default_out/keywords）。")
    print()
    print("路由口诀（说哪句话 → 走哪张图）：")
    print("  「画拓扑结构图 / 拓扑图 / 画拓扑」  → topology-manifold（UMAP 结构 + 里奇曲率）")
    print("  「画流形图 / 流形结构图」          → topology-spd（SPD 流形 + TDA + 相位图）")
    print("  「画地形图 / 黎曼地形图」          → topology-terrain")
    print("  「画五大宽基 / 五个指数一张图 / 宽基总览」 → five-index（缠论买卖点 + 拓扑结构，日报配图）")


def _target_tag(extra):
    """从透传参数里取标的，给文件名加个后缀（不同标的不能互相覆盖）。"""
    import re
    val = ""
    for i, a in enumerate(extra):
        if a == "--target" and i + 1 < len(extra):
            val = extra[i + 1]
        elif a.startswith("--target="):
            val = a.split("=", 1)[1]
        elif a in ("--index", "--name") and i + 1 < len(extra) and not val:
            val = extra[i + 1]
        if val:
            break
    val = re.sub(r"[\\/:*?\"<>|\s，,]+", "_", str(val)).strip("_")
    return val or "chart"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_list()
        return 0 if argv else 2

    if argv[0] in ("-l", "--list", "list"):
        _print_list()
        return 0

    name, extra = argv[0], argv[1:]
    item = registry.get(name)
    if item is None:
        # 支持直接说人话（「画流形图」「拓扑结构图」）→ 按 registry.keywords 反查
        hit = registry.resolve(name)
        if hit:
            print("[charts] 「%s」→ %s" % (name, hit))
            name = hit
            item = registry.get(name)
    if item is None:
        sys.stderr.write("未知图名：%s\n可用：%s\n（先跑 --list 看清单）\n"
                         % (name, ", ".join(registry.names())))
        return 2

    script = os.path.join(_REPO, item["module"])
    if not os.path.exists(script):
        sys.stderr.write("找不到脚本：%s\n（registry.py 里的 module 路径可能过期）\n" % script)
        return 2

    out_dir = os.path.join(_REPO, item["default_out"])
    out_kind = item.get("out_kind")

    if item.get("kind") == "api":
        print("[charts] 「%s」没有独立命令行入口，只能当库调用：" % name)
        print("           %s" % item.get("entry"))
        print("           文件：%s" % script)
        print("           日常用法：由 misc/engine/tech_buypoint.py 自动调起并出图。")
        return 2

    # 统一出口：只在用户没给 --out 时补，显式给了就完全透传
    given_out = any(a == "--out" or a.startswith("--out=") for a in extra)
    fwd = list(extra)
    if out_kind and not given_out:
        os.makedirs(out_dir, exist_ok=True)
        if out_kind == "file":
            ts = datetime.now().strftime("%Y%m%d")
            fwd += ["--out", os.path.join(out_dir, "%s_%s_%s.png"
                                         % (name, _target_tag(extra), ts))]
        else:
            fwd += ["--out", out_dir]

    _bootstrap_path()

    print("[charts] %s → %s" % (name, item["module"]))
    if out_kind and not given_out:
        print("[charts] 默认输出目录：%s" % out_dir)
    if not out_kind:
        print("[charts] 注意：该脚本没有 --out 参数，输出位置由脚本自己决定（见 registry desc）")

    old_argv = sys.argv
    sys.argv = [script] + fwd
    try:
        runpy.run_path(script, run_name="__main__")
        rc = 0
    except SystemExit as e:                     # 脚本用 sys.exit(rc) / exit(main()) 收尾
        code = e.code
        rc = 0 if code is None else (code if isinstance(code, int) else 1)
    finally:
        sys.argv = old_argv
    return rc


if __name__ == "__main__":
    sys.exit(main())
