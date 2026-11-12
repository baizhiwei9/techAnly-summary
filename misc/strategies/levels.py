#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一级别适配层：m15 / h1 / day / week
=====================================================================
五套口径里，①分型版 ②均线版 ③SMC ④VWAP 都是「喂什么级别的 K 线就算什么级别」，
公式本身与周期无关。所以「按参数切级别」只做三件事：

  1) 取数：m15/hour 走 minute_hub（腾讯→新浪→百度），日线走 datahub 三级灾备；
  2) 周线：由日线 resample(W-FRI) 合成（多级别分析不单独取周线数据）；
  3) 标签：级别名、新鲜度阈值、报告标题随之切换。

  小波（wavelet/）是**日线/周线策略**，不在这条统一适配层里：
  它的参数（weekly_w=30 / db4 / level=4）是按日线尺度定的，不能直接套 15 分钟。
  期货 1h 模块存在但当前停用，详见 wavelet/README.md。

用法：
  python3 strategies/run_level.py --target 中证500 --level m15
  python3 strategies/run_level.py --target 600519 --level week --strategy all
"""
from __future__ import annotations

import os
import sys

import pandas as pd

def _repo_root(start):
    """向上探测仓库根：含 chanlun/ 与 bin/ 的那一层。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "chanlun")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.abspath(start))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
for _p in (os.path.join(WS, "bin"), os.path.join(WS, "smc"),
           os.path.join(WS, "misc/core"), os.path.join(WS, "misc/engine"),
           os.path.join(WS, "chanlun/fractal"), os.path.join(WS, "chanlun/ma"),
           os.path.join(WS, "chanlun/multiscale"), WS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tech_summary as TS          # noqa: E402  取数 + 四层口径
import minute_hub as MH            # noqa: E402  15m/60m 分钟数据

# 统一级别名（顺序=报告展示顺序）
LEVELS = ("m15", "h1", "day", "week")

LABEL = {
    "m15": "15分钟",
    "h1": "小时线",
    "day": "日线",
    "week": "周线",
}

# SMC 摆动点回看 k：日线/周线用 k=3（过滤噪音），小时线/15分钟用 k=1（已经足够粗）
SMC_K = {"m15": 1, "h1": 1, "day": 3, "week": 3}

# 各口径的最小数据门槛
MIN_BARS = {"m15": 120, "h1": 120, "day": 170, "week": 60}


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一成 [dt, open, high, low, close, volume] 升序，dt 为 datetime。"""
    d = df.copy()
    if "dt" not in d.columns:
        for c in ("day", "date", "t"):
            if c in d.columns:
                d = d.rename(columns={c: "dt"})
                break
    d["dt"] = pd.to_datetime(d["dt"])
    for c in ("open", "high", "low", "close", "volume"):
        if c not in d.columns:
            d[c] = 0.0
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[["dt", "open", "high", "low", "close", "volume"]]
    return d.dropna(subset=["close"]).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)


def resample_week(df: pd.DataFrame) -> pd.DataFrame:
    """日线 → 周线（W-FRI 右闭合），保留 volume。"""
    d = df.set_index("dt")
    out = pd.DataFrame({
        "open": d["open"].resample("W-FRI").first(),
        "high": d["high"].resample("W-FRI").max(),
        "low": d["low"].resample("W-FRI").min(),
        "close": d["close"].resample("W-FRI").last(),
        "volume": d["volume"].resample("W-FRI").sum(),
    }).dropna(subset=["close"]).reset_index()
    return out.rename(columns={out.columns[0]: "dt"})


def load(target: str, level: str, n: int = 320):
    """按级别取数。返回 (tg, df)。"""
    if level not in LEVELS:
        raise ValueError("level 必须是 %s，收到 %r" % ("/".join(LEVELS), level))

    tg = TS.resolve(target)

    if level == "day":
        d = TS.load_day(tg)
    elif level == "h1":
        d = TS.load_h1(tg, n=max(n, 400))
    elif level == "week":
        d = resample_week(TS.load_day(tg))
    else:  # m15
        kind = "index" if tg["kind"] == "index" else "stock"
        d = MH.get_kline(tg["sina"], "15", n=n, kind=kind, cache=True, cache_ttl_min=10)
        if d is None or len(d) < MIN_BARS["m15"]:
            d = MH.get_kline(tg["sina"], "15", n=n, kind=kind, cache=False)
        if d is None:
            raise RuntimeError("15分钟数据获取失败（%s）" % tg["sina"])

    d = normalize(d)
    if len(d) < MIN_BARS[level]:
        raise RuntimeError("%s数据不足：%d 根 < 门槛 %d 根" % (LABEL[level], len(d), MIN_BARS[level]))
    return tg, d
