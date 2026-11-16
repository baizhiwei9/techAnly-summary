#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期权波动率三条线：VIX / 偏度 /（配合 R_cascade 用）
=====================================================================
数据源：**DataHub 唯一**（2026-11-16 大佬要求：「期权的数据应该也在 DataHub 上搞」）
  `data_hub.collectors.misc.option_metrics_history.get_option_metrics()`
  · VIX  = vix30（Options Utility 口径，OI 加权方差插值），缺列退 calc_vix
  · 偏度 = skew_index 列（BKM 严格口径，见下）

偏度口径（2026-11-17 大佬定：「偏度是用按照严格的来偏度来做的，不要偷懒」）
--------------------------------------------------------------------
`skew_index` = 100 − 10 × BKM(2003) 风险中性偏度
  · 唯一实现：`options_utility/calc/risk_layer.py::bkm_skew_index(strict=True)`
    严格条件 = 固定带宽 0.85S~1.15S、最少 8 档 OTM，不满足 **返回 NA**
  · 适配器：`data_hub/core/option_metrics.py::bkm_skew_index_from_contracts(strict=True)`
  · **本文件不再有任何偏度算法**：原先本地自写的 25Δ 反解 + 自行缓存的套偏度
    实现已于 2026-11-17 整体物理删除，不许再加第二套（只允许调库）。

取不到 DataHub 数据时：`series_for` 直接抛错，画图侧标 NA —— 不兜底、不插值。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

CACHE_DIR = os.environ.get("OPT_VOL_CACHE", os.path.join(
    os.path.expanduser("~"), ".cache", "opt_vol"))

# 指数名 → DataHub underlying
IDX2DH = {
    "沪深300": "510300",
    "中证500": "510500",
    "中证1000": "MO",
    "创业板指": "159915",
    "科创50": "588000",
    "上证50": "IO",
}


def _dh_metrics():
    """import DataHub 的期权指标入口（失败返回 None）。"""
    try:
        _op = os.environ.get("OPTSTACK_DIR") or os.path.join(
            os.path.expanduser("~"), ".openclaw", "workspace", "optstack")
        if _op not in sys.path:
            sys.path.insert(0, _op)
        from data_hub.collectors.misc.option_metrics_history import get_option_metrics
        return get_option_metrics
    except Exception as e:                                       # noqa: BLE001
        print("[optvol] DataHub 期权指标不可用：%s" % str(e)[:120], flush=True)
        return None


def series_from_datahub(name, start=None, end=None, verbose=False):
    """从 DataHub 取某指数的 VIX / 偏度（SKEW 指数口径，BKM 严格），返回统一 DataFrame。

    列：vix（%）、skew（SKEW 指数，100 中轴，BKM 严格口径）、atm_iv（%）、iv_skew
    """
    ul = IDX2DH.get(name)
    if ul is None:
        raise KeyError("没有 %s 的 DataHub 期权标的映射（可选：%s）"
                       % (name, "、".join(IDX2DH)))
    fn = _dh_metrics()
    if fn is None:
        return None
    import datetime as _dt
    end = end or _dt.date.today().strftime("%Y%m%d")
    start = start or (_dt.date.today() - _dt.timedelta(days=460)).strftime("%Y%m%d")
    df = fn(start, end, underlyings=[ul], verbose=verbose)
    if df is None or not len(df):
        return None
    df = df.copy()
    # 必须按日期排序后再取列（缓存合并后行序不保证），否则 values 会错位
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.sort_values("trade_date")
    out = pd.DataFrame(index=[str(x) for x in df["trade_date"]])
    out.index.name = "trade_date"
    # VIX：优先 vix30（Options Utility 同口径，OI 加权方差插值，抗单档噪声）；
    # 老缓存没这列时退回 DataHub 的 calc_vix（CBOE 方差互换法）
    if "vix30" in df.columns:
        out["vix"] = pd.to_numeric(df["vix30"], errors="coerce").values
        try:
            _fb = pd.to_numeric(df["vix"], errors="coerce").values
            out["vix"] = out["vix"].where(out["vix"].notna(), _fb)
        except Exception:                                        # noqa: BLE001
            pass
    else:
        out["vix"] = pd.to_numeric(df["vix"], errors="coerce").values
    # SKEW 指数：skew_index（BKM 严格，100−10×skew，100 中轴、>110 危险）。
    # 不再回退 cboe_skew —— 那是「虚值20%put/ATM×100」另一套口径（且被钳在 150），
    # 混用等于第二套偏度算法，违反「唯一口径」纪律；缺就是 NA。
    out["skew"] = (pd.to_numeric(df["skew_index"], errors="coerce").values
                   if "skew_index" in df.columns else float("nan"))
    out["atm_iv"] = pd.to_numeric(df["atm_iv"], errors="coerce").values * 100.0
    out["iv"] = out["atm_iv"]
    out["iv_skew"] = pd.to_numeric(df["iv_skew"], errors="coerce").values * 100.0
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if verbose:
        _l = out.iloc[-1]
        print("[optvol] %s ← DataHub(%s) %d 天 %s~%s | VIX=%.1f SKEW=%s"
              % (name, ul, len(out), out.index[0], out.index[-1], _l["vix"],
                 ("%.1f" % _l["skew"]) if pd.notna(_l["skew"]) else "NA"), flush=True)
    return out


def series_for(name, hist_path=None, force=False, verbose=False):
    """取某指数的 VIX / 偏度序列（**只走 DataHub**；取不到就抛错，由画图标 NA）。

    返回 DataFrame(index=YYYYMMDD str, cols: vix, skew)
    """
    dh_df = series_from_datahub(name, verbose=verbose)
    if dh_df is None or not len(dh_df):
        raise RuntimeError("%s（DataHub %s）没有期权指标数据" % (name, IDX2DH.get(name)))
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        dh_df.to_csv(os.path.join(CACHE_DIR, "%s.csv" % name))
    except Exception:                                            # noqa: BLE001
        pass
    return dh_df[["vix", "skew"]]
