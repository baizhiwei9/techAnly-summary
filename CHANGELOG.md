# CHANGELOG

> 每次改动必须在此登记：改了什么、为什么改、影响哪个文件。

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
