# techAnly-summary 对外接口与参数手册

> **这份文件是 techAnly-summary 的唯一调用说明。只读这一份就能正确调用，不需要翻源码。**
> 买卖点优先级、四套口径的权威定义在 `README.md` 的「买卖点优先级规则总表」与「硬规矩」两节；本文负责**接口、参数、变量、产物**。

## 0. 定位与边界

纯规则技术分析汇总（**0 LLM、0 token**），做三件事：

| 层 | 职责 | 入口 |
|---|---|---|
| 分析引擎 | 缠论（分型版/均线版）、SMC、VWAP 六轨、拓扑流形（SPD/TDA/里奇曲率）、小波 AC1-VMD | `misc/engine/*.py` 与各库模块 |
| 画图 | K线 + 买点/卖点标注、多面板、网格图 | `charts/*`、`chanlun/*/chan_*_chart.py` |
| 交付 | 文字报告、飞书卡片（图+文一条消息）、QQ、告警触发 | `misc/core/feishu_card.py`、`misc/core/notify.py`（通道由调用方注册）、`misc/core/mailer.py`（通用 SMTP 工具） |

**明确不做**：原始行情抓取（统一走 DataHub，本仓库只做适配层 `misc/core/dh_bridge.py`）、回测资金曲线以外的绩效归因、期权定价（归 options_utility）。

## 1. 安装与初始数据

```bash
pip install -r requirements.txt
python3 misc/engine/tech_summary.py --target 中证500      # 冒烟：跑通即环境 OK
```

首次运行会生成缓存（放在仓库 `.cache/`，缺失会自动重建）：

| 文件 | 内容 |
|---|---|
| `.cache/sw_member_all.json` | 5900+ 只个股的申万 L1/L2/L3 归属 |
| `.cache/sw_index_map.json` | 129 个申万行业指数代码 |
| `.cache/tech_stock_names.json` | 5900+ 股票中文名（缺失时报告里只剩 6 位代码） |

## 2. 快速开始

```bash
# ① 五层完整分析（宽基中文名 / ts_code / 6 位代码都行）
python3 misc/engine/tech_summary.py --target 中证500
# ② 出报告 + 推飞书卡片（文字 + 两张图，同一条消息）
python3 misc/engine/tech_summary.py --target 600519 --push
# ③ 只出报告不出图 / 改窗口
python3 misc/engine/tech_summary.py --target 000016.SH --no-chart --n-day 120 --n-h1 150
# ④ 买点分析：行业先过闸，再下钻成分股
python3 misc/engine/tech_buypoint.py --target 传媒
# ⑤ 盘中行业买点 Top10（含当日盘中 K 线）
python3 misc/engine/tech_intraday_buypoint.py --top 10 --push
# ⑥ 缠论·均线版单标的出图（图大、结构清楚）
python3 chanlun/ma/chan_ma_chart.py --target 中证500 --n 300
```

## 3. CLI 入口与参数

### 3.1 `misc/engine/tech_summary.py` —— 五层完整分析

| 参数 | 默认 | 说明 |
|---|---|---|
| `--target` / `--index` | `中证500` | 标的：宽基中文名 / `000905.SH` / 6 位代码 |
| `--n-day` | 120 | 日线窗口根数 |
| `--n-h1` | 150 | 小时线窗口根数 |
| `--no-chart` | 关 | 只出文字报告，不画图 |
| `--out` | 无 | 报告文本存档路径 |
| `--chart-dir` | `OUT_DIR` | 图片输出目录 |
| `--push` | 关 | 推飞书卡片（含双图） |
| `--chat` | 私聊 | 飞书 `chat_id` |

### 3.2 `misc/engine/tech_buypoint.py` —— 买点分析（行业→成分股）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--target` / `-t` | 必填 | 指数/行业/概念/个股：`中证500` / `传媒` / `文化传媒概念` / `600519` |
| `--kind` | `auto` | 标的类型：auto/index/industry/concept/stock |
| `--codes` | 无 | 直接给代码（逗号分隔），跳过标的解析 |
| `--no-scan` | 关 | 只分析板块自身，不扫成分股 |
| `--force-scan` | 关 | 行业自身无买点也强制扫成分股 |
| `--list-concepts` | 无 | 列出（匹配的）同花顺概念名 |
| `--scan-industries` | 关 | 扫全部申万行业，看哪些行业自身有买点 |
| `--level` | `all` | `--scan-industries` 的层级范围 |
| `--chart` / `--chart-top` | 关 / 3 | 是否出图、给前 N 只可买个股出图（0=不出） |
| `--targets` / `--top` | 无 / 0 | 多行业合并分析；只取有买点的前 N 只 |
| `--grid` | 关 | 多标的网格图（每只一个子图）+ 板块指数图 |
| `--dpi` / `--bars` | 0 / 120 | 出图 DPI（0=用 `TECH_CHART_DPI`）、每子图 K 线根数 |
| `--push` / `--chat` / `--out` | 关 / 私聊 / 无 | 推送与存档 |

### 3.3 `misc/engine/tech_intraday_buypoint.py` —— 盘中行业买点 Top

| 参数 | 默认 | 说明 |
|---|---|---|
| `--top` | 3 | 选几个行业（建议 2~3） |
| `--limit` | 0 | 调试：只扫前 N 个板块 |
| `--with-concept` | 关 | 行业 + 概念一起排 |
| `--no-stock` / `--stock-per` / `--stock-cap` | 关 / 3 / 40 | 只看行业；每行业列几只个股；每板块最多扫多少只成交额最大成分股 |
| `--push` / `--qq` / `--json` / `--dry` | 关 / 关 / 空 / 关 | 推飞书 / 推 QQ / 结果落盘 JSON / 只打印不推送 |

### 3.4 缠论出图

| 脚本 | 常用参数 |
|---|---|
| `chanlun/ma/chan_ma_chart.py` | `--target 中证500`（指数）/ `--target 600519 --n 300` / `--push` |
| `chanlun/fractal/chan_chart.py` | `--target`、`--n`、`--push`（分型版单标的图） |

## 4. 口径与买卖点

| 口径 | 模块 | 说明 |
|---|---|---|
| 缠论·分型版 | `chanlun/fractal/`、`charts/chan_fractal.py` | 分型→笔→线段→中枢；一二三买卖点 |
| 缠论·均线版 | `chanlun/ma/chan_ma_trend_v2.py` | 用 MA5/10 双穿 MA20 定笔、MA20/40 双穿 MA80 定线段；MA160 判大级别 |
| SMC | `smc/smc_core.py` `smc/smc_engine.py` | 结构破坏/BOS/CHoCH、OB、FVG；h1 及以上级别 |
| VWAP 六轨 | `charts/chan_vwap.py`、`SMC_VWAP_*` | VWAP ±kσ 通道与买点叠加 |
| 拓扑流形 | `topology/spd.py` `manifold_topo.py` `charts/terrain.py` | SPD 流形 + TDA（H0/H1）+ Ollivier-Ricci 曲率 |

买点三档（一买/二买/三买）与卖点对称三档的**权威判定表**在 `README.md`「买卖点优先级规则总表」；本文不复述，避免两份口径漂移。判定阈值全部走 §6 的 `SCAN_*` / `IBP_*` 变量。

## 5. 库模块地图

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `misc/core/datahub.py` | 三级灾备统一行情层（本仓取数门面） | `get_kline` 等取数函数（内部走 data_hub） |
| `misc/core/dh_bridge.py` | 技术分析库 → DataHub 的**纯适配层**（无抓取逻辑） | 见附录 A |
| `misc/core/datafeed.py` | 盘中买点数据获取（多源 + 限流保护） | 见附录 A |
| `misc/core/feishu_card.py` | 飞书交互式卡片（图文一条消息） | `build_card` / 发送函数 |
| `misc/core/notify.py` | **告警通知注册表**：库内只触发、通道由调用方注册（零内置通道，可公开） | `register` / `emit` / `emit_bool` / `autoload`，见附录 A |
| `misc/core/mailer.py` | 通用 SMTP 工具（收发件人脱敏输出） | 见附录 A |
| `charts/*` | 画图统一层：K线、买点、缠论、VWAP、拓扑、对比、网格 | `charts/registry.py` 注册表 |
| `smc/*` | SMC 多资产模拟交易引擎（结构/回测/DB/通知/飞书） | `smc_core` `smc_engine` `smc_db` |
| `topology/*` | SPD 流形 + TDA + 里奇曲率；`run.py` 为统一入口 | `spd.compute` `run.main` |
| `wavelet/*` | AC1-VMD 趋势层、小波信号监控（独立模块） | 见附录 A |
| `chanlun/multiscale/chanlun_backtest/` | 缠论多尺度回测（引擎/信号/因子/报告） | `engine.run_backtest` `signals.*` |

## 6. 环境变量（113 个）

> 变量很多是历史兼容；真正要调的集中在 `SCAN_*`（扫描阈值）、`IBP_*`（盘中 Top）、`SMC_*`（SMC 参数）、`TECH_*`（窗口/DPI）、`THS_*`（同花顺限流）、`FEISHU_*`/`MAIL_*`（推送）。

| 变量 | 默认值 | 出现位置（模块） |
|---|---|---|
| `CHAN_AUDIT` | `—` | `charts/chan_fractal.py` |
| `CHAN_FEISHU_CHAT` | `—` | `chanlun/fractal/chan_smc_index.py`, `chanlun/multiscale/chanlun_backtest/vendor/chan_smc_index.py`, `misc/core/feishu_card.py` |
| `CHAN_MACD_FAST` | `"10"` | `chanlun/fractal/chan_smc_index.py`, `chanlun/multiscale/chanlun_backtest/vendor/chan_smc_index.py` |
| `CHAN_MACD_SIGNAL` | `"5"` | `chanlun/fractal/chan_smc_index.py`, `chanlun/multiscale/chanlun_backtest/vendor/chan_smc_index.py` |
| `CHAN_MACD_SLOW` | `"20"` | `chanlun/fractal/chan_smc_index.py`, `chanlun/multiscale/chanlun_backtest/vendor/chan_smc_index.py` |
| `CHAN_MA_BIN` | `—` | `chanlun/multiscale/chanlun_backtest/ma_trend.py` |
| `CHAN_NEAR_PCT` | `"3"` | `chanlun/fractal/chan_smc_index.py`, `chanlun/multiscale/chanlun_backtest/vendor/chan_smc_index.py` |
| `CHAN_QQ_ACCOUNT` | `""` | `misc/core/push_image.py`, `misc/tools/publish_techanly.py` |
| `CHAN_QQ_TARGET` | `""` | `misc/core/push_image.py`, `misc/tools/publish_techanly.py` |
| `CHAN_SELL_ABOVE_PCT` | `"5"` | `chanlun/fractal/chan_smc_index.py` |
| `CHART_DPI` | `"200"` | `charts/buypoint.py`, `misc/engine/tech_summary.py` |
| `DATAHUB_DEP_DIRS` | `""` | `charts/phase_transition.py` |
| `DATA_HUB_PATH` | `—` | `charts/chan_fractal.py`, `charts/five_index.py`, `charts/five_topo.py` |
| `DG_BREAK` | `"4"` | `misc/core/datafeed.py` |
| `DG_MIN_GAP` | `"0.35"` | `misc/core/datafeed.py` |
| `DH_HTTP_TIMEOUT` | `"10"` | `charts/phase_transition.py` |
| `DH_MAX_RETRIES` | `"1"` | `charts/phase_transition.py` |
| `FEISHU_APP_ID` | `—` | `chanlun/fractal/chan_smc_index.py`, `chanlun/multiscale/chanlun_backtest/vendor/chan_smc_index.py`, `misc/core/feishu_card.py` |
| `FEISHU_APP_SECRET` | `—` | `chanlun/fractal/chan_smc_index.py`, `chanlun/multiscale/chanlun_backtest/vendor/chan_smc_index.py`, `misc/core/feishu_card.py` |
| `FEISHU_WEBHOOK` | `''` | `chanlun/multiscale/chanlun_backtest/run_daily_scan.py` |
| `FRACTAL_NOTIFY_MAX_AGE_BARS` | `"10"` | `chanlun/multiscale/Strategy/chanlun_fractal_Strategy.py` |
| `FRACTAL_STATE_FILE` | `"state/chanlun_fractal_state.json"` | `chanlun/multiscale/Strategy/chanlun_fractal_Strategy.py` |
| `GH_TOKEN` | `—` | `misc/tools/gh_publish.py` |
| `GITHUB_PUBLISH_DENYLIST` | `os.path.expanduser("~/.config/gh/publ...` | `misc/tools/gh_publish.py` |
| `GITHUB_TOKEN` | `—` | `misc/tools/gh_publish.py` |
| `IBP_QQ_ACCOUNT` | `"bot2"` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_QQ_TARGET` | `—` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_STOCK_VWAP_SIGMA` | `"-1.0"` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_WEEK_BARS` | `"900"` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_WEEK_BUY_NEAR_DN` | `"10"` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_WEEK_BUY_NEAR_UP` | `"30"` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_WEEK_EB_MAX_AGE` | `"40"` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_WEEK_TB_MAX_AGE` | `"60"` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_WEEK_TB_NEAR` | `"20"` | `misc/engine/tech_intraday_buypoint.py` |
| `IBP_WEEK_YB_MAX_AGE` | `"26"` | `misc/engine/tech_intraday_buypoint.py` |
| `IPV6_OK_HOSTS` | `""` | `misc/core/net_guard.py`, `misc/core/net_guard_hook.py` |
| `TECHANLY_NOTIFIERS` | `—` | `misc/core/notify.py`、`charts/chan_fractal.py`、`charts/rmt_rc.py`、`misc/engine/rcascade.py`（`os.pathsep` 分隔的通知模块路径/模块名，`emit()` 时自动 import） |
| `MAIL_CONFIG` | `—` | `misc/core/mailer.py` |
| `MAIL_DRY_RUN` | `""` | `misc/core/mailer.py` |
| `MAIL_RENDERER` | `—` | `misc/core/mailer.py` |
| `NET_GUARD` | `"1"` | `misc/core/net_guard_hook.py` |
| `NET_GUARD_VERBOSE` | `"0"` | `misc/core/net_guard.py`, `misc/core/net_guard_hook.py` |
| `OPENCLAW_JSON` | `—` | `smc/smc_feishu.py` |
| `OPTSTACK_DIR` | `—` | `misc/engine/opt_vol.py` |
| `OPT_VOL_CACHE` | `os.path.join(
    os.path.expanduser("~"` | `misc/engine/opt_vol.py` |
| `PCR_OI_WS` | `"/root/.openclaw/workspace"` | `charts/chan_fractal.py` |
| `RC_STALE_CUTOFF_HOUR` | `"18"` | `misc/engine/rcascade.py` |
| `RC_STALE_MISS` | `"0"` | `misc/engine/rcascade.py` |
| `RMT_PANEL_DIR` | `"/root/.cache/rmt_panel"` | `charts/rmt_rc.py` |
| `SCAN_BUY_NEAR_DN` | `"3"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_BUY_NEAR_UP` | `"10"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_DAY_BARS` | `"0"` | `misc/engine/tech_buypoint.py` |
| `SCAN_EB_MAX_AGE` | `"60"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_EB_NEAR` | `"10"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_H1_TB_MAX_AGE` | `"120"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_H1_TB_NEAR` | `"8"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_H1_YB_MAX_AGE` | `"60"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_SELL_MAX_AGE` | `"90"` | `misc/engine/tech_buypoint.py` |
| `SCAN_SELL_NEAR` | `"6"` | `misc/engine/tech_buypoint.py` |
| `SCAN_TB_MAX_AGE` | `"90"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_TB_NEAR` | `"10"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SCAN_YB_MAX_AGE` | `"40"` | `misc/engine/tech_buypoint.py`, `misc/engine/tech_summary.py` |
| `SMC_INIT` | `500_000.0` | `smc/smc_core.py` |
| `SMC_MSS_OFF` | `"0"` | `smc/smc_core.py` |
| `SMC_STOP_ATR` | `2.0` | `smc/smc_core.py` |
| `SMC_STOP_OFF` | `—` | `smc/smc_core.py` |
| `SMC_STOP_PCT` | `0.05` | `smc/smc_core.py` |
| `SMC_VWAP_ADDON` | `"1"` | `smc/smc_core.py` |
| `SMC_VWAP_FULL_K` | `3.0` | `smc/smc_core.py` |
| `SMC_VWAP_HALF_K` | `2.0` | `smc/smc_core.py` |
| `SMC_VWAP_MA` | `40` | `smc/smc_core.py` |
| `SMC_VWAP_MID_K` | `1.0` | `smc/smc_core.py` |
| `SMC_VWAP_SL_K` | `1.0` | `smc/smc_core.py` |
| `SMC_VWAP_SL_OFF` | `"0"` | `smc/smc_core.py` |
| `SMC_VWAP_TP` | `"1"` | `smc/smc_core.py` |
| `SMC_VWAP_WIN` | `60` | `smc/smc_core.py` |
| `SMC_ZONES` | `"1"` | `smc/smc_notify.py` |
| `SMC_ZONES_MAX` | `"6"` | `smc/smc_notify.py` |
| `SMC_ZONES_NEAR` | `"2.0"` | `smc/smc_notify.py` |
| `SWD_TTL_MIN` | `"180"` | `misc/core/sw_daily.py` |
| `TECHANLY_BIN` | `—` | `chanlun/multiscale/chanlun_backtest/sync_vendor.py` |
| `TECH_ALLOW_EM` | `"0"` | `chanlun/ma/mad_layer.py`, `chanlun/multiscale/chanlun_backtest/data.py`, `chanlun/multiscale/chanlun_backtest/run_daily_scan.py` |
| `TECH_CHART_DPI` | `"220"` | `charts/buypoint.py` |
| `TECH_DATAHUB_REQUIRE_QFQ` | `"0"` | `misc/engine/tech_summary.py` |
| `TECH_DATA_SOURCE` | `"datahub"` | `misc/core/dh_bridge.py`, `misc/engine/tech_summary.py` |
| `TECH_DH_QUIET` | `"1"` | `misc/core/dh_bridge.py` |
| `TECH_DH_VERBOSE` | `"0"` | `misc/core/dh_bridge.py`, `topology/data_loader.py` |
| `TECH_EMAIL_SCRIPT` | `"/root/.openclaw/workspace/scripts/se...` | `charts/rmt_rc.py` |
| `TECH_FEISHU_CHAT` | `—` | `chanlun/fractal/chan_smc_index.py`, `chanlun/multiscale/chanlun_backtest/vendor/chan_smc_index.py`, `misc/core/feishu_card.py` |
| `TECH_FONT` | `""` | `charts/phase_transition.py` |
| `TECH_GRID_DEBUG` | `—` | `charts/buypoint.py` |
| `TECH_MA20_DN_BARS` | `"5"` | `misc/engine/tech_summary.py` |
| `TECH_MA20_UP_BARS` | `"5"` | `misc/engine/tech_summary.py` |
| `TECH_MA_PANEL_BARS` | `"400"` | `misc/engine/tech_summary.py` |
| `TECH_WS` | `—` | `chanlun/fractal/chan_smc_index.py`, `chanlun/ma/chan_ma_trend_v2.py`, `chanlun/ma/mad_layer.py` |
| `THS_KLINE_TTL` | `"600"` | `misc/core/ths_hub.py` |
| `THS_RETRIES` | `"3"` | `misc/core/ths_hub.py` |
| `THS_SLEEP` | `"0.12"` | `misc/core/ths_hub.py` |
| `THS_TIMEOUT` | `"12"` | `misc/core/ths_hub.py` |
| `TOPO_NO_AUDIT` | `—` | `charts/five_topo.py` |
| `TS_TIMEOUT` | `"50"` | `misc/core/tushare_init.py` |
| `TUSHARE_DIRECT_TOKEN` | `''` | `misc/core/tushare_init.py`, `misc/tools/publish_techanly.py` |
| `TUSHARE_PROXY_URL` | `'https://ttx.dailyfetch.top/'` | `misc/core/tushare_init.py` |
| `TUSHARE_USE_DIRECT` | `'0'` | `misc/core/tushare_init.py` |
| `WAVELET_FEISHU_CHAT_ID` | `''` | `wavelet/signal_monitor.py` |
| `WAVELET_SIGNAL_FILE` | `'/tmp/wavelet_latest_signals.json'` | `wavelet/signal_monitor.py` |
| `WBP_PER_IND` | `"2"` | `misc/engine/wyckoff_buypoint_v2.py` |
| `WBP_STOCK_CAP` | `"40"` | `misc/engine/wyckoff_buypoint_v2.py` |
| `WBP_TOP_IND` | `"10"` | `misc/engine/wyckoff_buypoint_v2.py` |
| `WBP_TOP_STK` | `"20"` | `misc/engine/wyckoff_buypoint_v2.py` |
| `WF_QUOTE_GAP` | `"0.05"` | `misc/engine/wyckoff_today_fetch.py` |
| `WF_QUOTE_TRIES` | `"3"` | `misc/engine/wyckoff_today_fetch.py` |
| `WZS_FEISHU_CHAT` | `—` | `misc/tools/publish_techanly.py` |
| `WZS_QQ_TARGET` | `""` | `misc/tools/publish_techanly.py` |
## 7. 输出物

| 产物 | 位置 | 命名 |
|---|---|---|
| 文字报告 | stdout，或 `--out` 指定 | 由调用方决定 |
| K 线/买点图 | `--chart-dir`（默认 `OUT_DIR`） | `<标的>_<口径>_<日期>.png` |
| 飞书卡片 | 由 `--push` 直接发送 | 图 + 文一条消息 |
| 盘中 Top JSON | `--json` 指定路径 | 便于二次消费 |

## 8. 硬规矩（摘要，权威版见 README §九）

1. 取数一律走 `misc/core/dh_bridge.py`，**不要在业务代码里直接 akshare/tushare**——绕过会丢三级降级与限流保护。
2. 缠论「笔/线段/中枢」的判定只用 `chanlun/` 里的实现，图表层不得自带一套口径。
3. 未走完的笔/线段必须虚线；买点标注必须带年龄与距离（根数、百分比）。
4. 报告里中文名缺失就补 `.cache/tech_stock_names.json`，不要退回只显示 6 位代码。
5. 同花顺接口限流：`THS_SLEEP` / `THS_RETRIES` / `THS_TIMEOUT` 不要调小。
6. 推送失败不等于任务失败：卡片/QQ/邮件各自独立降级，日志里要能看出哪条通道失败。

## 附录 A：模块级签名速查

> 由 `ast` 从当前 main 自动抽取，逐个公开符号列出**完整签名与默认值**；空说明表示该函数无 docstring。
> 覆盖：缠论（均线版/分型版/多尺度回测）、SMC（core/engine/config/db/data/notify）、拓扑（SPD/manifold/terrain/targets/run）、小波（AC1-VMD/信号监控）、取数桥（datahub/dh_bridge/datafeed）、推送（feishu_card/mailer）、画图公共层（charts/common、charts/buypoint）。

### `chanlun/ma/chan_ma_trend_v2.py`
> 缠论·均线版（趋势浪子《交易悟道之路》原文口径）

| 接口 | 签名 | 说明 |
|---|---|---|
| `add_ind(df: pd.DataFrame) -> pd.DataFrame` | — |
| `resample_week(df: pd.DataFrame) -> pd.DataFrame` | — |
| `dual_cross_state(d: pd.DataFrame, fast1: int, fast2: int, slow: int) -> pd.Series` | 双穿状态：两条快线**同时**在慢线上方 = up，同时下方 = dn，其余 = mid |
| `build_bi(d: pd.DataFrame, fast1=5, fast2=10, slow=20) -> list` | 按原文「双穿」造笔：状态翻转即为笔的转折点。 |
| `dual_cross_age(d: pd.DataFrame, fast1: int, fast2: int, slow: int) -> int` | 当前双穿状态已经持续多少根（= 穿越发生在多少根之前）。 |
| `ma_alignment(d: pd.DataFrame, cols=None) -> dict` | 均线排列 —— 大佬 2026-11-08 关键补充： |
| `short_alignment(d: pd.DataFrame, cols=(5, 10, 20)) -> dict` | 短均线排列（笔级别用）：只看给定的几条，判多头/空头/混合。 |
| `ladder(d: pd.DataFrame) -> dict` | 两级 + 大级别支撑：笔 / 线段 / 大级别(MA160) |
| `wei_state(d: pd.DataFrame, fast: int, slow: int, look: int=20) -> str` | 女上位/男上位 —— 原文 751/776 行： |
| `trend_mode(d: pd.DataFrame) -> dict` | 同级别走势只有三种：趋势上 / 趋势下 / 盘整（原文 838 行） |
| `macd_bei_chi(d: pd.DataFrame, bi: list, look_bi: int=6) -> dict | None` | 背驰判定 —— 原文要求「同级别」+ MACD 三个流程： |
| `level_locate(d: pd.DataFrame) -> dict` | 当前级别定位：笔 / 线段在做什么 + 大级别(MA160)给不给支撑。 |
| `bi_walk(d: pd.DataFrame) -> dict` | 当前到底在「笔上」还是「笔下」，以及接下来怎么走（大佬 2026-11-09 要求） |
| `signals(d: pd.DataFrame, look: int=6) -> list` | 均线视角的买卖点。两种来源，都要报： |
| `macd_state(d: pd.DataFrame) -> str` | — |
| `load_index(ts_code: str) -> pd.DataFrame | None` | 指数日线：**data_hub `index_daily`** 优先 → tushare `index_daily` → 本地缓存 |
| `analyse(name, ts_code, level='day') -> dict | None` | 只看**当前级别**够不够支撑交易，不递归、不要求 A2/MA320。 |
| `verdict(d: pd.DataFrame) -> str` | 直接从 DataFrame 出「当前级别能不能交易」一句话（不用先跑 analyse 拿 r） |
| `tradeable(r) -> str` | 当前级别能不能支撑交易 —— 大佬要的就这一句话。 |
| `render(results, level)` | — |
| `main()` | — |

### `chanlun/fractal/chan_smc_index.py`
> 5宽基 · 缠论「几买/距离」+ SMC 日线/周线机会风险（极简版）

| 接口 | 签名 | 说明 |
|---|---|---|
| `merge_bars(bars)` | 包含处理（本地分型版口径）。输入 (high, low) 或 (high, low, close)。 |
| `fractals(mb)` | 顶底分型（本地口径）：顶只看高点、底只看低点，左右各一根合并K线。 |
| `build_bi(mb)` | 成笔（本地口径）：同类分型取更极端；异类间隔 ≥5 根合并K线。 |
| `segs_from_bi(bi, mb=None)` | 笔段列表。i0/i1 仍是**合并K线索引**（age/中枢等旧逻辑依赖它）； |
| `zhongshu_from_segs(segs)` | 中枢（本地口径：逐笔链式延伸）。返回 [{'i','end','zd','zg'}]，按时间顺序。 |
| `macd_hist(close)` | MACD：默认 (10, 20, 5) —— 缠论·分型版本机统一口径 |
| `chan_read(bars, tag)` | — |
| `smc_local(df, k, tag)` | 日/周线 SMC：摆动点 + FVG/OB + confirm_zone 买点确认。 |
| `fetch_daily(ts_code)` | 指数日线：**data_hub `index_daily`** 优先 → tushare `index_daily` → 本地缓存 |
| `resample_w(df)` | — |
| `bars_of(df)` | (high, low, close)。close 供 MACD 使用真实收盘价。 |
| `fetch_60(sh_code)` | 60分钟（小时线）——**data_hub `minute_bar`** 优先 → 腾讯/新浪分钟K线兜底 |
| `feishu_push(text, title='📐 宽基缠论+SMC · 机会/风险')` | — |
| `spark(series)` | — |
| `ladder(levels, cur, rows=13, width=52)` | 竖轴示意图：levels=[(price, text, tag)]；cur=现价。 |
| `collect(include_h1=True)` | 抓一次数据，返回每个指数的完整判定 dict（供榜单 + 明细复用） |
| `build_text(include_h1=True)` | include_h1=False → 全文不含小时级别（20:15 大盘技术拆解用） |

### `smc/smc_core.py`
> SMC 多资产模拟交易引擎 · 核心结构逻辑 (1H 级别)

| 接口 | 签名 | 说明 |
|---|---|---|
| `vwap_band(df, win=VWAP_WIN)` | 滚动 VWAP 与 σ（只算基础量，四条轨由调用方组合）。 |
| `vwap_tracks(df, win=VWAP_WIN)` | 一次性算出四条轨 + MA40 方向，供播报与决策共用。 |
| `atr(df, n=ATR_N)` | — |
| `stops_for(kind)` | 按品种类别返回止损参数 (atr_mult, pct_dd)；None 表示不加止损。 |
| `stop_lines(pos, i, atr14, kind, last_close=None)` | 返回 (止损价, 止损类型, 距离当前价百分比)；无止损返回 (None,None,None)。 |
| `sma(s, n)` | — |
| `pivots(df, k)` | — |
| `find_fvgs(df, atr_series, lookback=FVG_LOOKBACK, min_atr_ratio=FVG_MIN_ATR, end=None, include_filled=False)` | 3 根 K 线失衡缺口（FVG / Fair Value Gap）。 |
| `confirm_zone(df, z, end=None, win=None, need=None, near_atr=None, atr_val=None)` | 判断单个 FVG / OB 现在算不算「买点（看涨区）」或「压制有效（看跌区）」。 |
| `confirm_text(sig, px=None)` | 把 confirm_zone 的结果压成一行短文案。 |
| `pick_signals(fvgs, obs, df, end=None, max_n=4, win=None, need=None, atr_val=None, near_atr=None)` | 从候选 zone 里挑最该被看见的几条确认信号： |
| `smc_layer(df, k, atr_n=14, max_n=4)` | 单级别 SMC 层：摆动点 + FVG + OB + 方向确认信号（统一入口）。 |
| `find_order_blocks(df, hi_idx, lo_idx, atr_series=None, lookback=OB_LOOKBACK, lookback_far=OB_LOOKBACK_FAR, end=None, k=1, include_invalid=False, min_atr_ratio=0.0)` | 订单块（Order Block）。 |
| `zones_text(fvgs, obs, px=None, max_each=2)` | 把 FVG / OB 列表压成两行播报文案（飞书卡片能装下）。 |
| `aggregate(df, tf)` | — |
| class `Frame` | 单一级别：确认后摆动点链 + regime + 指标。 |
| ↳ `fvgs(i=None, lookback=FVG_LOOKBACK_FAR, min_atr_ratio=FVG_MIN_ATR, include_filled=False)` | 第 i 根（含）之前形成的 FVG。新鲜(fresh)在前，长期未破(sustained)在后。 |
| ↳ `obs(i=None, lookback=OB_LOOKBACK_FAR, include_invalid=False)` | 第 i 根（含）之前形成的 OB（按确认延迟 k 判突破）。 |
| ↳ `zones(i=None)` | {"fvgs": [...], "obs": [...]} 快照（<= i）。 |
| ↳ `signals(i=None, max_n=4, win=CONF_WIN, need=CONF_SCORE)` | 当前 zone 的「买卖点确认」判断（不破 + 方向确认）。 |
| ↳ `his(i)` | — |
| ↳ `los(i)` | — |
| ↳ `regime_at(i)` | — |
| `run_backtest(ltf, frames, variant='A', kind='stock', cls=None)` | — |
| `summarize(res)` | — |
| `build_frames(base_df, level='h1')` | base_df = 母数据（h1 级别: 60m 母数据；day 级别: 日线母数据）。 |

### `smc/smc_engine.py`
> SMC 模拟交易引擎 (h1 级别)

| 接口 | 签名 | 说明 |
|---|---|---|
| `run_one(c, it, tf=None, variant='A', refresh=False, baseline_before=None, silent_all=False)` | tf=None 时按标的类别自动选：股票/ETF=day（日线），期货=h1（小时）。 |
| `main()` | — |

### `smc/smc_config.py`
> SMC 引擎配置（公开版 · 仅保留纯计算所需部分）

| 接口 | 签名 | 说明 |
|---|---|---|
| `trade_tf(it)` | 某标的的交易级别：股票/ETF=日线(day)，期货=小时(h1)。 |
| `level_of(it)` | 某标的的交易级别：股票/ETF=day（日线），期货=h1（小时）。 |
| `base_freq(level)` | 某级别对应的母数据频率。 |

### `smc/smc_db.py`
> 本地模拟交易数据库 (SQLite)

| 接口 | 签名 | 说明 |
|---|---|---|
| `conn()` | — |
| `upsert_instruments(c, instruments)` | — |
| `put_state(c, code, tf, res, it)` | — |
| `put_equity(c, code, tf, curve)` | — |
| `last_event_id(c)` | — |
| `existing_event_keys(c, code, tf)` | — |
| `add_events(c, code, it, tf, events)` | 写入新事件，返回新写入的事件（不含已存在）。 |
| `add_events(c, code, it, tf, events, baseline_before=None, silent_all=False)` | 写入新事件，返回新写入的事件（不含已存在）。 |
| `prune_instrument(c, code, tf, events, trades)` | 删除该标的不再出现在回测结果里的陈旧事件/交易（保持 DB 与回测一致）。 |
| `add_trades(c, code, it, tf, trades)` | — |
| `set_meta(c, k, v)` | — |
| `get_meta(c, k, default=None)` | — |
| `unread_events(c, limit=100)` | — |
| `unread_events_full(c, limit=200)` | 带 extra（entry_dt/entry_px）的未读事件，供富格式通知。 |
| `close_log(c, limit=40)` | 最近的平仓记录（供「平仓逻辑」查询）。 |
| `recent_events(c, limit=8)` | 最近的开/平/减/加事件（不分已读未读），供「最近成交」展示。 |
| `mark_notified(c, ids)` | — |
| `put_bar_signals(c, code, tf, res)` | 写入最新一根 K 线的信号诊断（先清旧）。 |
| `put_stop_state(c, code, tf, entry_px, peak, stop_price, stop_kind, dist_pct, last_close)` | — |
| `clear_stop_state(c, code, tf)` | — |
| `get_stop_state(c, code, tf)` | — |
| `get_bar_signals(c, code=None, tf=None)` | — |

### `smc/smc_data.py`
> SMC 数据获取引擎

| 接口 | 签名 | 说明 |
|---|---|---|
| `get(code, kind, freq='60m', refresh=False)` | 取某个标的的 60m / 30m / 1d 数据（带磁盘缓存，当日已抓过则复用）。 |
| `fetch_all(refresh=False, quiet=False)` | 按各标的自己的交易级别抓母数据：股票/ETF 抓 1d，期货抓 60m（另抓 30m/1d 备用）。 |

### `smc/smc_notify.py`
> SMC 增量通知引擎

| 接口 | 签名 | 说明 |
|---|---|---|
| `send_feishu(text, title=None)` | 发飞书群（多尺度交易群）。 |
| `build_incremental()` | ★ 一轮只发**一条**合并消息（用户 2026-10-08 明确要求）。 |
| `snapshot()` | 全标的看板：持仓（含止损位）/ 空仓待机原因 / 合计权益。 |
| `open_brief(tag='day', dry=False)` | 开盘播报：持仓变化（对比上一次播报）+ 当前持仓列表 + 空仓数量。 |
| `close_log(n=15)` | 最近的平仓记录及理由（人看决策依据）。 |
| `zones_block(items=None, max_items=None, near=None, force_all=False, header=None, signal_only=False, win=None, need=None)` | 把各标的「临近现价」的 FVG / OB 输出成若干行（供快照/开盘播报/增量消息用）。 |
| `signals_block(items=None, max_items=None, header=None)` | 🎯 买卖点确认区：只列「不破 + 方向已确认」的标的（大佬 2026-11-08 口径）。 |
| `smc_core_px(v)` | 价格显示（与 smc_core._fmt_px 同口径，单独包一层避免循环依赖）。 |
| `dispatch(text, tag, use_coze=True, use_feishu=True)` | — |
| `flush(dry=False, use_coze=True, use_feishu=True)` | 一轮只发一条合并消息（用户 2026-10-08 要求）。 |

### `topology/spd.py`
> spd.py · SPD 流形 + TDA + 里奇曲率 指标计算（纯数值，零 LLM）

| 接口 | 签名 | 说明 |
|---|---|---|
| `shrink_cov(R, delta=0.1)` | 样本协方差 + 向单位阵收缩（保证 SPD 正定）。 |
| `le_spectral_dist(lw, lref, k=30)` | Log-Euclidean 谱距离：||log λ_w - log λ_ref||（取前 k 个主特征值）。 |
| `corr_metrics(R)` | 相关矩阵的谱熵 + 平均 |相关性|（SVD 实现，避免大矩阵特征分解）。 |
| `stock_2d_embedding(R)` | 成分股 2D 嵌入：Σ 的前两个右奇异向量（前两主成分载荷）。 |
| `subsample(pts, k=150)` | 确定性降采样点云（ripser 300 点 2.2s → 150 点 ~0.01s）。 |
| `h1_lifetime(pts, rips)` | Vietoris-Rips H1 最大寿命（环生死）。无 H1 类 → 0（环塌缩）。 |
| `hurst_rs(x)` | R/S 方法估计 Hurst 指数（1D 序列）。 |
| `ricci_proxy(g)` | 里奇曲率近似 = -测地距离二阶导数（中心差分）。凹(加速收缩) → 正。 |
| `compute(close, idx_close=None, n_days=60, rips=None, verbose=False)` | 核心计算：close 面板（n_days+1 行）→ 信号 dict。 |
| `classify(corr, h1n, corr_th=0.5, h1_th=0.5)` | 按 corr / 归一化 H1 划分当前象限。返回 zone 标签 key。 |

### `topology/manifold_topo.py`
> manifold_topo.py · 拓扑流形图（UMAP 2D × 里奇曲率网络）

| 接口 | 签名 | 说明 |
|---|---|---|
| `stock_names(codes)` | 股票代码 → 简称（tushare stock_basic）。失败返回 {}。 |
| `fetch_by_tushare(index_code: str, days: int=60)` | tushare 直连：index_weight 取成分 + daily 逐只取收盘。返回 (dates, close_df, name_map)。 |
| `fetch_by_datahub(target: str, days: int=60)` | 走 techAnly 取数层（dh_bridge / data_hub）。 |
| `ollivier_ricci(corr: np.ndarray, k: int=5, alpha: float=0.5)` | 节点平均 Ollivier-Ricci 曲率。 |
| `market_score(close: pd.DataFrame, index_close: pd.Series=None, corr: np.ndarray=None)` | 多维真实打分（−2 风险 … +2 机会）。 |
| `corr_manifold_inputs(close: pd.DataFrame, k: int=5)` | 从收盘面板算拓扑口径三件套：corr / b0 / b1 / 曲率均值。 |
| `avg_curvature(corr: np.ndarray, k: int=5)` | 节点平均 Ollivier-Ricci 曲率（取节点曲率均值 = 「平均曲率」）。 |
| `hermer2_score(b0, b1, curv_mean, b0_prev, b0_now)` | hermer2 口径打分（−2 风险 … +2 机会）。 |
| `trend_layer(close: pd.DataFrame, index_code=None)` | 趋势层（**真正含方向**的数据）—— 与结构分物理隔离，单独一行输出。 |
| `trend_layer_text(tl: dict) -> str` | 趋势层单行文案（图上一行 + stdout 同一份）。前缀写死，不许改。 |
| `pc1_profile(close: pd.DataFrame, corr: np.ndarray, codes, names, top_n: int=10)` | 第一主成分画像：它到底是个什么东西。 |
| `structure_diag(close: pd.DataFrame, corr: np.ndarray, k_cluster: int=5)` | 结构诊断：当前是「抱团」还是「撕裂」，两者各占多少。 |
| `betti_numbers(dmat: np.ndarray, thresh: float)` | ripser 实测在给定半径下的 Betti_0 / Betti_1（有限距离矩阵）。 |
| `umap_embed(dmat: np.ndarray, n_neighbors: int=10, min_dist: float=0.3, seed: int=7)` | — |
| `zs(x: np.ndarray) -> np.ndarray` | — |
| `plot(emb, mom5, curv, deg, corr, close, codes, names, index_code, index_name, out_path, k=5, mkt=None, diag=None, pcp=None, trend=None)` | — |
| `audit_hermer2(target='科创50', index_code='000688.SH', days=250, win=60, step=5, fwd=10, k=5, outdir=None)` | hermer2 口径偏误审计：滚动 60 日窗口 → hermer2_score → 看未来 10 日涨跌。 |
| `main()` | — |

### `topology/terrain.py`
> 【兼容转发 shim】黎曼流形地形图已迁至 charts/terrain.py，本文件仅转发。


### `topology/targets.py`
> targets.py · 标的解析与类型识别

| 接口 | 签名 | 说明 |
|---|---|---|
| class `Target` | 统一标的对象。 |
| `resolve_index(s)` | 把各种指数写法 → (ts_code, display_name) 或 (None, None) |
| `resolve(raw, board_indexes=None, board_concepts=None)` | 把用户输入解析成 Target。 |
| `parse_all(args)` | 解析 --target 参数列表 → [(raw, Target)...]（保留原始输入用于报错） |

### `topology/run.py`
> run.py · 拓扑流形分析统一入口

| 接口 | 签名 | 说明 |
|---|---|---|
| `do_list()` | — |
| `main(argv=None)` | — |

### `wavelet/acvmd_layer.py`
> AC1-VMD 趋势层（独立模块，不挂 tech_summary 主报告）

| 接口 | 签名 | 说明 |
|---|---|---|
| `to_symbol(target)` | 目标 → datahub 符号；返回 (symbol, is_index, 展示名) |
| `load(target, days=800)` | 取日线（自动多源回退），返回 DataFrame[dt,open,high,low,close,volume] |
| `ac1(r, window=AC1_WINDOW)` | lag-1 自相关（窗口末端 window 个收益率） |
| `vmd_slope(close)` | 末 VMD_WINDOW 根价格做 VMD，低序 2 模态合成趋势的末段斜率 |
| `layer(df)` | AC1-VMD 单标的一层；返回 dict（view 取 多/空/震荡） |
| `main()` | — |

### `wavelet/signal_monitor.py`
> 小波策略实盘信号监控

| 接口 | 签名 | 说明 |
|---|---|---|
| `fetch_etf_data(code, market='sh', days=500)` | 获取ETF日线数据（本地parquet新鲜则用 → **data_hub etf_daily** → AKShare 兜底） |
| `fetch_futures_data(symbol='AG', days=800)` | 获取期货主力连续日线 |
| `fetch_futures_1h_data(symbol='AG', days=200)` | 获取期货1小时K线 |
| `detect_etf_signals(df, params, code, name)` | 检测ETF信号变化 |
| `detect_futures_signals(df, params, symbol, name)` | 检测期货信号变化 |
| `load_state()` | 加载上次运行状态 |
| `save_state(state)` | 保存运行状态 |
| `is_new_signal(date_str, state, key)` | 判断是否是新信号（根据日期和信号值去重） |
| `send_feishu_message(chat_id, content)` | 发送飞书消息（卡片） |
| `send_feishu_text(chat_id, text)` | 发送纯文本消息 |
| `build_etf_signal_card(info, action, price)` | 构建ETF信号卡片 |
| `build_futures_signal_card(info, action, price)` | 构建期货信号卡片 |
| `build_status_summary(state)` | 构建持仓汇总卡片 — 多/空分栏格式，每个策略按条目列 |
| `detect_futures_1h_signals(df_1h, df_daily, symbol, name)` | 检测1小时级别期货信号变化 |
| `build_futures_1h_signal_card(info, action, price)` | 构建期货1小时级别信号卡片 |
| `run_monitor()` | — |
| `export_signals_for_consensus(state)` | 把小波最新信号导出到共享JSON，供缠论分型版/统一流水线读取合并 |

### `misc/core/datahub.py`
> DataHub · 三级灾备统一行情层（2026-10-22 建，用户要求）

| 接口 | 签名 | 说明 |
|---|---|---|
| `log(m, err=False)` | — |
| `is_hk(sym)` | — |
| `is_index(sym)` | — |
| `norm(sym)` | 统一成内部形式： |
| `to_ts(sym)` | → TuShare ts_code：000636.SZ / 00700.HK / 000300.SH |
| `to_em(sym)` | → 东财 secid：0.002463 / 1.600183 / 116.00700 |
| `to_sina(sym)` | → 新浪/腾讯符号：sz002463 / sh600183 / hk06160 |
| `d8(d)` | — |
| `validate(rows, tag='', strict=True)` | OHLC 合理性硬校验 —— 防「字段顺序错位」的核心闸门 |
| `daily(sym, end=None, days=1200, cross_check=False, layers=None, want_fresh=True, fresh_tol=0)` | 三级灾备日线。返回标准 [['20261022', o, h, l, c, vol], ...]（升序） |
| `index_daily(sym, end=None, days=600, cross_check=False)` | 指数日线（三级灾备） |
| `sw_code(code)` | 把 '801081' / '801081.SI' / '半导体' 都归一成 '801081.SI'；名称解析失败返回 None |
| `sw_daily(code, days=800, policy='L123')` | 申万行业指数日线（三级灾备，统一出口）。 |
| `trade_dates(end=None, days=60)` | 最近 days 个交易日（升序，含 end 当日若为交易日）。缓存 trade_cal。 |
| `margin_day(day, policy=('tushare', 'akshare'), use_cache=True, write=True)` | 某个交易日的全市场两融明细 {code6: {rzye,rqye,rzrqye,rqyl}}，按日缓存。 |
| `margin_cached_days()` | 已下载的日档日期列表（升序） |
| `margin_history(sym, end=None, days=60, fill=False)` | 单票两融历史（升序） |
| `margin_backfill(days=60, end=None, policy=('tushare', 'akshare'))` | 把最近 days 个交易日的日档全部补齐（一次性回补用，已存在则跳过） |
| `last_date(sym, end=None)` | — |
| `quote(sym)` | 实时快照（只用于盘中展示，不用于技术判定）：{last, prev, pct, name} |
| `self_test(symbols=None)` | 逐源体检：打印各源原始字段，人眼核对字段顺序是否被改 |

### `misc/core/dh_bridge.py`
> dh_bridge · 技术分析库 → data_hub 取数桥（**纯适配层，不含任何抓取逻辑**）

| 接口 | 签名 | 说明 |
|---|---|---|
| `log(msg)` | — |
| `module()` | 惰性加载 data_hub；不可用返回 None（只打一行日志，绝不抛异常） |
| `enabled()` | — |
| `norm_ohlcv(d, keep_amt=False)` | 任意源日线 → DataFrame[dt, open, high, low, close, volume] 升序（可带 vol/amt） |
| `sector_code(code)` | '885431.TI' / '881121' / 885431 → '885431'（同花顺板块码） |
| `sector_list(kind='行业')` | 板块列表 → [{'code','name'}...]；失败 [] |
| `board_code_by_name(name, kind='行业')` | 板块名 → 同花顺板块码（复用 data_hub 的包含匹配，申万名也能对上） |
| `sector_kline(code=None, name=None, kind='行业', years_back=6, period='daily', keep_amt=True)` | 板块K线 → DataFrame[dt,open,high,low,close,volume(,vol,amt)] 升序；失败 None |
| `sector_kline_rows(code=None, name=None, kind='行业', years_back=6, lmt=None)` | 板块K线 → [{date,open,high,low,close,vol,amt}...] 升序（老 datafeed/wyckoff 口径） |
| `sector_members(code=None, name=None, kind='行业')` | 板块成分股 → [{'code': 6位, 'name': str}...]；**概念板块 data_hub 取不到 → []** |
| `stock_kline_rows_ths(code, years_back=6, lmt=None)` | 个股日线（data_hub 的同花顺个股源）→ [{date,open,...,vol,amt}...] 升序 |
| `index_components(index_code)` | 指数成分股 → [ts_code...]；失败 [] |
| `index_daily(ts_code, lookback=1200, start=None, end=None)` | 指数日线 → DataFrame[dt,open,high,low,close,volume]；失败 None |
| `stock_daily(ts_code, start=None, end=None, lookback=None, adjust='')` | 个股日线 → DataFrame[dt,open,high,low,close,volume]；失败 None |
| `etf_daily(ts_code, lookback=None, start=None, end=None)` | ETF 日线 → DataFrame[dt,open,high,low,close,volume]；失败 None |
| `minute_bar(code, freq='60', n=400, kind='index')` | 分钟K线 → DataFrame[dt,open,high,low,close,volume] 升序；失败 None |
| `sector_spot(kind='行业')` | 板块实时快照 → DataFrame[code,name,price,pct_chg,vol,amt,kind]；失败 None |
| `sector_spot_one(name, kind='行业')` | 按板块名取单个快照 → dict（code/name/price/pct_chg/vol/amt/kind）；失败 None |
| `concept_members(code=None, name=None, ths_code=None, kind='概念')` | 概念板块成分股 → [{'code','name'}...]；失败 [] |
| `concept_code_by_name(name, kind='概念')` | 概念名 → 同花顺 885xxx.TI（供跨源对齐用）；失败 None |
| `sw_index_daily(code, start=None, end=None)` | 申万行业指数日线 → DataFrame[dt,open,high,low,close,volume,amt]；失败 None |
| `futures_main_daily(symbol, start=None, end=None)` | 期货主力连续日线 → DataFrame[dt,open,high,low,close,volume,oi]；失败 None |
| `etf_dividend(ts_code)` | ETF 分红序列 → DataFrame[dt, cum_dividend, per_dividend]；失败 None |
| `stock_name_map()` | ts_code → 中文名（进程内缓存）；失败 {} |
| `stock_name(ts_code)` | 单只中文名；失败 '' |

### `misc/core/datafeed.py`
> 盘中买点的数据获取层（多源，带限流保护）

| 接口 | 签名 | 说明 |
|---|---|---|
| `log(*a)` | — |
| `board_list(kind='行业', eastmoney_fallback=None)` | 板块列表，优先同花顺；失败才退回东财 |
| `board_kline(code, src='ths', years_back=6)` | 板块日线，统一返回 [{date,open,high,low,close,vol,amt}, ...]（升序） |
| `stock_kline(code, years_back=6)` | 个股日线，同花顺优先（东财 K 线最容易被封） |
| `ths_to_dg_board(kind='行业')` | 建立「板块名 → 东财 BK 码」映射（同花顺给 881xxx/885xxx，东财成分股接口要 BKxxxx） |
| `board_members(board_code, src='dg', cap=60, name=None, kind='行业')` | 板块成分股（按成交额降序，流动性优先） |
| `status()` | — |

### `misc/core/feishu_card.py`
> 飞书交互式卡片 · 图文合成一条消息

| 接口 | 签名 | 说明 |
|---|---|---|
| `token()` | — |
| `upload_image(tok, path)` | 上传图片拿 img_key。失败返回 None。 |
| `md_elem(text)` | — |
| `hr_elem()` | — |
| `img_elem(key, alt='')` | — |
| `build_card(title, blocks, img_keys=None, template='blue', sign='📎 来自 OpenClaw.Deepseek')` | 把文字块 + 图片按顺序拼成一张卡片 |
| `send_card(title, blocks, imgs=None, chat=None, template='blue', sign=None)` | 发送图文合成卡片。imgs: [本地路径]，会自动上传并插到 blocks 里（若 blocks 里没指定位置， |

### `misc/core/notify.py`
> notify · 告警通知注册表（库内只**触发**，通道由调用方注册，零内置通道）

| 接口 | 签名 | 说明 |
|---|---|---|
| `AlertEvent` | dataclass | 一次告警的上下文：`subject` / `body` / `attachments`（本地文件元组）/ `source` / `meta`（通道自定义，如 `{"to": ...}`）/ `dry_run` |
| `register(name, fn, *, replace=True)` | 注册通道。`fn(event) -> bool`，返回 False 视为失败、None 视为成功 |
| `unregister(name)` / `clear()` / `channels()` | 注销 / 清空（测试用）/ 列出通道名 |
| `autoload(spec=None) -> int` | 按 `TECHANLY_NOTIFIERS`（`os.pathsep` 分隔）加载注册文件；幂等，失败只告警 |
| `emit(subject, body="", *, attachments=None, source="", meta=None, dry_run=False) -> dict` | 交给所有已注册通道，返回 `{通道名: 是否成功}`；无通道时返回 `{}` 并打印醒目提示 |
| `emit_bool(...) -> bool` | 至少一个通道成功即 True |
| CLI | `python3 misc/core/notify.py --list` ｜ `--test [--dry-run]` |

> 跨进程调用：`export TECHANLY_NOTIFIERS=/path/to/notifier.py`，注册文件在 import 时调用
> `notify.register("email", fn)` 即可把告警接到邮件 / 飞书 / webhook。**公开库内不出现任何
> 真实账号、本机路径或口令。**

### `misc/core/mailer.py`
> mailer · 通用 SMTP 工具（收件人/发件人**全部脱敏输出**；告警请走 `notify.py`）

| 接口 | 签名 | 说明 |
|---|---|---|
| `mask(addr: str) -> str` | 邮箱脱敏：保留首尾少量字符，中间用 *** 盖掉。 |
| `mask_many(addrs) -> str` | 一串收件人 → 脱敏后用逗号连起来（日志/报告里只允许出现这个） |
| `scrub(text: str) -> str` | 把任意文本里的完整邮箱替换成脱敏形式（异常/日志输出前过一遍） |
| `load_config(extra=None) -> dict` | 读配置：环境变量 > 配置文件 > 调用方传入的默认值。返回 dict（可能缺项）。 |
| `whoami(cfg=None, verbose=True)` | 打印当前配置（**全部脱敏**），排障用。返回 dict。 |
| `configured(cfg=None) -> bool` | 配置是否够发信（发件人 + 口令 + 收件人齐了） |
| `send(subject, body, to=None, attach=None, html=True, dry_run=False, cfg=None, timeout=60)` | 发一封邮件。成功 True；失败 False（**不抛异常**）。 |
| `mask_attach(attach) -> str` | 附件名脱敏（只留文件名，目录不进日志） |
| `main(argv=None)` | — |

### `charts/common.py`
> charts · 公共基础设施


### `charts/buypoint.py`
> 买点图：日线 / 小时线 / 缠论均线版 —— 三面板一张图（画图模块）

| 接口 | 签名 | 说明 |
|---|---|---|
| `draw_buypoint_chart(disp, day, h1=None, out=None, n_day=120, n_h1=110, n_ma=400, dpi=None)` | 三面板买点图：日线 / 小时线 / 缠论均线版 → 出 PNG 路径 |
| `draw_stock_grid(picks, out_path, n_show=120, title=None, ncol=None, dpi=None, max_items=20)` | 一张图最多 20 个子图（不拆页），每只股票一个子图： |
| `find_sells_for_chart(d)` | 图上用的卖点（复用 tech_buypoint 的全中枢扫描，失败就返回空） |

### `chanlun/multiscale/chanlun_backtest/engine.py`
> 回测引擎 - 缠论买卖点交易系统（多空双向）

| 接口 | 签名 | 说明 |
|---|---|---|
| class `Trade` | 交易记录 |
| class `Position` | 持仓 |
| class `BacktestResult` | 回测结果 |
| class `BacktestEngine` | 回测引擎 |
| ↳ `open_position(entry_price: float, entry_time: datetime, signal_type: str, direction: str, stop_loss: float, structure_level: float, idx: int)` | 开仓 |
| ↳ `close_position(exit_price: float, exit_time: datetime, reason: str, idx: int) -> Optional[Trade]` | 平仓 |
| ↳ `check_exit_conditions(df: pd.DataFrame, idx: int) -> tuple` | 检查平仓条件 |
| ↳ `run(df: pd.DataFrame, signals: list) -> BacktestResult` | 运行回测 |

### `chanlun/multiscale/chanlun_backtest/signals.py`
> 回测信号生成模块 - 双向交易版（二买三买 + 二卖三卖）

| 接口 | 签名 | 说明 |
|---|---|---|
| `scan_signals(df: pd.DataFrame) -> list` | 扫描所有买卖点信号（双向） |
| `scan_signals_astock(df: pd.DataFrame) -> list` | 扫描 A 股买点信号（仅做多：二买 + 三买） |
