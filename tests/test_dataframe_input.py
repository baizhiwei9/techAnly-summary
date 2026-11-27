# -*- coding: utf-8 -*-
"""数据注入自测：不装 data_hub、不联网，日线 DataFrame 直接传给取数入口。

跑法::

    python tests/test_dataframe_input.py
    python -m pytest tests/test_dataframe_input.py

验证三件事：
1. 列名随意（dt/date/trade_date、vol/volume）都能归一；
2. mad_layer.load / chan_ma_trend_v2.load_index / chan_smc_index.fetch_daily
   在给 df 时不取数、不碰 DataHub；
3. 归一后的数据能继续跑算法（layers / 均线状态）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chanlun.data_input import normalize_daily  # noqa: E402


def _daily(n=320, col_style="dt") -> pd.DataFrame:
    """造一段上升日线（涨势清晰，便于断言均线状态）。"""
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = 3.0 * np.cumprod(1 + rng.normal(0.002, 0.008, n))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.003, n)))
    date_col = {"dt": "dt", "date": "date", "trade_date": "trade_date"}[col_style]
    vol_col = "vol" if col_style != "dt" else "volume"
    return pd.DataFrame({
        date_col: idx.strftime("%Y%m%d") if col_style == "trade_date" else idx,
        "open": open_, "high": high, "low": low, "close": close,
        vol_col: rng.integers(1_000_000, 3_000_000, n),
    })


def test_normalize_daily_alias():
    for style in ("dt", "date", "trade_date"):
        d = normalize_daily(_daily(col_style=style))
        assert list(d.columns)[:6] == ["dt", "open", "high", "low", "close", "volume"], d.columns
        assert len(d) == 320
        assert str(d["dt"].dtype).startswith("datetime64")


def test_normalize_daily_min_rows():
    try:
        normalize_daily(_daily(n=100), min_rows=200)
    except ValueError as e:
        assert "少于要求" in str(e)
    else:  # pragma: no cover
        raise AssertionError("行数不足应抛 ValueError")


def test_mad_layer_load_with_df():
    from chanlun.ma import mad_layer

    df = _daily()
    d, name = mad_layer.load("510300", df=df, adjust="hfq")
    assert len(d) == 320 and d.attrs["adjust"] == "hfq"
    assert name
    layer = mad_layer.layer(d)
    assert layer["status"] == "ok", layer
    assert layer["above_ma40"] is True and layer["pass_filter"] is True


def test_chan_ma_trend_load_index_with_df():
    from chanlun.ma import chan_ma_trend_v2 as cmt

    d = cmt.load_index("000300.SH", df=_daily())
    assert list(d.columns) == ["dt", "open", "high", "low", "close"]
    assert len(d) == 320


def test_chan_smc_fetch_daily_with_df():
    from chanlun.fractal import chan_smc_index as csi

    d = csi.fetch_daily("000300.SH", df=_daily(col_style="trade_date"))
    assert {"dt", "open", "high", "low", "close", "volume"} <= set(d.columns)
    assert len(d) == 320


def test_no_datahub_import_needed():
    """没装 DataHub 时，给 df 的链路不该去 import 它。"""
    import importlib.util
    if importlib.util.find_spec("data_hub") is not None:
        return          # 环境里装了 DataHub，允许被用到；本用例只在「没装」时校验
    assert "data_hub" not in sys.modules


def _main():
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print("  OK   %s" % name)
        except Exception as e:
            failed += 1
            print("  FAIL %s -> %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d 个" % (len(tests), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())