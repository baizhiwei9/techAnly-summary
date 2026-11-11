# -*- coding: utf-8 -*-
"""雪球实盘组合档案（多组合支持）

2026-10-08 新增：用户有第二个实盘组合「亚丽0611的实盘」(SP1045686)，
需要和「默尔根的实盘」(SP1032764) 一样各发早晚报（分两条消息）。

用环境变量 XQ_PORTFOLIO 选择当前组合（默认 moergen，向后兼容）；
XQ_PORTFOLIOS 可指定一次处理多个（逗号分隔），默认全部。

默尔根沿用原有路径（不迁移，保留历史）；亚丽使用新路径，互不干扰：
  * 快照   .xueqiu/last_snapshot.json            vs .xueqiu/portfolios/yali/last_snapshot.json
  * 日存档 memory/xueqiu/snapshots               vs memory/xueqiu/yali/snapshots
  * 持仓史 .xueqiu/holdings_history              vs .xueqiu/portfolios/yali/holdings_history
  * 报告   memory/xueqiu/reports                 vs memory/xueqiu/yali/reports
登录态（cookie）为账号级，两个组合共用 .xueqiu/xq_state.json。
"""
import os

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROFILES = {
    "moergen": {
        "key": "moergen",
        "cube": "SP1032764",
        "label": "默尔根实盘",
        "snap": ".xueqiu/last_snapshot.json",
        "snapdir": "memory/xueqiu/snapshots",
        "hist": ".xueqiu/holdings_history",
        "rep": "memory/xueqiu/reports",
        "show_ret": False,   # 默尔根：用户 2026-10-07 明确不展示涨跌
    },
    "yali": {
        "key": "yali",
        "cube": "SP1045686",
        "label": "亚丽实盘",
        "snap": ".xueqiu/portfolios/yali/last_snapshot.json",
        "snapdir": "memory/xueqiu/yali/snapshots",
        "hist": ".xueqiu/portfolios/yali/holdings_history",
        "rep": "memory/xueqiu/yali/reports",
        "show_ret": True,    # 亚丽：用户 2026-10-08 要求晚报展示组合涨跌（涨多/涨少）
    },
}

ORDER = ["moergen", "yali"]
DEFAULT = "moergen"


def current():
    """返回 (key, profile)，默认 moergen（向后兼容旧调用）。"""
    k = (os.environ.get("XQ_PORTFOLIO") or DEFAULT).strip().lower()
    return k, PROFILES.get(k, PROFILES[DEFAULT])


def all_profiles():
    """返回本次要处理的组合列表（XQ_PORTFOLIOS 逗号分隔，默认全部）。"""
    sel = (os.environ.get("XQ_PORTFOLIOS") or "").strip()
    keys = [x.strip().lower() for x in sel.split(",") if x.strip()] or ORDER
    out = [PROFILES[k] for k in keys if k in PROFILES]
    return out or [PROFILES[DEFAULT]]


def path(p, field):
    return os.path.join(WS, p[field])


def ensure(p, field):
    d = path(p, field)
    os.makedirs(d, exist_ok=True)
    return d
