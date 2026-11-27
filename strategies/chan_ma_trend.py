#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论·均线版（趋势浪子口径）—— 级别定位 + MA10穿MA20 买卖点

大佬 2026-11-08 16:xx 定稿口径：
  ① **级别跟着看的级别走**：日线版就用日线、周线版就用周线。
     ❌ 不许自作主张降到 4 小时 / 60 分钟（那是 langzi_scan 的老毛病）。
  ② **买卖点按分型版对应关系**：分型版看「笔」，均线版的「笔」= **MA10 上穿 MA20**。
     分型版：笔 = 相邻两个确认分型的连线；均线版：MA10 穿 MA20 = 笔的拐头。
     所以 买点 = MA10 上穿 MA20（刚穿，look 根内），卖点 = MA10 下穿 MA20。
  ③ **参数铁律**：均线 MA10/20/40/80/160，MACD(10,20,5)。
  ④ **不做多周期共振** —— 只报「现在到了哪个级别、朝哪个方向」。
     级别定位：笔级别(MA10/20) → 段/中枢级别(MA40/80) → 大周期(MA160)。

用法：
  python3 strategies/chan_ma_trend.py                    # 默认 5 宽基 × 日线
  python3 strategies/chan_ma_trend.py --level week       # 周线
  python3 strategies/chan_ma_trend.py --level day --code 000300.SH
  python3 strategies/chan_ma_trend.py --level week --text
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
from datetime import datetime

# 容器 IPv6 不通 → 强制 IPv4（全局硬规矩）
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import pandas as pd  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 参数铁律（趋势浪子口径，勿改） ──
# 大佬 2026-11-08 定的**层级链路**（笔→线段→分型，逐级放大）：
#   15:56 口径：笔 = MA10 穿 MA20
#   16:xx 口径：线段 = MA40 穿 MA80，分型 = MA80 交叉 MA160
#   → 两句话合起来正好是 10/20/40/80/160 的倍增链，中间 20/40 补成「次级」层
#   任何一层金叉/死叉，都代表走势升级/降级到对应层级
LINK = [
    ("笔",   10, 20),
    ("次级", 20, 40),
    ("线段", 40, 80),
    ("分型", 80, 160),
]
MA_FAST, MA_SLOW = LINK[0][1], LINK[0][2]      # 笔 = 10/20（买点口径用这条）
MA_BIG = 160
MAS = [10, 20, 40, 80, 160]
MACD_F, MACD_S, MACD_SIG = 10, 20, 5

IDX = [
    ("沪深300", "000300.SH", "sh000300"),
    ("中证500", "000905.SH", "sh000905"),
    ("中证1000", "000852.SH", "sh000852"),
    ("创业板指", "399006.SZ", "sz399006"),
    ("科创50", "000688.SH", "sh000688"),
]

LEVEL_TXT = {"day": "日线", "week": "周线"}


# ───────────────────────── 指标 ─────────────────────────
def add_ind(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for n in MAS:
        d[f"ma{n}"] = d["close"].rolling(n).mean()
    ef = d["close"].ewm(span=MACD_F, adjust=False).mean()
    es = d["close"].ewm(span=MACD_S, adjust=False).mean()
    d["dif"] = ef - es
    d["dea"] = d["dif"].ewm(span=MACD_SIG, adjust=False).mean()
    d["hist"] = (d["dif"] - d["dea"]) * 2
    return d


def resample_week(df: pd.DataFrame, dt_col="dt") -> pd.DataFrame:
    """日线 → 周线（W-FRI，与 chan_smc_index 同口径）"""
    d = df.set_index(dt_col)
    o = pd.DataFrame({
        "open": d["open"].resample("W-FRI").first(),
        "high": d["high"].resample("W-FRI").max(),
        "low": d["low"].resample("W-FRI").min(),
        "close": d["close"].resample("W-FRI").last(),
    }).dropna().reset_index()
    return o.rename(columns={o.columns[0]: dt_col})


# ───────────────────── 级别定位（不做共振） ─────────────────────
def locate_level(row) -> dict:
    """按层级链路逐层定位：笔(10/20) → 线段(20/40) → 分型(40/80)，MA160 作大级别背景。

    每层只看自己的那对均线：短在长上=向上，短在长下=向下。
    主级别 = 最高一层仍「翻不上来」的位置（即价格卡在哪一层）。
    """
    c = float(row["close"])
    if any(pd.isna(float(row["ma{}".format(n)])) for n in MAS):
        return {"level": "数据不足", "dir": "—", "layers": [], "detail": ""}

    layers = []
    for name, f, sl in LINK:
        mf, ms = float(row[f"ma{f}"]), float(row[f"ma{sl}"])
        d = "向上" if mf > ms else ("向下" if mf < ms else "纠缠")
        layers.append({"name": name, "fast": f, "slow": sl, "dir": d,
                       "fast_v": round(mf, 2), "slow_v": round(ms, 2)})

    m160 = float(row["ma160"])
    big = "之上" if c > m160 else ("之下" if c < m160 else "附近")

    # 主级别 = 最高一层已翻上的层（笔→线段→分型 逐层升级）
    # 三层全向下 = 还没成笔，就是空头，不能因为价格在 MA10 上方就说「向上」
    main, main_dir = "空头（各层全向下）", "向下"
    for L in reversed(layers):            # 分型 → 线段 → 笔，取最高的向上层
        if L["dir"] == "向上":
            main, main_dir = L["name"] + "级别", "向上"
            break

    # 方向结论：以「笔」为准（最灵敏），但要标注各层是否一致
    pen_dir = layers[0]["dir"]
    seg_dir = layers[1]["dir"]
    fxx_dir = layers[2]["dir"]
    agree = len({L["dir"] for L in layers}) == 1

    detail = " ｜ ".join(f"{L['name']}({L['fast']}/{L['slow']})={L['dir']}" for L in layers)
    detail += f" ｜ 价在MA160{big}"
    if agree:
        detail += " ｜ 各层同向"

    return {"level": main, "dir": main_dir, "pen_dir": pen_dir,
            "seg_dir": seg_dir, "fxx_dir": fxx_dir, "layers": layers,
            "big": big, "agree": agree, "detail": detail}


# ─────────────── 买卖点：MA10 穿 MA20（= 均线版的「笔」） ───────────────
def ma_cross_signal(d: pd.DataFrame, look: int = 6, fast=None, slow=None) -> dict | None:
    """均线穿越 → 买卖点。默认用「笔」= MA10 穿 MA20。

    层级链路（大佬口径）：笔=10/20、线段=20/40、分型=40/80。
    哪个级别先金叉，就代表走势升级到哪一层。
    """
    f = int(fast or MA_FAST)
    sl = int(slow or MA_SLOW)
    if len(d) < sl + look + 2:
        return None
    diff = (d["ma%d" % f] - d["ma%d" % sl]).values
    n = len(diff)
    for k in range(1, min(look + 1, n - 1)):
        i = n - k                     # 当前往前第 k 根
        a, b = diff[i - 1], diff[i]
        if pd.isna(a) or pd.isna(b):
            continue
        if a <= 0 < b:
            return {"type": "买点", "cross": f"金叉（MA{f} 上穿 MA{sl}）", "bars_ago": k - 1,
                    "px": float(d["close"].iloc[i]), "dt": str(d["dt"].iloc[i])[:10],
                    "pair": (f, sl)}
        if a >= 0 > b:
            return {"type": "卖点", "cross": f"死叉（MA{f} 下穿 MA{sl}）", "bars_ago": k - 1,
                    "px": float(d["close"].iloc[i]), "dt": str(d["dt"].iloc[i])[:10],
                    "pair": (f, sl)}
    return None


def cross_ladder(d: pd.DataFrame, look: int = 6) -> list:
    """三层穿越全部查一遍：笔 / 线段 / 分型，哪层刚拐头就报哪层。"""
    out = []
    for name, f, sl in LINK:
        sig = ma_cross_signal(d, look=look, fast=f, slow=sl)
        if sig:
            out.append(dict(sig, layer=name))
    return out


def macd_state(d: pd.DataFrame) -> str:
    if len(d) < 3:
        return "—"
    h = d["hist"].iloc[-3:].values
    if h[-1] > 0 and h[-2] <= 0:
        return "金叉初起"
    if h[-1] < 0 and h[-2] >= 0:
        return "死叉初起"
    if h[-1] > h[-2] > 0:
        return "红柱放大"
    if h[-1] < h[-2] < 0:
        return "绿柱放大"
    if h[-1] > h[-2] and h[-1] < 0:
        return "绿柱缩短（底背征兆）"
    if h[-1] < h[-2] and h[-1] > 0:
        return "红柱缩短（顶背征兆）"
    return "震荡"


# ───────────────────────── 数据 ─────────────────────────
def load_index(ts_code: str) -> pd.DataFrame | None:
    """读日线：优先 tushare，失败回退本地缓存。"""
    cache = os.path.join(WS, ".cache", f"ma_trend_{ts_code.replace('.', '_')}_d.csv")
    try:
        import tushare_init as ti
        pro = ti.get_pro()
        for _ in range(3):
            try:
                df = pro.index_daily(ts_code=ts_code, start_date="20200101")
                if df is not None and len(df) > 200:
                    df = df.rename(columns={"trade_date": "dt"})
                    df["dt"] = pd.to_datetime(df["dt"])
                    df = df.sort_values("dt").reset_index(drop=True)
                    try:
                        df.to_csv(cache, index=False)
                    except Exception:
                        pass
                    return df[["dt", "open", "high", "low", "close"]]
            except Exception:
                pass
    except Exception:
        pass
    if os.path.exists(cache):
        try:
            df = pd.read_csv(cache, parse_dates=["dt"])
            if len(df) > 200:
                return df
        except Exception:
            pass
    return None


# ───────────────────────── 渲染 ─────────────────────────
def analyse(name: str, ts_code: str, level: str = "day") -> dict | None:
    raw = load_index(ts_code)
    if raw is None:
        return None
    # ★ 级别跟随：日线版用日线，周线版用周线 —— 不降到 4h/60m
    d = raw if level == "day" else resample_week(raw)
    if len(d) < MA_BIG + 10:
        return None
    d = add_ind(d)
    last = d.iloc[-1]
    loc = locate_level(last)
    sig = ma_cross_signal(d)
    ladder = cross_ladder(d)
    return {
        "name": name, "code": ts_code, "level": level,
        "dt": str(d["dt"].iloc[-1])[:10], "close": float(last["close"]),
        "n": len(d), "loc": loc, "sig": sig, "ladder": ladder,
        "macd": macd_state(d), "hist": float(last["hist"]),
        "ma": {f"ma{k}": (None if pd.isna(last[f"ma{k}"]) else round(float(last[f"ma{k}"]), 2))
               for k in MAS},
    }


def render(results: list[dict], level: str) -> str:
    lt = LEVEL_TXT.get(level, level)
    L = [f"📈 缠论·均线版（趋势浪子口径）· {lt} · {datetime.now():%Y-%m-%d %H:%M}"]
    L.append(f"参数：MA {'/'.join(map(str, MAS))} ｜ MACD({MACD_F},{MACD_S},{MACD_SIG})")
    L.append("层级链路（倍增）：笔=MA10穿MA20 ｜ 次级=MA20穿MA40 ｜ 线段=MA40穿MA80 ｜ 分型=MA80穿MA160")
    L.append(f"级别跟随本报告级别（{lt}版就跑{lt}），不做多周期共振")
    L.append("")
    buys, sells, ladders = [], [], []
    for r in results:
        loc = r["loc"]
        L.append(f"【{r['name']}·{lt}】收 {r['close']:.2f}（{r['dt']}）")
        L.append(f"  当前级别 = {loc['level']} · {loc['dir']}")
        L.append(f"  档位：{loc['detail']}")
        L.append(f"  MACD({MACD_F},{MACD_S},{MACD_SIG})={r['macd']}（HIST {r['hist']:+.2f}）")
        lad = r.get("ladder") or []
        if lad:
            for g in lad:
                L.append(f"  {'🌙' if g['type'] == '买点' else '⭐'} [{g['layer']}] {g['type']}："
                         f"{g['cross']} @{g['px']:.2f}（{g['bars_ago']} 根前，{g['dt']}）")
                (buys if g["type"] == "买点" else sells).append(
                    f"{r['name']}[{g['layer']}]@{g['px']:.2f}")
                ladders.append(g["layer"])
        else:
            L.append("  穿越：近 6 根内各层均未拐头（无金叉/死叉）")
        L.append("")
    L.append("── 汇总 ──")
    L.append("🌙 买点（金叉）：" + ("、".join(buys) if buys else "无"))
    L.append("⭐ 卖点（死叉）：" + ("、".join(sells) if sells else "无"))
    if ladders:
        from collections import Counter
        c = Counter(ladders)
        L.append("  触发层级：" + "、".join(f"{k}×{v}" for k, v in c.most_common()))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="day", choices=["day", "week"], help="级别：日线/周线（默认日线）")
    ap.add_argument("--code", help="只跑一个指数（ts_code），如 000300.SH")
    ap.add_argument("--text", action="store_true", help="只打印文本")
    ap.add_argument("--out", help="存文件路径")
    a = ap.parse_args()

    items = IDX
    if a.code:
        items = [x for x in IDX if x[1] == a.code] or [(a.code, a.code, "")]
    res = []
    for name, ts, _ in items:
        try:
            r = analyse(name, ts, a.level)
            if r:
                res.append(r)
            else:
                print(f"[WARN] {name} 数据不足，跳过", flush=True)
        except Exception as e:
            print(f"[ERR] {name}: {str(e)[:120]}", flush=True)
    if not res:
        print("无结果", file=sys.stderr)
        return 1
    txt = render(res, a.level)
    print(txt)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt)
        print(f"\n[OK] 已存 {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
