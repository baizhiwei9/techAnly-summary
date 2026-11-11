# CHANGELOG

> 每次改动必须在此登记：改了什么、为什么改、影响哪个文件。

> **2026-11-11 两库合并说明**：私有库 `tech-summary` 的全部内容（含代码、文档、发布工具、历史记录）已**合并进**本库 `techAnly-summary`（公开）后删除。
> 历史条目**一条不丢**：techAnly 原有 30 条 + 从备份补回 3 条 = 共 **33 条**。
> 仅当「时间 + 正文完全相同」且两边各存一份时才算重复（那是同一条记录）。

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
