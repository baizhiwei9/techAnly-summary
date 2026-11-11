# 小波·多尺度策略（wavelet）

分支 `wavelet_day` 引入的第五套口径。原部署在
`云主机的开发环境/wavelet_strategy/`，这里做的是**归档 + 去敏**，
策略公式一个字没改。

## 一、它是什么

用**因果小波（causal wavelet）**把价格序列分解到多个尺度，取低频趋势 + 周线过滤，
再叠一层缠论分型底判断，输出 ETF / 期货的多空信号与仓位。

| 模块 | 作用 |
|---|---|
| `signal_monitor.py` | 实盘信号监控入口：扫 ETF + 白银期货，状态持久化，新信号推飞书 |
| `strategies/wavelet_portfolio_v4.py` | ETF 组合：`build_sig_single` / `causal_wt` / `chan_bottom_strength` |
| `strategies/wavelet_futures_v5.py` | 期货日线：`build_sig_futures_v5` / `backtest_futures_v5` |
| `strategies/wavelet_futures_1h.py` | 期货 1 小时：**模块存在，当前停用** |
| `strategies/wavelet_structure*.py` | 早期结构版（v1/v2/v3），保留作对照 |
| `strategies/wavelet_futures_stop.py` / `_v3` / `_v4` | 期货止损与历史版本 |

## 二、级别：日线 / 周线，不是 15 分钟

| 品种 | 交易级别 | 过滤级别 | 15 分钟 |
|---|---|---|---|
| 510300 / 159915 / 510500 ETF | 日线 | 周线（`weekly_w=30`, `weekly_level=3`） | ❌ 不支持 |
| AG 白银期货 | 日线（v5） | 周线 | ❌ 不支持 |
| AG 白银期货 | 1 小时（`wavelet_futures_1h.py`） | 日线趋势过滤 | ⚠️ 模块在，监控里已注释停用 |

**为什么不套 15 分钟**：`db4 / level=4 / weekly_w=30` 这组参数是按日线尺度定的，
直接搬到 15 分钟会把「周线过滤」变成「半天过滤」，信号含义完全变了。
要出 15 分钟的小波口径，必须重新定参 + 重新回测，不能只换一根 K 线。

## 三、运行

```bash
# 手动跑一次
cd strategies/wavelet
python3 signal_monitor.py

# crontab（工作日 5 次：10:15 / 11:35 / 14:15 / 15:10 / 22:15）
15 10 * * 1-5 /path/to/strategies/wavelet/run_monitor.sh >> /var/log/wavelet_signal_monitor.log 2>&1
```

依赖：`numpy pandas pywt akshare requests`，推送另需 `lark-cli`（可选）。

## 四、环境变量（去敏后唯一配置入口）

| 变量 | 必填 | 说明 |
|---|---|---|
| `WAVELET_FEISHU_CHAT_ID` | 推送时必填 | 飞书群 chat_id；不设则只算不发 |
| `WAVELET_SIGNAL_FILE` | 否 | 信号共享文件，默认 `/tmp/wavelet_latest_signals.json` |
| `LARKSUITE_CLI_CONFIG_DIR` | 推送时 | lark-cli 配置目录 |
| `LARKSUITE_CLI_DATA_DIR` | 否 | 默认同配置目录 |
| `LARKSUITE_CLI_LOG_DIR` | 否 | 默认 `/tmp` |

> 公开库里没有真实 chat_id、webhook、本机绝对路径。部署机自己在 crontab 里 export。

## 五、状态与产物

- 状态：`state/`（`signal_monitor.json`，跨次运行去重、持仓变化检测）
- 行情缓存：`data/*.parquet`
- 共享信号：`WAVELET_SIGNAL_FILE`，供缠论分型版 / 统一流水线读取合并

## 六、和四层汇总的关系

小波是**独立账户、独立风控**的一条策略线（模拟盘：小波 50 万 + 缠论分型版 50 万），
不参与 `bin/tech_summary.py` 的四层打分。它导出的 `wavelet_latest_signals.json`
只作为「多策略共识」的一票，不覆盖四层结论。
