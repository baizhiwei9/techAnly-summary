# techAnly 合并多尺度交易

本目录是「本地缠论多尺度交易工程」与「远程 techAnly-summary 技术分析汇总库」的合并落盘位置（2026-11-11）。

## 目录结构

```
techAnly_合并多尺度交易/
├── README.md                  # 本说明
├── MERGE_STATUS.md            # 合并进度、口径对齐清单、待办
├── techAnly-summary/          # 远程库完整克隆（含 .git，main / wavelet_day 两支）
│   ├── bin/                   # 缠论分型版、均线版、SMC、买点、出图、飞书推送
│   ├── smc/                   # SMC 核心
│   ├── strategies/            # 五套口径统一级别入口 + 小波策略归档
│   ├── skills/                # tech-summary / chan-ma-trend 技能说明
│   └── tools/                 # 发布/缓存/提交工具
└── chanlun_multiscale/        # 本地缠论多尺度交易工程
    ├── chanlun_backtest/      # 结构版核心：包含→分型→笔→中枢→背驰→买卖点
    │   ├── structure_indicators.py   # MACD(10,20,5)，对齐远程定稿
    │   ├── structure_signals.py      # v1 信号（实盘在用）
    │   ├── structure_signals_v2.py   # v2 候选（仅对比，未接实盘）
    │   ├── signal_gates.py           # 15m/1h/日线/周线 四级交易闸门
    │   ├── ma_trend.py               # 缠论·均线版（新版，复用远程 chan_ma_trend_v2）
    │   ├── run_ma_scan.py            # 均线版扫描（日线/周线）
    │   └── compare_v2_batch2.py      # 第二批旧/新对比出图
    ├── Strategy/
    │   └── chanlun_fractal_Strategy.py  # 生产监控：期货+股指期货+ETF，四级周期
    ├── data_fetcher.py        # 数据采集（akshare / tushare）
    ├── run_unified_monitor.py # 统一监控入口
    └── reports/batch2_compare/ # 2026-11-11 新旧信号对比图 + CSV
```

## 合并口径（已对齐）

- MACD：`(10, 20, 5)`，可用 `CHAN_MACD_FAST/SLOW/SIGNAL` 覆盖
- 结构：K 线包含 → 分型 → 笔 → 中枢 → 背驰 → B1/S1/B2/S2/B3/S3
- 级别闸门：
  - 15 分钟：仅结构参考，不发信号
  - 1 小时：只认三买/三卖；一买须 MA10>MA20（一卖对称）
  - 日线：一/二/三买 + 卖点，年龄/距买点距离/MA20 方向/结构未破
  - 周线：只认一买/二买（卖点对称），闸门放宽
- 周线由日线 `resample("W-FRI")` 合成，不单独取数
- 均线版 = 远程原文口径（`bin/chan_ma_trend_v2.py`）：笔 = MA5/MA10 双穿 MA20、
  线段 = MA20/40 双穿 MA80、大级别 = 价格 vs MA160、MACD 10/20/5；
  本地通过 `chanlun_backtest/ma_trend.py` 直接加载，不复制公式

## 运行

```bash
# 远程库按级别跑（分型版/均线版/SMC/VWAP）
cd techAnly-summary
python strategies/run_level.py --symbol 510300 --level day

# 本地结构版每日扫描
cd chanlun_multiscale
python -m chanlun_backtest.run_daily_scan

# 本地均线版（新版）扫描
python -m chanlun_backtest.run_ma_scan --level day
python -m chanlun_backtest.run_ma_scan --level week --symbol sh510300

# 第二批新旧信号对比（只读，不写实盘状态）
python -m chanlun_backtest.compare_v2_batch2
```

## 注意

- `structure_signals_v2.py` 为**候选实现**，尚未接入实盘；实盘仍走 `structure_signals.detect_structure_signals`。
- 运行需 `pandas / numpy / akshare / matplotlib / scipy / TA-Lib`（远程库另有 requirements.txt）。
- 账户、飞书 chat_id、绝对路径等敏感信息以环境变量注入，不写死在代码里。
