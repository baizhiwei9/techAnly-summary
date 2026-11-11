# strategies · 策略索引与级别适配

本目录把仓库里的技术分析口径按「策略」归档，并提供**一条命令按级别跑**的统一入口。
不改动 `bin/`、`smc/` 里已经跑通的实现，只做索引 + 级别适配，避免复制两份公式。

## 一、现在有哪几套

| # | 策略 | 口径 | 代码位置 | 说明 |
|---|---|---|---|---|
| ① | **缠论·分型版** | 包含处理 → 分型 → 笔 → 中枢 → 背驰 → 一二三买 | `bin/chan_smc_index.py` | 交易级别主口径 |
| ② | **缠论·均线版** | 笔 = MA5/10 双穿 MA20；线段 = MA20/40 双穿 MA80；大级别 = 价格 vs MA160 | `bin/chan_ma_trend_v2.py` | 只看当前级别能否支撑交易 |
| ③ | **SMC** | 摆动点 → 结构突破 → FVG/OB → 方向确认 | `smc/smc_core.py` | 只作提示，不单独入场 |
| ④ | **VWAP 六轨** | VWAP ±1σ/±2σ/±3σ（窗口 60）+ MA40 方向 | `smc/smc_core.py` | ±3σ 全平 / +2σ 减半 / −1σ 全止损 |
| ⑤ | **小波·多尺度** | 因果小波(db4, level=4) + 周线过滤 + 分型底 | `strategies/wavelet/` | **日线/周线策略**，见本目录 wavelet/README |
| ⑥ | **威科夫吸筹** | 吸筹判定 → 四层过滤 → Top10 行业 → Top20 个股 | `bin/wyckoff_buypoint_v2.py` | 盘中用，过滤层是周线/日线 |

> 用户常说的「缠论的、分型版、均线版、小波、SMC」= ① ② ⑤ ③；
> 「还有什么」= ④ VWAP 六轨、⑥ 威科夫吸筹。

## 二、级别支持矩阵（2026-11-11 实测）

`strategies/run_level.py` 统一支持 `--level m15|h1|day|week`：

| 策略 | 15分钟 | 小时线 | 日线 | 周线 | 级别怎么来 |
|---|:---:|:---:|:---:|:---:|---|
| ① 缠论分型版 | ✅ | ✅ | ✅ | ✅ | 给什么级别 K 线就算什么级别；周线由日线 resample |
| ② 缠论均线版 | ✅ | ✅ | ✅ | ✅ | 同上（MA160 需 ≥170 根） |
| ③ SMC | ✅ | ✅ | ✅ | ✅ | 同上；k 值：m15/h1=1，day/week=3 |
| ④ VWAP 六轨 | ✅ | ✅ | ✅ | ✅ | 同上，窗口 60 根 |
| ⑤ 小波 | ❌ | ⚠️ 模块在、停用 | ✅ | ✅（作过滤） | 小波参数按日线尺度定，不能直接套 15 分钟 |
| ⑥ 威科夫 | 盘中（过滤层为周/日） | — | ✅ | ✅（过滤） | 吸筹判定本身不是按任意级别重采样 |

### 几个必须说清的点

1. **15 分钟数据只有约 320 根（腾讯分钟线上限）**，约 1 个月。
   跑结构够用（笔/中枢/MA160 都能算），但**不能拿它当长周期回测**。
2. **周线不是单独取数**，是日线 `resample("W-FRI")` 合成，保留 volume。
3. **分型版的小时线买点规则不同**：日线认一/二/三买；小时线只认三买，
   一买须 MA10>MA20 金叉，二买一律不认（`bin/tech_summary.py` 的口径）。
   周线在 `bin/tech_intraday_buypoint.py` 里只认一买/二买，与日线三买是两套闸门。
4. **小波的级别**：ETF 组合 = 日线，周线只做趋势过滤；期货日线 v5 = 日线；
   期货 1h 模块（`strategies/wavelet/strategies/wavelet_futures_1h.py`）存在，
   但 `signal_monitor.py` 里已整体注释停用。**没有 15 分钟小波**。

## 三、用法

```bash
# 四层汇总，默认日线
python3 strategies/run_level.py --target 中证500 --level day

# 15 分钟 / 小时线 / 周线
python3 strategies/run_level.py --target 600519 --level m15
python3 strategies/run_level.py --target 600519 --level h1
python3 strategies/run_level.py --target 600519 --level week

# 只跑某一套口径
python3 strategies/run_level.py --target 000905.SH --level m15 --strategy fractal
#   fractal / ma / smc / vwap / all

# 四个级别一次跑完
python3 strategies/run_level.py --target 中证500 --level all
```

标的写法与主库一致：中文宽基名（中证500）、`000905.SH`、`sh000905`、`600519`、`SZ000001`。

## 四、目录

```
strategies/
├── levels.py          # 统一取数 + 级别解析 + 周线合成
├── run_level.py       # 按级别跑四层（fractal/ma/smc/vwap）
├── README.md          # 本文件
└── wavelet/           # 小波多尺度（日线/周线）
    ├── signal_monitor.py
    ├── run_monitor.sh
    ├── strategies/    # 小波核心模块（portfolio v4 / futures v5 / 1h / structure）
    └── README.md
```

## 五、硬规矩

- 脚本开头必须禁 IPv6（容器 IPv6 不通会静默卡死），`run_level.py` 复用 `tech_summary`
  的补丁，单独写新脚本时记得自己加。
- 纯 Python 规则计算，0 token，不调用任何大模型。
- 公开库不放真实飞书 chat_id / webhook / 本机绝对路径；部署配置走环境变量。
