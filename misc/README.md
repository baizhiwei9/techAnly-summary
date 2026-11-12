# misc/ · 杂项（不属于缠论 / SMC / 小波的那部分）

顶层只留四个代码目录：`chanlun/`（缠论）、`smc/`、`wavelet/`（小波）、`misc/`（其余全部）。

| 子目录 | 放什么 |
|---|---|
| `core/` | **共享底层**（谁都用）：`datahub` 日线三级灾备 · `minute_hub` 分钟线 · `tushare_init` 统一取数 · `feishu_card` 飞书卡片 · `push_image` 图片推送 · `weekly_zs` 申万行业 · `xq_ind_monitor` 雪球监控 · `xq_portfolio` 雪球组合 |
| `engine/` | **分析引擎**（核心，不是"报告"）：`tech_summary` 四层分析 · `tech_buypoint` 买点扫描 · `tech_intraday_buypoint` 盘中 Top10 · `buypoint_chart` 三联图 · `wyckoff_buypoint_v2` 威科夫 · `sector_buypoint_top20` · `simple_chart` · `wyckoff_today_fetch` + 两个 `*_daily.sh` |
| `strategies/` | **统一级别入口**：`run_level.py --level m15/h1/day/week` + `levels.py` |
| `tools/` | **发布工具**：`gh_commit` · `gh_publish` · `publish_techanly` · `bootstrap_cache` |
| `examples/` | 真实样板产物：报告 txt + 两张样板图 |
| `compare/` | 笔/中枢引擎对比图与说明（**只有 4 个文件，别往里塞东西**） |

> ### ⭐ 库是库，别把杂事混进来（大佬 2026-11-12 定规矩）
> 这个仓库（`techAnly-summary`）**只放技术分析代码 + 与引擎一致性直接相关的对比产物**。
> 其他项目的日常产出（土狗收盘 / 组合复盘 / 鱼哥 L3 / 股债比值 / 各种日报图表）
> **一律写到工作区顶层的 `reports/`**，绝不进本仓库、也绝不进本目录。
>
> 旧 `misc/reports/` 就是这么被浊进来的（一次混了 27 个别的项目的产出，还带 webhook）→
> 2026-11-12 已彻底删除。发布脚本 `TREE_SKIP_PATHS` 已把 `reports` 加入永不入库名单。

## 2026-11-12 第二次整理（v2）

| 改动 | 原因 |
|---|---|
| `misc/report/` → `misc/engine/` | 里面装的是分析引擎（tech_summary 等），叫 report 会让人以为是产出报告 |
| `weekly_zs.py` / `xq_ind_monitor.py` / `xq_portfolio.py` → `misc/core/` | 它们是被到处 import 的底层模块，摆在 misc 根目录不像"底层" |
| 全部 `sys.path` 引导同步更新 | `misc/engine` 已在 17 个文件的 path 列表里 |

> 所有脚本走「向上探测同时含 `bin/` 与 `chanlun/` 的那层」自动引导，
> 所以 `python3 misc/engine/tech_summary.py`、`python3 bin/tech_summary.py`（shim）都能跑。
