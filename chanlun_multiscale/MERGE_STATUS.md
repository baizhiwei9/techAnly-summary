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

## 远程库分支状态

仓库：`https://github.com/baizhiwei9/techAnly-summary`（公开，唯一主库）

| 分支 | HEAD | 内容 |
|---|---|---|
| `wavelet_day`（当前工作分支） | `9fa28f1` | 五套口径统一级别入口 + 小波策略归档 + 各级别闸门标注 |
| `origin/main` | `b5810de` | 出图清晰度提升、单图最多 20 子图、买卖点标注 |

两支从 `5d72f91` 分叉，尚未回合：
- `main` 领先 2 个出图提交（`e226aba`、`b5810de`）
- `wavelet_day` 领先 2 个多尺度/小波提交（`51f3bea`、`9fa28f1`）

**待办**：把 `main` 的出图改动合回 `wavelet_day`（或反之），再统一推 `main`。

## 下一步

1. 人工复核第二批 v2 三买（尤其科创50ETF 日线）后，决定是否将 `structure_signals_v2` 接实盘
2. 合并远程 `main` 与 `wavelet_day` 两支
3. 实盘监控继续用 `Strategy/chanlun_fractal_Strategy.py`（v1 信号 + 四级闸门）
