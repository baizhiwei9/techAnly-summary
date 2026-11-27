#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FVG / OB 买点回测 —— 检验「不破 + 方向向上」到底有没有用

大佬 2026-11-08 要求：
  ① 随便找个标的（白银期货今年）测一下 FVG/OB 买点有没有用
  ② 质疑「回补必须补完才算废」这个口径 —— 只要测试完往上走了也算
  ③ 因此本脚本对比几套口径，看哪套能赚钱

口径对比（全部只用 <= i 数据，无 look-ahead；i 收盘触发，i+1 开盘入场）：
  S1 严格版   ：不破 + 已回踩 + 方向≥2条 + 不超 1×ATR（当前生产口径）
  S2 放宽回补 ：同上，但**允许缺口被完全回补后再收复**（大佬口径）
  S3 不要回踩 ：不破 + 方向≥2条（不要求区间被测试过）
  S4 只要不破 ：不破 + 方向≥1条（最宽松，看有多少噪音）

用法：
  python3 strategies/smc_zone_backtest.py --code AG0 --freq 1d
  python3 strategies/smc_zone_backtest.py --code AG0 --freq 60m --since 2026-01-01
  python3 strategies/smc_zone_backtest.py --code CU0 --freq 60m
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import os
import socket
import sys

_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "smc"))
import smc_core as C  # noqa: E402

FWD = (3, 5, 10, 20)          # 前瞻收益窗口（根）
ATR_STOP = 1.5                # 止损：入场 - 1.5×ATR
NEAR_CAP = 1.0                # 距区间边沿上限（×ATR）


# ───────────────────────── 数据 ─────────────────────────
def load(code: str, freq: str, since: str | None = None) -> pd.DataFrame:
    fn = os.path.join(WS, "smc", "cache", f"{code}_{freq}.csv")
    if not os.path.exists(fn):
        raise SystemExit(f"缓存不存在：{fn}（先跑 smc_engine 拉数据）")
    d = pd.read_csv(fn)
    d["dt"] = pd.to_datetime(d["dt"])
    d = d.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    if since:
        d = d[d["dt"] >= pd.Timestamp(since)].reset_index(drop=True)
    return d


# ───────────────────── 单根 bar 的信号判定 ─────────────────────
def zone_ok_bull(z: dict, px: float, atr: float, mode: str) -> bool:
    """判断一个**看涨** zone 在第 i 根是否算买点。

    mode: s1 严格 / s2 允许回补后收复 / s3 不回踩 / s4 只要不破
    """
    top, bottom = float(z["top"]), float(z["bottom"])
    is_fvg = "fill" in z
    fill = float(z.get("fill", 0) or 0)

    # ① 不破：现在的判定
    if is_fvg:
        # 原口径：回补 100% 就算废
        broken_orig = fill >= 1.0
        if mode == "s2":
            # 大佬口径：补完了也不算废，只要现价还在缺口下沿之上（=收复了）
            broken_orig = fill >= 1.0 and px < bottom
    else:
        broken_orig = bool(z.get("invalid")) or px < bottom
        if mode == "s2":
            # OB 同理：收盘破过下沿，但现价已收回上方 → 仍算有效
            broken_orig = bool(z.get("invalid")) and px < bottom
    if broken_orig:
        return False

    # ② 已回踩
    if mode not in ("s3", "s4"):
        touched = (z.get("last_touch_idx") is not None) if is_fvg else bool(z.get("touched"))
        if not touched:
            return False

    # ③ 不走远（防追高）
    if atr <= 0:
        return False
    if (px - top) / atr > NEAR_CAP:
        return False

    # ④ 方向确认：阳线 / 收高 / 破前高 / 站上沿
    score = int(z.get("_score", 0))
    need = 1 if mode == "s4" else 2
    return score >= need


def score_bull(df, i, top, win=3) -> int:
    """方向确认分数（只用 <= i 数据）"""
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    c = df["close"].values.astype(float)
    if i < win + 1:
        return 0
    px = float(c[i])
    hh = float(np.nanmax(h[i - win:i]))
    return int(sum([px > o[i], px > float(c[i - 1]), px > hh, px >= top]))


# ───────────────────────── 回测主循环 ─────────────────────────
def backtest(df: pd.DataFrame, mode: str, min_bar: int = 100,
             cooldown: int = 20, one_pos: bool = True) -> dict:
    """cooldown: 同一 zone 触发后，多少根内不再重复触发（防同一缺口连续刷单）
    one_pos : True = 持仓互斥（一笔没走完不开新仓）"""
    n = len(df)
    trades = []
    used = {}            # zone key -> 上次触发的 bar index
    busy_until = -1      # 持仓互斥：这笔到哪根才走完
    for i in range(min_bar, n - 1):        # i 收盘触发，i+1 开盘入场
        if one_pos and i < busy_until:
            continue
        sub = df.iloc[: i + 1]
        try:
            fr = C.Frame(sub, 1)
            z = fr.zones(i)
            # ⚠️ 关键：zones() 默认把「已完全回补」的 FVG 过滤掉了，
            # 而 s2（大佬口径：补完后再收复仍算买点）必须拿到这些缺口，
            # 所以这里**额外**取一份 include_filled=True 的清单合并进来。
            if mode == "s2":
                z["fvgs"] = fr.fvgs(i, include_filled=True)
        except Exception:
            continue
        atr = z.get("atr") or 0
        if not atr:
            continue
        px = z["close"]
        cand = [x for x in (z.get("fvgs") or []) if x["dir"] == "bull"]
        cand += [x for x in (z.get("obs") or []) if x["dir"] == "bull"]
        # 去重：同一根 bar 只取「最近的一个」zone，避免重复计数
        hits = []
        for zz in cand:
            zz = dict(zz)
            key = (("fvg" if "fill" in zz else "ob"), zz["dir"],
                   round(float(zz["bottom"]), 4), round(float(zz["top"]), 4))
            last = used.get(key)
            if last is not None and i - last < cooldown:      # ★ 同一 zone 冷却期内不重复触发
                continue
            zz["_key"] = key
            zz["_score"] = score_bull(df, i, float(zz["top"]))
            if zone_ok_bull(zz, px, atr, mode):
                hits.append(zz)
        if not hits:
            continue
        # 取距现价最近的一个
        hits.sort(key=lambda x: abs(float(x.get("dist_pct") or 99)))
        zz = hits[0]
        used[zz["_key"]] = i
        entry = float(df["open"].iloc[i + 1])          # 次日开盘入场
        stop = entry - ATR_STOP * atr
        rec = {"i": i, "dt": str(df["dt"].iloc[i])[:16], "kind": zz["kind"] if "kind" in zz else ("fvg" if "fill" in zz else "ob"),
               "top": round(float(zz["top"]), 2), "bottom": round(float(zz["bottom"]), 2),
               "entry": round(entry, 2), "stop": round(stop, 2), "atr": round(float(atr), 2),
               "score": zz["_score"], "fill": float(zz.get("fill", 0) or 0)}
        # 前瞻收益 + 是否先打止损
        for w in FWD:
            j = i + 1 + w
            rec[f"r{w}"] = (float(df["close"].iloc[j]) / entry - 1) * 100 if j < n else np.nan
        # 止损命中：入场后 20 根内，最低价先破 stop，还是先到「20根收盘」离场
        seg_l = df["low"].values[i + 1: min(n, i + 1 + 20)]
        stop_j = None
        for k, lv in enumerate(seg_l):
            if float(lv) <= stop:
                stop_j = i + 1 + k
                break
        rec["stopped"] = stop_j is not None
        rec["stop_bar"] = stop_j
        # 持仓互斥：止损就在止损那根离场，否则持满 20 根
        busy_until = (stop_j + 1) if stop_j is not None else min(n, i + 1 + 20)
        # 止损后最大不利/有利
        rec["mfe"] = (float(np.nanmax(df["high"].values[i + 1: min(n, i + 1 + 20)])) / entry - 1) * 100 if i + 1 < n else np.nan
        rec["mae"] = (float(np.nanmin(df["low"].values[i + 1: min(n, i + 1 + 20)])) / entry - 1) * 100 if i + 1 < n else np.nan
        trades.append(rec)
    return summarize(trades, mode)


def summarize(trades: list[dict], mode: str) -> dict:
    out = {"mode": mode, "n": len(trades), "trades": trades}
    if not trades:
        return out
    for w in FWD:
        arr = np.array([t[f"r{w}"] for t in trades if not pd.isna(t[f"r{w}"])], float)
        if arr.size:
            out[f"r{w}_avg"] = round(float(arr.mean()), 2)
            out[f"r{w}_win"] = round(float((arr > 0).mean() * 100), 1)
            out[f"r{w}_med"] = round(float(np.median(arr)), 2)
    out["stop_rate"] = round(float(np.mean([t["stopped"] for t in trades]) * 100), 1)
    out["mfe_avg"] = round(float(np.nanmean([t["mfe"] for t in trades])), 2)
    out["mae_avg"] = round(float(np.nanmean([t["mae"] for t in trades])), 2)
    # 基准：同期买入持有
    return out


# ───────────────────────── 基准 ─────────────────────────
def benchmark(df: pd.DataFrame, min_bar: int = 100) -> dict:
    """同区间「无脑持有」的 3/5/10/20 根平均收益，作为对照"""
    out = {}
    for w in FWD:
        arr = []
        for i in range(min_bar, len(df) - 1 - w):
            arr.append((float(df["close"].iloc[i + 1 + w]) / float(df["open"].iloc[i + 1]) - 1) * 100)
        if arr:
            out[f"r{w}_avg"] = round(float(np.mean(arr)), 2)
            out[f"r{w}_win"] = round(float((np.array(arr) > 0).mean() * 100), 1)
    out["n"] = len(df) - min_bar
    return out


# ───────────────────────── 输出 ─────────────────────────
MODES = [("s1", "严格版（不破+回踩+方向2/4+不超1ATR）"),
         ("s2", "放宽回补（补完后再收复仍算，大佬口径）"),
         ("s3", "不回踩（不破+方向2/4）"),
         ("s4", "只要不破（不破+方向≥1）")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="AG0")
    ap.add_argument("--freq", default="1d", choices=["1d", "60m", "30m"])
    ap.add_argument("--since", default=None, help="只测这段时间之后，如 2026-01-01")
    ap.add_argument("--min-bar", type=int, default=100)
    ap.add_argument("--verbose", action="store_true", help="打印每笔交易")
    a = ap.parse_args()

    df = load(a.code, a.freq, a.since)
    print("=" * 78)
    print(f"标的 {a.code} · {a.freq} · {len(df)} 根 · "
          f"{str(df['dt'].iloc[0])[:10]} ~ {str(df['dt'].iloc[-1])[:10]}")
    bm = benchmark(df, a.min_bar)
    print(f"\n【基准·无脑持有】{bm.get('n')} 根样本")
    for w in FWD:
        if f"r{w}_avg" in bm:
            print(f"  +{w:>2}根: 平均 {bm[f'r{w}_avg']:+.2f}%  胜率 {bm[f'r{w}_win']:.1f}%")

    allres = []
    for mode, label in MODES:
        r = backtest(df, mode, a.min_bar)
        allres.append(r)
        print(f"\n【{label}】")
        print(f"  信号 {r['n']} 个 ｜ 止损率 {r.get('stop_rate', 0):.1f}% "
              f"｜ MFE均 {r.get('mfe_avg', 0):+.2f}% ｜ MAE均 {r.get('mae_avg', 0):+.2f}%")
        for w in FWD:
            if f"r{w}_avg" in r:
                print(f"  +{w:>2}根: 平均 {r[f'r{w}_avg']:+.2f}%  "
                      f"中位 {r[f'r{w}_med']:+.2f}%  胜率 {r[f'r{w}_win']:.1f}%")
        if a.verbose:
            print("  ── 明细 ──")
            for t in r["trades"]:
                print(f"    {t['dt']} {t['kind']:>3} {t['bottom']:.2f}~{t['top']:.2f} "
                      f"入{t['entry']:.2f} 分{t['score']}/4 回补{t['fill']*100:.0f}% "
                      f"→3根{t['r3']:+.2f}% 5根{t['r5']:+.2f}% 10根{t['r10']:+.2f}% 20根{t['r20']:+.2f}% "
                      f"{'止损' if t['stopped'] else ''}")

    # ── 结论 ──
    print("\n" + "=" * 78)
    print("【结论】各口径 5 根 / 10 根 平均收益 vs 基准")
    b5, b10 = bm.get("r5_avg", 0), bm.get("r10_avg", 0)
    for r, (mode, label) in zip(allres, MODES):
        if not r["n"]:
            print(f"  {mode}: 无信号")
            continue
        e5 = r.get("r5_avg", 0) - b5
        e10 = r.get("r10_avg", 0) - b10
        print(f"  {mode} {label[:12]:<12} n={r['n']:>3}  "
              f"5根 {r.get('r5_avg', 0):+.2f}%（超额 {e5:+.2f}%）  "
              f"10根 {r.get('r10_avg', 0):+.2f}%（超额 {e10:+.2f}%）")
    print("\n注：超额 = 信号收益 − 同期无脑持有收益；胜率按单笔正收益计，未扣交易成本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
