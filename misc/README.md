# misc/ · 杂项（其余全部）

顶层只留四个代码目录：`chanlun/`（缠论）、`smc/`、`wavelet/`（小波）、`misc/`（其余全部）。
本目录收纳所有**不属于上面三块**的东西，按用途再分子目录：

| 子目录 | 放什么 |
|---|---|
| `core/` | **共享底层**（谁都用）：`datahub` 日线三级灾备 · `minute_hub` 分钟线 · `tushare_init` 统一取数 · `feishu_card` 飞书卡片 · `push_image` 图片推送 |
| `report/` | **报告与买点**（对外主入口）：`tech_summary` 五层分析 · `tech_buypoint` 买点 · `tech_intraday_buypoint` 盘中 Top10 · `buypoint_chart` 三联图 · `wyckoff_buypoint_v2` 威科夫 · `sector_buypoint_top20` · `simple_chart` · `wyckoff_today_fetch` + 两个 `*_daily.sh` |
| `strategies/` | **统一级别入口**：`run_level.py --level m15/h1/day/week` + `levels.py` 适配层 |
| `tools/` | **发布工具**：`gh_commit`（Git Data API 单 commit）· `gh_publish` · `publish_techanly`（本项目发布）· `bootstrap_cache`（首次部署建缓存） |
| `examples/` | 真实样板产物：报告 txt + 两张样板图 |
| `compare/` | 笔/中枢引擎对比图与说明（只有 4 个文件，别往里塞东西） |

> ### ⭐ 库是库，别把杂事混进来（大佛 2026-11-12 定规矩）
> 这个仓库（`techAnly-summary`）**只放技术分析代码 + 与引擎一致性直接相关的对比产物**。
> 其他项目的日常产出（土狗收盘 / 组合复盘 / 鱼哥 L3 / 股债比值 / 各种日报图表）
> **一律写到工作区顶层的 `reports/`**，绝不进本仓库、也绝不进本目录。
>
> 旧 `misc/reports/` 就是这么被浊进来的（一次混了 27 个别的项目的产出，还带 webhook）→
> 2026-11-12 已彻底删除，改名 `compare/` 并写死白名单；发布脚本 `TREE_SKIP_PATHS` 已把
> `reports` 加入永不入库名单。

本层直接放的零散脚本：

| 文件 | 作用 |
|---|---|
| `weekly_zs.py` | 申万行业指数日线（行业 → 指数映射 + 日线取数） |
| `xq_ind_monitor.py` | 雪球组合监控（读持仓 → 行业提示 → 推邮件/卡片） |
| `xq_portfolio.py` | 雪球多组合档案（默尔根 / 亚丽两个实盘各走独立路径） |

> 所有脚本走 `_repo_root()` 自动引导（向上找同时含 `bin/` 与 `chanlun/` 的那层），
> 所以 `python3 misc/report/tech_summary.py`、`python3 bin/tech_summary.py`（shim）都能跑，不用手工改 `sys.path`。
