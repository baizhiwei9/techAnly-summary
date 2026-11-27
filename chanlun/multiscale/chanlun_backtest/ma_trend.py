#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论·均线版（新版，对齐远程 `bin/chan_ma_trend_v2.py`）

2026-11-11 大佬定稿：均线版不再用旧的 MA20/40/160 + MACD 12/26/9 简版，
直接复用远程原文口径（趋势浪子《交易悟道之路》）：

  · 笔     = MA5 与 MA10 **同时**穿过 MA20（双穿），方向翻转即笔翻转
  · 线段   = MA20 与 MA40 双穿 MA80
  · 大级别 = 价格 vs MA160（只这一条，不用 MA320）
  · MACD   = 10/20/5
  · 买卖点 = 笔由下转上（一类买点）、上涨笔中快线回踩不碰慢线（延续点）、
             背驰（MACD 面积衰减）或笔翻下（卖点）

口径单源：`chanlun_backtest/vendor/chan_ma_trend_v2.py`（远程原文逐字节拷贝），
库内直接加载，不再依赖外部仓库路径。上游更新时用
`python -m chanlun_backtest.sync_vendor` 重新同步。
查找顺序：$CHAN_MA_BIN → vendor/ → 同仓 bin/ → 合并目录 techAnly-summary/bin/。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_BIN_CANDIDATES = []


def _find_bin():
    here = os.path.dirname(os.path.abspath(__file__))
    up1 = os.path.dirname(here)                 # chanlun/multiscale/chanlun_backtest
    up2 = os.path.dirname(up1)                  # chanlun/multiscale
    root = os.path.dirname(os.path.dirname(up2))  # 仓库根
    cands = [
        os.environ.get("CHAN_MA_BIN"),
        os.path.join(here, "vendor"),                          # 库内单源（优先）
        os.path.join(root, "chanlun", "ma"),                   # 2026-11-12 新布局
        os.path.join(root, "bin"),                             # 兼容 shim
        os.path.join(up2, "..", "..", "bin"),                  # 兜底
        os.path.join(up1, "bin"),
    ]
    for p in cands:
        if p and os.path.exists(os.path.join(p, "chan_ma_trend_v2.py")):
            return os.path.abspath(p)
    raise ImportError(
        "找不到 chan_ma_trend_v2.py。请设置 CHAN_MA_BIN 指向包含该文件的 bin 目录，"
        "或确认仓库 bin/ 存在。已查找: %r" % [p for p in cands if p])


_BIN_CANDIDATES = [_find_bin()]
if _BIN_CANDIDATES[0] not in sys.path:
    sys.path.insert(0, _BIN_CANDIDATES[0])

import chan_ma_trend_v2 as M  # noqa: E402

__all__ = ["M", "normalize", "prepare", "analyze", "recent_signals",
           "format_report", "MIN_BARS"]

MIN_BARS = M.BIG_MA + 10        # 170：够算 MA160 + 10 根余量


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一成 [dt, open, high, low, close]（dt 为 datetime，升序）。"""
    d = df.copy()
    if "dt" not in d.columns:
        for c in ("datetime", "timestamp", "day", "date", "trade_date", "时间", "日期"):
            if c in d.columns:
                d = d.rename(columns={c: "dt"})
                break
    if "dt" not in d.columns:
        raise ValueError("数据缺少时间列（dt/datetime/timestamp/day/date/trade_date）")
    d["dt"] = pd.to_datetime(d["dt"], errors="coerce")
    for c in ("open", "high", "low", "close"):
        if c not in d.columns:
            raise ValueError("数据缺少列: %s" % c)
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["dt", "open", "high", "low", "close"])
    d = d.sort_values("dt").drop_duplicates("dt", keep="last").reset_index(drop=True)
    return d[["dt", "open", "high", "low", "close"]]


def prepare(df: pd.DataFrame, level: str = "day") -> pd.DataFrame:
    """按级别准备 K 线：日线/小时线/分钟线原样；周线由日线合成。"""
    d = normalize(df)
    if level in ("week", "weekly", "w"):
        if len(d) < 2:
            return d
        d = M.resample_week(d)
    d = M.add_ind(d)
    return d


def analyze(df: pd.DataFrame, level: str = "day") -> dict | None:
    """均线版当前状态（与远程 tech_summary.ma_layer 同口径）。

    返回 None 表示数据不足（< 170 根）。
    """
    d = prepare(df, level)
    if len(d) < MIN_BARS:
        return None
    last = d.iloc[-1]
    bi = M.build_bi(d)
    r = {
        "dt": str(d["dt"].iloc[-1])[:10],
        "close": float(last["close"]),
        "n": len(d),
        "loc": M.level_locate(d),
        "sigs": M.signals(d),
        "bi_n": len(bi),
        "bi_last": bi[-1] if bi else None,
        "macd": M.macd_state(d),
        "hist": float(last["hist"]),
        "mode": M.trend_mode(d),
        "beichi": M.macd_bei_chi(d, bi),
        "align": M.ma_alignment(d),
        "align_short": M.short_alignment(d),
        "walk": M.bi_walk(d),
    }
    r["tradeable"] = M.tradeable(r)
    r["df"] = d
    r["bi"] = bi
    return r


def recent_signals(df: pd.DataFrame, level: str = "day") -> list:
    """只取当前级别的买卖点清单（新鲜穿越 + 长期排列）。"""
    r = analyze(df, level)
    return (r or {}).get("sigs") or []


def format_report(r: dict | None, name: str = "", level: str = "day") -> str:
    """文本报告：只回答「当前级别能不能支撑交易」。"""
    lt = {"day": "日线", "week": "周线", "h1": "小时线",
          "60m": "60分钟", "m15": "15分钟"}.get(level, level)
    head = "📈 缠论·均线版（新版）｜%s｜%s" % (name or "—", lt)
    if not r:
        return head + "\n  数据不足（MA160 需要 ≥%d 根）" % MIN_BARS
    L = [head]
    L.append("  现价 %.2f ｜ %s" % (r["close"], r["tradeable"]))
    al = r.get("align") or {}
    mo = r.get("mode") or {}
    loc = r.get("loc") or {}
    L.append("  排列 %s（%s）｜ 走势 %s ｜ 当前主走 %s%s" % (
        al.get("state", "—"), al.get("desc", "—"), mo.get("mode", "—"),
        loc.get("label", "—"), ("·" + str(loc.get("dir"))) if loc.get("dir") else ""))
    lad = (loc.get("ladder") or {})
    for key, label in (("A0", "笔"), ("A1", "线段"), ("BIG", "大级别")):
        x = lad.get(key) or {}
        if x.get("state"):
            L.append("  %s：%s（%s，%s 根）" % (
                label, x.get("align", x.get("state")), x.get("dir", "—"), x.get("bars", 0)))
    bc = r.get("beichi")
    if bc:
        L.append("  背驰：%s（前段力度 %.1f → 本段 %.1f）" % (
            bc.get("type", "—"), bc.get("power_prev", 0), bc.get("power_now", 0)))
    sigs = r.get("sigs") or []
    for s in sigs[:5]:
        L.append("  %s：%s（%s，%s 根前）" % (
            s.get("type"), s.get("kind"), s.get("src"), s.get("bars_ago")))
    if not sigs:
        L.append("  信号：当前级别无新鲜穿越 / 无长期排列")
    wk = r.get("walk") or {}
    if wk.get("ok"):
        L.append("  当前位置：%s → %s" % (wk.get("where", "—"), wk.get("advice", "—")))
    return "\n".join(L)
