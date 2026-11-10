# CHANGELOG

> 每次改动必须在此登记：改了什么、为什么改、影响哪个文件。

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
