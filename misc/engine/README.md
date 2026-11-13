# report/ · 报告与买点

对外主入口都在这里。改动前先读 `../skills/tech-summary/SKILL.md`。

**取数（2026-11-13 统一）**：本目录所有行情的**日线 / 分钟线统一走 DataHub**
（`optstack/data_hub`：`index_daily` / `stock_daily` / `minute_bar`）。
DataHub 不可用（别的机器没有 optstack）时自动静默降级本机老链路
（tushare index_daily / `core/datahub` 三级 / `core/minute_hub`）。
开关：`TECH_DATA_SOURCE=datahub|local`（默认 datahub）；`DATA_HUB_PATH` 指定 DataHub 根目录。

| 文件 | 作用 |
|---|---|
| `tech_summary.py` | **五层分析主脚本**：缠论分型版 + 缠论均线版 + SMC + VWAP 六轨 + 综合打分；报告 + 双图 + 推送 |
| `tech_buypoint.py` | 买点分析：指数/行业/概念先过闸，再下钻成分股；个股直连四层 |
| `tech_intraday_buypoint.py` | 盘中：行业买点 Top10 + 个股下钻（个股须站上 VWAP −1σ） |
| `buypoint_chart.py` | 买点三联图 / 网格图（⚠️ 改布局必须跑 `TECH_GRID_DEBUG=1`，验收 `overlaps=0 oob=0`） |
| `wyckoff_buypoint_v2.py` | 威科夫吸筹买点（吸筹项再过四层） |
| `sector_buypoint_top20.py` | 板块买点 Top20 |
| `simple_chart.py` | 单图速览 |
| `wyckoff_today_fetch.py` | 今日快照抓取 |
| `*_daily.sh` | 每日包装脚本（crontab 入口） |

## 出图硬规矩（2026-11-11 定）

- 网格图**一张最多 20 个子图，不分页**
- 每个子图下方必须标买卖点（左下买点 / 右下卖点 + VWAP 分位）
- 布局用**绝对英寸**（`add_axes`），不用 `tight_layout` / `bbox_inches="tight"`
- 文字先量宽（`_fit_text`），超宽缩字号，最窄 8.5pt，再超就截断
- **验收标准：`TECH_GRID_DEBUG=1` 跑出来 `overlaps=0` 且 `oob=0`**
