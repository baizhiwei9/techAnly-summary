# techAnly-summary · 技术分析汇总（买点 + 卖点 + 四套口径）

> 一个标的，四套技术口径一次跑完：**缠论分型版 + 缠论均线版 + SMC + VWAP 六轨**，
> 再叠一层**买点/卖点判定**：指数、行业、概念、个股全都支持。
>
> **全流程纯 Python 规则计算，不依赖任何大模型 / LLM API，不发一次 LLM 请求，0 token。**
> 所有结论都来自 K 线数据的确定性计算（包含处理、分型、笔、中枢、背驰、均线、VWAP），
> 同样的输入永远得到同样的输出，可复现、可回测、可离线跑。

把四套独立的技术分析口径**统一到一张报告 + 两张图里**，避免「看缠论说买、看 VWAP 说卖」的自相矛盾。

核心入口四个：

| 脚本 | 作用 |
|---|---|
| `bin/tech_summary.py` | 单标的四层完整分析（缠论分型版 + 缠论均线版 + SMC + VWAP 六轨） |
| `bin/tech_buypoint.py` | 买点分析（行业/指数/概念先过闸，再下钻成分股；个股直连四层分析） |
| `bin/tech_intraday_buypoint.py` | **盘中行业买点 Top10 + 个股下钻**（东财实时板块，个股须站在 VWAP −1σ 以上） |
| `bin/chan_ma_chart.py` | **缠论·均线版单标的出图**（只画均线版一层，图大结构清楚） |

### 按级别跑 + 策略索引（2026-11-11 新增）

`strategies/` 目录把五套口径按策略归档，并提供统一的级别入口：

```bash
python3 strategies/run_level.py --target 中证500 --level m15   # 15分钟
python3 strategies/run_level.py --target 600519 --level h1     # 小时线
python3 strategies/run_level.py --target 600519 --level day    # 日线
python3 strategies/run_level.py --target 600519 --level week   # 周线（日线合成）
```

| 策略 | 15分钟 | 小时线 | 日线 | 周线 | 入口 |
|---|:---:|:---:|:---:|:---:|---|
| 缠论·分型版 | ✅ | ✅ | ✅ | ✅ | `bin/chan_smc_index.py` / `strategies/run_level.py` |
| 缠论·均线版 | ✅ | ✅ | ✅ | ✅ | `bin/chan_ma_trend_v2.py` / `strategies/run_level.py` |
| SMC | ✅ | ✅ | ✅ | ✅ | `smc/smc_core.py` / `strategies/run_level.py` |
| VWAP 六轨 | ✅ | ✅ | ✅ | ✅ | `smc/smc_core.py` / `strategies/run_level.py` |
| 小波·多尺度 | ❌ | ⚠️停用 | ✅ | ✅（过滤） | `strategies/wavelet/` |
| 威科夫吸筹 | 盘中（过滤层周/日） | — | ✅ | ✅（过滤） | `bin/wyckoff_buypoint_v2.py` |

> 完整说明与「为什么小波不套 15 分钟」见 **[strategies/README.md](strategies/README.md)**。

### 本地缠论多尺度交易工程并入（2026-11-11）

本地结构版工程已并入 **[chanlun_multiscale/](chanlun_multiscale/README.md)**，与 `bin/` 口径对齐：

- MACD `(10,20,5)`；包含 → 分型 → 笔 → 中枢 → 背驰 → B1/S1/B2/S2/B3/S3
- 15m/1h/日线/周线四级交易闸门（15m 只做结构参考，1h 只认三买，周线只认一/二买）
- `structure_signals_v2.py` 为第二批候选（三买中枢递进校验 + 二买锚定一买），**仅对比、未接实盘**
- 实盘仍走 `chanlun_multiscale/Strategy/chanlun_fractal_Strategy.py`（v1 信号 + 四级闸门）

---

## 目录

| 章节 |
|---|
| 一、核心：买点 / 卖点怎么给 |
| 二、四套技术口径 |
| 三、安装 |
| 四、示例产物 |
| 五、用法 |
| 六、输出 |
| 七、综合打分口径 |
| 八、目录结构 |
| 九、硬规矩（踩过的坑，改代码前必读） |
| 十、校验 |
| 十一、已知限制 |
| 十二、说明 / 备注 |
| 十三、发布 / 更新到 GitHub（每次改完必做） |

---

## 一、核心：买点 / 卖点怎么给

### 1. 买点（三档，按优先级）

| 优先级 | 买点 | 含义 | 认定条件（全部满足） |
|---|---|---|---|
| ★★★ | **三买** | 向上离开中枢后回踩不破 ZG，结构已确认 | ① 向上离开某中枢（遍历**所有**中枢）② 回踩低点 > 该中枢 ZG ③ 回踩之后再未跌破该低点 ④ 买点年龄 ≤ 90 根 ⑤ 现价距买点 ≤ 10% ⑥ 现价仍 > 该中枢 ZG |
| ★★ | **一买** | 底背驰出现的转折低点 | ① 底背驰（笔的幅度/MACD 力度衰减）② **MA20 必须向上（同向确认）** ③ 年龄 ≤ 40 根 ④ 现价 +10% / −3% 内 |
| ★ | **二买** | 底背驰后回踩不破一买低点 | ① 一买之后的下跌笔低点高于一买低点 ② 年龄 ≤ 60 根 ③ 现价未跌破二买低点 3% ④ 现价距买点 ≤ 10%（MA20 向上只加分，不作硬门槛） |

> **一买必须叠加 MA20 向上同向确认**（`MA20(t) > MA20(t-5)`）。只背驰、均线还在往下 → 不认。
> 三买本身就是「结构已确认」，不强制 MA20；二买是回踩确认，MA20 向上作为质量加分。

> ### ⚠️ 二买胜率最低，务必看清
> **二买是三档买点里胜率最低的一档**，明显低于三买和一买。原因很直白：
>
> - 二买本质是「一买之后的**回踩**」，此刻趋势**尚未确认转多**，回踩既可能是缩量洗盘，也可能是直接破位下跌的中继；
> - 一买有「底背驰 + MA20 向上」双确认，三买有「离开中枢后回踩不破 ZG」的结构确认，**二买只有「不破一买低点」这一个条件**，容错最低；
> - 实测里二买出现后直接跌破一买低点、把信号打成假的概率明显更高。
>
> **所以：二买只作观察 / 次优信号，不要单独拿它做决定。** 必须叠加 MA20 向上、量能配合、板块强度、大盘环境
> 等其它确认；排序永远是 **三买 > 一买 > 二买**，二买排在最后。

### 2. 卖点（对称三档）

| 优先级 | 卖点 | 含义 | 认定条件 |
|---|---|---|---|
| ★★★ | **三卖** | 向下离开中枢后反抽不破 ZD | ① 向下离开某中枢（遍历**所有**中枢）② 反抽高点 < 该中枢 ZD ③ 反抽之后收盘从未涨破该高点 ④ 年龄 ≤ 90 根 ⑤ 现价距卖点 ≤ 6%（涨破 3% 以上 = 卖点失效） |
| ★★ | **一卖** | 顶背驰出现的高点 | ① 顶背驰 ② **MA20 必须向下（同向确认）** ③ 年龄 ≤ 90 根 ④ 现价未涨破卖点 ⑤ 现价距卖点 ≤ 6% |
| ★ | **二卖** | 顶背驰后反抽不过一卖高点 | ① 反抽高点低于一卖高点 ② 年龄 ≤ 90 根 ③ 现价未涨破卖点 ④ 现价距卖点 ≤ 6%（MA20 向下只加分，不作硬门槛） |

> **一卖必须叠加 MA20 向下同向确认**（`MA20(t) < MA20(t-5)`），与「一买必须 MA20 向上」严格对称。
> 只顶背驰、均线还在往上 → 一卖不认（那通常是上涨中继的回调，不是顶）。
> 被否时要写清原因，例如「MA20 未向下（斜率 +0.32%）→ 一卖不认」，不能笼统写「卖点被否」。

判定顺序固定：**先止损（VWAP −1σ/−2σ）→ 再看卖点结构（三卖 > 一卖 > 二卖）→ 再看买点**。

### 3. 顺序：先看行业，再看个股

```
第一步：判行业/板块指数自己有没有买点（行业指数跑同一套三买/一买/二买判定）
          ↓ 有买点
第二步：才下钻扫成分股，输出可买个股（三买优先，一买其次，二买最后）
          ↓ 没买点
       直接停：只输出行业结构 + 卖点提示，个股部分跳过
```

这样避免「行业在下跌趋势里挑出一堆伪买点」。实测 31 个申万一级行业里通常只有 4 个能过闸。

### 4. 四类标的，一个入口

| 类型 | 输入示例 | 行为 |
|---|---|---|
| 指数 | `中证500` / `上证50` / `000905.SH` | 指数自身结构 + 成分股买点扫描 |
| 行业 | `传媒` / `游戏` / `银行` | 申万行业指数结构 + 成分股扫描 |
| 概念 | `文化传媒概念` / `短剧游戏` | 同花顺概念指数结构 + 成分股扫描 |
| 个股 | `600519` / `300750` | 单只四层完整分析 |

---

## 二、四套技术口径

| 层 | 口径 | 关键判定 |
|---|---|---|
| ① 缠论·分型版 | 包含处理 → 顶底分型 → 笔 → 中枢 ZG/ZD → 背驰 | 日线认一/二/三买 + 二/三卖；**小时线只认三买，及一买 + MA10 金叉** |
| ② 缠论·均线版 | 笔 = MA5/MA10 双穿 MA20；线段 = MA20/MA40 双穿 MA80；大级别 = 价格 vs MA160 | 只问「当前级别能不能支撑交易」，不递归、不用 MA320 |
| ③ SMC | 摆动点 → 结构突破 → FVG / OB → 方向确认 | 日线 k=3，小时线 k=1；**只作机会/风险提示，不参与入场** |
| ④ VWAP 六轨 | 量加权 VWAP ± 1σ/2σ/3σ（窗口 60 根）+ MA40 方向 | +3σ 全平 / +2σ 减半 / −1σ 全止损；右侧标正态分位 |
| ⑤ 买点/卖点分析 | 买点：①三买 > ②一买（须 MA20 向上）> ③二买（**胜率最低·仅作参考**）；卖点：①三卖 > ②一卖（须 MA20 向下）> ③二卖 | 行业先过闸才下钻个股（详见第一章） |

---

## 三、安装

```bash
git clone https://github.com/baizhiwei9/techAnly-summary.git
cd techAnly-summary
pip install -r requirements.txt
```

系统字体（K 线图中文字形，必需其一）：

```bash
sudo apt install fonts-wqy-microhei          # 或 fonts-noto-cjk
```

Tushare token（日线数据源之一，可用环境变量或配置文件）：

```bash
export TUSHARE_PROXY_TOKEN=你的代理token      # 走 ttx.dailyfetch.top 代理
# 或
mkdir -p ~/.config/tushare && echo 你的token > ~/.config/tushare/proxy_token
```

首次运行初始化缓存（**必做一次**，生成申万行业分类缓存）：

```bash
python3 tools/bootstrap_cache.py
# → .cache/sw_member_all.json      5900+ 只个股的 L1/L2/L3 归属
# → .cache/sw_index_map.json       129 个申万行业指数代码
# → .cache/tech_stock_names.json   5900+ 个股票中文名（不生成报告里只剩 6 位代码）
```

飞书推送（可选，不用就不配）：

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export TECH_FEISHU_CHAT=oc_xxxxxxxx      # 目标群 chat_id
```

> 没有 LLM key 也不用配 —— 本项目**不调用任何大模型**，只吃行情接口。

---

## 四、示例产物

`examples/` 里是真实跑出来的样板（2026-11-09 收盘数据）：

| 文件 | 内容 |
|---|---|
| `sample_report_中证500.txt` | 中证500 的六节完整文字报告 |
| `sample_day_600519.png` | 贵州茅台 日线四面板图（K线+缠论笔/中枢/买卖点+SMC+VWAP六轨+均线版+量+MACD） |
| `sample_h1_600519.png` | 贵州茅台 小时线 60m 同款图 |

---

## 五、用法

```bash
# ① 五层完整分析（宽基中文名 / ts_code / 6 位股票代码都行）
python3 bin/tech_summary.py --target 中证500
python3 bin/tech_summary.py --target 600519            # 贵州茅台
python3 bin/tech_summary.py --target 000016.SH         # 上证50

# ② 生成 + 推飞书卡片（文字报告 + 两张图，同一条消息）
python3 bin/tech_summary.py --target 中证500 --push

# ③ 只出报告不出图 / 改窗口 / 存档
python3 bin/tech_summary.py --target 中证500 --no-chart
python3 bin/tech_summary.py --target 中证500 --n-day 120 --n-h1 150
python3 bin/tech_summary.py --target 中证500 --out memory/tech_reports/x.txt

# ④ 买点分析：行业先过闸 → 才下钻成分股
python3 bin/tech_buypoint.py --target 传媒
python3 bin/tech_buypoint.py --target 文化传媒概念 --push
python3 bin/tech_buypoint.py --target 中证500           # 扫 500 只成分
python3 bin/tech_buypoint.py --target 600519            # 个股→四层分析
python3 bin/tech_buypoint.py --target 传媒 --no-scan    # 只看板块不扫成分
python3 bin/tech_buypoint.py --target 医药生物 --force-scan  # 行业无买点也强制扫
python3 bin/tech_buypoint.py --list-concepts 游戏        # 列同名概念

# ⑤ 盘中行业买点 Top10（东财实时板块，含当日盘中那根K线）
python3 bin/tech_intraday_buypoint.py                    # 行业 + 每行业3只个股
python3 bin/tech_intraday_buypoint.py --top 10 --push    # 推飞书卡片
python3 bin/tech_intraday_buypoint.py --push --qq        # 同时推 QQ（需 IBP_QQ_TARGET）
python3 bin/tech_intraday_buypoint.py --with-concept     # 行业 + 概念一起排
python3 bin/tech_intraday_buypoint.py --no-stock         # 只看行业，不扫个股
python3 bin/tech_intraday_buypoint.py --stock-per 2 --stock-cap 30
python3 bin/tech_intraday_buypoint.py --limit 20 --dry    # 调试：只扫前 20 个

# ⑥ 缠论·均线版**单标的出图**（只画均线版一层，图大、结构清楚）
python3 bin/chan_ma_chart.py --target 中证500              # 指数
python3 bin/chan_ma_chart.py --target 600519 --n 300       # 个股，看最近 300 根
python3 bin/chan_ma_chart.py --target 沪深300 --push       # 出图 + 推 QQ/飞书
```

> 均线版单图画的是：K线 + MA10/MA20/MA160 + **笔**（橙，MA5/10 双穿 MA20）
> + **线段**（深蓝粗，MA20/40 双穿 MA80）+ **中枢**（黄框 ZD~ZG）
> + **走势类型底色**（红=上涨/绿=下跌/灰=盘整）+ 四角标结论（可交易性 / 大级别 MA160 /
> 当前走势+笔线段中枢数 / 当前位置+关键位）。未走完的笔和线段用虚线连到最新收盘。

### 可调参数（环境变量）

| 变量 | 默认 | 作用 |
|---|---|---|
| `SCAN_TB_MAX_AGE` | 90 | 三买最大年龄（根） |
| `SCAN_TB_NEAR` | 10 | 三买：现价高于回踩低点%以内 |
| `SCAN_YB_MAX_AGE` | 40 | 一买最大年龄（根） |
| `SCAN_EB_MAX_AGE` | 60 | 二买最大年龄（根） |
| `SCAN_EB_NEAR` | 10 | 二买：现价距买点%以内 |
| `SCAN_SELL_MAX_AGE` | 90 | 卖点最大年龄（根） |
| `SCAN_SELL_NEAR` | 6 | 卖点：现价距卖点%以内 |
| `TECH_MA20_DN_BARS` | 5 | 一卖 MA20 向下判定的回看根数 |
| `TECH_MA20_UP_BARS` | 5 | 一买 MA20 向上判定的回看根数 |
| `CHAN_NEAR_PCT` | 3 | 「就在点位上」的容差% |

---

### 盘中 Top10 + 个股下钻（两步走：先选行业，再从选出的行业里选个股）

#### 第一步：行业排序

```
① 周线买点（一二三买都算；三买1 > 一买2 > 二买3）
  ↓ ② 日线三买（有 > 无；日线一买/二买 不纳入）
  ↓ ③ 买点共振层数（分型/均线/SMC/VWAP 越多越前）
  ↓ ④ 同层内 ① 分型版 > ② 均线版 > ③ SMC > ④ VWAP
```

| 层 | 判定 |
|---|---|
| ① 分型版 | **周线**：一买/二买（按周放宽闸门）+ 周线三买；**日线**：只认三买 |
| ② 均线版 | `M.signals()` 出现「买点」即算（新鲜穿越 或 长期排列延续） |
| ③ SMC | 只认 `dir=bull & state=buy`（已回踩 + 方向确认）；`extended` / `wait_*` 不算 |
| ④ VWAP | MA40 向上 **且** 没冲进 +1σ 以上（不追高） |

- 数据用**东财行业板块**（100 个，实时日K）：申万 akshare 只有 T-1，盘中用不了
- 同名不同级板块（IT服务 / IT服务Ⅱ / IT服务Ⅲ）自动去重
- 报告会标「⚠️ 分型/均线分歧」：分型看到买点、均线版还在下笔 → 趋势未确认，只宜轻仓试错
- 上榜若多为「③二买」，报告会提示二买胜率最低，仅作观察

#### 第二步：个股下钻（**VWAP −1σ 以上是硬门槛**）

> 现行口径（大佬 2026-11-10 二次修正）：**允许跌破 VWAP 中线，但不许跌破 −1σ**。
> −1σ 是 VWAP 体系的全止损线，破了说明量价结构失效，不进候选。
> 环境变量 `IBP_STOCK_VWAP_SIGMA`（默认 −1.0）可调。

```
行业选出（前 N 个）
  ↓ 取东财板块成分股，按成交额降序（流动性优先），每板块最多扫 --stock-cap(40) 只
  ↓ ✂️ 现价 < VWAP −1σ（z < −1.0）→ 直接淘汰（硬门槛，不看买点）
  ↓ 站上中线的再看买点：① 分型版（三买>一买>二买）> ② 均线版 > 仅VWAP（无缠论买点，排最后）
  ↓ 每行业输出前 --stock-per(默认 3) 只
```

个股排序键 `(tier, pri, -n_res, -pct)`：tier 1=分型版 / 2=均线版 / 9=仅VWAP。
每只输出含 VWAP 偏离 σ + 分区提醒（≥+3σ 全平不追 / ≥+2σ 减半追高慎 / ≥+1σ 警戒 / ≤−0.8σ 贴近止损线）、站上 MA20/MA160、多头排列、`⚠️均线版卖点` 风险标记。

开关：`--stock-per N`（每行业个股数，默认 3）｜`--stock-cap N`（每板块最多扫几只，默认 40）｜`--no-stock`（只看行业）。

支持的宽基中文名：`沪深300 / 中证500 / 中证1000 / 创业板指 / 科创50`，
另有 `EXTRA_IDX` 里的 `上证指数 / 上证50 / 深证成指 / 中证100 / 中证200 / 中证800 / 中证红利 / 中证全指 / 北证50`。
加新指数：往 `tech_summary.py` 的 `EXTRA_IDX` 加一行 `"中文名": "ts_code"`。

> ### ⚠️ 二买胜率最低（必读）
> **二买在实战里胜率最低**（远低于三买和一买）：它本质是「一买之后的回踩」，回踩时趋势还没确认转多，
> 假突破 / 直接破位的情况很多。所以二买只作为**观察 / 次优信号**，必须配合 MA20 向上、成交量、
> 板块强度等其他确认，**不要单独用二买下决定**。
> 排序永远是 **三买 > 一买 > 二买**（二买最后），报告里该段标题带「（胜率最低·仅作参考）」。
>
> 卖点对称：**一卖必须 MA20 向下**（同向确认），只顶背驰而均线还向上 → 不认；二卖 MA20 向下仅加分；三卖不强制。

---

### 买卖点优先级规则总表（权威口径）

```
买点：① 三买  >  ② 一买（必须 MA20 向上）  >  ③ 二买（胜率最低，仅作参考）
卖点：① 三卖  >  ② 一卖（必须 MA20 向下）  >  ③ 二卖（仅加分确认）
```

| 类型 | pri | 核心条件 | 硬门槛 |
|---|---|---|---|
| 三买 | 1 | 向上离开中枢（**遍历所有中枢**）→ 回踩低点 > ZG → 之后未跌破 → 年龄 ≤90 根 → 现价距买点 ≤10% → 现价仍 > ZG | 结构+年龄+距离；不要求 MA20 |
| 一买 | 2 | 底背驰 + 年龄 ≤40 根 + 现价 +10%/−3% 内 | **MA20 向上（一票否决）** |
| 二买 | 3 | 回踩不破一买低点 + 年龄 ≤60 根 + 未跌破 3% + 现价 ≤10% | MA20 向上只加分 |
| 三卖 | 1 | 向下离开中枢 → 反抽不回 ZD → 反抽高点未涨破 → 年龄 ≤90 根 → 距卖点 ≤6% | 结构+年龄+距离；不强制 MA20 |
| 一卖 | 2 | 顶背驰 + 年龄 ≤90 根 + 未涨破 3% + 距卖点 ≤6% | **MA20 向下（一票否决）** |
| 二卖 | 3 | 一卖后次高点，同上闸门 | MA20 向下只加分 |

**两条总原则：**

1. **优先级 = 结构确认度**，不是「谁先出现」。三买有「离开中枢+回踩不破 ZG」双结构确认，一买有「底背驰+MA20 向上」双确认，二买只有「不破一买低点」一个条件 → 容错最低。
2. **优先级只是排序，不能替代闸门**。任何买点都必须过「年龄 / 距离 / 结构未破 / 方向」四道闸门，不过闸门一律 `accept=False`，只能进「见过但被否」清单，**不许标 🌙、不许进可买清单**。

**级别（三种场景别混用）**

| 场景 | 周线 | 日线 | 小时线 |
|---|---|---|---|
| 单标的四层 / 买点扫描 | — | **主判定**：三买/一买/二买 + 卖点 | 次级：只认三买（一买须 MA10>MA20） |
| **⭐ 盘中行业→个股** | **一买/二买/三买 都算**（方向） | **只认三买**（入场时机） | 不跑 |

盘中口径（大佬 2026-11-10）：周线有买点 + 日线三买 = **双级别共振**，排序最前。
周线闸门按周放宽（一买≤26周 且 MA20 向上 / 二买≤40周 / 三买≤60周、距回踩低点≤20%）。


**执行顺序**：先判行业/板块自身有无买点 → 有才下钻成分股。

**排序键**：`(not accept, pri, age)` —— 能用的在前 → 三买1/一买2/二买3 → 同类型取新鲜的。

> 完整细则（去重规则、评级优先级、环境变量总表、被否文案）见 `skills/tech-summary/SKILL.md` 第二节。
> 三个入口（`tech_summary.py` / `tech_buypoint.py` / `tech_intraday_buypoint.py`）**必须同口径**，闸门逻辑只能有一份实现。

---

## 六、输出

- **文字报告**（stdout / `--out`）：六节结构
  1. 结论（先看这里）　2. 缠论·分型版　3. 缠论·均线版　4. SMC 结构　5. VWAP 六轨　6. 综合判断（±1 打分制）
- **两张图**：`.cache/charts/tech_<标的>_day_<YYYYMMDD>.png` / `..._h1_....png`
- **飞书卡片**（`--push`）：文字分节 + 两张图内嵌同一条消息

图 = 四面板：价格（K线 + 缠论笔/中枢/买卖点 + SMC FVG/OB + VWAP 六轨）/ 均线版结构 / 量 / MACD。

---

## 七、综合打分口径

每条 ±1，看总分：

| 条件 | 分 |
|---|---|
| 日线中枢上方 / 下方 | +1 / −1 |
| 日线底背驰 / 顶背驰 | +1 / −1 |
| 小时线已向上离开中枢 | +1 |
| 均线版本级有买点 / 在卖点 | +1 / −1 |
| 价格站上 / 跌破 MA160 | +1 / −1 |
| 日线 VWAP ≥ +2σ / ≤ −1σ | +1 / −1 |

| 总分 | 倾向 |
|---|---|
| ≥ +2 | 偏多，等回踩结构支撑低吸 |
| +1 | 偏多但结构未确认，轻仓/等确认 |
| 0 | 中性，等方向选择 |
| −1 | 偏空，反弹减持、不追多 |
| ≤ −2 | 空头结构，空仓等底部结构（背驰/三买） |

---

## 八、目录结构

```
bin/
  tech_summary.py        五层分析主脚本（报告 + 双图 + 推送）
  tech_buypoint.py       买点分析（指数/行业/概念/个股）
  buypoint_chart.py      买点分析三联图
  chan_smc_index.py      缠论分型版核心 + 日线取数 + 标的表
  chan_ma_trend_v2.py    缠论均线版核心（笔/线段/走势类型/verdict）
  chan_chart.py          缠论几何（保证图与判定同口径）
  datahub.py             日线三级灾备（akshare → tushare → 网页）
  minute_hub.py          60m 分钟线（index / stock 两种 kind）
  weekly_zs.py           申万行业指数日线
  chan_ma_chart.py       **缠论均线版单标的出图**（只画均线版一层，图大看得清）
  push_image.py          统一图片推送（QQ + 飞书，程序生图用）
  feishu_card.py         飞书图文合成卡片
  tushare_init.py        Tushare 统一初始化（代理 token + 硬超时 + 直连兜底）
tools/
  bootstrap_cache.py     首次运行初始化缓存（申万行业分类 / 行业指数映射）
  gh_commit.py           发布：Git Data API，多文件合成**单个 commit**
  gh_publish.py          发布：单文件 PUT（兜底）+ 私密串拦截
  publish_techanly.py    发布：本项目专用封装（自动剥私 + 写 CHANGELOG）
smc/
  smc_core.py            FVG / OB / 摆动点 / VWAP 六轨
  smc_config.py          级别映射（精简版）
skills/tech-summary/
  SKILL.md               技能说明（口径细则、踩坑清单、校验方法）
skills/chan-ma-trend/
  SKILL.md               缠论·均线版技能（笔/线段/走势类型/绘图规范）
examples/
  sample_report_中证500.txt / sample_day_600519.png / sample_h1_600519.png   真实样板产物
```

---

## 九、硬规矩（踩过的坑，改代码前必读）

1. **禁 IPv6**：脚本开头必须挂 `socket.getaddrinfo` 过滤 `AF_INET`，否则 tushare / 分钟线会**静默卡死**到超时（不是报错，极难查）。
2. **K 线上色 = `close vs 上一根 close`**，标题必须写 `今日 ±x.xx%`。
   用 `close vs open` 会把"低开高走但收跌"涂成红阳线，与实际涨跌幅反向。
3. **小时线不能裸认一买/二买**：一买要叠加 MA10 > MA20 金叉才 ✓，二买一律 ✗，只认三买。
   （日线口径另算：二买**已恢复**，见 SKILL.md 第十一节，走 `SCAN_EB_*` 闸门、MA20 向上只加分。）
4. **SMC 的 FVG/OB 只作提示**，回测已验证无可用 α（±0.3% 内），绝不参与入场判定。
5. **VWAP 三档判定顺序**：先 `−2σ/−1σ` 止损 → 再 `+3σ` 全平 → 再 `+2σ` 减半 → 再 `+1σ` 警戒。
   不能 if/elif 并列 `+2σ/+3σ`。
6. **VWAP 成交量含 0 值**要 `replace(0, nan)`；整窗量为 0 时退化为等权，避免 NaN。
7. **「不画 MA5」≠「不用 MA5」**（大佬 2026-11-10 澄清）：
   图太乱所以绘图只画 MA10/MA20（+MA160），但**计算必须保留 MA5** ——
   笔的定义就是 `MA5/MA10 双穿 MA20`。实测拿 MA10 顶替 MA5 后，双穿状态改变
   中证500 126 根 / 风语筑 151 根 / 沪深300 143 根 / 茅台 80 根 → 笔会大面积错位。
   代码上要分清：`MAS=[5,10,20,40,80,160]`、`A0=(5,10,20)` 不动，只有 `MA_DRAW=(10,20)` 控绘图。

8. **图内文字不能用 emoji**（Noto CJK 无彩色 emoji 字形 → 豆腐块）。
   🌙/⭐ 不是 emoji 而是 `TextPath` 从 `FreeSerif.ttf` 取字形转 matplotlib Path marker；
   月牙内部是空的，**必须靠 `edgecolor` 描边填充**。
9. **`minute_hub` 磁盘缓存 key 不含 `n`**：先用 n=60 取过，n=400 会直接返回 60 根的旧缓存 → 误判"数据不足"。
   长度不够时必须 `cache=False` 强制刷新。
10. **`datahub.is_index()` 永远返回 True**（600519.SH 也是），判指数要用 `is_index_code()` 按前缀规则。
11. **数据缺口会造假信号**：本地缓存停在几天前 + 直接拼当日 K 线，会让笔/线段在缺口处断裂，
    凭空造出"新的回踩低点"。拼接前必须算日历差，`gap_days > 4` 就改用全量缓存或现拉。
12. **缠论结构对数据长度极度敏感**：同一只股票 441 根 / 1200 根算出的中枢和买点**完全不同**。
    扫描器和出图必须吃同一份数据。
13. **走势类型的三个坑，全踩过**：
    - 不能用"相邻线段方向"判（方向必然交替 → 100% 变盘整）；
    - 也不能用 `M.trend_mode()` **逐根跑历史**（要求 MA5/10/20 近 20 根完全不接触，
      日线 1200 根里只成立 1~3 根 → 合并后整图一个灰块）。
      `trend_mode()` 只适合回答"**当下这一根**是什么状态"；
    - ✅ 正确：走势类型是**线段级别**概念 → 比较**相邻两摆**的高低点：
      后高>前高 且 后低>前低 = 上涨；都降低 = 下跌；其余 = 盘整。
      ⚠️ 配对必须"低点→高点"（端点 `dir` 是"从该点出发的方向"，按相邻两点直接配会错位一格）。
14. **笔/线段端点必须取"区间内真实极值"，不是双穿状态翻转那根的收盘**
    （翻转点是"均线穿完那根"，收盘 ≠ 本笔高低点；不修则整图峰谷缩水、位置偏移，
    与分型版图对不上）。实测：603466 上涨笔翻转收 14.23，该笔区间实际最高 14.60。
15. **最后一笔/段必须补"活端点"**（`live=True`），否则图右侧整段空白、走势类型停住。
    ⚠️ 活端点起点用**极值那根**、不能用翻转那根（否则会把上一笔高点当本笔新高），
    绘图用**虚线**从最后极值连到最新收盘。
16. **`trend_mode()` / `wei_state()` 的 `look=20` 是区间判定**，不是"当前方向"；
    拿它做逐根历史分段一定得出恒定结果。
17. **`axhline` 的 xmin/xmax 是轴坐标比例（0~1），不是数据坐标** → 画区间横线用 `hlines(ys, x0, x1)`。
18. **`akshare` 没有 timeout，会静默挂死** → 必须用 ThreadPool 包一层硬超时。

---

## 十、校验

```bash
# 六节齐全（应输出 6）
python3 bin/tech_summary.py --target 中证500 --no-chart | grep -c "═══"

# 股票也通
python3 bin/tech_summary.py --target 600519 --no-chart | head -3

# 最后一根 K 线颜色与涨跌幅同号
python3 - <<'PY'
from PIL import Image
import numpy as np
a = np.array(Image.open(".cache/charts/tech_中证500_day_$(date +%Y%m%d).png").convert("RGB")).astype(int)
sub = a[200:1000, :1740, :]                       # 排除右侧分位标注
red = (np.abs(sub - np.array([214, 39, 40])).sum(axis=2) < 50)
grn = (np.abs(sub - np.array([44, 160, 44])).sum(axis=2) < 50)
print("最右红列", np.where(red.any(axis=0))[0].max(), "最右绿列", np.where(grn.any(axis=0))[0].max())
PY

# 图与"缠论均线版"技能口径一致
python3 - <<'PY'
import sys; sys.path.insert(0,"bin")
import chan_ma_trend_v2 as M, tech_summary as T
tg=T.resolve("中证500"); d=T.load_day(tg); dd=M.add_ind(d)
st=T.ma_structure(d)
# ① 笔的翻转点数量必须与技能口径一致（只是端点取极值，不该多出/少掉一笔）
assert len([p for p in st["bi"] if not p.get("live")]) == len(M.build_bi(dd))
# ② 端点必须是真极值：上涨笔端点 = 区间最高、下跌笔端点 = 区间最低
hi, lo = d["high"].values.astype(float), d["low"].values.astype(float)
pts=[p for p in st["bi"] if not p.get("live")]
for k,p in enumerate(pts):
    # ⚠️ 区间要用**翻转索引 flip**，不能用 p["i"]（i 已经是极值那根，区间会错位）
    a = p["flip"]
    b = pts[k+1]["flip"] if k+1 < len(pts) else len(d)-1
    want = lo[a:b+1].min() if p["dir"]=="up" else hi[a:b+1].max()
    assert abs(p["px"]-want) < 1e-6, (p, want)
# ③ 末段必须有活端点（否则图右边整段空白）
assert st["bi"][-1].get("live"), "缺末笔活端点"
assert st["seg"][-1].get("live"), "缺末段活端点"
# ④ 走势类型必须真的有分段（不能退化成一整块）
assert len(st["mode"]) >= 2, st["mode"]
print("✅ 图与技能口径一致：笔%d 线段%d 走势类型%d段" % (
      len(st["bi"]), len(st["seg"]), len(st["mode"])))
PY

# 推送成功标志
python3 bin/tech_summary.py --target 中证500 --push 2>&1 | grep "\[card\]"
```

---

## 十一、已知限制

- 单标的跑（`--target` 一次一个），批量看多个标的请循环调用
- 均线版子图窗口默认 400 根（`TECH_MA_PANEL_BARS`）；260 根里常只有 1–2 个中枢，看不出序列
- 股票日线走 `datahub` 三级（akshare 首次约 26s，含 tqdm 进度条），有 6 小时 CSV 缓存
- 港股（5 位代码）tushare 多数账号无权限，会落到网页层，可能拿不到
- 数据源为 akshare / tushare / 新浪 / 腾讯 / 东财公开接口，**仅供技术结构研究**

---

## 十二、说明 / 备注

- **本仓库是公开库**（`baizhiwei9/techAnly-summary`），作者个人量化工具集的一部分。
- **设计目的**：以后所有技术分析类口径都往这里汇总——新增一层就加一个 `*_layer(df, level)` 函数，
  在 `tech_summary.py` 的报告与绘图里挂一节，保持"一种口径一个函数、互不侵入"。
- **0 token 是硬约束**：所有判定、绘图、推送都是纯 Python 程序，不调用任何 LLM API。
  唯一的"智能"来自规则化的结构判定，结果可复现、可回测。
- 与 `chan-fractal` / `chan-ma-trend` / `smc-structure` 三个技能同口径：
  `bin/` 下的 `chan_smc_index.py` / `chan_ma_trend_v2.py` 就是那两个技能的权威实现，此处直接复用，**不另写一套**。

⚠️ 本工具输出为技术结构解读，**不构成投资建议**。

---

## 十三、发布 / 更新到 GitHub（每次改完必做）

> **唯一权威库：https://github.com/baizhiwei9/techAnly-summary （public）**
> 2026-11-11 大佬定稿：技术分析只保留这一个库，原 `tech-summary` 已合并进来并删除。

```bash
# 1) 先看差异（dry-run 不会写 CHANGELOG、不会产生 commit）
python3 bin/gh_publish_techanly.py --msg "fix: xxx" --dry-run

# 2) 正式发布（--notes 会写进 CHANGELOG.md）
python3 bin/gh_publish_techanly.py \
  --msg "fix(chart): 均线版端点改取区间极值" \
  --notes "### 改了什么\n- ...\n\n### 为什么\n- ...\n\n### 影响文件\n- bin/tech_summary.py"
```

**约定（重要）**

- **每次改动都必须发一次**，且 `--msg` / `--notes` 要说清「改了什么 / 为什么 / 影响哪个文件」——
  `CHANGELOG.md` 会自动置顶一条，commit 与 CHANGELOG 一一对应。
- 所有文件**合成 1 个 commit**（Git Data API），不会出现 10 个文件 10 条同名记录。
- 上传前自动拦截私密信息：tushare token、飞书 chat_id / webhook、GitHub token、
  OpenAI 风格 key、QQ openid、本机工作区绝对路径 —— 命中即**拒绝上传**（不是警告）。
- 为什么不用 `git push`：本机 `github.com:443` 间歇性阻断（TLS 半路断/超时），
  只有 `api.github.com` 可用 → 全流程走 REST。
- ⚠️ 老脚本 `gh_publish_techsummary.py` 已改为**转发**到 `gh_publish_techanly.py`，不会推错库。
