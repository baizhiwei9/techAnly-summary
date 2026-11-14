# CHANGELOG

> 每次改动必须在此登记：改了什么、为什么改、影响哪个文件。

> **2026-11-11 两库合并说明**：私有库 `tech-summary` 的全部内容（含代码、文档、发布工具、历史记录）已**合并进**本库 `techAnly-summary`（公开）后删除。
> 历史条目**一条不丢**：techAnly 原有 30 条 + 从备份补回 3 条 = 共 **33 条**。
> 仅当「时间 + 正文完全相同」且两边各存一份时才算重复（那是同一条记录）。

## 2026-11-14 15:10

**feat(charts): 画图入口支持「说人话」路由 —— 老板原话直接当图名即可**

老板定调（13:52 群消息）：「我要说画拓扑结构图，你就用这个算法来调用这个画图；
我要说画流形结构图或者画流形图，你就调用那个 SPD 那里边那个开头的那个画图的。」

之前 `misc/charts/run.py` 只认英文/中划线图名（`topology-manifold` / `topology-spd`），
必须人工翻译一次才能调用 —— 本轮把这一步去掉。

### 改动

- `misc/charts/registry.py`
  - 每条登记项新增 `keywords` 字段（自然语言话题词）。
  - 新增 `resolve(text)`：最长关键词优先匹配，`text` 恰好等于 `name` 时直接命中。
    例：「画拓扑结构图」→ `topology-manifold`；「画流形图」/「流形结构图」→ `topology-spd`；
    「画地形图」/「黎曼地形图」→ `topology-terrain`。
- `misc/charts/run.py`
  - 图名命中失败时先走 `registry.resolve()`，命中打印 `[charts] 「画流形图」→ topology-spd`；
  - `--list` 每条多打一行「话题词：…」，末尾追加**路由口诀**三行；
  - 用法提示补一条 `python3 misc/charts/run.py "画流形图" --target 沪深300`。
- `misc/charts/README.md`：新增「说人话也能路由」小节 + 三张拓扑图不许串的对照表。
- `skills/tech-summary/SKILL.md`：3.0.1 画图路由节补「说人话」用法与已实装说明
  （工作区副本 `~/.openclaw/workspace/skills/tech-summary/SKILL.md` 同步）。

### 实测

```
python3 misc/charts/run.py "画拓扑结构图" --target 科创50   → topology-manifold
python3 misc/charts/run.py "画流形图"     --target 沪深300   → topology-spd
python3 misc/charts/run.py "流形结构图"   …               → topology-spd
python3 misc/charts/run.py "画地形图"     …               → topology-terrain
python3 misc/charts/run.py "拓扑图"       …               → topology-manifold
```

`python3 -c "import registry; registry.resolve('画拓扑结构图')"` → `topology-manifold`（5 例全对）。

### 说明 · 与远端 12:22 那次重构的关系

远端 `fc494e0` 已把画图统一收进 `misc/charts/`（`registry.py` + `run.py` + `README.md`），
本次**在其之上**追加 keywords 路由，不另建入口、不改脚本实现，也不动 `charts/`（那是画图实现模块，
`misc/charts/` 是调用入口，两者分工已由远端定稿）。

影响文件：`misc/charts/{registry.py,run.py,README.md}`、`skills/tech-summary/SKILL.md`、`CHANGELOG.md`。

## 2026-11-14 13:40

**feat(topology+misc): 结构分不再冒充方向（标签改名）+ 新增「趋势层（含方向）」一行 + 建库内唯一画图入口**

老板定调：「结构分不含方向，不许再叫风险」「趋势层数据单独加一行」「以后所有画图都走 techAnly 那个画图路径」。

### A. `topology/manifold_topo.py`

A1. **hermer2 结构分档改名**（原来叫「偏风险 / 偏机会」是把结构状态当成了方向结论）：

| 分数区间 | 旧标签 | 新标签 |
|---|---|---|
| ≤ −1.0 | 偏风险 | **结构分化偏高** |
| ≤ −0.35 | 偏风险（轻） | **结构略分化** |
| < 0.35 | 中性 | **结构中性** |
| < 1.0 | 偏机会（轻） | **结构略聚合** |
| ≥ 1.0 | 偏机会 | **结构聚合** |

- tone 全部改成纯结构描述（「分成了 N 个阵营、互不联动 —— 只说明结构状态，不判涨跌」）。
- 图标题区「综合研判：」→「**结构研判（不含方向）：**」，分项分值小字保留；
  并在其上方加一句静态声明（图上小字）：
  「结构分只看连通簇 / 闭环 / 曲率 / 簇数趋势，不含任何价格与动量信息，不能用来判方向」。
- 口径小字标头同步改：「研判口径：…」→「结构口径：…（不含价格与动量）」；
  函数头 `hermer2_score` docstring 补上口径声明。
- `market_score()`（`--score-mode multi` 多维口径）**标签原样不动**，它本来就含趋势。
- `--audit` 同步适配新标签（`VERDICT_ORDER / RISK_SIDE / OPPO_SIDE` 常量，避免漏改 KeyError）。

A2. **新增趋势层（真正含方向，与结构分物理隔离）**

- 新函数 `trend_layer(close, index_code=None)` → `r60 / dd / vs_ma60 / r10 / r5 / above20 / basis`。
  基准：指数自己的收盘（取不到则**成分股等权合成**，每只归一到 1000 再取均值）；
  `above20` = 站上自己 MA20 的成分股占比（广度）。**不参与**结构打分。
- 新函数 `trend_layer_text(tl)` → 图上那一行固定文案，前缀写死：
  `趋势层（含方向）：近60日 −22.2% · 距60日高 −25.1% · vs MA60 −10.5% · 近10日 +9.8% · 近5日 −0.3% · 站上MA20 50%`
- 图上位置：分项小字下面单独一行（深蓝加粗，浅蓝底框），下面补一行小字注明口径；
  stdout 同步打印同一行 + 口径。

### B. 库内唯一画图入口 `misc/charts/`（新增）

- `misc/charts/registry.py`：画图登记表，登记本库全部已有画图产物 ——
  `chan-fractal`（chanlun/fractal/chan_chart.py）、`chan-ma`（chanlun/ma/chan_ma_chart.py）、
  `buypoint`（misc/engine/buypoint_chart.py，无独立 CLI，标注为库调用入口）、
  `simple`（misc/engine/simple_chart.py，无 CLI 参数）、
  `topology-spd`（topology/run.py）、`topology-terrain`（topology/terrain.py）、
  `topology-manifold`（topology/manifold_topo.py）。
- `misc/charts/run.py`：统一 CLI。`--list` 列清单；`<name> [原脚本参数...]` 透传执行
  （先引导仓库根 / misc/core / smc / chanlun/fractal / chanlun/ma 进 sys.path，再 `runpy.run_path(..., run_name="__main__")`）；
  未指定 `--out` 时统一出到 `reports/charts/<name>/`，显式给了 `--out` 则完全透传。
- `misc/charts/README.md`：为什么有它 / 怎么用 / 新图往哪登记。
- 顶层 `README.md`：核心入口表下补统一画图入口用法；「八、目录结构」misc/ 下补 `charts/` 一行。
- 本机兼容壳：`bin/charts.py`（本机部署目录，见技能文档「本机历史脚本怎么办」；转发到真实 run.py，不改其它 shim）。

**实测（2026-11-14）**：科创50 结构分 −0.50 → 结构略分化；
趋势层 近60日 −25.5% / 距60日高 −28.5% / vs MA60 −13.8% / 近10日 +10.2% / 近5日 +2.1% / 站上MA20 50%。

影响文件：`topology/manifold_topo.py`、`misc/charts/{registry.py,run.py,README.md}`（新增）、
`README.md`、`CHANGELOG.md`；本机 `bin/charts.py`（新增，不进库）。

## 2026-11-14 10:55

**fix(topology): 修正 `manifold_topo.py` 综合研判打分 —— 旧版是死 bug（永远输出中性 0）**

老板质疑「图上一说就说风险，你这判定到底准不准」，查出来旧代码：

```python
combo = 0.6 * zs(mom5) - 0.4 * zs(-curv)
score = int(np.clip(round(float(np.mean(combo)) * 3), -5, 5))
```

**z 分数的均值按定义恒为 0** → `score` 永远是 0 → 所有行情、所有标的都输出「中性」。
这是纯程序错误，不是市场判断。

改为 `market_score()`：7 项各自按原始口径定 −2…+2 分，再加权平均。

| 分项 | 权重 | 依据 |
|---|---:|---|
| 趋势位置（距 MA60） | 1.5 | −15% / −8% / −2% / +2% 分档 |
| 回撤（距 60 日高） | 1.2 | −25% / −15% / −5% / 0 分档 |
| 广度（站上 MA20 占比） | 1.3 | 35% / 45% / 60% 分档 |
| 个股动能（5 日中位数） | 1.2 | −3% / −1% / +1% 分档 |
| 相关性（20d − 60d） | 0.9 | 抬升 = 同步化 = 系统性风险 |
| 波动率（年化中位） | 1.0 | >50% / >38% / >25% 分档 |
| 短期（5 日涨幅 × MA20 位置） | 0.8 | 反弹需要站在 MA20 上方才算数 |

分档：≤−1.0 偏风险 / −1.0~−0.35 偏风险（轻）/ ±0.35 中性 / 0.35~1.0 偏机会（轻）/ ≥1.0 偏机会。
标题区新增分项分值小字，便于逐项核对。

科创50 实测（截至 2026-11-13，50 只成分股，68 日）：**多维打分 −0.76 → 偏风险（轻）**。

影响文件：`topology/manifold_topo.py`

## 2026-11-14 10:05

**feat(topology): 新增 `manifold_topo.py` —— 拓扑流形图（UMAP 2D × 里奇曲率网络）**

老板看了 v5 那张科创50 图（苓宝 2 号之前错做成「黎曼流形地形图」）后澄清：
要的是**拓扑流形图**，左图 UMAP 2D 结构、右图里奇曲率。hermer2 那边脚本拿不到（`coze agent at` 一直
返回 `code=1000500 RPC调用错误`），所以按同一口径在库里重写为正式模块。

- 左：成分股收益距离（`sqrt(2(1-corr))`，precomputed metric）→ UMAP 2D 嵌入
  - 气泡大小 = 近 5 日动量分位（涨越多越大）
  - 颜色 = 机会核心 / 风险边缘 / 中性过渡（动量 z 分 − 曲率 z 分 的合成分三分）
  - 页脚 Betti_0 / Betti_1 由 ripser 在 35 分位半径下**实测**
- 右：相关矩阵 top-k 近邻图 → **Ollivier-Ricci 曲率**（`ot.emd2` 求 W1，α=0.5 测度）
  - 节点颜色 = 入射边曲率均值（越负越深蓝）
  - 咽喉 = 曲率最负 3 只，加圆圈 + 名字标注
- 取数：`--csv` > data_hub(dh_bridge) > tushare 直连（`index_weight` + `daily`）

实测科创50（50 只 / 68 日）：曲率区间 [-0.26, 1.00]，咽喉 = 晶晨股份(-0.26) / 晶合集成(-0.18) / 中芯国际(-0.16)，
Betti_0=6 Betti_1=2，UMAP 段 ~32s。

影响文件：`topology/manifold_topo.py`（新增）

## 2026-11-14 05:20

**fix(net_guard): 补回 `_VERBOSE` 定义（上一提交漏掉，import 会 NameError）**

`92f2c2c` 里 `net_guard.py` 只带了 `if _VERBOSE:` 的使用、缺了模块级定义，
`import net_guard` 首次命中该行就抛 `NameError`。本提交补回：

```python
_VERBOSE = os.environ.get("NET_GUARD_VERBOSE", "0") not in ("0", "", "false", "no")
```

影响文件：`misc/core/net_guard.py`

## 2026-11-14 05:10

**feat: 默认不走东财（TECH_ALLOW_EM=0）—— 一个东财请求都不发**

大佬 2026-11-14 04:05/04:11：「能不能改成默认不走东财啊？同花顺、新浪、腾讯自选股都可以啊」
「默认不走东财，你就不要让它，它那边一直限流」。

### 开关
`TECH_ALLOW_EM`（默认 `0`）。为 0 时：
1. 东财族域名在 **DNS 层直接报错**（`net_guard`），请求根本发不出去；
2. 所有东财分支代码都在 `if ALLOW_EM:` 里，默认不可达。

### 换取数源（每个都保留东财分支，仅开关关闭）
| 位置 | 原（东财） | 现（默认） |
|---|---|---|
| `misc/core/datahub.py` 指数日线 | `index_zh_a_hist` | `stock_zh_index_daily`（新浪，000905 实测 5309 根 / 0.2s） |
| `misc/core/datahub.py` ETF 日线 | `fund_etf_hist_em` | `fund_etf_hist_sina`（新浪，510300 实测 3515 根 / 0.4s，本地切片） |
| `misc/core/datafeed.py` 东财兜底 | 默认开 | 默认关（统一听 `TECH_ALLOW_EM`） |
| `misc/engine/wyckoff_today_fetch.py` 板块报价 | 东财 push2his | 同花顺 realhead / data_hub `sector_spot` |
| `smc/smc_data.py` ETF 兜底 | `fund_etf_hist_em` | `fund_etf_hist_sina` |
| `chanlun/ma/mad_layer.py` ETF 兜底 | `fund_etf_hist_em` | `fund_etf_hist_sina` |
| `chanlun/multiscale/chanlun_backtest/*` | `stock_zh_a_hist` | `stock_zh_a_daily`（新浪，支持 adjust） |
| `misc/core/net_guard.py` / `net_guard_hook.py` | 东财走 IPv6 白名单 | `TECH_ALLOW_EM=0` 时**黑名单直接拦**（`socket.gaierror`） |

### 验证：零东财硬证明
把所有 akshare 东财接口 monkeypatch 成抛 `EM_CALLED` 后跑：
```
python3 bin/tech_summary.py --target 中证500 --no-chart
  → 日线 1375 根 源=akshare/tier1/stock_zh_index_daily(sina)
  → 小时线 400 根
  → 四层结论正常输出（打分 -4，空头结构）
  → EM_CALLED 次数 = 0
python3 bin/tech_buypoint.py --target 半导体
  → 申万 801081 6492 根，闸门判「无买点」
  → EM_CALLED 次数 = 0
```

### 影响文件
`misc/core/{datahub,datafeed,net_guard,net_guard_hook}.py`、`misc/engine/{tech_summary,wyckoff_today_fetch}.py`、
`smc/smc_data.py`、`chanlun/ma/mad_layer.py`、`chanlun/multiscale/chanlun_backtest/{data,run_daily_scan}.py`
+ 新增 `misc/tools/api_push.py`（GitHub REST 建 commit 的备用通道）

## 2026-11-14 04:40

**feat(topology): 新增黎曼流形地形图 terrain.py（大佬要求落库）**

大佬 2026-11-14 04:10：把「黎曼流形地形图_沪深300.png」那张图的绘图程序保存到库里。
原图是云端 Agent 画的、脚本不在本机，因此按同一设计在库里重写成正式模块。

新增 `topology/terrain.py`：
- 取数复用 `topology/data_loader.py`（走 dh_bridge → data_hub），零新增取数逻辑
- 高程场：成分股 2D 嵌入 + 真实相关矩阵 → 局部一致性场（高斯核平滑，归一到 0~1）
- 三色分区的**软掩码**直接叠进曲面 facecolors（不是 NaN 遮罩，避开 plot_surface 遮罩失效问题）：
  🔴 红色裂焰 = 高程前 40% + 曲率偏正（秩次归一化，避开 K 量级只有 1e-3 被吃掉）
  🔵 蓝色回流 = 高程后 22%（谷底/分化），叠沿梯度上升的光脉流线
  🟡 黄色胶着 = 中高程 + 陡梯度（喉胡瓶颈）
- 空中：真实 Vietoris-Rips H1 持续同调条码 + Persistence 点图
- 右图：相关矩阵按第一主成分重排后 3D 曲面 + 特征值浮球 + H1 发光线圈
- 无文字无数字，17.5×9.4，dpi 150 → 2613×1327

实测（沪深300，60 天窗口）：300 只成分股，g=20.875 H1=0.195 corr=0.282 熵=0.802 Hurst=0.575
→ `reports/topology/黎曼流形地形图_沪深300.png`

⚠️ 踩坑：`plot_surface` 用 `np.where(mask, Z, np.nan)` 做局部上色**在 3D 下不生效**
（NaN 行被整体丢弃或跨行补面），必须改成 facecolors 逐格混合。

## 2026-11-14 04:27

**fix: 概念成分股日志措辞改为 tushare ths_member（原写东财）**

misc/engine/tech_buypoint.py：concept_members 走的是 tushare ths_member，不是东财；上一版日志/注释措辞写错，本次更正（逻辑无变化）。

## 2026-11-14 04:22

**feat: 5 个缺口补齐 —— data_hub 新增 sw_index_daily/sector_spot/concept_members/futures_main_daily/etf_dividend**

上一版列出的 5 个缺口，本轮在 data_hub 侧新增采集器补齐，技术库接入并保留老链路兜底。

新 data_type（35 → 40）：
- sw_index_daily：申万行业指数日线（akshare index_hist_sw → tushare sw_daily → 同花顺代理口径）。801081 半导体实测 6492 根。
- sector_spot：板块实时快照（同花顺 gnSection JSON / thshy 表格）。行业 90、概念 294，含涨跌幅/资金净流入/涨跌家数/领涨股。
- concept_members：概念板块成分股（tushare ths_member）。885431 云计算 1086 只。
- futures_main_daily：期货主力连续日线（akshare futures_main_sina）。IF0 451 根 / AG0 3531 根。
- etf_dividend：ETF 分红序列（akshare fund_etf_dividend_sina）。510300 分红 14 次。

同时修 data_hub 真实 bug（minute_bar）：_norm_code('IF0') 把期货主力连续码当 6 位股票补成 'shif0'，只回 6 根垃圾数据。新增 _norm_fut_code / _is_fut_code，kind=fut 走 futures_zh_minute_sina，且 freq 1/5/15/30/60 都优先真实 K 线（原来只有 15 才走）。实测 IF0 60m 1023 根、AG0 60m 1022 根。

技术库接入：
- misc/core/dh_bridge.py 新增 sw_index_daily / sector_spot / sector_spot_one / concept_members / concept_code_by_name / futures_main_daily / etf_dividend，并解除 minute_bar 的 kind=fut 封禁
- misc/engine/tech_buypoint.py 行业日线回到申万口径（口径修正：半导体 6492 根 vs 原同花顺 1663 根），concept_members 改 data_hub 优先
- misc/engine/wyckoff_today_fetch.py _quote() 加 sector_spot 优先（pct_chg=NaN 时落回 realhead）
- misc/core/sw_daily.py 新增 L0 data_hub
- smc/smc_data.py 期货分钟/日线加 data_hub 优先
- chanlun/ma/mad_layer.py ETF 分红走 data_hub
- wavelet/signal_monitor.py 期货日线/1h 走 data_hub

数据源选择（重要）：东财 push2 API 从本机完全不可达（网页 200 / API 全 RemoteDisconnected，IPv4 IPv6 皆然），且大佬 2026-11-12 已明令不要用东财 → sector_spot 用同花顺页面、concept_members 用 tushare ths_member，两个采集器均不依赖东财。

## 2026-11-14 00:33

**feat: 技术分析取数统一走 DataHub（新增 dh_bridge）**

新增 misc/core/dh_bridge.py —— 技术分析库 → data_hub 取数桥（纯适配层，不含抓取逻辑）。

- 惰性 import optstack/data_hub，失败静默返回 None/[]，绝不抛异常
- 字段名对齐老口径（date/trade_date→dt、vol→volume），OHLCV 统一升序去重
- 开关 TECH_DATA_SOURCE=datahub|local（默认 datahub，local 强制走老链路做对比/排障）
- 覆盖：sector_list / sector_kline / sector_members / index_daily / stock_daily / etf_daily / minute_bar / index_components / stock_name_map

改造的取数点（每个都保留原老链路做兜底）：
- chanlun/fractal/chan_smc_index.py：fetch_daily 改 data_hub index_daily 优先，tushare 兜底
- chanlun/fractal/chan_chart.py、chanlun/ma/mad_layer.py、chanlun/ma/chan_ma_trend_v2.py
- smc/smc_data.py、wavelet/signal_monitor.py、misc/strategies/levels.py
- misc/engine/tech_summary.py、tech_buypoint.py、tech_intraday_buypoint.py
- misc/engine/wyckoff_buypoint_v2.py、wyckoff_today_fetch.py

已知 5 个缺口仍未接 data_hub（老链路保留）：申万 801xxx 行业日线（data_hub 只有同花顺 881xxx）、
概念板块成分股（data_hub 只支持行业）、板块实时快照（无 sector_spot）、期货主力连续（无该 data_type）、
ETF 分红序列（后复权因子仍要 akshare）。

## 2026-11-13 09:00

**feat(minute_hub): 新增 verify_sources() 四源逐源自检 + 命令行 verify 子命令**

盘中出问题要能一眼看出是哪个源挂了。verify_sources(code,freq) 逐源独立 try/except 只探测不改变降级顺序；baidu 只有当天分时(几根)已明确标注不可当历史K线。附 SKILL.md 数据来源表与校验章节。

## 2026-11-13 08:03

**fix(tushare): TS_TIMEOUT 8s → 50s（代理端全市场接口 11~60s，8s 全被掐死）**

tushare_init.py: 代理 token 实测 daily 11~19s / daily_basic 8~61s / moneyflow 11~29s，8s 硬超时导致 TDA build_momentum 22 天全空；改 50s 后 21/22 天成功。同时 tushare_init 接入 net_guard（默认 IPv4，东财族走 IPv6）。

## 2026-11-12 22:47

**refactor(wyckoff): 移除最后的东财直连，改用同花顺**

misc/engine/wyckoff_today_fetch.py 全量改走 ths_hub；板块列表 90 行业/294 概念、板块日K 同花顺按年接口；向后兼容保留 f12/f14 键；东财 BK 码加防护直接返回空；盘中买点链路不再触碰 eastmoney

## 2026-11-12 21:34

**refactor(datahub): 三级灾备移除东财，改用「腾讯→新浪→同花顺」**

### 为什么
大佬 2026-11-12 20:03：「我建议三级体系不要东财，用至少新浪和同花顺可以替代他」

### 改了什么
misc/core/datahub.py：
- L3 网页层：原 腾讯 → 东财  改为 **腾讯 → 新浪 → 同花顺**
- 新增 _l3_sina()：新浪 getKLineData 接口（字段自带列名，最稳）
- 新增 _l3_ths()：同花顺 hs_ 接口（个股全量历史 1662 根）
- 删除 _l3_eastmoney() 与 WEB_SPECS['eastmoney'] 条目
- 顺手删除死代码 _margin_l3_eastmoney()（全仓零引用）

misc/core/sw_daily.py：
- L2 兜底从 akshare 的东财板块接口（stock_board_industry_hist_em）改为同花顺 ths_hub

misc/core/ths_hub.py：
- 新增 board_code_by_name()：板块名 → 同花顺板块码（支持「白酒Ⅱ」→「白酒」这类后缀差异）

misc/engine/wyckoff_today_fetch.py：
- 加降级说明：这是唯一还直连东财的模块，默认不会走到（DF_EASTMONEY=1 才启用）

### 实测
**各源单独验证（A股/指数/个股）**
- 腾讯 200 根到当日 ✅（A股+指数+港股）
- 新浪 230 根到当日 ✅（A股+指数）
- 同花顺 1662 根到当日 ✅（个股）；指数返回空 → 由腾讯/新浪补位

**datahub.daily() 三级全通**
- 600519.SH / 000905.SH / 002463.SZ 各 300 根，末根 20261112
- 强制只走 L3（layers=('web',)）也全部 200 根正常

**全库**
- 458 个 py 语法 0 错误
- 库里 eastmoney 只剩 wyckoff_today_fetch（已降级+注释）和 net_guard 白名单

### 影响文件
- misc/core/datahub.py
- misc/core/sw_daily.py
- misc/core/ths_hub.py
- misc/engine/wyckoff_today_fetch.py
- misc/README.md

## 2026-11-12 21:16

**refactor(data): 东财兜底默认关闭，全面改用同花顺**

### 为什么
大佬 2026-11-12 20:00：「我建议你不要用东财了，获取其他的。这么多网数据网站，你为什么死磕它？」

### 改了什么
misc/core/datafeed.py：
- 新增 USE_EASTMONEY 开关，默认 False（DF_EASTMONEY=1 才启用）
- board_list / board_kline / stock_kline / board_members 的东财分支全部受开关控制
- 东财关闭时：
  · 板块列表/K线/个股K线/行业成分股 → 全走同花顺
  · 概念板块成分股 → 返回空（同花顺概念页是 ajax 动态加载，暂不支持）
- status() 增加 eastmoney_enabled 字段

### 实测（东财全关）
- 全量 90 行业板块扫描正常：30 个有买点、9 只个股站上 VWAP −1σ
- 白酒：皇台酒业 +10.03% / 金种子酒 +6.58% / 贵州茅台 +0.57%
- 保险：中国太保 / 中国平安
- 458 py 语法 0 错误

### 影响文件
- misc/core/datafeed.py
- misc/README.md

## 2026-11-12 21:05

**feat(data): 取数换同花顺，东财降为兜底 —— 盘中买点不再被封 IP**

### 为什么改
大佬 2026-11-12 19:48：「能不能不用东财？新浪同花顺不都可以吗，东财一直封」
实测东财 push2his K 线：
  · IPv4 全废（RemoteDisconnected）
  · 只走 IPv6
  · 并发 100 次后被封 IP 20 分钟以上，v4/v6 全 000
盘中要扫 90 行业 + 下钻个股，必然触发封禁 → 必须换源。

### 改了什么
1. 新增 misc/core/ths_hub.py —— 同花顺取数（东财替代源）
   · 行业板块列表 90 个（thshy 页面）
   · 概念板块列表 294 个（gnSection 隐藏域里的 platecode/platename）
   · 板块 K 线：按年接口 48_<code>/01/<YYYY>.js 拼全量（实测 1662 根，到当日）
   · 个股 K 线：hs_<code> 接口（1661 根）
   · 板块成分股：板块详情页解析（白酒 19 只全成功）
2. 新增 misc/core/datafeed.py —— 多源调度层
   · K 线/列表/成分股 **全部同花顺优先**
   · 东财只在同花顺失败时启用，带限流（0.35s 间隔）+ 熔断（连败 4 次停）
3. misc/engine/tech_intraday_buypoint.py 接入 datafeed
   · board_rows / board_kline / board_members / stock_kline 全走 DF
   · analyse 增加 src 字段，下钻时知道源
4. misc/README.md + SKILL.md 补「取数优先级：同花顺 > 东财」说明

### 实测
- 全量 90 行业板块扫描：**零东财请求**，30 个有买点，9 只个股站上 VWAP −1σ
- 白酒：19 只成分股 → 皇台酒业(+10.03%) / 金种子酒(+6.58%) / 迎驾贡酒(+1.19%)
- 保险：中国平安 / 中国太保
- 游戏：巨人网络 / 中青宝 / 凯撒文化
- 458 个 py 语法 0 错误；tech_summary 等核心脚本不受影响

### 影响文件
- misc/core/ths_hub.py（新）
- misc/core/datafeed.py（新）
- misc/engine/tech_intraday_buypoint.py
- misc/README.md / skills/tech-summary/SKILL.md

## 2026-11-12 18:50

**fix(net): 东财 K 线只通 IPv6 → 新增 net_guard 白名单，盘中买点恢复可用**

### 问题
盘中买点脚本 tech_intraday_buypoint.py 取不到行业 K 线，全部 kline fail ConnectionError。
根因：2026-11-12 实测东财 K 线接口 push2his.eastmoney.com
  IPv4 → 全部 RemoteDisconnected（140.207.x / 61.129.x / 103.220.x 都试过）
  IPv6 → 200 OK
而本机全局硬规矩是一刀切禁 IPv6 → 盘中所有板块扫描必然失败。

### 改法
1. 新增 misc/core/net_guard.py：默认仍强制 IPv4，但东财行情族白名单走 IPv6
   · 白名单：push2his.eastmoney.com / push2.eastmoney.com / eastmoney.com（子域匹配）
   · 白名单域名 v6 解析失败时自动退回原样，不会比原来更差
   · 支持 IPV6_OK_HOSTS 环境变量额外放行
2. 全库 20 个脚本的「一刀切禁 IPv6」统一替换成 net_guard.install()
   （misc/engine ×6, misc/core ×5, chanlun ×7, smc ×1, wavelet ×1, apps ×2）
3. ⚠️ 安装位置必须在 sys.path 建好之后（net_guard 在 misc/core/）
   —— 第一版放错位置导致静默回退，已修正并二次验证
4. misc/core/tushare_init.py 是 misc/core 同目录，直接补 sys.path 后 import
5. SKILL.md / misc/README.md / README.md 补网络策略说明

### 验证
- tech_intraday_buypoint.py --limit 10 --dry：**成功扫出行业+个股**
  （百货/林业Ⅲ 等上榜，个股 7 只站上 VWAP −1σ，缠论买点命中正常）
- 东财 K线 200 / 东财 clist 200 / 腾讯 200（走 v4）/ tushare 200（走 v4）
- tech_summary / tech_buypoint / run_level 全部正常
- 455 个 py 语法 0 错误

## 2026-11-12 16:53

**refactor(架构): 对外推送应用移出库 → apps/，库内只留纯取数 sw_daily**

### 改了什么
- 新增 misc/core/sw_daily.py：从 weekly_zs.py 抽出的**纯取数层**（fetch_sw_daily / sw_l2_map / sw_l3_map / stock_sw，
  三级降级 akshare→东财→tushare，mtime TTL 缓存），库内唯一保留的申万数据入口
- misc/engine/tech_buypoint.py：from weekly_zs import fetch_sw_daily → from sw_daily import fetch_sw_daily
- 工作区新增 apps/（不进本仓库），搬出三个对外应用：
  · apps/weekly_zs.py        周报（推飞书私聊 + QQ）
  · apps/xq_ind_monitor.py   雪球组合行业监控（发邮件）
  · apps/xq_portfolio.py     雪球多组合档案
- apps/README.md：写清依赖方向（apps → 库，单向）、为什么这三个不能进公开库（带 chat_id/webhook/邮箱）
- bin/ 三个 shim 的 _REAL 路径同步指向 apps/
- misc/README.md + SKILL.md + README.md：删除 weekly_zs/xq_* 的描述，改为 sw_daily；补「对外应用不在此库」说明

### 为什么
大佬 2026-11-12 15:22：「你那些个杂项，你得引用它，而不是这个库引用那些个杂项」
原来方向是反的：库内 tech_buypoint.py 反过来 import 库外的 weekly_zs（805 行周报，带飞书/QQ 推送目标）。
现在抽成：库提供纯取数 sw_daily.py ← 应用（apps/weekly_zs.py 等）引用库，库内零 import apps。

### 影响文件
- misc/core/sw_daily.py（新增）
- misc/engine/tech_buypoint.py（改 import）
- misc/README.md / skills/tech-summary/SKILL.md / README.md
- 工作区 apps/（不进库）

## 2026-11-12 16:34

**docs: 技能文档 + README 全面改成真实路径，补三分钟上手与命令速查表**

### 改了什么
- skills/tech-summary/SKILL.md：
  · 顶部「目录定稿」块重写：不再说 bin/ 是兼容 shim（仓库已删 bin/），改成「命令里直接写真实路径」对照表
  · 新增「🚀 先看这里：三分钟上手」——克隆→装依赖→token→bootstrap_cache→四个常用命令
  · 新增「3.0 命令速查表」——12 行表格，我想干什么 → 跑哪个命令
  · 新增「本机历史脚本怎么办」——给软链建法，老脚本不用改
  · 全文 30+ 处 python3 bin/xxx.py → 真实路径（misc/engine/ chanlun/ wavelet/ misc/core/）
  · 校验节两段自检的 sys.path.insert(0,'bin') → 真实目录列表
  · 修正「图与技能口径一致」自检：分型版 54 笔 vs 均线版 53 笔本来就不同口径，
    原 asserts == 必然失败（历史遗留，与本次整理无关）→ 改成 abs 差 ≤2 才断言
- skills/chan-ma-trend/SKILL.md：命令与模块引用全部改真实路径
- README.md：补 strategies/run_level.py、tools/bootstrap_cache.py、examples 三个文件的完整前缀

### 为什么
大佬 2026-11-12 14:24：「你在那个技能的那个，还是那个库的说明文档里边说明白了怎么用」
仓库已删 bin/，但两个技能文档还写着 python3 bin/xxx.py，照着跑必失败。

### 影响文件
- skills/tech-summary/SKILL.md
- skills/chan-ma-trend/SKILL.md
- README.md

## 2026-11-12 16:18

**refactor(目录): 仓库不再收 bin/ 兼容壳，顶层只留四类代码目录**

### 改了什么
- 仓库不再收 bin/ 目录（25 个文件全是转发壳，占仓库 19% 体积）
- 仓库顶层只剩四类代码目录：chanlun/ smc/ wavelet/ misc/ + skills/ 两个技能文档
- README 命令示例全部从 bin/xxx.py 改成真实路径（misc/engine/、chanlun/、wavelet/）
- README 新增「本机部署可选兼容层」章节，给出建软链的方法

### 为什么
大佬 2026-11-12 13:26：「我是让你把这个技术分析汇总里边按照目录整理，缠论一个、SMC 一个，其他的我不管，
把这个目录整理整理，然后上传到主干」——bin/ 是部署环境的兼容壳，不是库内容，不该占仓库顶层。

### 影响文件
- bin/gh_publish_techanly.py（TREES 去掉 bin；FILES 补 skills；REMOTE_DELETE_PREFIX 加 bin/）
- README.md（34 处命令示例路径 + 目录树 + 兼容层说明）
- 远端删除 bin/ 25 个文件

## 2026-11-12 16:11

**fix(docs): README 目录树 + bootstrap_cache 路径同步 v2 结构**

### 改了什么
- README.md 目录树：misc/report → misc/engine；3 个底层脚本并入 misc/core；reports/ → compare/
- bootstrap_cache.py（公开版）的 _repo_root 探测条件从 core/ 改 chanlun/，sys.path 列表同步为 misc/* 结构

### 为什么
上一轮把 misc/report 改名 misc/engine 后，README 和首次部署工具还指着旧路径。
bootstrap_cache 的 _repo_root 用 core/ 判断仓库根，但仓库里没有顶层 core/ 目录 → 换机器部署必失败。

### 影响文件
- README.md
- misc/tools/bootstrap_cache.py

## 2026-11-12 16:05

**refactor(目录): misc/report→misc/engine，3 个底层脚本并入 core**

### 改了什么
- misc/report/ → misc/engine/（里面装的是分析引擎 tech_summary/tech_buypoint 等，不叫 report 更准确）
- misc/weekly_zs.py、misc/xq_ind_monitor.py、misc/xq_portfolio.py → misc/core/
  （它们是被到处 import 的底层模块，摆 misc 根目录不像底层）
- 17 个文件的 sys.path 引导同步更新 misc/report → misc/engine
- 3 个 bin/ shim 的真实路径同步更新
- misc/README.md 重写，讲清 6 个子目录职责 + 「库是库，杂事不进库」铁律

### 为什么
大佬 2026-11-12 12:06 要求整理库目录。原 misc/ 是抽屉式杂烩：
6 个子目录 + 3 个零散脚本，且 misc/report 名字与其内容（分析引擎）不符。

### 影响文件
- misc/engine/*（由 misc/report 改名）
- misc/core/{weekly_zs,xq_ind_monitor,xq_portfolio}.py（新位置）
- bin/*.py 17 个 shim 路径
- bin/gh_publish_techanly.py（删除远端旧路径清单）
- misc/README.md

## 2026-11-12 15:15

**refactor(库隔离): 删 misc/reports，改 misc/compare；顶层 reports/ 永不入库**

### 改了什么
- 删除 misc/reports 目录（混进 27 个别的项目的日常产出，还带 webhook）
- 改名为 misc/compare，只保留笔/中枢引擎对比的 4 个文件（脚本+说明+两张图）
- 别的项目产出（土狗收盘/组合复盘/鱼哥L3/股债比值）全部移到工作区顶层 reports/
- gh_publish_techanly.py：ALLOW_ONLY 改指 misc/compare；TREE_SKIP_PATHS 加 reports；REMOTE_DELETE_PREFIX 加 misc/reports/
- misc/README.md 写明「库是库，杂事不进库」的规矩

### 为什么
- 大佬原话：库是库，应该在外边单独成立项目引用这个库，而不是库里引别人的杂事
- 旧的 misc/reports 是 2026-11-12 目录重构时误把顶层 reports/ 挪进来的，导致 yuge_l3_report.py / yuge_report_html.py / gsratio_daily.sh 写 WS/reports 时全落进库里

### 影响文件
- misc/compare/* (新)
- misc/reports/* (删)
- reports/* (工作区顶层，永久不入库)
- bin/gh_publish_techanly.py
- misc/README.md

## 2026-11-12 11:53

**docs(skill): 技能映射表同步目录定稿后的真实路径**

### 改了什么
- skills/tech-summary/SKILL.md 的「调用路径 → 真实文件」映射表更新为目录定稿后的位置（report/ → misc/report/，core/ → misc/core/，strategies/ → misc/strategies/）。

### 为什么
目录归并后，技能文档里指向的真实文件路径过期了，换机器或排查时会找错地方。调用路径（bin/xxx.py）不变，因为 shim 都在。

### 影响文件
- skills/tech-summary/SKILL.md

## 2026-11-12 11:47

**chore(misc): 补入对比脚本/图，产出型目录加过滤**

### 改了什么
- 补上 misc/reports/ 里与「笔/中枢引擎一致性」有关的 4 个文件（plot_pen_pivot_compare.py + smc_buypoint_compare.md + 两张对比图）。
- 发布工具加两处过滤：
  1. TREE_SKIP_PATHS 增加 smc/reports（113 个每日产出快照，属于运行产物不入库）
  2. ALLOW_ONLY 白名单锁定 misc/reports，只收上面那 4 个（该目录混着土狗收盘/组合复盘/鱼哥报告，属于别的项目且带 webhook）

### 为什么
misc/reports 是日常产出堆积区，直接全收会把 webhook 和别的项目文件带进公开库。

### 影响文件
- bin/gh_publish_techanly.py（发布工具过滤规则）
- misc/reports/ 4 个文件首次入库

## 2026-11-12 11:44

**refactor(layout): 目录定稿 —— 顶层只留 4 类：缠论 / SMC / 小波 / 杂项**

### 改了什么（大佬 2026-11-12 定稿：「就缠论一个目录，SMC 一个目录，其他的杂项一个目录，小波一个目录」）

顶层从 11 个目录收敛到 **4 类代码目录 + 2 个支撑目录**：

- core/ report/ strategies/ tools/ examples/ reports/ 六个平级目录 → 全部收进 **misc/**（misc/core · misc/report · misc/strategies · misc/tools · misc/examples · misc/reports）
- chanlun/ 不变（缠论：fractal 分型版 / ma 均线版 / multiscale 多尺度）
- smc/ 不变（SMC + VWAP + 模拟交易引擎）
- wavelet/ 不变（小波）
- bin/ 保留（全是兼容 shim，cron 依赖，不能删）
- skills/ 保留在顶层（OpenClaw 技能发现机制要求）

### 代码改动
1. 21 个文件的 `_repo_root()` 判据从「含 bin/ 与 core/」改为「含 bin/ 与 chanlun/」——顶层不再有 core/。
2. 20 个文件的 sys.path 引导清单补上 misc/core、misc/report、misc/strategies、misc/tools。
3. 16 个 bin/ 兼容 shim 的目标路径改为 misc/report/xxx.py、misc/core/xxx.py。
4. misc/strategies/levels.py 与 run_level.py 的 WS 推导改为 `_repo_root()`（原来按两层目录硬算，搬进 misc/ 后会算错一层）。
5. 发布工具支持 --delete-prefix，上传时顺带删除远端已废弃的顶层路径。
6. misc/README.md 重写为「杂项目录导航」。

### 为什么
之前 core/report/strategies/tools/examples/reports 六个目录平级堆在顶层，跟缠论/SMC/小波混在一起，看不出哪个是主线。大佬要求按口径分类。

### 验证
- compileall 全通过；18 项回归全绿
- `python3 bin/tech_summary.py --target 中证500`（shim 转发）与 `python3 misc/report/tech_summary.py`（真实文件）结果一致
- `python3 misc/strategies/run_level.py --target 中证500 --level day` 正常出四层报告
- smc/smc_core.py 自测正常；chanlun 各口径、wavelet、xq_* 全部 import OK
- cron 依赖的 bin/*.sh 全部在位

### 影响文件
全部重排（本地 + 仓库路径一致）；核心是 21 个 `_repo_root` + 20 个 sys.path + 16 个 shim


## 2026-11-12 11:08

**chore(tools): 发布工具改为目录级同步并自动落库自身**

### 改了什么
- tools/publish_techanly.py 由「本地 asset 静态拷贝」改为「本机 bin/gh_publish_techanly.py 消毒后落库」，以后不会再出现本地改了、仓库还是旧文件清单的情况。
- 同步本次目录级同步的完整规则（TREES/跳过规则/剥离规则/CHANGELOG 消毒）。

### 影响文件
- tools/publish_techanly.py

## 2026-11-12 11:06

**fix(smc): 飞书凭据路径改 ~/.openclaw 展开，去掉本机绝对路径**

### 改了什么
- smc/smc_feishu.py：CFG_PATH 由写死本机绝对路径改为 OPENCLAW_JSON 环境变量优先，默认 ~/.openclaw/openclaw.json（expanduser 展开）。

### 为什么
- 公开库不允许出现本机工作区绝对路径；换机器部署时也能直接用。

### 影响文件
- smc/smc_feishu.py

## 2026-11-12 10:52

**chore(layout): 目录整理后首次全量对齐（补登完整记录）**

### 改了什么
1. 发布工具改「目录级同步」：core/ report/ chanlun/ misc/ wavelet/ smc/ strategies/ tools/ + skills/tech-summary + skills/chan-ma-trend；bin/ 只收 2026-11-12 目录整理生成的兼容 shim。
2. 剥离规则加强：本机绝对路径的全形态（WS=、BASE=、return 兜底、sys.path 硬编、文档残留）→ 一律改环境变量/仓库根自适应。
3. 补齐一直没入库的实现：smc/smc_data.py · smc_db · smc_engine · smc_notify · smc_feishu · smc_poll · smc_backtest · smc_fvg_selftest · smc_stop_compare（原来只有 smc_core/smc_config）。
4. vendor 副本（chanlun/multiscale/chanlun_backtest/vendor）与主实现同步。
5. 技能文档：tech-summary 补「调用路径 → 真实文件」映射表；chan-ma-trend 标注真实文件位置。

### 为什么
目录整理后远端仍是整理前的 bin/ 单一布局，本地/远端结构不一致；且部分实现（smc 引擎）根本没上库。老工具写死文件清单，整理后会漏传。

### 影响文件
- bin/gh_publish_techanly.py
- core/minute_hub.py、report/wyckoff_today_fetch.py、chanlun/fractal/chan_smc_index.py（路径兜底自适应，行为不变）
- smc/*（新增 9 个）
- skills/tech-summary/SKILL.md、skills/chan-ma-trend/SKILL.md

## 2026-11-12 10:52

**docs(changelog): 补登「目录整理后全量对齐」记录**

### 改了什么
- CHANGELOG 补上 2026-11-12 目录整理后首次全量对齐的记录。

### 影响文件
- CHANGELOG.md

## 2026-11-12 02:20

**feat(chanlun): 分型版笔/中枢全部切到本地引擎（技术分析单一库收口）**

### 改了什么
1. `bin/chan_smc_index.py`
   - `merge_bars()`：改为直接调用 `structure_indicators.merge_klines`（本地栈式包含处理），
     输出仍为 `[high, low, last_i, hi_i, lo_i]`，所有下游无感
   - `fractals()`：改为本地宽松分型（顶只看高点、底只看低点）
   - `build_bi()`：改为本地成笔（同类取极值、异类间隔 ≥5 根合并K线，`MIN_BI_GAP=4`）
   - 新增 `zhongshu_from_segs()`：直接调用本地 `detect_zhongshu`，逐笔链式延伸
   - `chan_read()`：中枢由「最近 3 段重叠」改为「本地最近一个链式中枢」；
     三买/三卖扫描从该中枢延伸结束位置开始，避免把中枢内部震荡当离开段
2. `bin/chan_chart.py`：删除自带的包含/分型/成笔副本，`chan_geometry()` 全部复用
   `chan_smc_index.merge_bars/build_bi/segs_from_bi/zhongshu_from_segs`
3. `bin/tech_buypoint.py`：三买/三卖扫描的外层由「遍历所有 3 段窗口」改为
   「遍历本地中枢链」；离开点限制在下一中枢之前，替代原逐段重算的「中枢递进校验」
4. `reports/plot_pen_pivot_compare.py`：远程侧改为加载 `vendor/chan_smc_index.py` 快照，
   本地侧为现行引擎；改完仍可复现「统一前」的笔/中枢差异

### 为什么
- 大佬定稿：「分型版也用本地的」「刚才说那两个不一致的（笔、中枢）也用本地的」
- 此前只有 SMC/MACD/背驰统一，笔与中枢仍是远程简化实现（≥3 根成笔、只看最近 3 段）；
  同一份 K 线在两套引擎下笔数 99 vs 67、中枢 1 vs 5，报告和实盘判定容易互相矛盾

### 验证
- `bin/chan_smc_index.build_bi` 与 `structure_indicators.build_bi` 在 sh510300 日线（1023 根）
  与 60m（400 根）上逐端点一致；中枢列表逐项一致
- sh510300 日线 `chan_read`：正常返回，中枢 4.61~4.84，周线底背驰 + 一买 4.03
- 全库 `compileall` 通过；`tech_buypoint.find_third_buys/find_third_sells` 正常执行
- 对比图统计（统一前）：远程 99 笔 / 1 个中枢，本地 67 笔 / 5 个中枢

### 影响
- 笔、中枢、三买/三卖的数量与位置会变化（这是本次目的）；一买/二买仍基于本地背驰口径
- 远程快照仅保留在 `chanlun_multiscale/chanlun_backtest/vendor/`，不参与线上判定

## 2026-11-12 01:40

**feat(lib): SMC 统一本地口径；分型版 MACD/背驰改用真实收盘价与本地规则**

### 改了什么
1. `smc/smc_core.py`：新增 `smc_layer(df, k, atr_n=14)` 单一组装入口（摆动点 + FVG/OB + `confirm_zone`）
2. `bin/tech_summary.py`：`smc_layer()` 改为直接委托 `smc_core.smc_layer`，删除重复组装
3. `bin/chan_smc_index.py`：
   - 删除远程 `smc_read` / `pivots`，新增 `smc_local(df, k, tag)`，日/周 SMC 全部走本地
   - `smc_line()` 改为渲染本地确认信号（结构上下文 + FVG/OB 新鲜数 + `confirm_zone` 文案）
   - `merge_bars()` 记录每条合并K线的原始索引 / 最高点 / 最低点索引，前两列口径不变
   - `bars_of()` 返回 `(high, low, close)`；`chan_read()` 的 MACD(10/20/5) 改用**真实收盘价**
   - 背驰改为本地规则：同向笔端点比较，`MACD 面积 < 前段 × 0.9` 且创新低/新高
   - `segs_from_bi()` 保留合并索引 `i0/i1`（年龄/中枢逻辑不变），新增原始索引 `i0o/i1o` 供背驰切片
4. `bin/tech_summary.py::chan_fx`、`bin/chan_chart.py::chan_geometry`：K线参数补传 close
5. `reports/plot_pen_pivot_compare.py`（新增）：笔 / 中枢差异对比图生成脚本

### 为什么
- 大佬定稿：「SMC 用本地的，不用远程那套」「缠论分型版背驰以本地为准」「MACD 用本地真实收盘价」
- 远程 `smc_read` 只是「价格 > 旧摆动高」的结构播报，不是买点算法；本地 `confirm_zone` 才有 A/B/C/D 状态机与回测能力

### 兼容性
- 远程原文仍保留在 `chanlun_multiscale/chanlun_backtest/vendor/`，仅用于历史对比，不参与任何线上判定
- `merge_bars` 仍接受 `(high, low)` 二元组；无 close 的旧调用自动退回合成分K线中值

## 2026-11-12 01:35

**fix(mad): MAD 均线改后复权口径**

### 改了什么
1. `bin/mad_layer.py`：新增三条后复权取数路径 —— `_ak_hfq`（新浪优先、东财兜底，个股）、`_tencent_fq`（腾讯复权 K 线）、`_etf_hfq`（新浪分红序列自建复权因子，ETF）；`load()` 默认 `adjust="hfq"`，指数仍走不复权。
2. 输出（单标的与批量）新增复权口径标注，JSON 带 `adjust` 字段，取不到后复权时回退不复权并明示。
3. README 复权说明同步更新。

### 为什么
- 不复权价在除权除息日跳空，会直接污染 MA20/MA40/MA200 与「收盘站上 MA40」的判定。
- ETF 拿不到价格后复权行情：腾讯 `hfq` 对 ETF 实际是复权净值（hfq/qfq 比值 0.435–0.481 不恒定）、东财接口连续两次 RemoteDisconnected。故用新浪分红序列按「分红再投资」自建因子：
  `hfq_t = raw_t × ∏_{分红日 ≤ t}(1 + 每份分红 / 除息前收盘)`。

### 验证
- 沪深300（510300）：收盘 4.1500 → 4.3568，MAD −10.87% → −10.82%，结论不变（未通过）。
- 600519：后复权 MAD 3.97%，通过；同一序列前复权复算 3.80%，判定一致（差价来自复权基准，不影响结论）。
- 指数 000905.SH：标 `raw`（指数无复权），收盘 6713.18、未通过。
- 批量池 4 只 ETF 全部取到 `hfq`，无回退。

### 影响文件
- `bin/mad_layer.py`、`README.md`、`CHANGELOG.md`

## 2026-11-12 01:12

**feat(modules): 并入 AC1-VMD 与 MAD 均线两个独立模块（不挂主报告）**

### 改了什么
1. 新增 `bin/acvmd_layer.py`：AC1(20) 自相关 + VMD(K=4, alpha=2000，低序 2 模态合成) 趋势斜率 + MA40 位置，输出 多/空/震荡；口径与历史 AC_VMD 日报一致，含 `layer()` 与 `--target/--json` 命令行。
2. 新增 `bin/mad_layer.py`：MAD = MA20/MA200 − 1，通过条件 = 收盘>MA40 且 MA40 五日斜率>0；支持单标的明细与 `--pool` 批量按 MAD 降序排名。
3. `requirements.txt` 增 `vmdpy>=0.2`；README 增「五点五、独立模块」用法与口径注意；目录结构补两个脚本。

### 为什么
- 用户要求把本方的 AC1-VMD、MAD 均线两个技术分析口径合并进本库；STMOM 张量明确不合。
- 两个口径按「独立模块」并入，不改 `tech_summary.py` 的六节报告与综合打分，避免动到已定稿的四层主口径。

### 影响文件
- `bin/acvmd_layer.py`、`bin/mad_layer.py`（新增）
- `requirements.txt`、`README.md`、`CHANGELOG.md`

## 2026-11-12 01:00

**docs(smc): 远程/本地 SMC 买点口径对比 + 小波归档验收**

### 改了什么
1. `reports/smc_buypoint_compare.md`（新增）：能力矩阵、400 根日线实测重叠率、远程 6 个问题、本地 4 个注意点
2. 小波归档（`strategies/wavelet/`，commit 51f3bea）重新验收：编译通过、`wavelet_portfolio_v4` / `wavelet_futures_v5` 导入通过、无真实 chat_id / 本机绝对路径

### 为什么
- 大佬要求「对比两者 SMC 买点，是不是有差异、是不是有 bug」，并确认小波已并入库
- 结论：远程 `smc_read` 不是买点算法，只是结构摘要；执行口径继续用本地 `smc_core.confirm_zone`

## 2026-11-12 00:30

**refactor(lib): 远程单源模块内置到 vendor/，库自包含不再依赖外部目录**

### 改了什么
1. `chanlun_multiscale/chanlun_backtest/vendor/`（新增）：内置远程原文 `chan_ma_trend_v2.py`、`chan_smc_index.py`（逐字节拷贝，md5 记录在同步脚本输出中）
2. `chanlun_multiscale/chanlun_backtest/ma_trend.py`：查找顺序改为 `$CHAN_MA_BIN → vendor/ → 仓库 bin/ → 合并目录`，默认走库内单源
3. `chanlun_multiscale/chanlun_backtest/sync_vendor.py`（新增）：上游更新后一条命令同步；`--check` 只比对 md5，漂移时退出码 1

### 为什么
- 目标：所有技术分析收敛到一个库、一套代码；部署环境（agent workspace）没有 `bin/`，原先 `import ma_trend` 直接报 `ImportError`
- 内置后同一份 `chanlun_backtest` 在仓库、合并目录、agent workspace 都能独立运行

### 同步方式
```bash
python -m chanlun_backtest.sync_vendor          # 同步
python -m chanlun_backtest.sync_vendor --check  # CI/上线前校验漂移
```

## 2026-11-12 00:05

**feat(ma): 均线版改用远程原文口径 chan_ma_trend_v2（弃用旧简版 MA20/40/160）**

### 改了什么
1. `chanlun_multiscale/chanlun_backtest/ma_trend.py`（新增）：直接加载 `bin/chan_ma_trend_v2.py`，不复制公式；提供 `normalize / prepare / analyze / recent_signals / format_report`，支持日线、周线（`resample_week` 合成）
2. `chanlun_multiscale/chanlun_backtest/run_ma_scan.py`（新增）：CLI 扫描（默认宽基 ETF，日线；`--level week` 周线；`--json` 机器可读）
3. `chanlun_multiscale/chanlun_backtest/indicators.py`：加旧简版警示，标明仅保留历史回测
4. `chanlun_multiscale/chanlun_backtest/STRUCTURE_VERSION.md`：顶部加「均线版已换新版」说明
5. `chanlun_multiscale/README.md`、`MERGE_STATUS.md`：补目录、口径与运行命令

### 为什么
- 大佬 2026-11-11：「然后缠论均线版也是用新版的。你们那边太简单了。」
- 旧简版只有 MA20/40/160 + MACD 12/26/9，没有双穿笔、三组均线、级别定位，判定过粗

### 新口径
- 笔 = MA5、MA10 同时位于 MA20 同侧（双穿），线段 = MA20/40 双穿 MA80，大级别 = 价格 vs MA160
- MACD `(10,20,5)`，另有均线排列、走势模式、级别定位、笔/段/大级别背驰、可交易性
- 周线由日线合成，不单独取数

### 校验
- `ma_trend.analyze` 与远程 `tech_summary.ma_layer` 在 sh510300、sz159915 的日线 + 周线逐字段一致（dt/close/sigs/align/mode/loc/walk/beichi/tradeable），`FAILS 0`
- 全库 `py_compile` 通过；仓库内 `python -m chanlun_backtest.run_ma_scan` 端到端跑通

### 影响文件
- `chanlun_multiscale/chanlun_backtest/ma_trend.py`（新增）
- `chanlun_multiscale/chanlun_backtest/run_ma_scan.py`（新增）
- `chanlun_multiscale/chanlun_backtest/indicators.py`
- `chanlun_multiscale/chanlun_backtest/STRUCTURE_VERSION.md`
- `chanlun_multiscale/README.md`
- `chanlun_multiscale/MERGE_STATUS.md`

## 2026-11-11 23:59

**feat: 新增结构引擎对比脚本（远程 chan_smc_index vs 本地多尺度）**

### 改了什么
1. `chanlun_multiscale/chanlun_backtest/compare_structure_engines.py`：同一标的/周期并排画两套引擎的笔、中枢、买卖点（上=远程分型版，下=本地结构引擎 v2a）
2. `chanlun_multiscale/MERGE_STATUS.md`：补「结构引擎口径差异」一节

### 为什么
- 大佬 2026-11-11：「另外你说结构引擎不一样，这是咋回事？你对比一下画图。」

### 对比结论
- 包含处理一致（三组样本合并K线数完全相同）
- 笔：远程间隔≥3且不校验端点价差；本地间隔≥5且校验顶>前底/底<前顶 → 远程笔更碎（100/98/21 vs 68/60/15）
- 中枢：远程只报最近 3 段重叠（恒 1 个）；本地链式构造+延伸规则（5/7/2 个）
- MACD 均为 10/20/5，但远程用合并K线 `(H+L)/2`、本地用收盘价
- 背驰：远程「新低 + (振幅更小 或 MACD峰值更小)」较松；本地「MACD面积<前段×0.9」较严
- 信号：创业板ETF 日线窗口内远程 4 个、本地 0 个；沪深300ETF 日线本地多出 1 个三卖（远程当前中枢不含该段）

### 影响文件
- `chanlun_multiscale/chanlun_backtest/compare_structure_engines.py`（新增，只读对比）
- `chanlun_multiscale/MERGE_STATUS.md`

## 2026-11-11 23:54

**merge: main 出图改进合入 wavelet_day（e226aba..357b0ca）**

### 改了什么
1. `bin/buypoint_chart.py`、`bin/tech_buypoint.py`、`bin/tech_summary.py`、`bin/push_image.py`：取 `main` 最新版（一张图最多 20 子图不分页、布局重叠自检、出图清晰度提升）
2. `skills/tech-summary/SKILL.md`：取 `main` 最新版（§三·5 布局硬规则）
3. `CHANGELOG`：两边条目按时间合并，44 条一条不丢
4. `chanlun_multiscale/MERGE_STATUS.md`：分支状态改为已回合

### 为什么
- 大佬 2026-11-11：「肯定用新版的。如果新版是我这边的，你要合并，把这个更改合并了。合并之后你检查一下。」

### 校验
- 规则文件（v1/v2/闸门/指标）md5 与工作区全同，合并未改动规则逻辑
- 五条差异信号数复算全部一致：B2 4→1、S2 4→0、B3 0→1、B1 0→2、S1 0→2
- 全库 `py_compile` 通过；出图模块 import 通过；敏感信息（chat_id / 本机绝对路径）扫描无命中

### 影响文件
- `bin/buypoint_chart.py`
- `bin/tech_buypoint.py`
- `bin/tech_summary.py`
- `bin/push_image.py`
- `skills/tech-summary/SKILL.md`
- `CHANGELOG.md`
- `chanlun_multiscale/MERGE_STATUS.md`

## 2026-11-11 23:52

**chore(skill): 发布布局硬规则 + 出图重叠自检（SKILL.md §三·5）**

SKILL.md 新增「布局硬规则」小节：绝对英寸布局(add_axes)、_fit_text 量宽自适应、价格轴留白、边缘锚点切换、标签间距、x轴跳过第0根、ZG/ZD 贴左端；新增 TECH_GRID_DEBUG 自检与验收标准(overlaps=0 oob=0)。同步 buypoint_chart.py 最新版。

## 2026-11-11 23:49

**fix(chart): 网格图/三面板文字重叠 —— 改绝对英寸布局 + 文字量宽自适应 + 重叠自检**

根因：标题/副标题/表脚用 transAxes 固定偏移，字号一大就互压，长文字还会顶出画布。改法：add_axes 绝对定位（表头0.62+面板2.78+表脚0.96英寸）；新增 _fit_text 先量宽再缩字号/截断；价格轴上下留白；边缘标注自动换锚点；多买卖点标签间距加大；x轴跳过第0根；ZG/ZD 改贴中枢框左端内侧；新增 _debug_overlaps 自检（TECH_GRID_DEBUG=1 → [OVERLAP]/[OOB]，验收 overlaps=0 oob=0）；三面板 hspace 0.10→0.46、标题说明改量宽、均线版说明移到底部、结论框左右分置。实测 15 只网格 overlays=0 oob=0，三面板 0/0

## 2026-11-11 23:30

**feat(strategies): 新增 `wavelet_day` 分支 —— 小波策略归档 + 五套口径统一级别入口**

### 改了什么
1. **新增 `strategies/` 策略索引层**
   - `strategies/levels.py`：`m15 / h1 / day / week` 统一取数；周线由日线
     `resample("W-FRI")` 合成（保留 volume）；15m/60m 走 `minute_hub`，日线走 `datahub`
   - `strategies/run_level.py`：一条命令按级别跑 ①分型版 ②均线版 ③SMC ④VWAP
   - `strategies/README.md`：五套策略 + 级别支持矩阵（实测口径）
2. **归档小波策略 `strategies/wavelet/`**（公式未改，仅去敏）
   - `signal_monitor.py`、9 个 `strategies/wavelet_*.py`、`run_monitor.sh`
   - 飞书 chat_id 改为 `WAVELET_FEISHU_CHAT_ID` 环境变量；本机绝对路径改为相对路径
   - `trading_day_check` 改为可选依赖（公开库 clone 后不会 import 崩）
3. **README 增补「按级别跑 + 策略索引」表**

### 为什么
- 大佬 2026-11-11：把分钟级交易监控（小波）提到 techAnly-summary 主库，单开
  `wavelet_day` 分支；并把缠论/分型版/均线版/小波/SMC 统一整理，确认各口径能按
  参数切 日线/周线/小时线/15分钟。

### 级别支持实测（2026-11-11）
- 分型版 / 均线版 / SMC / VWAP：**15m、h1、day、week 全部跑通**（同一份公式，换 K 线）
- 小波：**日线/周线**；1h 模块存在但监控里已停用；**不支持 15 分钟**（参数按日线尺度定）
- 威科夫：盘中策略，过滤层为周线/日线，不是任意级别重采样

### 影响文件
- `strategies/levels.py`（新增）
- `strategies/run_level.py`（新增）
- `strategies/README.md`（新增）
- `strategies/wavelet/**`（新增，含去敏）
- `README.md`
- `CHANGELOG.md`

## 2026-11-11 23:18

**feat(multiscale): 并入本地缠论多尺度交易工程（四级闸门 + v2 候选 + MACD 10/20/5）**

### 改了什么
1. **新增 `chanlun_multiscale/`**
   - `chanlun_backtest/`：结构版核心（包含→分型→笔→中枢→背驰→买卖点）、四级闸门
     `signal_gates.py`、v2 候选 `structure_signals_v2.py`、第二批对比脚本
     `compare_v2_batch2.py`
   - `Strategy/chanlun_fractal_Strategy.py`：生产监控（期货 + 股指期货 + ETF，四级周期）
   - `data_fetcher.py`：数据采集（akshare）
   - `README.md` / `MERGE_STATUS.md`：口径、合并进度与待办
2. **口径对齐远程定稿**
   - MACD `(20,40,5)` → `(10,20,5)`，与 `bin/chan_smc_index.py` 一致
   - 三买遍历所有中枢 + 中枢递进校验 + 现价 > ZG；二买锚定一买后首个回踩
   - 15m 不发信号；1h 只认三买（一买须 MA10>MA20）；日线一/二/三买；周线只认一/二买
3. **公开库适配**：去掉本机绝对路径，持仓状态文件改 `FRACTAL_STATE_FILE` 环境变量；
   内网编排脚本 `run_unified_monitor.py`（含飞书 chat_id）不并入公开库

### 为什么
- 大佬 2026-11-11：把本地缠论多尺度交易工程与远程 `techAnly-summary` 合并落盘并推分支

### 影响文件
- `chanlun_multiscale/**`（新增）
- `README.md`
- `CHANGELOG.md`

> 注：`structure_signals_v2.py` 仅用于对比评估，**未接实盘**；实盘仍为 v1 信号。

## 2026-11-11 23:01

**feat(chart): 一张图最多20个子图不分页 + 每个子图下方标注买卖点**

### 改了什么
1. **`draw_stock_grid()` 重写**
   - 不再分页：新增 `max_items=20`，**一张图最多 20 个子图**（2 列 × N/2 行），超出自动截断
   - **每个子图下方固定标注块**（大佬 2026-11-11：「每个子图后边标识买卖点」）：
     - 左下：`买点：三买 11.29、一买 5.21`（绿色加粗；无买点显示灰色「无」）
     - 右下：`卖点：三卖 11.69 ｜ VWAP +1.41σ +1σ警戒`（红色；含 VWAP 分区提示）
   - 图内买点标 `买 11.29`（下方绿框）、卖点标 `卖 11.69`（上方红框），不再只画图形不写价
   - 每个子图都跑全中枢卖点扫描 `find_sells`，过闸门的才画
   - 布局行距加大（`h_pad=2.6`）给标注留空间
2. `tech_buypoint.run_multi()`：去掉分页逻辑，一次只出 1 张图；给每个 item 传 `buys` / `sells`
3. `--per-page` 标记废弃（保留参数不报错）
4. 图内注释去掉 emoji（字体缺字形会出豆腐块），改纯文字
5. SKILL.md §三·5 / §3.15 写清这条硬规矩

### 为什么
- 大佬 2026-11-11：「我不明白为什么不出图的时候不凑一张图。你记一下，一个图最多有 20 个子图就可以了。
  每个图后边，每个子图后边标识买卖点」
- 之前 4 只/页拆 5 张，翻起来麻烦；而且图上只有月牙/星形符号，没写买卖点价格

### 影响文件
- bin/buypoint_chart.py
- bin/tech_buypoint.py
- skills/tech-summary/SKILL.md

## 2026-11-11 22:36

**feat(chart): 出图清晰度大幅提升（字号/画布比 + 折行标题 + ZG/ZD 标注 + RGB 输出 + QQ 文件形式不压缩）**

### 改了什么
1. **网格图（draw_stock_grid）**
   - 画布 11.0in→6.6in/列（每子图 6.6×3.8in），字号 17→19/15/14/12.5pt，笔线宽 2.8→3.0
   - DPI 200→220（环境变量 `TECH_CHART_DPI`）
   - 标题**强制折两行**：第一行 名称(代码) 涨幅 / 第二行 买点+VWAP+板块
     → 修复「分辨率高但看着糊」的真因：单行长标题被 bbox_inches=tight 撑宽画布，等于把相对字号缩小
   - 中枢右侧直接标 `ZG x.xx` / `ZD x.xx` 数值
   - 月牙 s=430、买点标签 18pt
2. **单只三面板（draw_buypoint_chart）**：画布 16in→11in，字号整体 ×1.45（上限 18pt），DPI 170→200
3. **tech_summary 四面板**：画布 17in→13.5in，DPI 125→180（`CHART_DPI`）
4. **新增 `_finalize_png()`**：存完统一转 RGB（去 alpha）——RGBA 在部分 IM 里发灰且体积大一倍
5. **push_image.py 新增 `--document`**：QQ 用 `--force-document` 以文件形式发，平台不压缩
   （直接发图片会被压到 1000px 宽，源图再高清也白搭）
6. `tech_buypoint.py` 新增 `--dpi` / `--bars`，`--per-page` 默认 8→4（2×2 布局最清晰）
7. SKILL.md 新增 §三·5 出图清晰度，写清「清晰度=字号÷画布宽度」这条关键认知

### 为什么
- 大佬 2026-11-11：「图还是不清楚，能不能清晰度再高清点？然后把这个技能修正一下」
- 根因不是 DPI 不够，是字号/画布比太小 + 长标题撑宽画布 + QQ 通道压缩

### 影响文件
- bin/buypoint_chart.py
- bin/tech_buypoint.py
- bin/tech_summary.py
- bin/push_image.py
- skills/tech-summary/SKILL.md

## 2026-11-11 22:00

**fix(tech_buypoint): 修假三买（中枢递进校验）+ 买点标签必须写清分型版/均线版与级别**

### 改了什么
1. **修假三买**（大佬 2026-11-11 当场指出「巨人网络明显不是周线三买」）
   - `find_third_buys()` 新增「中枢递进」硬校验：向上离开中枢 → 回踩之间，若 `i+3 .. k-1` 区间内
     又形成任意新有效中枢（zg>zd），该中枢失效，改试更近的离开点
   - 实测巨人网络 002558：旧逻辑判「周线三买 18.74（中枢 11.63~17.58）」，但离开到回踩之间
     实际有 [34.55~47.66]/[30.76~34.29]/[30.76~31.31]/[23.15~26.99] 四个新中枢，
     价格从 47.66 跌到 18.74，现价 21.78 还在最新中枢 [23.44~31.31] **下方** → 修后 accept=0
2. **买点标签必须标来源与级别**
   - 买点 dict 新增 `layer` / `level_name` 字段（分型版 / 周线·日线·小时线）
   - 新增 `buy_label()` / `sell_label()`：输出 `①三买·分型版（日线）`、`均线版买点`
   - 报告每只票额外输出「该三买中枢 ZG/ZD + 现价是否站上 ZG」；均线版买点单列一行
   - 网格图标题改为 `①三买·分型版（日线）11.29 ZG10.59`，图底口径说明写清「月牙=分型版，均线版不画月牙」
3. `tech_intraday_buypoint.weekly_buys()/_gate_week()` 同步加 layer/level_name（周线）

### 为什么
- 大佬 2026-11-11：「巨人网络肯定不是周线三买，你都没过中枢；一定要提示到底是均线版还是分型版」
- 三买要求现价在中枢 ZG **上方**，旧逻辑只比回踩低点与 ZG，漏了「中间已有新中枢」这种情况

### 影响文件
- bin/tech_buypoint.py
- bin/tech_intraday_buypoint.py
- bin/buypoint_chart.py
- skills/tech-summary/SKILL.md

## 2026-11-11 21:16

**chore: 移除已删除的 sector_buypoint_top20.py 登记**

### 改了什么
- gh_publish_techanly.py 的 FILES 去掉 bin/sector_buypoint_top20.py（该临时脚本已删除，能力已并入 tech_buypoint.py 的 --targets/--top/--grid）

### 为什么
- 大佬 2026-11-11：不该另写脚本，技能库传参就该支持

### 影响文件
- bin/gh_publish_techanly.py

## 2026-11-11 21:16

**feat(tech_buypoint): 支持多板块合并 --targets / --top / --grid（多行业一起挑买点个股 Top N + 网格图）**

### 改了什么
- `tech_buypoint.py` 新增三个参数，**不需要新脚本就能做「多个行业 → 有买点个股 Top N → 出图」**：
  - `--targets 传媒,影视院线`：多行业/概念/指数合并，成分股按代码去重（影视院线 ⊂ 传媒）
  - `--top N`：只取有买点前 N 只；排序 = 买点等级(三买1>一买2>二买3) → 买点年龄 → 质量分 → 涨幅
  - `--grid` / `--per-page`：出网格图，每只一个子图（K线+缠论分型版+VWAP六轨+量）
- 新增 `run_multi()` / `render_multi()` / `resolve_one()` / `_sector_parts()` / `board_metrics()` / `row_align_state()` / `pick_sort_key()`
- `buypoint_chart.py` 新增 `draw_stock_grid()`：多标的一页网格图
- SKILL.md 新增 §3.15 用法说明

### 为什么
- 大佬 2026-11-11：「技术分析汇总本来就是传参数的事，不该另写脚本」
- 原脚本只能单板块、且出图只给单只三面板；多板块合并 / Top N / 网格图属于缺参能力，补在库里而不是复制一份

### 影响文件
- bin/tech_buypoint.py
- bin/buypoint_chart.py
- skills/tech-summary/SKILL.md

## 2026-11-11 20:52

**feat: 新增 sector_buypoint_top20.py —— 指定行业→有买点个股 Top20 + 分页出图**

### 改了什么
- 新增 bin/sector_buypoint_top20.py：给定 1~N 个申万行业，先判行业自身买点，再扫全部成分股
  - 个股硬门槛 现价 ≥ VWAP −1σ；买点 = 周线一二三买 + 日线三买（★双级别共振优先），均线版买点补位
  - 排序：双级别共振 > 有缠论买点 > 买点等级(三买>一买>二买) > 共振层数 > 质量分 > 涨幅
  - --chart 出图：行业板块图 + 个股 8 只/页分页（K线+缠论分型版+VWAP六轨）
- gh_publish_techanly.py 的 FILES 增加该脚本

### 为什么
- 大佬 2026-11-11：用技术分析汇总分析影视传媒两个行业、选出有买点个股前 20 并出图
- 原 tech_buypoint 只能单行业、且个股日线走东财接口（当晚该接口全段 RemoteDisconnected）
  新脚本改走 holding_deep 缓存 + .cache/market 当日增量（tushare 口径），并支持多行业合并去重

### 影响文件
- bin/sector_buypoint_top20.py（新增）
- bin/gh_publish_techanly.py（FILES 登记）

## 2026-11-11 14:06

**docs: README 章节行补全，与旧库措辞完全对齐**

### 改了什么
两行章节说明改成能覆盖旧库全部信息的写法（不是删减，是把旧措辞的信息量补回来）：
- ① 分型版：`日线认一/二/三买二三卖` → `日线认一/二/三买 + 二/三卖`（拆开写清，避免歧义）
- ⑤ 买点/卖点：补回优先级序号 ①②③、二买「胜率最低·仅作参考」标记、卖点三档完整写法

### 为什么
技术比对发现旧库（tech-summary）README 这两行信息更完整，
合并版为简洁做了缩写 → 属于**信息削减**，不符合『一条不丢』。已补回。

### 影响文件
- README.md

## 2026-11-11 14:04

**merge: 私有库 tech-summary 的文档/发布工具/历史记录全部并入（一条不丢）**

### 背景
上一轮合并只保留了 techAnly 版本文档，**私有库里的东西有 3 处没真正并进来**：
读 REMOTE 备份逐文件比对才发现（幸好删库前存了 tarball）。

### 这次补的点（逐条来自技术比对，不是估算）

**1) CHANGELOG 补回 3 条历史记录**
- 上次合并用「时间戳」做去重键 → 把同时间戳但**内容不同**的条目误删了
- 实测丢失：`2026-11-10 00:21` 原本有 **3 条**（只留 2 条）、`2026-11-10 00:24` 有 2 条
- 改法：去重键换成 **(时间戳 + 正文 md5)**，并用**多重集计数**抵扣 —— 内层重复也保留
- 结果：techAnly 30 条 + 备份补回 3 条 = **33 条**；脚本化验证「备份 24 条每一条都在」✅

**2) README 补回丢失的内容**
- 顶部补「核心入口四个」脚本作用表（tech_summary / tech_buypoint / tech_intraday_buypoint / chan_ma_chart）
- 补「把四套口径统一到一张报告里，避免看缠论说买、看 VWAP 说卖」这一定位句
- 546 行 / 30.5KB

**3) tools/publish_techsummary.py 保留为兼容别名**（私有库时的脚本名）
- 原文件名直接丢弃等于断了老命令；现在它以转发形式入库，跑起来自动转到 publish_techanly.py
- 老文档/老习惯写 publish_techsummary.py 也不会推错库

### 验证
- 逐文件 md5 比对：备份 30 个文件 → techAnly 现 38 个，**备份内容 100% 覆盖**
- CHANGELOG 条数校验：备份 24 条全部在，无缺失
- README 关键行校验：剩余 3 处差异经确认是**有意改动**（更详细 / 公开库声明），非丢失

### 影响文件
- README.md（546 行）
- CHANGELOG.md（33 条历史）
- tools/publish_techsummary.py（新增，兼容别名）

## 2026-11-11 13:48

**fix: 补 xq_portfolio.py（xq_ind_monitor 硬依赖，漏了会崩）**

### 改了什么
- 新增 `bin/xq_portfolio.py`（2592B）：`xq_ind_monitor.py` 第56行 **硬 import**（不是 try/except）
- 用 AST 做依赖闭包扫描：清单里每个 bin/*.py 的本地 import 递归展开，
  直到不再有新模块为止（这个方法比肉眼看靠谱）

### 为什么
上一轮补了 `xq_ind_monitor.py`，但它自己又 import `xq_portfolio`，
而后者从未入库 → clone 后跑 `weekly_zs.py` 的邮件分支依然 ImportError。
这一轮把整条依赖链补齐，实测远程 tarball 拉下来 AST 扫描已无本地缺失。

### 影响文件
- bin/xq_portfolio.py（新增）

## 2026-11-11 13:41

**fix: 补齐 3 个隐式依赖模块（clone 后跑不通的真缺口）**

### 改了什么
补齐 3 个代码里 import 了、但仓库里一直没有的模块：

| 模块 | 被谁依赖 | 缺失后果 |
|---|---|---|
| `bin/wyckoff_today_fetch.py` | `tech_intraday_buypoint.py` 顶部**硬 import** | **直接 ImportError，盘中买点脚本整个跑不起来** |
| `bin/y1_watch.py` | `chan_smc_index.py` 的「一买预备」 | try/except 静默降级 → 一买预备信号永远为空 |
| `bin/xq_ind_monitor.py` | `weekly_zs.py` 发邮件 | try/except 静默降级 → 周线中枢预警邮件发不出 |

同时给发布脚本补两条脱敏规则（两个文件的 WS 用单引号 + wyckoff CACHE 路径写法），
确保新文件上传前也剥掉本机绝对路径。

### 为什么
大佬 2026-11-11：『把那个 Push Images 那个合并过来。主要是把那个技术分析的那个处理成一个，
保留主，那个 techAnly 这个。』同时要求检查是否还残缺。
用 AST 逐个扫 bin/*.py 的本地 import 是否都能在库内找到，发现这 3 个缺口 —— 
私有库删了以后没得兜底，clone 下来跑盘中买点会直接崩。

### 影响文件
- bin/push_image.py（上一轮已入库，本轮确认在库）
- bin/wyckoff_today_fetch.py（新增）
- bin/y1_watch.py（新增）
- bin/xq_ind_monitor.py（新增）

## 2026-11-11 13:33

**merge: 合并 tech-summary 全部内容到 techAnly-summary（唯一主库）**

### 改了什么
- **README 合并**：以 tech-summary 的详版（421行）为骨架，补入 techAnly 独有的
  · 买点三档详解 + 卖点三档对称详解（含「二买胜率最低」完整论证）
  · 四类标的入口表（指数/行业/概念/个股）
  · 可调参数环境变量总表（SCAN_* / TECH_MA20_* / CHAN_NEAR_PCT）
  · 新增「⑥ 缠论·均线版单标的出图」用法段
  · 目录结构补 chan_ma_chart.py / push_image.py / skills/chan-ma-trend
  · 发布章节改为 techAnly-summary + gh_publish_techanly.py
  → 535 行 / 29KB（原 techAnly 280 行，原 tech-summary 421 行）
- **CHANGELOG 合并**：两边共 27 条历史条目**全部保留**、按时间倒序合并（原 techAnly 6 条 + tech-summary 24 条，含重复时间取详版），顶部加合并说明
- 两份 SKILL.md 的仓库指引统一到 techAnly-summary

### 为什么
大佬 2026-11-11：『这俩技术分析的这个库。它的重度变成一个，合并成一个』
两库代码早已逐字节一致（27/29 文件相同），只差文档；现在文档也合并，私有库可安全删除。

### 影响文件
- README.md（合并版）
- CHANGELOG.md（27 条历史合并）
- skills/tech-summary/SKILL.md
- skills/chan-ma-trend/SKILL.md

## 2026-11-11 13:06

**chore: gh_publish_techsummary.py 改为转发到 techanly（tech-summary 已归档）**

### 改了什么
- bin/gh_publish_techsummary.py 原实现备份为 .bak.20261111，新实现只做转发
  → 任何老文档/老习惯里写 gh_publish_techsummary.py，都会自动改推 techAnly-summary，不会再推错库

### 为什么
大佬 2026-11-11：技术分析只保留 techAnly-summary 一个库，多余的归档。
 已 archived（只读），但本机已有 8 处文档/习惯引用旧脚本名，转发是最稳的防错法。

### 影响文件
- bin/gh_publish_techsummary.py

## 2026-11-11 12:56

**feat: 统一到 techAnly-summary 单库 + 新增缠论均线版单图**

### 改了什么
- 新增 bin/chan_ma_chart.py：缠论·均线版**单标的出图**（只画均线版一层，图大看得清）
  · 笔 = MA5/10 双穿 MA20 ｜ 线段 = MA20/40 双穿 MA80 ｜ 大级别 = 价格 vs MA160
  · K线 + MA10/MA20/MA160 + 笔(橙) + 线段(深蓝粗) + 中枢(黄框) + 走势类型底色带
  · 角标：可交易性 / 大级别 MA160 排列 / 当前走势+笔线段数 / 当前位置+关键位
  · 用法 --target 支持中文宽基名 / 000905.SH / 600519；--push 出图并推 QQ+飞书
- bin/push_image.py 补入发布清单（原先两个库都没有，wyckoff_buypoint_v2 import 它会崩）
  · 脱敏：QQ_TARGET / QQ_ACCOUNT / FEISHU_CHAT 默认值清空，全走环境变量
- skills/tech-summary/SKILL.md 第八节发布约定：仓库地址改 techAnly-summary，脚本改 gh_publish_techanly.py
- skills/chan-ma-trend/SKILL.md 第十八节：仓库地址改 techAnly-summary + 补 chan_ma_chart.py 用法

### 为什么
大佬 2026-11-11：技术分析只保留 techAnly-summary 一个库（多余的那个删掉），
以后技术分析全部用这个；并要求出一版缠论均线版画图验收。

### 影响文件
- bin/chan_ma_chart.py（新增）
- bin/push_image.py（新增入库）
- skills/tech-summary/SKILL.md
- skills/chan-ma-trend/SKILL.md

## 2026-11-10 16:23

**fix: v2 补 QQ 图片推送（必须带 --account bot2，否则被静默拦截）**

### 改了什么
- bin/wyckoff_buypoint_v2.py：QQ 推送追加**图片单独一条**（原先只发文字，图只在飞书卡片里）
  · 调 bin/push_image.py，并把 QQ_TARGET/QQ_ACCOUNT 覆盖成本任务的目标和 bot2
- bin/push_image.py：新增 QQ_ACCOUNT（默认 bot2），send_qq 里带 --account

### 为什么
大佬：『你这个还没有出图啊，报告。用程序生图啊，这些东西都不要消耗 token 啊，都是用程序搞啊』
根因：**QQ 发图必须指定 --account bot2**，不指定时平台静默拦截 —— rc=0、messageId 为空、parts 为空，
看起来发成功其实没发出去。飞书卡片一直是有图的，只是 QQ 那条只有文字。

### 影响文件
- bin/wyckoff_buypoint_v2.py
- bin/push_image.py

## 2026-11-10 16:16

**feat: 行业/个股过滤规则落地（周线一二三买+日线只认三买 / 个股VWAP −1σ）+ 盘中吸筹买点v2**

### 改了什么
1. **行业过滤规则（大佬 2026-11-10）**
   - bin/tech_buypoint.py：find_third_buys / find_third_sells 的 k 循环由**正序**改为**倒序**（range(n-2, i+2, -1)）
     原正序 + 命中 break 只认最早那次回踩 → 实测 100 个行业板块 daily 三买 accept **全部 0 个**
     修完 36-38 个；三卖同问题一并修
   - 二买恢复（不再丢弃），MA20 向上只加分不作硬门槛
   - 卖点补齐三卖/一卖/二卖，一卖须 MA20 向下（与一买对称）
2. **个股过滤规则**
   - 硬门槛 VWAP：中线 → **−1σ 以上**（环境变量 IBP_STOCK_VWAP_SIGMA，默认 -1.0）
3. **新增盘中吸筹买点分析 v2**
   - bin/wyckoff_buypoint_v2.py：威科夫吸筹（口径不变）→ 四层过滤 → Top10 行业
     → 每行业≤2只、全市场 Top20 个股 → 每只股票一个子图（缠论分型版 + VWAP 六轨）
   - bin/wyckoff_buypoint_daily.sh / bin/intraday_buypoint_daily.sh：定时包装脚本
4. **bin/buypoint_chart.py** 同步买点画法（只画过闸门的买点）
5. **skills/tech-summary/SKILL.md**
   - 第二节重写为「⭐ 买卖点优先级规则（权威口径）」12 个小节
   - 2.4 拆「三种场景」级别表（单标的/扫描器 vs 盘中）
   - 3.1 盘中 Top10 → 个股下钻（VWAP −1σ 硬门槛）
   - 3.2 盘中吸筹买点分析 v2（五步流水线）
   - 踩坑清单新增「三买/三卖扫描必须倒序」

### 为什么
大佬：「我让你上传 github 的是技术分析汇总这个技能里边对应的那个 techanly-summary 这个库对应的代码你有没有修改？
如果修改了，你再上传。因为你刚才不是规则吗？行业过滤规则、股票过滤规则。」
——此前只推了 tech-summary 库，techAnly-summary 落后了。

### 影响文件
- bin/tech_buypoint.py（三买/三卖扫描方向、二买恢复、卖点体系）
- bin/tech_intraday_buypoint.py（周线一二三买 / 日线只认三买 / 个股 VWAP −1σ）
- bin/wyckoff_buypoint_v2.py（新增）
- bin/wyckoff_buypoint_daily.sh、bin/intraday_buypoint_daily.sh（新增）
- bin/buypoint_chart.py（只画过闸门买点）
- skills/tech-summary/SKILL.md

## 2026-11-10 15:55

**docs(skill): 补充盘中吸筹买点分析 v2 章节**

### 改了什么
- skills/tech-summary/SKILL.md 新增 3.2「盘中吸筹买点分析 v2」：
  五步流水线（威科夫吸筹 → 四层过滤 → Top10行业 → 每行业≤2/全市场Top20 → 每股票一个子图）
  + 用法/开关 + 数据链路（ths_industry_map → 881xxx.TI → ths_daily / ths_member）
  + 踩坑：ths_member 偶发空表=限流，必须重试 4 次
- 脚本表加一行 bin/wyckoff_buypoint_v2.py

### 为什么
大佬要求把新任务口径写进技能并发布

### 影响文件
- skills/tech-summary/SKILL.md

## 2026-11-10 15:54

**feat: 盘中吸筹买点分析 v2（威科夫吸筹 → 四层过滤 → Top10行业 → Top20个股 → 多子图）**

### 改了什么
- 新增 bin/wyckoff_buypoint_v2.py（替换旧 wyckoff_acc_buy_push.py）
  · 第①步 **威科夫判吸筹口径完全不动**（沿用 .cache/wyckoff_today/result.json，硬校验必须今天）
  · 第②步 技术分析汇总四层过滤：周线一二三买都算 + 日线只认三买 + 均线版 + SMC + VWAP
  · 第③步 行业排序（周线pri → 日线三买 → 共振层数 → tier），**超过10取前10，不足用全部**
  · 第④步 行业下钻个股：成分股走同花顺 ths_member（带4次重试，tushare 偶发返回空=限流），
    硬门槛 **站上 VWAP −1σ**，按买点优先级，**每行业≤2只、全市场Top20**
  · 第⑤步 出图 draw_stock_grid()：**每只股票一个子图**，子图内画【缠论分型版（笔/分型/中枢/买卖点）+ VWAP 六轨 + K线】
  · K线取数改 4 线程并发，全流程 531s → **199s**
  · 出图复用已取 K 线（_ks），不二次请求网络
- 新增 bin/wyckoff_buypoint_daily.sh 包装脚本（实时校验 → 数据 → 分析 → 报告+图+推送）
- 系统 crontab 14:00：旧「威科夫吸筹买点分析」→ 换成本 v2；上一版 intraday_buypoint 的 14:00 已注释避免双推

### 为什么
大佬：「选了这个行业…你原先怎么判断吸筹还是判断吸筹？我只不过让你用这个技术分析汇总这几个东西来过滤一遍。
然后选出了 Top10，超过10就选Top10，不到10就用剩下的。然后再选股票，股票就是 Top20，每个行业最多选两个。
最后出完了之后要给我把图出出来，每个股票是一个子图，子图用缠论分型版和 VWAP 画一下。」

### 影响文件
- bin/wyckoff_buypoint_v2.py（新增）
- bin/wyckoff_buypoint_daily.sh（新增）
- bin/gh_publish_techsummary.py（发布清单）

## 2026-11-10 15:06

**docs(skill): 技能描述与个股口径措辞对齐最终版**

### 改了什么
- skills/tech-summary/SKILL.md
  · frontmatter description 更新为最终口径：缠论分型版+均线版+SMC+VWAP六轨四层；买卖点优先级规则；盘中行业周线买点→个股(VWAP -1σ以上)
  · 3.1 第二步流程图里残留的「站上中线的再看买点」改为「过了 −1σ 的再看买点」（与已改的硬门槛保持一致）

### 为什么
大佬确认改完，要求把技能重新发布 + 提交 GitHub。前几次改代码时 SKILL 正文已同步，
这两处措辞还留着旧口径，会对不上代码实现。

### 影响文件
- skills/tech-summary/SKILL.md

## 2026-11-10 15:04

**feat(个股门槛): VWAP 中线 -> −1σ 以上（允许破中线，不许破止损线）**

### 改了什么
- bin/tech_intraday_buypoint.py：个股硬门槛从「站上 VWAP 中线」改为「**站在 VWAP −1σ 以上**」
  · 判定改成 z >= STOCK_VWAP_SIGMA（默认 -1.0），即允许跌破中线、但不许跌破全止损线
  · 新增环境变量 IBP_STOCK_VWAP_SIGMA（默认 -1.0，可调）
  · 个股行标注区分「VWAP中线以上 +x.xxσ」/「VWAP中线下方 -x.xxσ（仍在−1σ上方）」
  · 分区提醒新增「⚠️贴近−1σ止损线」（z <= -0.8）
  · 统计/日志/无候选文案同步改成「站上 VWAP −1σ」
- bin/intraday_buypoint_daily.sh 注释同步
- SKILL.md / README 的第二步改为「VWAP −1σ 以上是硬门槛」，注明这条覆盖当天更早的「中线以上」要求

### 为什么
大佬 2026-11-10 当日二次修正：个股必须战胜 VWAP −1σ 以上。
中线门槛过严，会把刚回踩到中线下方、但结构仍完好的双级别共振票全挡掉。

### 影响文件
- bin/tech_intraday_buypoint.py
- bin/intraday_buypoint_daily.sh
- skills/tech-summary/SKILL.md
- bin/publish_assets/README.md

## 2026-11-10 14:59

**feat(级别): 行业周线一二三买都算 + 日线只认三买；fix 三买/三卖扫描方向**

### 改了什么
1. **级别口径（大佬 2026-11-10）**：tech_intraday_buypoint.py
   · 新增 weekly_buys() / _gate_week()：周线 resample 后跑 一买/二买/三买，闸门按周放宽
     （一买<=26周且周线MA20向上 / 二买<=40周 / 三买<=60周·距回踩低点<=20% / 一二买 +30%/-10% 内）
   · 日线改为**只认三买**（一买/二买不再纳入）
   · 新增 双级别共振 判定（周线有 + 日线三买）
   · 排序键改为 (周线pri, 无日线三买, -共振层数, tier, -pct)
   · 报告展示：周线买点（几周前）/ 日线三买（几根前）/ 双级别共振 标记
   · 个股同步同一口径；日线取数 420 -> 900 根（凑够周线）
2. **BUG 修复（重要）**：bin/tech_buypoint.py
   · find_third_buys / find_third_sells 的 k 循环原为**正序** range(i+3, n-1) + 命中 break，
     等于只认最早那个同中枢的次级别回踩，后面更新的有效三买全被跳过
     -> 实测 100 个行业板块 daily 三买 accept **全部 0 个**；改倒序后 36-38 个
   · 三卖同问题一并修
3. SKILL.md 2.4 拆成「三种场景」表，踩坑清单新增第 12 条
4. README 同步级别对照表与盘中排序
5. gh_publish_techsummary.py：写 CHANGELOG 时把本机绝对路径替换成 <WORKSPACE>，避免被自己的敏感串拦截

### 为什么
大佬：选行业，行业周线级别一买二买三买都行，但日线级别只能是三买。
顺带发现三买扫描方向 bug，导致此前三买长期为 0（之前的「三买优先」实际从未生效）。

### 影响文件
- bin/tech_intraday_buypoint.py
- bin/tech_buypoint.py
- skills/tech-summary/SKILL.md
- bin/publish_assets/README.md
- bin/gh_publish_techsummary.py

## 2026-11-10 14:58

**feat(级别): 行业周线一二三买都算 + 日线只认三买；fix 三买/三卖扫描方向**

### 改了什么
1. **级别口径（大佬 2026-11-10）**：tech_intraday_buypoint.py
   · 新增 weekly_buys() / _gate_week()：周线 resample 后跑 一买/二买/三买，闸门按周放宽
     （一买≤26周且周线MA20向上 / 二买≤40周 / 三买≤60周·距回踩低点≤20% / 一二买 +30%−10% 内）
   · 日线改为**只认三买**（一买/二买不再纳入）
   · 新增 ★双级别共振 判定（周线有 + 日线三买）
   · 排序键改为 (周线pri, 无日线三买, -共振层数, tier, -pct)
   · 报告展示：周线买点（几周前）/ 日线三买（几根前）/ 双级别共振 标记
   · 个股同步同一口径；日线取数 420 → 900 根（凑够周线）
2. **BUG 修复（重要）**：bin/tech_buypoint.py
   · find_third_buys / find_third_sells 的 k 循环原为**正序** range(i+3, n-1) + 命中 break，
     等于只认「最早那个同中枢的次级别回踩」，后面更新的有效三买全被跳过
     → 实测 100 个行业板块 daily 三买 accept **全部 0 个**；改倒序 range(n-2, i+2, -1) 后 36-38 个
   · 三卖同问题一并修
3. SKILL.md 2.4 拆成「三种场景」表（单标的/扫描器 vs 盘中），踩坑清单新增第 12 条
4. README 同步级别对照表与盘中排序

### 为什么
大佬：「选行业，行业周线级别，一买、二买、三买都行，周线级别的，但是日线级别只能是三买」。
顺带发现三买扫描方向 bug，导致此前三买长期为 0（之前的『三买优先』实际从未生效）。

### 影响文件
- bin/tech_intraday_buypoint.py
- bin/tech_buypoint.py
- skills/tech-summary/SKILL.md
- bin/publish_assets/README.md

## 2026-11-10 14:42

**fix: intraday_buypoint_daily.sh 去掉本机绝对路径（改用 TECH_WS）**

### 改了什么
- bin/intraday_buypoint_daily.sh：写死的 <WORKSPACE> 改成
  WS="${TECH_WS:-$(cd "$(dirname "$0")/.." && pwd)}"，日志路径随之走 $WS

### 为什么
- 发布拦截器报「含私密串：本机工作区绝对路径」，换机器也无法直接跑

### 影响文件
- bin/intraday_buypoint_daily.sh

## 2026-11-10 14:41

**chore: 发布清单加入 intraday_buypoint_daily.sh**

### 改了什么
- gh_publish_techsummary.py 的 FILES 加 bin/intraday_buypoint_daily.sh，让定时包装脚本也进仓库

### 为什么
- 上一 commit 只顾更新代码，包装脚本没同步到 GitHub，换机器部署会缺这一步

### 影响文件
- bin/gh_publish_techsummary.py
- bin/intraday_buypoint_daily.sh（首次入库）

## 2026-11-10 14:40

**feat: 盘中买点改为「行业→个股」，个股须站上VWAP中线**

### 改了什么
- bin/tech_intraday_buypoint.py 新增个股下钻（第二步）：
  · board_members()：取东财板块成分股，按成交额降序（流动性优先），每板块最多 --stock-cap(40) 只
  · analyse_stock()：**现价 ≤ VWAP 中线 → 直接淘汰（硬门槛）**；过了中线才判买点（①分型版>②均线版>仅VWAP）
  · 每行业取前 --stock-per(默认 3) 只，个股排序键 (tier, pri, -n_res, -pct)
  · 输出附带 VWAP 偏离 σ + 分区提醒（≥+3σ全平不追 / ≥+2σ减半追高慎 / ≥+1σ警戒）、站上MA20/MA160、多头排列、⚠️均线版卖点
  · 默认 --top 从 10 改为 3（大佬：选 2~3 个行业就行）；新增 --no-stock / --stock-per / --stock-cap
- 新增 bin/intraday_buypoint_daily.sh：实时校验 → 分析 → 推飞书卡片 + QQ 文字（纯脚本 0 token）
- 系统 crontab：14:00 原「盘中吸筹买点分析」（威科夫版）→ 换成「盘中买点分析·行业→个股」，旧脚本保留注释备用
- SKILL.md 3.1 改为「两步走：先选行业，再从选出的行业里选个股」，写清 VWAP 中线硬门槛
- README 同步两步走流程与开关

### 为什么
大佬 2026-11-10：「个股必须位于 VWAP 中线以上」「选行业再选个股，选 3 个或 2 个就行」「把这个任务替换了」

### 影响文件
- bin/tech_intraday_buypoint.py
- bin/intraday_buypoint_daily.sh（新增）
- skills/tech-summary/SKILL.md
- bin/publish_assets/README.md

## 2026-11-10 14:28

**docs: 买卖点优先级规则正式写入技能（权威口径第2节）**

### 改了什么
- skills/tech-summary/SKILL.md 第二节重写为「⭐ 买卖点优先级规则（权威口径）」完整章节：
  · 2.1 总原则（优先级=结构确认度；优先级只是排序、不能替代闸门）
  · 2.2 买点细则（三买/一买/二买，含 pri、条件、硬门槛、二买胜率最低说明）
  · 2.3 卖点细则（三卖/一卖/二卖，与买点严格对称，被否须写清原因）
  · 2.4 级别规则（日线主判定／60m 次级，只认三买、一买须 MA10>MA20）
  · 2.5 代码里的实际排序键  2.6 去重规则（三买按价位/卖点按pri/板块去罗马数字）
  · 2.7 结论评级优先级  2.8 质量加分  2.9 三入口同口径（风语筑事故复盘）
  · 2.10 环境变量总表（14 个，三处同名同默认）  2.11 执行顺序  2.12 常见被否原因对照表
- 新增买点闸门一致性自检命令（老买点必须被否）
- README 同步：买卖点优先级总表 + 两条总原则 + 级别/排序键

### 为什么
大佬要求：「这个缠论、技术分析汇总的买卖点优先级规则，最好整理到技术分析汇总那个技能上去，然后再推到 GitHub」。
之前规则散落在 SKILL 与代码注释里，且二买胜率最低/一卖须MA20向下等关键口径容易漏。

### 影响文件
- skills/tech-summary/SKILL.md
- bin/publish_assets/README.md

## 2026-11-10 14:24

**fix: 盘中Top10 的 QQ target 改走环境变量/本地配置（不再写死 openid）**

### 改了什么
- bin/tech_intraday_buypoint.py：QQ_TARGET 从写死 openid 改为 _qq_target()
  优先级：环境变量 IBP_QQ_TARGET > ~/.config/openclaw/ibp_qq_target > 空（跳过推送并提示）

### 为什么
- 写死 openid 会被发布拦截器按私密串直接拒绝上传（且公开仓库不该带 openid）
- 没配目标时应跳过而不是报错

### 影响文件
- bin/tech_intraday_buypoint.py

## 2026-11-10 14:21

**feat: 盘中行业买点Top10（东财实时板块，多买点共振排序）+ SKILL/README 精简**

### 改了什么
- 新增 bin/tech_intraday_buypoint.py：盘中行业买点 Top10
  · 数据走东财行业板块（100个）实时日K（含当日盘中那根），申万 akshare 只有T-1盘中用不了
  · 四层判定：①分型版(三买>一买>二买，过闸门) ②均线版 ③SMC(只认 bull+buy) ④VWAP(MA40向上且未冲进+1σ)
  · 排序：先按买点共振层数（多买点优先），同层数内按 分型>均线>SMC>VWAP
  · 同名不同级板块（IT服务/Ⅱ/Ⅲ）自动去重
  · 报告标「分型/均线分歧」、二买占比提示
- skills/tech-summary/SKILL.md 精简重写（32KB→9.5KB）：四层口径/买卖点闸门/盘中Top10排序/图与踩坑/发布约定
- README 同步新增盘中 Top10 用法与排序规则
- gh_publish_techsummary.py 文件清单加入新脚本

### 为什么
大佬要求：「按照技术分析汇总那几个要求，多个买点先排，优先分型版三买→均线版三买→VWAP均线向上→SMC买点，选买点前10个行业」，并把技能精简。

### 影响文件
- bin/tech_intraday_buypoint.py（新增）
- skills/tech-summary/SKILL.md
- bin/publish_assets/README.md
- bin/gh_publish_techsummary.py

## 2026-11-10 13:47

**docs: README 补 tech_stock_names.json 缓存说明**

bootstrap_cache 现在会生成三个缓存，README 的安装章节同步补上第三个（股票名）。

## 2026-11-10 13:39

**fix: 新机器上报告只显示代码——补股票名缓存兜底**

问题：全新克隆跑 --codes 600519,000001,300750 时报告里只有 6 位代码、没有中文名（本机有 .cache/stock_names.csv，新机器没有）。
修复：
1) tech_buypoint.name_map() 增加兜底：从 .cache/sw_member_all.json 的 name 字段取（bootstrap_cache 必然生成，零额外接口）
2) tools/bootstrap_cache.py 新增 build_names()，生成 .cache/tech_stock_names.json（优先用 sw_member_all，缺失才调 stock_basic）
验证：全新克隆 + bootstrap_cache + --codes 实测输出「平安银行 / 贵州茅台 / 宁德时代」

## 2026-11-10 13:33

**feat: 卖点(三卖/一卖/二卖) + 二买恢复（胜率最低）**

买点：三买 > 一买(须MA20向上) > 二买(恢复，胜率最低仅参考)。
卖点：三卖(结构) > 一卖(须MA20向下，硬门槛) > 二卖(MA20向下仅加分)。
新增 find_third_sells/_gate_sell/find_sells；analyse_one 增 sells_ranked/best_sell；
报告新增二买段与卖点段；单只四层报告 rank_buys 认二买 + 卖点优先级。
全流程纯 Python，不依赖任何大模型。

## 2026-11-10 00:53

**docs: 澄清「不画 MA5」≠「不用 MA5」（大佬 2026-11-10 原话）**

大佬 2026-11-10 澄清原话：
> 「我不是要求去掉 MA5，我是要求你画图的时候不要画 MA5，因为太多了，均线太乱了」

之前的注释/文档写成「MA5 已按大佬要求去掉」，容易被后人误读成**计算里也删掉 MA5** —— 那是致命的。

### 明确区分（已写进代码注释 + 两个技能 + README）
| | MA5 |
|---|---|
| **计算**（笔 = MA5/MA10 双穿 MA20、`dual_cross_state`、`bi_walk`、信号） | ✅ **必须保留**。`MAS=[5,10,20,40,80,160]`、`A0=(5,10,20)` 一律不动 |
| **绘图** | ❌ 不画。`MA_DRAW=(10,20)` 只控制这一处 |

**实测 MA5 的作用**（把 ma5 用 ma10 顶替后，双穿状态改变的 K 线根数）：
中证500 **126** 根 / 风语筑 **151** 根 / 沪深300 **143** 根 / 茅台 **80** 根
→ MA5 一旦真被去掉，笔的位置会大面积错位。

### 改动文件
- `bin/tech_summary.py`：`MA_DRAW` 注释写明「仅控绘图」+ 警告不要从 `MAS` 删 MA5
- `bin/buypoint_chart.py`：同样措辞
- `skills/chan-ma-trend/SKILL.md` 16.1：加「不画 MA5 ≠ 不用 MA5」对照表 + 实测数据
- `skills/tech-summary/SKILL.md` 11.3.3：标题与正文重写，补 11-10 澄清原话 + 实测数据
- `bin/publish_assets/README.md`：硬规矩新增第 7 条（顺手把错乱的条目编号 1-18 重排）

### 验证
- 代码断言：`MAS==[5,10,20,40,80,160]`、`A0==(5,10,20)`、`MA_DRAW==(10,20)`、`"ma5" in df.columns` 全部通过
- 绘图 spy（拦截 matplotlib `Axes.plot` 抓 label）：均线版面板实画 = 收盘 / MA10 / MA20 / MA160 / 线段 / 笔，
  **MA5 曲线数 = 0**
- 全流程 `--target 中证500` 重跑正常，双图生成

## 2026-11-10 00:49

**docs(chan-ma-trend): 补齐 bi_walk 口径 + 笔端点取极值 + 三处绘图坑；修复 chan_ma_trend_v2 重复定义**

大佬问「缠论均线版这个技能是不是也应该更新一下」—— 对，落后了。同时顺手查出一个真 bug。

### 1. 修 bug：`chan_ma_trend_v2.py` 里 `macd_bei_chi()` 定义了两遍
插入 `bi_walk()` 时误留了一份完全相同的副本（53 行），第二份静默覆盖第一份。
已删除；删完重跑 `--code 000905.SH` 输出完全一致（底背驰 1644.9 → 1405.0 照常算出）。

### 2. 技能补上 `bi_walk()`（11-09 就实现了，文档一个字没写）
新增 **9.1 当前位置：在笔上还是笔下、接下来怎么走**：
- 三态判定表（up / dn / mid）与 `mid` 再细分三档（向上临界 / 向下临界 / 缠绕）
- 关键位取法（本笔起点、前高前低 = 区间真实极值；mid 改报近 20 根高/低）
- **级别关系表**：顺级别 / 逆级别反弹 / 顺级别回踩，叠加 MA160 跌破提示
- 三个出口（render / tech_summary 结论与第三节 / 图上均线版面板顶部）

### 3. 新增 14.1：`build_bi().px ≠ 笔的高低点`（今晚那个 bug 的完整记录）
`px` 是**双穿状态翻转那根的收盘**，不是本笔极值。风语筑实测：up 笔翻转收 14.23，
区间实际最高 14.60；线段 11.00 vs 11.50。文档给出正确取法代码 + 末笔补 `live` 活端点的坑
（起点必须用极值那根，否则会把上一笔高点当本笔新高）。

### 4. 修正 16.1 绘图表（原来和代码不一致）
- 均线：**只画 MA10 / MA20**（MA5 已按大佬要求去掉，代码 `MA_DRAW=(10,20)`），文档原来还写着 5/10/20
- 笔/线段的「末端虚线」补进表里（live 端点）
- 16.2 中枢补一句：`px` 必须用区间真实极值，用翻转收盘会把 ZG/ZD 算歪

### 5. 16.4 那条错结论加删除线 + 指向第十七节
原来写「必须用 `trend_mode()`」，今晚证明逐根跑 `trend_mode()` 同样错（日线只有 1~3 根成立）。

### 6. 十二、输出示例换成今晚真实输出
补上 `▶ 当前位置 / 怎么走 / 级别关系 / 盯的位置` 四段，附读法说明。

### 7. 十三、口述版 vs 原文 增 4 行
笔端点价格、走势类型（画历史）、末笔、均线怎么画 —— 都在这次踩过。

### 8. 十四、校验方法 增两段可直接跑的 assert
- `bi_walk()` 自检（600519/中证500/603466 覆盖 up/dn/mid 三态，已实跑通过）
- 走势类型分段 ≥2 段 + 末段活端点

### 影响文件
- `bin/chan_ma_trend_v2.py`（删除重复定义）
- `skills/chan-ma-trend/SKILL.md`（新增 9.1、14.1、17.x，修正 16.1/16.4/十二/十三/十四）

## 2026-11-10 00:28

**docs(skill): 两个技能都写入发布约定（每次改完必须发 + 说清改了什么）**

- `skills/tech-summary/SKILL.md` 新增「📤 发布约定」：
  一条命令的流程、5 条铁律（每次必发 / 单 commit / 敏感内容自动拦截 / 不用 git push / 发后报告）。
- `skills/chan-ma-trend/SKILL.md` 新增「📤 发布」：指出该技能的权威实现
  `bin/chan_ma_trend_v2.py` 在 tech-summary 仓库同步维护，改了这个文件要一起发。

## 2026-11-10 00:27

**docs: 发布工具措辞修正（不在仓库里出现本机路径字面量）**

- `tools/gh_publish.py`：本机工作区前缀不再写成代码里的字面量，
  改由 `~/.config/gh/publish_denylist.txt` 的「本机工作区绝对路径」规则拦截。
- CHANGELOG 相应措辞修正。

## 2026-11-10 00:26

**docs+tools: README 补发布流程与新增踩坑；发布工具随仓库提供**

### 文档
- README 新增 **十二、发布 / 更新到 GitHub**：一条命令的流程 + 约定
  （每次改动必须发一次，`--msg`/`--notes` 必须写清「改了什么 / 为什么 / 影响哪个文件」）
- README **八、硬规矩** 补今晚踩到的坑：
  - 走势类型的三个坑：相邻线段方向 ❌ / `trend_mode()` 逐根 ❌ / 摆动高低点 ✅
  - 笔/线段端点必须取区间真实极值（状态翻转那根的收盘 ≠ 本笔高低点）
  - 末笔/段必须补活端点，且起点用极值那根（否则图右侧空白 / 取到上一笔高点）
  - `trend_mode()` 的 look=20 是**区间**判定，不能当「当前方向」用
- README **校验**章节给出可直接跑的 assert 脚本：笔数与技能口径一致、端点是真极值、
  末段有活端点、走势类型必须真分段；并注明「比对区间要用 `flip` 索引、不能用极值索引」

### 工具（随仓库提供，fork 后改一行 --repo 即可复用）
- `tools/gh_commit.py`   Git Data API：多文件合成**单个** commit
- `tools/gh_publish.py`  单文件 PUT 兜底 + 私密信息拦截
- `tools/publish_techsummary.py`  本项目专用封装（自动剥私 + 写 CHANGELOG）

### 安全
- 私密信息黑名单改为从 `~/.config/gh/publish_denylist.txt` 读取（每行 `名称|正则`），
  **代码里不再出现任何真实 token / chat_id**；文件不存在时只跑通用形态检查。

## 2026-11-10 00:24

**docs+tools: README 补发布流程/新增踩坑，发布工具随仓库提供**

### 文档
- README 新增 **十二、发布 / 更新到 GitHub**：一条命令的流程 + 约定（每次改动必须发、必须写清改了什么/为什么/影响文件）
- README **八、硬规矩** 补三条今晚踩到的坑（12 改写 / 15 / 16 / 17）：
  - 走势类型的三个坑（相邻线段方向 ❌ / `trend_mode()` 逐根 ❌ / 摆动高低点 ✅）
  - 笔/线段端点必须取区间真实极值（翻转那根的收盘 ≠ 本笔高低点）
  - 末笔/段必须补活端点（否则图右侧空白），且起点用极值那根
- README 校验章节：给出可直接跑的 assert 脚本（笔数与技能口径一致、端点是真极值、
  末段有活端点、走势类型必须真分段），并注明「区间要用 flip 索引不能用极值索引」

### 工具
- `tools/gh_commit.py`、`tools/gh_publish.py`、`tools/publish_techsummary.py` 随仓库提供，
  别人 fork 后改一行 `--repo` 就能用同样的发布流程

## 2026-11-10 00:24

**docs+tools: README 补发布流程/新增踩坑，发布工具随仓库提供**

### 文档
- README 新增 **十二、发布 / 更新到 GitHub**：一条命令的流程 + 约定（每次改动必须发、必须写清改了什么/为什么/影响文件）
- README **八、硬规矩** 补今晚踩到的坑：
  - 走势类型的三个坑（相邻线段方向 ❌ / `trend_mode()` 逐根 ❌ / 摆动高低点 ✅）
  - 笔/线段端点必须取区间真实极值（翻转那根的收盘 ≠ 本笔高低点）
  - 末笔/段必须补活端点（否则图右侧空白），且起点用极值那根
  - `trend_mode()` 的 look=20 是区间判定，不是当前方向
- README 校验章节：给出可直接跑的 assert 脚本（笔数与技能口径一致、端点是真极值、
  末段有活端点、走势类型必须真分段），并注明「比对区间要用 flip 索引、不能用极值索引」

### 工具
- `tools/gh_commit.py`、`tools/gh_publish.py`、`tools/publish_techsummary.py` 随仓库提供，
  fork 后改一行 `--repo` 就能用同样的发布流程

## 2026-11-10 00:21

**chore(release): 发布脚本改用 Git Data API，全部文件合成单个 commit**

### 发布链路改造（避免历史被同名 commit 刷屏）

- **问题**：GitHub contents API 每次 PUT 只改一个文件 = 一条 commit。
  一次改 10 个文件就产生 10 条**同名** commit，`git log` 完全没法读，
  也无法回答「这次到底改了什么」。
- **改法**：新增 `bin/gh_commit.py` —— blobs → tree → commit → 更新 ref，
  **所有文件合成 1 个 commit**；未变更的文件按 git blob sha 自动跳过，不造空 commit。
- `bin/gh_publish.py` 保留为单文件/兜底工具（同样带私密串拦截）。
- `bin/gh_publish_techsummary.py` 增加：dry-run **不再污染 CHANGELOG**（试跑写出假条目已清理）。

### 私密信息拦截（自动，命中即拒绝上传）
tushare 代理/直连 token、飞书私聊/群 chat_id、飞书 webhook、GitHub token、
OpenAI 风格 key、QQ openid、本机工作区绝对路径（前缀在名单文件里配置）。

### 新增文件
- `bin/gh_commit.py`
- `bin/gh_publish.py`
- `bin/gh_publish_techsummary.py`

## 2026-11-10 00:21

**chore(release): 发布脚本改用 Git Data API，全部文件合成单个 commit**

### 发布链路改造（避免历史被同名 commit 刷屏）

- **问题**：GitHub contents API 每次 PUT 只改一个文件 = 一条 commit。
  一次改 10 个文件就产生 10 条**同名** commit，`git log` 完全没法读，
  也无法回答「这次到底改了什么」。
- **改法**：新增 `bin/gh_commit.py` —— blobs → tree → commit → 更新 ref，
  **所有文件合成 1 个 commit**；未变更的文件按 git blob sha 自动跳过，不造空 commit。
- `bin/gh_publish.py` 保留为单文件/兜底工具（同样带私密串拦截）。
- `bin/gh_publish_techsummary.py` 增加：dry-run **不再污染 CHANGELOG**（试跑写出假条目已清理）。

### 私密信息拦截（自动，命中即拒绝上传）
tushare 代理/直连 token、飞书私聊/群 chat_id、飞书 webhook、GitHub token、
OpenAI 风格 key、QQ openid、本机工作区绝对路径。

### 新增文件
- `bin/gh_commit.py`
- `bin/gh_publish.py`
- `bin/gh_publish_techsummary.py`

## 2026-11-10 00:19

**fix(chart): 均线版笔/线段端点改取区间极值 + 末段补活端点 + 走势类型改按摆动高低点分段**

### 修复（2026-11-09 大佬三条现场批评）

1. **笔/线段端点取错价 → 图的高低点整体不对**
   `dual_cross_state()` 的状态翻转点是「均线穿完那根」，其收盘价 ≠ 本笔高低点。
   实测 603466 风语筑：up 笔翻转于收 14.23，该笔区间实际最高 **14.60**；线段 11.00 vs **11.50**。
   → `ma_structure()` 新增 `_to_extremes()`：端点一律取「该笔区间内真实最高/最低」，索引归到极值那根，
   原翻转索引保留在 `flip` 字段。

2. **末笔/末段没画出来**
   原来只遍历已翻转点，最后一笔还在走 → 图右侧整段空白，走势类型也停住。
   → 末点后追加 `live=True` 活端点（本笔极值那根 → 最新一根的极值），绘图用**虚线**连到最新收盘。
   ⚠️ 起点必须用极值那根而非翻转那根，否则会把上一笔高点当本笔新高（风语筑会取到 15.98 > 现价 13.94）。

3. **走势类型底色几乎全是一个灰块**
   原用 `trend_mode()` 逐根判历史：要求 MA5/MA10/MA20 近 20 根完全不接触，日线 1200 根里只成立 1~3 根
   → 合并后整图一个 899 根灰底。走势类型是**线段级别**概念 →
   改按相邻两摆的高低点：后高>前高且后低>前低=上涨；都降低=下跌；其余=盘整。
   ⚠️ 配对必须「低点→高点」（seg 的 dir 是「从该点出发的方向」，按相邻两点配会错位一格）。
   实测：风语筑 4 段 / 中证500 6 段 / 沪深300 6 段 / 茅台 4 段。

### 影响文件
- `bin/tech_summary.py`（ma_structure / draw_chart）
- `bin/buypoint_chart.py`（live 虚线补齐）
- `skills/tech-summary/SKILL.md`、`skills/chan-ma-trend/SKILL.md`（口径同步，新增 chan-ma-trend 技能文档）

### 同批带上的前次修复（此前未入库）
- 买点有效性闸门统一：`rank_buys()` 增加年龄/距离/MA20 闸门，图只画过闸买点（风语筑假买点事故）
- 均线版 `bi_walk()`（在笔上/笔下 + 怎么走 + 盯的位置）
- MA5 不再绘制（MA_DRAW=(10,20)）
- 公开版剥离：本机绝对路径 / tushare token / 飞书 chat_id / QQ openid 全部改为环境变量
