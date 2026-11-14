# misc/charts · 本库唯一画图入口

**为什么有它**：本库能出图的地方散在 4 个目录里（`chanlun/`、`smc/`、`topology/`、`misc/engine/`），
以前每次出图都要去翻脚本路径、猜参数、还要自己建输出目录。2026-11-14 起统一收口到这一个入口，
**以后所有画图都从这里走**，脚本本身一行不改（只做 sys.path 引导 + 参数透传）。

## 用法

```bash
python3 misc/charts/run.py --list                          # 列出全部图（名称/说明/脚本/默认输出）
python3 misc/charts/run.py topology-manifold --target 科创50   # 后面参数原样透传给真实脚本
python3 misc/charts/run.py chan-ma --target 沪深300 --level day
python3 misc/charts/run.py topology-spd --target 沪深300,科创50
python3 misc/charts/run.py chan-fractal --index 中证500 --level h1 --n 120
```

规则：
- **第一个参数是图名**，剩下的全部透传给真实脚本（本层不解析、不改写）。
- **没给 `--out` 就自动出到 `reports/charts/<图名>/`**（统一出口）；你显式给了 `--out` 完全听你的。
- 个别脚本没有 CLI 参数（`buypoint` / `simple`），`--list` 里会标 `[库调用·无 CLI]` 并写明可调用的函数。

## 新加一张图

只改 `registry.py`，加一条 dict（`name / desc / module / default_out / out_kind / kind / entry`），
别的地方不用动。`module` 写**相对仓库根的路径**，`out_kind` 用 `"file"` 或 `"dir"`。
