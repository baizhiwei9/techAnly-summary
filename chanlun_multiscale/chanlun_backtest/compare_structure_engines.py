#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结构引擎对比：远程 techAnly 分型版 vs 本地多尺度结构引擎
⚠️ 只读对比脚本：不修改实盘信号，不写状态文件。

上：远程 bin/chan_smc_index.py（merge_bars → fractals → build_bi → segs_from_bi → chan_read）
下：本地 chanlun_backtest/structure_indicators.py + structure_signals_v2（v2a 规则）

用法:
  python -m chanlun_backtest.compare_structure_engines
输出:
  reports/structure_engine_compare/<symbol>_<tf>.png
  reports/structure_engine_compare/summary.csv
"""
import os
import sys
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

# ── 定位远程引擎 bin/chan_smc_index.py ──
_REMOTE_CANDIDATES = [
    os.path.join(os.path.dirname(_HERE), "bin"),                    # workspace 布局
    os.path.join(os.path.dirname(os.path.dirname(_HERE)), "bin"),   # 仓库布局 repo/bin
    "/tmp/techAnly/bin",
]
REMOTE_BIN = None
for _p in _REMOTE_CANDIDATES:
    if os.path.exists(os.path.join(_p, "chan_smc_index.py")):
        REMOTE_BIN = _p
        sys.path.insert(0, _p)
        break
if REMOTE_BIN is None:
    raise SystemExit("找不到远程引擎 chan_smc_index.py")

import chan_smc_index as remote

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib import font_manager

from chanlun_backtest import structure_indicators as si
from chanlun_backtest.structure_signals_v2 import detect_structure_signals_v2

# ── 中文字体 ──
_FONTS = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
_fam = []
for f in _FONTS:
    if os.path.exists(f):
        try:
            font_manager.fontManager.addfont(f)
            _fam.append(font_manager.FontProperties(fname=f).get_name())
        except Exception:
            pass
matplotlib.rcParams["font.sans-serif"] = _fam + ["DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

OUT_DIR = os.path.join(os.path.dirname(_HERE), "reports", "structure_engine_compare")

COLOR = {
    "B1": "#1f77b4", "B2": "#ff7f0e", "B3": "#d62728",
    "S1": "#9467bd", "S2": "#8c564b", "S3": "#2ca02c",
}
BUY_KINDS = ("B1", "B2", "B3")
SELL_KINDS = ("S1", "S2", "S3")
TAG2KIND = {"一买": "B1", "二买": "B2", "三买": "B3",
            "一卖": "S1", "二卖": "S2", "三卖": "S3"}
KIND2TAG = {v: k for k, v in TAG2KIND.items()}


def resample_weekly(df):
    d = df.copy()
    d["dt"] = pd.to_datetime(d["timestamp"], errors="coerce")
    d = d.dropna(subset=["dt"]).set_index("dt")
    a = d.resample("W-FRI").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open", "close"])
    a = a.reset_index().rename(columns={"dt": "timestamp"})
    a["timestamp"] = a["timestamp"].dt.strftime("%Y-%m-%d")
    return a


def candles(ax, df, width=0.62):
    o = df["open"].values; h = df["high"].values
    l = df["low"].values; c = df["close"].values
    for i in range(len(df)):
        up = c[i] >= o[i]
        col = "#d62728" if up else "#2ca02c"   # A股红涨绿跌
        ax.plot([i, i], [l[i], h[i]], color=col, lw=0.55, zorder=2)
        body_low = min(o[i], c[i])
        body_h = max(abs(c[i] - o[i]), (h[i] - l[i]) * 0.012)
        ax.add_patch(Rectangle((i - width / 2, body_low), width, body_h,
                               facecolor=col, edgecolor=col, lw=0.4, zorder=3))


# ───────────── 远程引擎 ─────────────
def merge_with_map(bars):
    """与 chan_smc_index.merge_bars 完全相同的逻辑，附带 merged→原始K线 映射"""
    out = []
    for i, (h, l) in enumerate(bars):
        if not out:
            out.append({"h": h, "l": l, "s": i, "e": i})
            continue
        ph, pl = out[-1]["h"], out[-1]["l"]
        if (h <= ph and l >= pl) or (h >= ph and l <= pl):
            up = len(out) < 2 or out[-1]["h"] >= out[-2]["h"]
            if up:
                out[-1]["h"] = max(ph, h); out[-1]["l"] = max(pl, l)
            else:
                out[-1]["h"] = min(ph, h); out[-1]["l"] = min(pl, l)
            out[-1]["e"] = i
        else:
            out.append({"h": h, "l": l, "s": i, "e": i})
    return out


def remote_engine(df, tag="day"):
    """复刻 chan_read 的笔/中枢/买卖点，并保留 merged 索引用于画图"""
    bars = list(zip(df["high"].astype(float).values,
                    df["low"].astype(float).values))
    mb = remote.merge_bars(bars)
    mmap = merge_with_map(bars)
    if len(mmap) != len(mb):
        raise RuntimeError("merged 映射与远程引擎不一致: %d vs %d" % (len(mmap), len(mb)))
    for a, m in zip(mb, mmap):
        if abs(a[0] - m["h"]) > 1e-9 or abs(a[1] - m["l"]) > 1e-9:
            raise RuntimeError("merged K线 h/l 与远程引擎不一致")

    bi = remote.build_bi(mb)
    segs = remote.segs_from_bi(bi)
    out = {"ok": len(mb) >= 12 and len(bi) >= 4, "mb": mb, "mmap": mmap,
           "bi": bi, "segs": segs, "zs": None, "zs_i": None,
           "signals": [], "out": None}
    if not out["ok"]:
        return out

    # 仍调用一次远程 chan_read，用于交叉校验买卖点清单
    out["out"] = remote.chan_read(bars, tag)

    approx = np.array([(h + l) / 2 for h, l in mb], float)
    hist, _dif, _dea = remote.macd_hist(approx)
    amp = lambda s: abs(s["to"] - s["from"])

    def pw(s, up):
        a, b = max(0, s["i0"] - 1), min(len(hist) - 1, s["i1"])
        if b <= a:
            return 0.0
        seg = hist[a:b]
        return float(seg.max() if up else -seg.min())

    # 中枢：最后 3 段重叠（与 chan_read 相同）
    for i in range(len(segs) - 3, -1, -1):
        w = segs[i:i + 3]
        zg = min(x["hi"] for x in w)
        zd = max(x["lo"] for x in w)
        if zg > zd:
            out["zs"] = (zd, zg); out["zs_i"] = i
            break

    dn_div = []
    for k, s in enumerate(segs):
        if s["up"]:
            continue
        same = [j for j in range(k) if not segs[j]["up"]]
        if not same:
            continue
        p = segs[same[-1]]
        if s["to"] < p["to"] and (amp(s) < amp(p) or pw(s, False) < pw(p, False)):
            dn_div.append(k)

    buys = []   # (tag, price, merged_idx)
    if dn_div:
        k0 = dn_div[-1]
        buys.append(("一买", round(segs[k0]["to"], 2), segs[k0]["i1"]))
        for k in range(k0 + 1, len(segs)):
            if not segs[k]["up"] and segs[k]["to"] > segs[k0]["to"]:
                buys.append(("二买", round(segs[k]["to"], 2), segs[k]["i1"]))
                break

    up_div = []
    for k, s in enumerate(segs):
        if not s["up"]:
            continue
        same = [j for j in range(k) if segs[j]["up"]]
        if not same:
            continue
        p = segs[same[-1]]
        if s["to"] > p["to"] and (amp(s) < amp(p) or pw(s, True) < pw(p, True)):
            up_div.append(k)

    sells = []  # (tag, price, merged_idx)
    if up_div:
        k0 = up_div[-1]
        sells.append(("一卖", round(segs[k0]["to"], 2), segs[k0]["i1"]))
        for k in range(k0 + 1, len(segs)):
            if segs[k]["up"] and segs[k]["to"] < segs[k0]["to"]:
                sells.append(("二卖", round(segs[k]["to"], 2), segs[k]["i1"]))
                break

    if out["zs"]:
        zd, zg = out["zs"]; zs_i = out["zs_i"]
        dleaf = [k for k in range(zs_i + 3, len(segs) - 1)
                 if not segs[k]["up"] and segs[k]["lo"] < zd]
        for k in dleaf:
            r = segs[k + 1]
            if r["up"] and r["hi"] < zd:
                sells.append(("三卖", round(r["hi"], 2), r["i1"]))
                break
        uleaf = [k for k in range(zs_i + 3, len(segs) - 1)
                 if segs[k]["up"] and segs[k]["hi"] > zg]
        for k in uleaf:
            r = segs[k + 1]
            if not r["up"] and r["lo"] > zg:
                buys.append(("三买", round(r["lo"], 2), r["i1"]))
                break

    # 与 chan_read 输出交叉校验（tag + price）
    chk = out["out"]
    if chk.get("ok"):
        want_b = sorted((t, p) for t, p in chk.get("buys", []))
        want_s = sorted((t, p) for t, p in chk.get("sells", []))
        got_b = sorted((t, p) for t, p, _ in buys)
        got_s = sorted((t, p) for t, p, _ in sells)
        if want_b != got_b or want_s != got_s:
            raise RuntimeError("远程买卖点复刻不一致: buys %s vs %s; sells %s vs %s"
                               % (got_b, want_b, got_s, want_s))

    def x_of(mi):
        m = mmap[max(0, min(mi, len(mmap) - 1))]
        return (m["s"] + m["e"]) // 2

    for t, p, mi in buys + sells:
        out["signals"].append({"kind": TAG2KIND[t], "tag": t, "price": float(p),
                               "x": x_of(mi)})
    out["signals"].sort(key=lambda s: s["x"])
    return out


# ───────────── 本地引擎 ─────────────
def local_engine(df):
    d = si.calculate_structure_indicators(df.copy())
    bi = d.attrs.get("bi_ends") or []
    zs_list = d.attrs.get("zhongshus") or []
    sigs = detect_structure_signals_v2(d, b1_mode="v1")   # v2a = 新版规则
    return d, bi, zs_list, sigs


# ───────────── 画图 ─────────────
def plot_symbol(sym, name, tf, df, window):
    n = len(df)
    start = max(0, n - window)
    wdf = df.iloc[start:].reset_index(drop=True)

    rem = remote_engine(df, tag="day" if tf == "daily" else "week")
    ldf, lbi, lzs, lsigs = local_engine(df)

    # ── 窗口内信号 ──
    rem_sigs = [s for s in rem["signals"] if s["x"] >= start]
    loc_sigs = [dict(s) for s in lsigs if s["idx"] >= start]

    def counts(sigs, key="kind"):
        c = {k: 0 for k in BUY_KINDS + SELL_KINDS}
        for s in sigs:
            if s[key] in c:
                c[s[key]] += 1
        return c

    cr, cl = counts(rem_sigs), counts(loc_sigs)

    fig, axes = plt.subplots(2, 1, figsize=(16, 11), sharex=True)
    panels = [
        (axes[0], "remote", cr),
        (axes[1], "local", cl),
    ]
    for ax, eng, cc in panels:
        candles(ax, wdf)
        if eng == "remote":
            # 笔
            if rem["ok"]:
                xs = [((rem["mmap"][max(0, min(b[2], len(rem["mmap"]) - 1))]["s"] +
                        rem["mmap"][max(0, min(b[2], len(rem["mmap"]) - 1))]["e"]) // 2
                       - start) for b in rem["bi"]]
                ys = [b[1] for b in rem["bi"]]
                ax.plot(xs, ys, color="#2c7fb8", lw=1.05, zorder=4, alpha=0.9)
                # 当前中枢：最近 3 段重叠，右侧延伸到最新一笔
                if rem["zs"] is not None:
                    zd, zg = rem["zs"]
                    x0 = xs[rem["zs_i"]]
                    x1 = xs[-1]
                    if x1 < x0:
                        x0, x1 = x1, x0
                    if x1 >= 0 and x0 <= len(wdf) - 1:
                        xa = max(x0, 0); xb = min(x1, len(wdf) - 1)
                        if xb <= xa:
                            xb = xa + 1
                        ax.add_patch(Rectangle(
                            (xa, zd), xb - xa, max(zg - zd, 1e-9),
                            facecolor="#f1c40f", alpha=0.13,
                            edgecolor="#b7950b", lw=0.6, zorder=1))
                        ax.text(xa + 2, zg, "中枢 %.2f~%.2f" % (zd, zg),
                                fontsize=8, color="#7d6608", va="bottom", zorder=6)
            title = ("① 远程 techAnly 引擎（chan_smc_index.chan_read）｜"
                     "合并K线 %d 根、笔端点 %d 个、当前中枢 1 个"
                     % (len(rem["mb"]), len(rem["bi"])))
            ax.plot([], [], color="#2c7fb8", lw=1.4, label="远程笔")
            for s in rem_sigs:
                xi = s["x"] - start
                _marker(ax, wdf, xi, s["kind"], s["tag"])
        else:
            # 本地笔
            if len(lbi) >= 2:
                xs = [b.kline_idx - start for b in lbi]
                ys = [b.price for b in lbi]
                ax.plot(xs, ys, color="#d95f02", lw=1.05, zorder=4, alpha=0.9)
            # 本地中枢（全部，最新一个加深）
            for zi, zs in enumerate(lzs):
                try:
                    x0 = lbi[zs.start_bi].kline_idx
                    x1 = lbi[zs.end_bi].kline_idx
                except Exception:
                    continue
                if x1 < start or x0 > n - 1:
                    continue
                latest = (zi == len(lzs) - 1)
                ax.add_patch(Rectangle(
                    (max(x0, start) - start, zs.zd), max(1, min(x1, n - 1) - max(x0, start)),
                    max(zs.zg - zs.zd, 1e-9),
                    facecolor="#f1c40f", alpha=0.16 if latest else 0.07,
                    edgecolor="#b7950b", lw=0.6 if latest else 0.35, zorder=1))
                if latest:
                    ax.text(max(x0, start) - start + 2, zs.zg,
                            "最新中枢 %.2f~%.2f" % (zs.zd, zs.zg),
                            fontsize=8, color="#7d6608", va="bottom", zorder=6)
            title = ("② 本地多尺度结构引擎（structure_indicators + v2a）｜"
                     "合并K线 %d 根、笔端点 %d 个、中枢 %d 个"
                     % (len(ldf.attrs.get("merged") or []), len(lbi), len(lzs)))
            ax.plot([], [], color="#d95f02", lw=1.4, label="本地笔")
            for s in loc_sigs:
                _marker(ax, wdf, s["idx"] - start, s["kind"], KIND2TAG[s["kind"]])

        stat = "  ".join("%s=%d" % (KIND2TAG[k], cc[k]) for k in BUY_KINDS + SELL_KINDS)
        ax.set_title("%s ｜ 窗口内: %s" % (title, stat), fontsize=11.5, loc="left")
        ax.set_ylabel("价格")
        ax.grid(alpha=0.18, lw=0.5)
        ax.set_xlim(-1, len(wdf))
        ax.legend(loc="upper left", fontsize=8, framealpha=0.85)
        lo = wdf["low"].min(); hi = wdf["high"].max()
        pad = (hi - lo) * 0.10
        ax.set_ylim(lo - pad, hi + pad)

    step = max(1, len(wdf) // 9)
    ticks = list(range(0, len(wdf), step))
    labels = [str(wdf["timestamp"].iloc[i])[:10] for i in ticks]
    axes[1].set_xticks(ticks)
    axes[1].set_xticklabels(labels, rotation=25, ha="right", fontsize=8)

    fig.suptitle("结构引擎对比｜%s（%s）%s｜最近 %d 根K线｜黄框=中枢｜上=远程引擎 下=本地引擎"
                 % (name, sym, tf, len(wdf)), fontsize=15, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    fn = os.path.join(OUT_DIR, "%s_%s.png" % (sym.replace(".", "_"), tf))
    fig.savefig(fn, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return {
        "remote_merged": len(rem["mb"]), "remote_bi": len(rem["bi"]),
        "remote_zs": 1 if rem["zs"] else 0,
        "local_merged": len(ldf.attrs.get("merged") or []), "local_bi": len(lbi),
        "local_zs": len(lzs),
        "remote_sig": cr, "local_sig": cl, "chart": fn,
    }


def _marker(ax, wdf, xi, kind, tag):
    if xi < 0 or xi >= len(wdf) or kind not in COLOR:
        return
    if kind in BUY_KINDS:
        y = wdf["low"].iloc[xi] * 0.982
        ax.scatter([xi], [y], marker="^", s=95, color=COLOR[kind],
                   edgecolor="white", linewidth=0.6, zorder=6)
        ax.text(xi, y * 0.992, tag, ha="center", va="top",
                fontsize=7.2, color=COLOR[kind], fontweight="bold", zorder=7)
    else:
        y = wdf["high"].iloc[xi] * 1.018
        ax.scatter([xi], [y], marker="v", s=75, color=COLOR[kind],
                   edgecolor="white", linewidth=0.6, zorder=6, alpha=0.85)
        ax.text(xi, y * 1.008, tag, ha="center", va="bottom",
                fontsize=7.0, color=COLOR[kind], zorder=7)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    sys.path.insert(0, os.path.dirname(_HERE))
    from data_fetcher import get_index_daily

    symbols = [
        ("sh510300", "沪深300ETF", "daily", 320),
        ("sz159915", "创业板ETF", "daily", 320),
        ("sh510300", "沪深300ETF", "weekly", 160),
    ]

    rows = []
    cache = {}
    for sym, name, tf, window in symbols:
        if sym not in cache:
            bars = get_index_daily(sym)
            d = pd.DataFrame(bars)
            for c in ["open", "high", "low", "close", "volume"]:
                d[c] = pd.to_numeric(d[c], errors="coerce")
            d = d.dropna(subset=["close"]).reset_index(drop=True)
            if "datetime" in d.columns:
                d = d.rename(columns={"datetime": "timestamp"})
            cache[sym] = d
        df = cache[sym] if tf == "daily" else resample_weekly(cache[sym])
        if len(df) < 120:
            print("跳过 %s %s：数据不足 (%d)" % (sym, tf, len(df)))
            continue
        r = plot_symbol(sym, name, tf, df, window)
        for eng in ("remote", "local"):
            cc = r[eng + "_sig"]
            rows.append({
                "symbol": sym, "name": name, "tf": tf, "engine": eng,
                "merged_bars": r[eng + "_merged"], "bi_points": r[eng + "_bi"],
                "zhongshu_used": r[eng + "_zs"],
                "B1": cc["B1"], "B2": cc["B2"], "B3": cc["B3"],
                "S1": cc["S1"], "S2": cc["S2"], "S3": cc["S3"],
                "total": sum(cc.values()),
                "chart": os.path.relpath(r["chart"], os.path.dirname(OUT_DIR)),
            })
        print("[chart] %s %s | 远程 merged=%d bi=%d zs=%d %s | 本地 merged=%d bi=%d zs=%d %s -> %s"
              % (sym, tf, r["remote_merged"], r["remote_bi"], r["remote_zs"], r["remote_sig"],
                 r["local_merged"], r["local_bi"], r["local_zs"], r["local_sig"], r["chart"]))

    if rows:
        pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "summary.csv"),
                                  index=False, encoding="utf-8-sig")
    print("输出目录:", OUT_DIR)


if __name__ == "__main__":
    main()
