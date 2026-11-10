# CHANGELOG

> 每次改动必须在此登记：改了什么、为什么改、影响哪个文件。

## 2026-11-10 13:33

**feat: 卖点(三卖/一卖/二卖) + 二买恢复（胜率最低）**

买点：三买 > 一买(须MA20向上) > 二买(恢复，胜率最低仅参考)。
卖点：三卖(结构) > 一卖(须MA20向下，硬门槛) > 二卖(MA20向下仅加分)。
新增 find_third_sells/_gate_sell/find_sells；analyse_one 增 sells_ranked/best_sell；
报告新增二买段与卖点段；单只四层报告 rank_buys 认二买 + 卖点优先级。
全流程纯 Python，不依赖任何大模型。
