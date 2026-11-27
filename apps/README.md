# apps/ —— 对外推送应用（不进公开库）

按 techAnly-summary v2026-11-12 16:53 架构调整：带 chat_id / webhook / 邮箱等
推送凭证的应用不放进公开仓库，统一放这里。

依赖方向：**apps → 库（单向）**，库内零 import apps。

| 应用 | 用途 |
|---|---|
| weekly_zs.py | 申万行业周报（推飞书私聊 + QQ） |
| xq_ind_monitor.py | 雪球组合行业监控（发邮件） |
| xq_portfolio.py | 雪球多组合档案 |

取数已抽到库内 `misc/core/sw_daily.py`（fetch_sw_daily / sw_l2_map / sw_l3_map / stock_sw）。
