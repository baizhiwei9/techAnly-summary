#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_loader.py · 拓扑流形分析取数层
====================================
统一从 techAnly 的取数桥（dh_bridge）取数；本地无 dh_bridge 时回退到
data_hub（用 DATA_HUB_PATH 指向 datahub-three-tier，便于本地开发验证）。

原则：
  · 只做「取数 + 归一化成 close 面板」，不含任何 SPD 计算 / 画图逻辑
  · 每个函数失败返回空值，绝不抛异常（上层自然跳过该标的）
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta

_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根

# dh_bridge 在 misc/core/ 下，把它加入 sys.path 以便 `import dh_bridge`
_CORE = os.path.join(_WS, "misc", "core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

_lock = threading.RLock()
_bridge_cache = [None]           # (module, mode) 惰性缓存；mode: bridge / datahub / None
_board_cache = {"行业": None, "概念": None}


def _log(msg):
    print("[topology] %s" % msg, file=sys.stderr, flush=True)


def bridge():
    """惰性加载取数桥。返回 (module, mode)。

    mode == "bridge"   → techAnly 的 dh_bridge（优先）
    mode == "datahub"  → 直接 data_hub（本地回退）
    mode is None       → 都不可用
    """
    if _bridge_cache[0] is not None:
        return _bridge_cache[0]
    with _lock:
        if _bridge_cache[0] is not None:
            return _bridge_cache[0]
        # 1) dh_bridge（techAnly 环境）
        try:
            import dh_bridge as b
            if b.enabled():
                _bridge_cache[0] = (b, "bridge")
                return _bridge_cache[0]
            # dh_bridge 存在但 data_hub 没启用/不可用 → 仍可能能用它兜底老链路，
            # 但我们优先直接试 data_hub
        except Exception:
            pass
        # 2) 直接 data_hub（本地 DATA_HUB_PATH 或 optstack）
        for base in _candidates():
            try:
                if base not in sys.path:
                    sys.path.insert(0, base)
                import importlib
                mod = importlib.import_module("data_hub")
                if hasattr(mod, "get_data"):
                    _quiet_datahub()
                    _bridge_cache[0] = (mod, "datahub")
                    return _bridge_cache[0]
            except Exception:
                continue
        _bridge_cache[0] = (None, None)
        return _bridge_cache[0]


def _quiet_datahub():
    """把 data_hub 内部 INFO 日志降到 WARNING（对齐 dh_bridge 的降噪做法）。"""
    if os.environ.get("TECH_DH_VERBOSE", "0") in ("1", "true", "yes"):
        return
    try:
        import logging
        for _nm in ("data_hub", "data_hub.core", "data_hub.core.config"):
            logging.getLogger(_nm).setLevel(logging.WARNING)
        logging.getLogger("data_hub").propagate = False
    except Exception:
        pass


def _candidates():
    out = []
    env = os.environ.get("DATA_HUB_PATH")
    if env:
        out.append(os.path.abspath(os.path.expanduser(env)))
    out.append(os.path.join(_WS, "optstack"))
    out.append(_WS)
    d = _WS
    for _ in range(4):
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
        out.append(os.path.join(d, "optstack"))
        out.append(d)
    return out


# ───────────────────────── 板块列表 ─────────────────────────
def board_list(kind="行业"):
    """板块列表 → [(name, code)...]；失败 []"""
    if _board_cache[kind] is not None:
        return _board_cache[kind]
    mod, mode = bridge()
    if mod is None:
        _board_cache[kind] = []
        return []
    try:
        if mode == "bridge":
            lst = mod.sector_list(kind=kind)
            out = [(x.get("name", ""), x.get("code", "")) for x in lst]
        else:
            df = mod.get_data("sector_list", kind=kind)
            out = _df_to_pairs(df)
    except Exception as e:
        _log("board_list(%s) 失败: %s" % (kind, str(e)[:80]))
        out = []
    _board_cache[kind] = out
    return out


def _df_to_pairs(df):
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return []
    name_col = "name" if "name" in df.columns else None
    code_col = "code" if "code" in df.columns else ("board_code" if "board_code" in df.columns else None)
    if not name_col:
        return []
    out = []
    for _, r in df.iterrows():
        out.append((str(r.get(name_col) or "").strip(),
                    str(r.get(code_col) or "").strip()))
    return out


# ───────────────────────── 成分股 ─────────────────────────
def components(target):
    """按 Target 取成分股 ts_code 列表；失败 []"""
    mod, mode = bridge()
    if mod is None:
        return []
    k = target.kind
    if k == "index":
        return _index_components(mod, mode, target.code)
    if k == "industry":
        return _sector_members(mod, mode, target.name, target.code, "行业")
    if k == "concept":
        return _sector_members(mod, mode, target.name, target.code, "概念")
    if k == "board":
        # 板块：先行业、失败再概念
        m = _sector_members(mod, mode, target.name, target.code, "行业")
        if m:
            return m
        return _sector_members(mod, mode, target.name, target.code, "概念")
    return []


def _index_components(mod, mode, code):
    try:
        if mode == "bridge":
            return mod.index_components(code)
        d = mod.get_data("index_components", index_code=code)
        if isinstance(d, (list, tuple)):
            return [str(x) for x in d if x]
        col = "con_code" if "con_code" in d.columns else ("ts_code" if "ts_code" in d.columns else None)
        if col:
            return [str(x) for x in d[col].tolist() if x]
        return []
    except Exception as e:
        _log("index_components(%s) 失败: %s" % (code, str(e)[:80]))
        return []


def _sector_members(mod, mode, name, code, kind):
    try:
        if mode == "bridge":
            if code:
                return mod.sector_members(code=code, kind=kind)
            return mod.sector_members(name=name, kind=kind)
        if code:
            df = mod.get_data("sector_members", kind=kind, code=code)
        else:
            df = mod.get_data("sector_members", kind=kind, name=name)
        col = "code" if "code" in df.columns else ("ts_code" if "ts_code" in df.columns else None)
        if col:
            return [str(x).split(".")[0].zfill(6) for x in df[col].tolist() if str(x).strip()]
        return []
    except Exception as e:
        _log("sector_members(%s) 失败: %s" % (name or code, str(e)[:80]))
        return []


# ───────────────────────── 日线面板 ─────────────────────────
def _norm_code(ts_code):
    """'000001.SZ' / '600519' → '600519'（6 位裸码）；指数保留 tushare 格式"""
    s = str(ts_code or "").strip().upper()
    if "." in s:
        s = s.split(".")[0]
    return s


def close_panel(codes, n_days=60):
    """成分股收盘面板 → (dates: list[str] 长度 n_days+1, close: DataFrame n_days+1 行 × 成分列)

    取最近 n_days+1 个有数据的交易日。优先 data_hub 的批量接口，回退逐只取数。
    失败返回 (None, None)。
    """
    if not codes:
        return None, None
    mod, mode = bridge()
    if mod is None:
        return None, None

    # 日期窗口：自然日冗余（约 2 倍交易日 + 30 天缓冲，覆盖节假日）
    end = datetime.now()
    start = end - timedelta(days=int(n_days * 2.2) + 30)
    start8, end8 = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    import pandas as pd

    # 1) 批量：data_hub 有 get_stock_daily_batch（dh_bridge 环境也通过 module() 拿）
    try:
        batch_fn = None
        if mode == "datahub" and hasattr(mod, "get_stock_daily_batch"):
            batch_fn = mod.get_stock_daily_batch
        elif mode == "bridge":
            m2 = mod.module()
            if m2 is not None and hasattr(m2, "get_stock_daily_batch"):
                batch_fn = m2.get_stock_daily_batch
        if batch_fn is not None:
            df = batch_fn([str(c) for c in codes], start8, end8)
            if df is not None and not df.empty:
                panel = df.pivot_table(index="trade_date", columns="ts_code", values="close") \
                    if "ts_code" in df.columns else None
                if panel is not None and len(panel):
                    panel = panel.reindex(sorted(panel.index))
                    return _trim_panel(panel, n_days)
    except Exception as e:
        _log("批量日线失败，回退逐只: %s" % str(e)[:80])

    # 2) 逐只取数（慢但通用）
    rows = {}
    for c in codes:
        try:
            if mode == "bridge":
                df = mod.stock_daily(_norm_code(c), start=start8, end=end8)
                if df is not None and not df.empty:
                    rows[c] = df.set_index("dt")["close"]
            else:
                df = mod.get_data("stock_daily", ts_code=c, start_date=start8, end_date=end8)
                if df is not None and not df.empty:
                    key = "trade_date" if "trade_date" in df.columns else "date"
                    s = df.set_index(key)["close"]
                    s.index = pd.to_datetime(s.index.astype(str)).strftime("%Y%m%d")
                    rows[c] = s
        except Exception:
            continue
    if not rows:
        return None, None
    panel = pd.DataFrame(rows)
    panel = panel.sort_index()
    return _trim_panel(panel, n_days)


def _trim_panel(panel, n_days):
    """面板：去掉缺失过度的列，前向填充，截最后 n_days+1 行"""
    import pandas as pd
    panel = panel.copy()
    if len(panel) <= 1:
        return None, None
    missing_ratio = panel.isna().mean()
    keep = missing_ratio[missing_ratio <= 0.2].index
    if len(keep) < 5:
        return None, None
    panel = panel[keep].ffill().fillna(0.0)
    panel = panel.tail(n_days + 1)
    if len(panel) < n_days + 1:
        return None, None
    return [str(x) for x in panel.index], panel


def index_close(code, n_days=60):
    """指数收盘序列（用于图上金线）；失败返回 None"""
    mod, mode = bridge()
    if mod is None:
        return None
    import pandas as pd
    end = datetime.now()
    start = end - timedelta(days=int(n_days * 2.2) + 30)
    start8, end8 = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    try:
        if mode == "bridge":
            df = mod.index_daily(code, start=start8, end=end8)
            if df is not None and not df.empty:
                s = df.set_index("dt")["close"]
                s.index = pd.to_datetime(s.index).strftime("%Y%m%d")
                return s.sort_index().tail(n_days + 1)
        else:
            df = mod.get_data("index_daily", ts_code=code, start_date=start8, end_date=end8)
            if df is not None and not df.empty:
                key = "trade_date" if "trade_date" in df.columns else "date"
                s = df.set_index(key)["close"]
                s.index = pd.to_datetime(s.index.astype(str)).strftime("%Y%m%d")
                return s.sort_index().tail(n_days + 1)
    except Exception as e:
        _log("index_close(%s) 失败: %s" % (code, str(e)[:80]))
    return None
