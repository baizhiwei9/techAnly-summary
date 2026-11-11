# 合并进度（2026-11-11）

## 一句话

远程 `techAnly-summary` 与本地缠论多尺度交易工程正在做**口径合并**：指标参数、级别闸门、买卖点判定已按远程定稿对齐；信号 v2 仍处于对比评估阶段，未接实盘。

## 已完成

| 批次 | 内容 | 文件 | 状态 |
|---|---|---|---|
| 第一批 | 四级交易闸门（15m/1h/日线/周线）对齐远程 | `chanlun_backtest/signal_gates.py` | ✅ 已接入 |
| 第二批 | 三买中枢递进校验（修「巨人网络」假三买）；二买锚定一买后首个回踩 | `chanlun_backtest/structure_signals_v2.py` | 🟡 仅对比，未接实盘 |
| 第三批 | MACD `(20,40,5)` → `(10,20,5)` 全库统一 | `chanlun_backtest/structure_indicators.py` | ✅ 已接入 |

对齐依据：远程 `techAnly-summary` main `5d72f91` + 2026-11-08 / 11-10 / 11-11 定稿。
本地备份：`structure_indicators.py.bak_20261111_macd`、`Strategy/chanlun_fractal_Strategy.py.bak_20261111_batch13`。

## 第二批对比结果

数据：`reports/batch2_compare/summary.csv`（旧版 / v2a / v2b 信号数），图：同目录 PNG。

要点：
- v2 显著压缩「二买」过滥（旧版任意更高低点都算二买），沪深300ETF 日线二买 4 → 1
- 三买在科创50ETF 日线出现 2 个（旧版 0），需人工复核是否为有效中枢递进
- MACD 10/20/5 与旧 20/40/5 的信号总数差异很小（`macd_summary.csv`：29→28、27→27、23→22、26→25）

### 逐条差异图（2026-11-11 新增）

`compare_batch2_rules.py` 给每条规则差异单独出一张图（上=旧口径，下=新口径，
虚线+圆圈=只在一边出现的信号），输出 `reports/batch2_rules/`：

| # | 差异 | 代表案例 | 旧 → 新 |
|---|---|---|---|
| 1 | 二买锚定一买后首个回踩 | 沪深300ETF 日线 | 4 → 1（旧版独有 719/763/838） |
| 2 | 二卖锚定一卖后首个反抽 | 沪深300ETF 日线 | 4 → 0（旧版独有 848/914/967/1018） |
| 3 | 三买遍历中枢+递进校验+现价>ZG | 沪深300ETF 周线 | 0 → 1（新增 144） |
| 4 | 一买改用远程段背驰口径 | 创业板ETF 日线 | v2a 0 → v2b 2（新增 789/1016） |
| 5 | 一卖改用远程段背驰口径 | 沪深300ETF 日线 | v2a 0 → v2b 2（新增 757/772） |

汇总：`reports/batch2_rules/diff_summary.csv`

## 远程库分支状态

仓库：`https://github.com/baizhiwei9/techAnly-summary`（公开，唯一主库）

| 分支 | HEAD | 内容 |
|---|---|---|
| `wavelet_day`（当前工作分支） | `0a13c09`（merge） | 多尺度工程 + v2 新版规则 + main 出图改进（已回合） |
| `main` | `357b0ca` | 出图清晰度、单图最多 20 子图、买卖点标注、布局重叠自检 |

**已回合**（2026-11-11）：
- `origin/main` 的 4 个出图提交（`e226aba`、`b5810de`、`3c215c9`、`357b0ca`）合入 `wavelet_day`
- 两支出图/规则文件无重叠；`CHANGELOG` 按时间合并，两边条目一条不丢
- 合并后校验：规则文件与工作区 md5 全同；五条差异信号数复算全部一致（B2 4→1、S2 4→0、B3 0→1、B1 0→2、S1 0→2）；全库 `py_compile` 通过；敏感信息扫描无命中
- 冲突处理原则：**一律以新版 v2 规则为准**，不覆盖

**待办**：`wavelet_day` 稳定后再把整支（含多尺度 + v2）快进/合并到 `main`。

## 下一步

1. 人工复核第二批 v2 三买（尤其科创50ETF 日线）后，决定是否将 `structure_signals_v2` 接实盘
2. 实盘监控继续用 `Strategy/chanlun_fractal_Strategy.py`（v1 信号 + 四级闸门）；切 v2 前先跑灰度对比
3. `wavelet_day` 验证稳定后合入 `main`
