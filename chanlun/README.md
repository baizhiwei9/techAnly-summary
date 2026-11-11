# chanlun/ · 缠论

**一套几何，三个口径**，分目录放，避免混在一起看串。

```
fractal/       缠论·分型版：包含处理 → 顶底分型 → 笔 → 中枢 → 背驰 → 一二三买卖点
   chan_smc_index.py   入口（几何委托 ../multiscale/chanlun_backtest/structure_indicators.py）
   chan_chart.py       出图（几何复用上面那个，不重复实现）
   y1_watch.py         一买预备观察

ma/            缠论·均线版：MA5/MA10 双穿 MA20 → 笔 / 线段 / 走势类型
   chan_ma_trend_v2.py 核心判定（笔/线段/走势类型/verdict）
   chan_ma_chart.py    单标的出图（只画均线版一层）
   mad_layer.py        MAD 均线层（独立模块，后复权口径）

multiscale/    缠论·多尺度回测工程
   chanlun_backtest/structure_indicators.py   ★ 几何唯一实现（包含/分型/笔/逐笔链式中枢）
   Strategy/chanlun_fractal_Strategy.py       实盘策略（v1 信号 + 四级闸门）
   data_fetcher.py / run_*.py / structure_signals*.py   回测、扫描、口径对比
```

## 硬规矩

1. **几何只有一套实现**：`multiscale/chanlun_backtest/structure_indicators.py`。
   分型版的笔/中枢、`chan_chart` 的画图、`tech_buypoint` 的三买三卖扫描**全部复用**它，
   不允许任何地方再维护「最近 3 段重叠」那套旧逻辑。
2. **两个口径不混用**：分型版看「包含/分型/笔/中枢」，均线版看「MA5/10 双穿 MA20」。
   出图时各画各的，报告里必须写清「分型版」还是「均线版」。
3. **MACD 固定 (10, 20, 5)**，且必须用**真实收盘价**（不能用合并K线的 `(H+L)/2`）。
