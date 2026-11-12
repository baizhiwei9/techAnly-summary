# SMC 多资产模拟交易引擎（股票/ETF=日线级别，期货=小时级别）

复刻《SMC 股票现货多时间框架回测引擎设计文档（m30 验证版）》的确定性结构规则，
按需求把标的从单一沪深300ETF 扩展到
**宽基指数 + 贵金属 + 有色 + 农产品期货**。

> ⚠️ **交易级别（2026-10-07 用户修正）**：**股票/ETF 走「日线」级别交易，期货用「小时(h1)」级别**。
> 因此股票/ETF 的母数据是**日线**（长历史），期货的母数据是 **60m**。

## 目录

| 文件 | 作用 |
|---|---|
| `smc_config.py` | 标的池 / 级别映射 / 路径 / 通知参数 / 交易时段 |
| `smc_data.py` | **数据获取引擎**：60m / 30m / 日线 多级回退抓取 + 磁盘缓存 |
| `smc_core.py` | 结构逻辑：pivot / regime / bias / 入场 / 离场状态机 / 回测 |
| `smc_backtest.py` | 变体 A/B/C/D 回测，输出 `stats_h1.json` |
| `smc_db.py` | **本地模拟交易数据库**（SQLite：`smc_sim.db`） |
| `smc_engine.py` | 模拟交易引擎：全标的推演 → 增量落库 |
| `smc_poll.py` | **30 分钟轮询器**（交易时段活跃 → 刷新 → 增量通知） |
| `smc_notify.py` | 增量通知 / 持仓快照（发 Coze 群 + 飞书） |
| `smc_fvg_selftest.py` | **FVG / OB 自测**（可人工核对缺口 + 无 look-ahead 断言） |

## 标的池（15）

- **宽基**：沪深300ETF `sh510300` / 中证500ETF `sh510500` / 中证1000ETF `sh512100` / 创业板ETF `sz159915` / 科创50ETF `sh588000`
- **贵金属**：沪金 `AU0` / 沪银 `AG0`
- **有色**：沪铜 `CU0` / 沪铝 `AL0` / 沪锌 `ZN0`
- **农产品**：豆粕 `M0` / 玉米 `C0` / 豆油 `Y0` / 白糖 `SR0` / 棉花 `CF0`

## 级别映射（文档第 4 节）

| 类别 | 交易级别 | 母数据 | HTF(bias) | MTF(离场) | SUB(入场) |
|---|---|---|---|---|---|
| **股票/ETF** | **day（日线）** | 1d | month | week | day |
| **期货** | **h1（小时）** | 60m | day | h2 | m30 |

## 规则要点（确定性，可复现）

- 方向：**仅做多**；无价格止损，由结构状态机离场
- 成本：单边 8bp（买卖各扣一次）；全仓单仓
- 摆动点：`k=3`(HTF) / `1`(MTF) / `1`(SUB)，窗口内唯一极值
- regime：上升链/下降链 + 收盘价突破最近摆动高/低点
- 入场（变体 A）：SUB 收盘突破「未消费」摆动高点（做多 CHoCH）→ **下一根 LTF 开盘**全仓
  - B：追加 收盘>SMA40；C：追加 SMA20>SMA40>SMA80；D：追加 MA 排列首次翻转 OR
- 离场（MTF 最新收盘 `m_last`）：
  - **MSS 清仓**：`len(lo)>=2 且 lo[-2].low > m_last`
  - **XCH 减半**：`lo[-1].low > m_last`（仅 1 个低点则全清）
  - **加回满仓**：半仓且 `m_last > hi[-1].high` 且有现金
- 质量过滤：`bias`（HTF regime + h1 级别 EMA50 价格过滤）/ `jump`(2.0×ATR14) / `gap`(0.5×ATR14) / `liq`(20日均额30%)

## 📐🧱 FVG（公允价值缺口）/ OB（订单块）—— 2026-11-08 新增

> ⚠️ **只作为「机会 / 风险提示」输出，绝不参与入场判定**。
> variant A/B/C/D 的入场规则、离场状态机、止损参数**一行未改**（见文末等价性验证）。
> 所有计算只用 `<= i` 的数据，无 look-ahead。

### FVG（3 根 K 线失衡）

- 看涨：`low[i] > high[i-2]` → 缺口区间 `(high[i-2], low[i])`
- 看跌：`high[i] < low[i-2]` → 缺口区间 `(high[i], low[i-2])`
- 强度 = 缺口幅度 / **ATR14**（形成那根的值）；低于 `min_atr_ratio=0.25` 的算噪音丢弃
- 状态机（形成后只用 `i+1..end` 的价格打缺口）：
  - `active` 未回补（<50%）
  - `mitigated` 已部分回补（≥50%，未完全）
  - `filled` 完全回补（看涨跌破下沿 / 看跌涨破上沿）—— **默认不输出**
- 只保留最近 `lookback=50` 根内形成、且未 filled 的，按强度降序

### OB（订单块）

- 看涨 OB：**向上突破结构**（收盘 `close[j] > 前一个已确认摆动高`）之前的**最后一根阴线**（`close<open`）的 `(low, high)`
- 看跌 OB：向下破结构之前**最后一根阳线**的 `(low, high)`
- ⚠️ 摆动点确认延迟 `k`：`hi_idx/lo_idx` 索引 `x` 要到 `x+k` 才确认，代码里严格用 `x+k <= j` 过滤，**不偷看未来**
- 状态：`touched` 已回踩（看涨某根 low ≤ 上沿 / 看跌某根 high ≥ 下沿）；`invalid` 已失效（看涨收盘破下沿 / 看跌收盘破上沿），失效默认不输出
- 只保留 `lookback=50` 根内、且未失效的，按新鲜度（age 升序）

### 接口

```python
smc_core.find_fvgs(df, atr_series, lookback=50, min_atr_ratio=0.25, end=None, include_filled=False)
smc_core.find_order_blocks(df, hi_idx, lo_idx, atr_series=None, lookback=50, end=None, k=1, include_invalid=False)
smc_core.zones_text(fvgs, obs, px=None)          # → (lines, summary)，两行播报文案
smc_core.confirm_zone(df, z, end=None, win=3, need=2, atr_val=None, near_atr=1.0)  # 🎯 买卖点判定
smc_core.pick_signals(fvgs, obs, df, end=None)   # 挑最该看的几条确认信号
smc_core.confirm_text(sig, px=None)              # → 一行短文案

fr = smc_core.Frame(df, k)
fr.fvgs(i)      # 第 i 根（含）之前的未回补 FVG
fr.obs(i)       # 第 i 根（含）之前的有效 OB
fr.zones(i)     # {"i","dt","close","atr","fvgs","obs"}
fr.signals(i)   # 🎯 不破 + 方向确认的买卖点判断
```

### 🎯 买卖点确认（不破 + 方向向上 = 才是买点）

> 大佬 2026-11-08 口径：**「它不破，另外得方向向上走才是买点」**。
> 光有 zone 不破不算买点，必须方向确认。四段判定（全部只用 `<= i` 数据）：

| 步骤 | 看涨区（支撑 → 买点） | 看跌区（压力 → 做空/离场） |
|---|---|---|
| **A 不破** | 收盘未跌破下沿；FVG 未完全回补 | 收盘未涨破上沿；FVG 未完全回补 |
| **B 已回踩** | 区间被测试过（OB: touched；FVG: last_touch_idx 有值） | 区间被回抽过 |
| **C 方向确认** | 最近 `CONF_WIN=3` 根内至少 `CONF_SCORE=2` 条成立：① 阳线 ② 收高 ③ 破前 3 根高点 ④ 站上区间上沿 | 全反向：① 阴线 ② 收低 ③ 破前低 ④ 压下沿 |
| **D 不走远** | 现价离上沿 ≤ `CONF_NEAR_ATR=1.0`×ATR；超了判 `extended`（已走远·不追），防追高 | 离下沿同理，判「已跌远·不追空」 |

状态与标签：

- `✅ 买点确认` / `🔻 压制有效`（A+B+C 全满足）
- `🟡 已回踩·待上攻` / `🟡 已回抽·待转弱`（A+B 满足但 C 不够）
- `🟡 未破·待回踩` / `🟡 未破·待回抽`（A 满足，B 还没发生）
- `🟠 已走远·不追` / `🟠 已跌远·不追空`（A+B+C 满足但 D 不满足）
- `⚠️ 已破位` / `🔺 压力被破·转强`（A 不满足）

播报里单列一段 **「🎯 买卖点确认（不破 + 方向确认）」**，只列 `buy/reject/broken/breakout` 四类；
一个都没有时输出「当前无「不破+方向确认」的标的（已回踩待上攻 N 个：…）」。
⚠️ 依然**只做提示，不参与入场判定**。

### 播报接入

- `smc_notify.snapshot()`（`--digest`）、`open_brief()`（开盘播报）、`build_incremental()`（增量消息）末尾
  统一追加一段「📐🧱 FVG/OB 结构提示」，只列**距现价 ≤2%** 的标的（`SMC_ZONES_NEAR`），最多 6 个（`SMC_ZONES_MAX`）。
- 开关：环境变量 `SMC_ZONES=0` 全关。
- 长度：纯文本统一截断到 4500 字符（卡片远低于 50KB）。
- `smc_engine.run_one()` 把 zone 快照写进 `res["zones"]`（仅供播报，不参与判定）。
- `smc_engine.run_one()` 的 `res["zones"]` 另含 `signals` / `sig_buy` / `sig_reject`（同上，仅播报）。

### 自测

```bash
python3 smc/smc_fvg_selftest.py            # sh510300 日线 + AU0 小时线
```

输出会打印每个 FVG 的**形成 3 根 K 的开高低**供人工核对，并断言：
形成点 `i` 在 `end=i` 时即可见、`end<i` 时不可见、后续 `end=i+40` 区间与形成时的区间一致。

### 等价性验证（入场规则未改动的证据）

新旧 `smc_core.py` 对比（`sh510300` 日线 + `AU0` 小时线 × variant A/B/C/D 共 8 组）：

```
sh510300 day A: 新18/-24.78% vs 旧18/-24.78%  trades_equal=True
sh510300 day B: 新13/-20.02% vs 旧13/-20.02%  trades_equal=True
sh510300 day C: 新 8/-7.43%  vs 旧 8/-7.43%   trades_equal=True
sh510300 day D: 新18/-26.91% vs 旧18/-26.91%  trades_equal=True
AU0      h1  A: 新10/-0.57%  vs 旧10/-0.57%   trades_equal=True
AU0      h1  B: 新 9/-0.60%  vs 旧 9/-0.60%   trades_equal=True
AU0      h1  C: 新 6/-0.05%  vs 旧 6/-0.05%   trades_equal=True
AU0      h1  D: 新16/-1.32%  vs 旧16/-1.32%   trades_equal=True
```

且 `def run_backtest` … `def summarize` 之间 **318 行逐字完全一致**，文件头部常量（止损/成本/仓位）也完全一致。

## 数据源（多级回退）

- 宽基 ETF 60m：**新浪 K线(datalen 1023)** → 腾讯 mkline(320) → akshare
- 期货 60m：**新浪 `futures_zh_minute_sina`(1023)** → minute_hub 兜底
- 日线：tushare `fund_daily`（ETF）/ akshare `futures_zh_daily_sina`（期货）
- 全部强制 IPv4（容器 IPv6 不通）

历史窗口：60m 约 1 年（1023 根），日线可回溯多年（用于 day/week 级别）。

## 定时任务

```
# 每30分钟判断一次（≤45分钟有一次判断），仅交易时段活跃
*/30 9-23 * * 1-5 cd ${TECH_WS:-<repo>} && timeout 600 python3 smc/smc_poll.py >> smc/cache/poll.log 2>&1
# 每日 15:05 持仓/权益快照
5 15 * * 1-5 cd ${TECH_WS:-<repo>} && timeout 600 python3 smc/smc_notify.py --digest >> smc/cache/poll.log 2>&1
```

## 手工命令

```bash
python3 smc/smc_data.py --refresh          # 刷新全部行情（按各类别级别）
python3 smc/smc_engine.py                  # 全标的推演 + 落库（股票=day / 期货=h1）
python3 smc/smc_engine.py --notify         # 落库后发增量通知
python3 smc/smc_backtest.py                # 变体回测（按类别自动选级别）
python3 smc/smc_notify.py --digest         # 发持仓快照
python3 smc/smc_notify.py                  # 发未读增量事件
```

## 通知

- 主通道：**飞书「多尺度交易」群**（应用机器人 `smc_feishu.send_card` → `FEISHU_APP_CHAT`；未配置时回退群 webhook）
- 兜底：Coze 群文件/文本（`coze-bridge send`）
- 增量语义：事件入库后 `notified=0`；发送成功才置 1。同 DB 重复运行不会重复推送。

### ⚠️ 消息形态（2026-10-08 用户三次修正后定稿）
1. **一轮只发一条**消息（不一个动作一条）；
2. **同一标的一轮只发「最后一次」动作**，开仓/平仓**绝不同时出现**（用户："又开又平我没法跟"）；
3. **引擎层根治“又开又平”**：`smc_core` 新增 `pos["entry_bar"]`，同一根 K 线内**不允许**触发止损或离场状态机（即“一根K线内先开后平”的噪声事件不再产生）；
4. 只发**今天**的事件（`smc_poll` 传 `baseline_before=今日00:00`）；
5. 格式：`🟢 做多（看多）` / `🔴 离场（看空/离场）` + 时间/成交价/逻辑/建仓/盈亏，末尾附「动作后状态」（看多持仓 + 空仓观望 + 合计权益）。
6. **轮询频率：整点一次**（`0 * * * 1-6`，2026-10-08 由每 30 分钟改为每小时；用户只要 h1 交易）。

### 🎯 买卖点自测

```bash
python3 smc/smc_fvg_selftest.py sh510300:1d AU0:60m
```

除 FVG/OB 核对，还逐个重算 `confirm_zone` 的 4 条条件 + 状态机，断言与主实现一致
（打印 `4条={...} score x/y | 未破 | 偏离 n×ATR | 状态`），并验证同一 `end` 重复计算完全一致。
