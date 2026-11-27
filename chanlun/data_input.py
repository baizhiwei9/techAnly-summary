# -*- coding: utf-8 -*-
"""外部数据注入：日线 DataFrame 归一（公开库不强制依赖 DataHub 取数）。

用法::

    from chanlun.data_input import normalize_daily
    df = normalize_daily(my_df)          # 任意列名 -> dt/open/high/low/close/volume
    df, name = mad_layer.load("510300", df=df)

认得的列名：dt / date / trade_date / time 视为日期；vol / volume / amount 依次兜底；
其余 open/high/low/close 同名或大写均可。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

_DATE_ALIASES = ("dt", "date", "trade_date", "datetime", "time")
REQUIRED = ("dt", "open", "high", "low", "close")


def normalize_daily(df: pd.DataFrame, *, keep_volume: bool = True,
                    min_rows: int = 0) -> pd.DataFrame:
    """任意日线 DataFrame -> 标准列（升序、去重、去空）。

    Args:
        df: 调用方传入的日线数据，至少含 日期 + OHLC。
        keep_volume: 保留 volume 列（缺失则补 0，不报错）。
        min_rows: 少于该行数直接抛 ValueError（默认不检查）。

    Raises:
        ValueError: 缺列或行数不足。
    """
    if df is None:
        raise ValueError("daily DataFrame 为空")
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    if df.empty:
        raise ValueError("daily DataFrame 为空")

    lower = {str(c).lower(): c for c in df.columns}
    rename = {}
    for key in REQUIRED:
        if key == "dt":
            col = next((lower[a] for a in _DATE_ALIASES if a in lower), None)
        else:
            col = lower.get(key)
        if col is None:
            raise ValueError("日线 DataFrame 缺少 %s 列；实际列名: %s" % (key, list(df.columns)))
        rename[col] = key

    if keep_volume:
        vol_col = lower.get("volume") or lower.get("vol")
        if vol_col is None:
            rename["__no_volume__"] = "__no_volume__"
        else:
            rename[vol_col] = "volume"

    d = df.rename(columns=rename)
    d["dt"] = pd.to_datetime(d["dt"], errors="coerce")
    for key in ("open", "high", "low", "close"):
        d[key] = pd.to_numeric(d[key], errors="coerce")
    if not keep_volume:
        d = d.drop(columns=[c for c in ("volume",) if c in d.columns])
    elif "volume" not in d.columns:
        d["volume"] = 0.0
    else:
        d["volume"] = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)

    d = d.dropna(subset=["dt", "close"]).sort_values("dt")
    d = d.drop_duplicates(subset=["dt"], keep="last").reset_index(drop=True)

    if min_rows and len(d) < min_rows:
        raise ValueError("日线只有 %d 行，少于要求的 %d 行" % (len(d), min_rows))
    return d


def attach_adjust(df: pd.DataFrame, adjust: Optional[str]) -> pd.DataFrame:
    """记录复权口径到 df.attrs（与原 load() 的约定一致）。"""
    df.attrs["adjust"] = adjust or "raw"
    return df