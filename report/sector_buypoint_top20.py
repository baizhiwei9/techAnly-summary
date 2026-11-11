#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指定行业 → 技术分析汇总四层买点 → 个股 Top20 + 出图

大佬 2026-11-11 需求：
  「用技术分析汇总分析影视传媒这两个行业，选出符合条件有买点的股票前 20，图也画出来」

流程（完全复用 tech-summary 技能口径，不另起一套）：
  ① 行业自身：申万指数日线 → 缠论分型版买点（三买>一买>二买），周线一二三买做方向参考
  ② 全成分股：东财/缓存日线（900 根，够算周线）
       硬门槛：现价 ≥ VWAP −1σ（跌破止损线直接淘汰）
       买点：周线 一买/二买/三买 都算；日线只认三买  → ★ 双级别共振优先
       再叠 均线版买点 / MA20 / MA160 / 多头排列 做质量分
  ③ 排序取 Top20，出图（每只一个子图：K线 + 缠论分型版 + VWAP 六轨 + 量）

全流程纯 Python，0 token。

用法：
  python3 bin/sector_buypoint_top20.py --ind 传媒 --ind 影视院线
  python3 bin/sector_buypoint_top20.py --ind 传媒,影视院线 --top 20 --chart --out /tmp/x.md
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# ── 全局硬规矩：禁 IPv6（容器 IPv6 不通会静默卡死）──
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

def _repo_root(start):
    """向上探测仓库根：同时含 bin/ 与 core/ 的那一层（2026-11-12 目录整理后可任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "core")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
# 顺序有讲究：最后插入的优先级最高 → bin/ 放最前 = 最低优先级，
# 保证 import 命中真实目录，而不是 bin/ 里的兼容 shim。
for _sd in ("bin", "", "core", "chanlun/fractal", "chanlun/ma", "chanlun/multiscale",
            "chanlun/multiscale/vendor", "report", "misc", "wavelet", "smc"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import chan_smc_index as C            # noqa: E402
import chan_ma_trend_v2 as M          # noqa: E402
import tech_summary as TS             # noqa: E402
import tech_buypoint as TB            # noqa: E402
import smc_core as S                  # noqa: E402
from tech_intraday_buypoint import (  # noqa: E402
    to_df, board_members, weekly_buys, WEEK_BARS, STOCK_VWAP_SIGMA,
)

CHART_DIR = os.path.join(WS, ".cache/charts")
os.makedirs(CHART_DIR, exist_ok=True)
SIGN = "📎 来自 OpenClaw.Deepseek"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ═══════════════════ 一、行业（板块）自身买点 ═══════════════════
def sector_analyse(name):
    """申万行业指数：日线缠论买点（主判定）+ 周线买点（方向）"""
    swmap = TB.load_sw_index_map()
    code = swmap.get(name) or swmap.get(TB.strip_roman(name))
    if not code:
        return {"ok": False, "name": name, "err": "找不到申万行业指数代码"}, None
    d = TB.sector_index_daily("industry", code, name)
    if d is None or len(d) < 120:
        return {"ok": False, "name": name, "code": code, "err": "行业指数数据不足"}, None

    cl = d["close"].astype(float).values
    close = float(cl[-1])
    prev = float(cl[-2]) if len(cl) > 1 else close
    ma20 = TS.ma20_state(d)
    fx = C.chan_read(C.bars_of(d), "day")
    dd = M.add_ind(d)
    al = M.ma_alignment(dd)

    tb = TB.find_third_buys(d, cl)
    yb, eb = [], []
    for b in (fx.get("buy_list") or []):
        if b.get("tag") == "一买":
            yb.append(TB._gate_y1(b, close, ma20))
        elif b.get("tag") == "二买":
            yb.append(TB._gate_y2(b, close, ma20))
    cands = sorted(tb + yb + eb, key=lambda x: (not x["accept"], x["pri"], x.get("age", 0)))
    best = next((x for x in cands if x["accept"]), None)
    sells = TB.find_sells(d, fx, close, ma20)
    best_sell = next((x for x in sells if x["accept"]), None)

    wk_acc, _wz, wk_all = weekly_buys(d, close)
    best_wk = wk_acc[0] if wk_acc else None

    vw = S.vwap_tracks(d)
    v, sg = float(vw["vwap"][-1]), float(vw["sigma"][-1])
    z = (close - v) / sg if sg > 0 else 0.0

    sec = {
        "ok": True, "name": name, "code": code,
        "dt": str(d["dt"].iloc[-1])[:10], "close": close, "pct": (close / prev - 1) * 100,
        "stage": fx.get("stage"), "zpos": fx.get("zpos"), "zs": fx.get("zs"),
        "align": al.get("state"), "ma20": ma20,
        "buys": cands, "best": best,
        "sells": sells, "best_sell": best_sell,
        "wk": best_wk,
        "wk_all": [(x["tag"], x["level"], x.get("age", 0)) for x in wk_all if x.get("accept")],
        "vw_z": z, "vw_dir": str(vw["ma_dir"][-1]),
        "above_ma20": (close > float(dd["ma20"].iloc[-1])) if pd.notna(dd["ma20"].iloc[-1]) else None,
    }
    return sec, d


# ═══════════════════ 二、个股买点 ═══════════════════
def analyse_stock(code, name, ind, d):
    if d is None or len(d) < 120:
        return None
    cl = d["close"].astype(float).values
    close = float(cl[-1])
    prev = float(cl[-2])
    pct = (close / prev - 1) * 100 if prev else 0.0

    vw = S.vwap_tracks(d)
    v, sg = float(vw["vwap"][-1]), float(vw["sigma"][-1])
    if not (v > 0):
        return None
    z = (close - v) / sg if sg > 0 else 0.0
    above_vwap = bool(z >= STOCK_VWAP_SIGMA)
    if not above_vwap:
        return {"code": code, "name": name, "ind": ind, "close": close, "pct": pct,
                "above_vwap": False, "vw_z": z, "vw_dir": str(vw["ma_dir"][-1]),
                "vwap": v, "tier": 99, "n_res": 0}

    wk_acc, _wz, wk_all = weekly_buys(d, close)
    best_wk = wk_acc[0] if wk_acc else None
    dtb = [x for x in TB.find_third_buys(d, cl) if x.get("accept")]
    dtb.sort(key=lambda x: (x["pri"], x.get("age", 999)))
    best_d = dtb[0] if dtb else None

    dd = M.add_ind(d)
    sigs = M.signals(dd)
    ma_buy = next((s for s in sigs if s["type"] == "买点"), None)
    ma_sell = next((s for s in sigs if s["type"] == "卖点"), None)
    align = M.ma_alignment(dd)
    ma20v = float(dd["ma20"].iloc[-1]) if pd.notna(dd["ma20"].iloc[-1]) else None
    ma160v = float(dd["ma160"].iloc[-1]) if pd.notna(dd["ma160"].iloc[-1]) else None

    sm = TS.smc_layer(d, 3)
    smc_buy = [s for s in sm["sigs"] if s["dir"] == "bull" and s["state"] == "buy"]

    tiers = []
    if best_wk or best_d:
        tiers.append(1)
    if ma_buy:
        tiers.append(2)
    if smc_buy:
        tiers.append(3)
    tier = min(tiers) if tiers else 9

    return {"code": code, "name": name, "ind": ind, "dt": str(d["dt"].iloc[-1])[:10],
            "close": close, "pct": pct, "above_vwap": True, "vwap": v, "vw_z": z,
            "vw_dir": str(vw["ma_dir"][-1]), "tier": tier, "tiers": tiers,
            "n_res": len(tiers), "wk": best_wk, "d3": best_d,
            "both": bool(best_wk and best_d),
            "ma": ma_buy, "ma_sell": ma_sell, "align": align.get("state"),
            "above_ma20": (ma20v is not None and close > ma20v),
            "above_ma160": (ma160v is not None and close > ma160v),
            "smc": bool(smc_buy),
            "wk_all": [(x["tag"], x["level"], x.get("age", 0)) for x in wk_all if x.get("accept")][:3]}


def stock_rank_key(s):
    """排序：双级别共振 > 有缠论买点(tier) > 买点等级(三买1>一买2>二买3) > 共振层数 > 质量 > 涨幅"""
    pri = 9
    fx = s.get("wk") or s.get("d3")
    if fx:
        pri = fx["pri"]
    q = 0
    if s.get("above_ma20"):
        q += 1
    if s.get("above_ma160"):
        q += 1
    if s.get("align") == "多头排列":
        q += 2
    if s.get("ma"):
        q += 1
    return (-int(bool(s.get("both"))), 0 if (s.get("wk") or s.get("d3")) else 1,
            s["tier"], pri, -s["n_res"], -q, -s["pct"])


# ═══════════════════ 三、取数 ═══════════════════
def fetch_stock(m, hist):
    """个股日线：本地 holding_deep 缓存 + 当日 .cache/market 增量（tushare 口径，与扫描器同源）"""
    code = m["code"]
    if len(code) == 6 and code.isdigit():
        code = TB.DH.to_ts(TB.DH.norm(code))
    try:
        d = TB.load_daily(code, hist)
    except Exception as e:
        log("  load fail %s %s" % (code, str(e)[:50]))
        d = None
    m["ts_code"] = code
    return m, d


# ═══════════════════ 四、出图 ═══════════════════
def draw_pages(picks, tag, per_page=8, n_show=110):
    """每只股票一个子图，2 列分页出图（图太大 QQ/飞书都看不清，分页）"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from chan_chart import chan_geometry
    from tech_summary import MOON, STAR, RED, GRN, BLUE, rank_buys, ma20_state

    out = []
    pages = [picks[i:i + per_page] for i in range(0, len(picks), per_page)]
    for pi, page in enumerate(pages, 1):
        n = len(page)
        ncol = 2
        nrow = (n + 1) // 2
        fig, axes = plt.subplots(nrow, ncol, figsize=(7.6 * ncol, 3.9 * nrow), squeeze=False)
        axes = axes.ravel()
        for k, s in enumerate(page):
            ax = axes[k]
            d = s.get("_d")
            if d is None or len(d) < 60:
                ax.text(.5, .5, "取数失败", ha="center", transform=ax.transAxes)
                ax.axis("off")
                continue
            total = len(d)
            start = max(0, total - n_show)
            view = d.iloc[start:].reset_index(drop=True)
            nv = len(view)
            x = np.arange(nv)
            o = view["open"].values.astype(float)
            h = view["high"].values.astype(float)
            lo = view["low"].values.astype(float)
            c = view["close"].values.astype(float)
            prev_c = np.concatenate([[np.nan], c[:-1]])
            if start > 0:
                prev_c[0] = float(d["close"].iloc[start - 1])
            cols = [RED if (np.isfinite(prev_c[i]) and c[i] >= prev_c[i]) else GRN for i in range(nv)]
            bw = 0.62
            for i in range(nv):
                ax.vlines(x[i], lo[i], h[i], color=cols[i], lw=0.7, zorder=3)
                b0, b1 = min(o[i], c[i]), max(o[i], c[i])
                if abs(b1 - b0) < 1e-9:
                    b1 = b0 + max((h[i] - lo[i]) * 1e-3, 1e-6)
                ax.add_patch(Rectangle((x[i] - bw / 2, b0), bw, b1 - b0,
                                       facecolor=cols[i], edgecolor=cols[i], lw=0.35, zorder=4))
            # VWAP 六轨
            vw = S.vwap_tracks(d)
            for key, col, lw_, ls, lab in (("up2", "#c0392b", 0.9, "--", "+2σ"),
                                           ("up1", "#e08b7a", 0.7, ":", "+1σ"),
                                           ("vwap", "#222222", 1.5, "-", "VWAP"),
                                           ("dn1", "#1e8449", 0.9, "--", "−1σ"),
                                           ("dn2", "#0b4f2c", 1.0, "-", "−2σ")):
                v_ = vw[key][start:]
                if not np.all(np.isnan(v_)):
                    ax.plot(x, v_, color=col, lw=lw_, ls=ls, zorder=5,
                            label=lab if k == 0 else None)
            # 缠论分型版
            geo = chan_geometry(d)
            bx, by = [], []
            for p in geo["bi"]:
                xi = p["orig"] - start
                if -2 <= xi <= nv + 2:
                    bx.append(xi)
                    by.append(p["price"])
            if len(bx) >= 2:
                ax.plot(bx, by, color=BLUE, lw=1.3, zorder=6, label="缠论笔" if k == 0 else None)
            if geo["zs"] and geo["zs_x"]:
                zs0, zs1 = geo["zs"]
                x0 = max(geo["zs_x"][0] - start, -0.5)
                x1 = min(geo["zs_x"][1] - start, nv)
                if x1 > x0:
                    ax.add_patch(Rectangle((x0, zs0), x1 - x0, zs1 - zs0,
                                           facecolor="#ffd24d", alpha=0.25,
                                           edgecolor="#b8860b", lw=1.1, zorder=3))
                    ax.axhline(zs1, color="#b8860b", lw=0.6, ls="--", alpha=0.7, zorder=4)
                    ax.axhline(zs0, color="#b8860b", lw=0.6, ls="--", alpha=0.7, zorder=4)
            # 买点（只画过闸门的）
            fx = C.chan_read(C.bars_of(d), "day")
            if fx.get("ok"):
                _cl = float(d["close"].iloc[-1])
                for b in rank_buys(fx.get("buy_list") or [], ma20_state(d), close=_cl):
                    if not b["accept"]:
                        continue
                    cand = [p for p in geo["bi"] if p["type"] == -1]
                    if not cand:
                        continue
                    pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
                    xi = pt["orig"] - start
                    if not (-1 <= xi <= nv):
                        continue
                    ax.scatter([xi], [pt["price"]], s=200, marker=MOON, color="#0b6e0b",
                               edgecolor="#0b6e0b", lw=1.5, zorder=10)
                    ax.annotate("%s %.2f" % (b["tag"], b["level"]), xy=(xi, pt["price"]),
                                xytext=(0, -22), textcoords="offset points", ha="center",
                                fontsize=7, color="#0b6e0b", fontweight="bold", zorder=11,
                                bbox=dict(boxstyle="round,pad=0.18", fc="white",
                                          ec="#0b6e0b", alpha=0.92, lw=0.7))
                for b in (fx.get("sell_list") or []):
                    cand = [p for p in geo["bi"] if p["type"] == 1]
                    if not cand:
                        continue
                    pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
                    xi = pt["orig"] - start
                    if not (-1 <= xi <= nv):
                        continue
                    ax.scatter([xi], [pt["price"]], s=170, marker=STAR, color="#8b0000",
                               edgecolor="white", lw=0.9, zorder=10)
            bits = []
            if s.get("wk"):
                bits.append("周" + str(s["wk"]["tag"]))
            if s.get("d3"):
                bits.append("日三买")
            if not bits:
                bits.append("仅VWAP")
            ax.set_title("%s(%s) %+.2f%% ｜ %s ｜ VWAP %+.2fσ ｜ %s"
                         % (s["name"], s["code"], s["pct"], "+".join(bits),
                            s.get("vw_z", 0), s.get("ind", "")),
                         fontsize=9, loc="left", pad=4)
            ax.grid(True, ls=":", color="#dddddd", alpha=0.6)
            ax.tick_params(labelsize=6.5)
        for k in range(n, len(axes)):
            axes[k].axis("off")
        if pages and len(pages) > 1:
            fig.suptitle("技术分析汇总买点 · %s · 第 %d/%d 页" % (tag, pi, len(pages)),
                         fontsize=12, y=0.998)
        fig.tight_layout(rect=(0, 0, 1, 0.985))
        p = os.path.join(CHART_DIR, "sector_%s_p%d_%s.png"
                         % (tag, pi, datetime.now().strftime("%Y%m%d")))
        fig.savefig(p, dpi=115, facecolor="white", bbox_inches="tight")
        plt.close(fig)
        out.append(p)
        log("[图] %s" % p)
    return out


# ═══════════════════ 五、报告 ═══════════════════
def fmt_fx(x, weekly=False):
    if not x:
        return ""
    m = {"一买": "①一买", "二买": "③二买", "三买": "②三买"}.get(x["tag"], x["tag"])
    unit = "周" if weekly else "根"
    return "%s %.2f（%d%s前，现价%+.1f%%）" % (m, x["level"], x.get("age", 0), unit, x.get("gap", 0))


def build_text(secs, picks, n_scan, n_below, top):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    A = L.append
    A("📊 行业买点 → 个股 Top%d · %s" % (top, now))
    A(SIGN)
    A("口径：行业/个股均用技术分析汇总（缠论分型版+均线版+SMC+VWAP六轨）｜买点：三买>一买(MA20向上)>二买(胜率最低)｜个股硬门槛：现价须站上 VWAP −1σ")
    A("")
    A("═══ 一、行业自身有没有买点 ═══")
    for s in secs:
        if not s.get("ok"):
            A("  ⛔ %s：%s" % (s.get("name"), s.get("err")))
            continue
        b = s.get("best")
        A("  %s（%s）收 %.2f（%+.2f%%）｜%s｜中枢 %s"
          % (s["name"], s["code"], s["close"], s["pct"], s.get("stage") or "-",
             ("%.2f~%.2f" % tuple(s["zs"])) if s.get("zs") else "-"))
        A("    均线排列 %s ｜MA20 %s ｜VWAP偏离 %+.2fσ（MA40 %s）"
          % (s.get("align") or "-",
             ("%.2f %s" % (s["ma20"].get("ma20") or 0,
                           "向上" if s["ma20"].get("up") else "未向上")) if s["ma20"].get("ok") else "-",
             s.get("vw_z", 0), s.get("vw_dir")))
        if s.get("wk_all"):
            A("    周线买点（方向）：%s" % "、".join("%s %.2f(%d周)" % t for t in s["wk_all"][:3]))
        if b:
            A("    🌙 日线行业买点：%s → ✅ 过闸，个股可下钻"
              % fmt_fx(b))
        else:
            A("    🌙 日线行业买点：无（行业结构本身没有入场信号 → 个股只按个股自身结构选，板块效应偏弱）")
        if s.get("best_sell"):
            A("    ⛔ 行业卖点：%s（注意板块层面风险）" % fmt_fx(s["best_sell"]))
        A("")
    A("═══ 二、筛选统计 ═══")
    A("  成分股扫描 %d 只 → 站上 VWAP −1σ 的 %d 只（被 −1σ 止损线挡掉 %d 只）→ 有缠论买点取前 %d"
      % (n_scan, n_scan - n_below, n_below, top))
    A("")
    A("═══ 三、个股买点 Top%d ═══" % len(picks))
    for i, s in enumerate(picks, 1):
        bits = []
        if s.get("wk"):
            bits.append("周线" + fmt_fx(s["wk"], True))
        if s.get("d3"):
            bits.append("日线三买 %.2f（%d根前）" % (s["d3"]["level"], s["d3"].get("age", 0)))
        if not bits and s.get("ma"):
            bits.append("均线版：%s" % s["ma"]["kind"].split("（")[0])
        elif s.get("ma"):
            bits.append("+均线版买点")
        tagx = " ★双级别共振" if s.get("both") else ""
        zz = s.get("vw_z", 0) or 0
        ztag = ("VWAP %+.2fσ" % zz) + (" ⚠️+2σ以上·追高慎" if zz >= 2 else
                                       (" ⚠️贴近−1σ" if zz <= -0.8 else ""))
        q = []
        if s.get("above_ma20"):
            q.append("站上MA20")
        if s.get("above_ma160"):
            q.append("站上MA160")
        if s.get("align") == "多头排列":
            q.append("多头排列")
        if s.get("ma_sell"):
            q.append("⚠️均线版卖点")
        A("  %2d) %s(%s) %+.2f%% ｜%s｜%s%s"
          % (i, s["name"], s["code"], s["pct"], s.get("ind", ""), ztag, tagx))
        A("      %s%s" % (" + ".join(bits), ("（%s）" % "、".join(q)) if q else ""))
    A("")
    A("注：二买胜率最低（回踩尚未确认转多），只作次优观察；三买(回踩不破中枢上沿)确认度最高。")
    A("⚠️ 技术结构扫描，不构成投资建议。")
    return "\n".join(L)


# ═══════════════════ 六、main ═══════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ind", action="append", default=None, help="申万行业名，可多次/逗号分隔")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--cap", type=int, default=400, help="每个行业最多扫多少只成分股（按成交额降序）")
    ap.add_argument("--chart", action="store_true")
    ap.add_argument("--per-page", type=int, default=8)
    ap.add_argument("--out", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    inds = []
    for x in (a.ind or []):
        inds += [y.strip() for y in str(x).replace("，", ",").split(",") if y.strip()]
    if not inds:
        inds = ["传媒", "影视院线"]

    # ① 行业自身
    secs, sec_d = [], {}
    for nm in inds:
        try:
            s, d = sector_analyse(nm)
        except Exception as e:
            s, d = {"ok": False, "name": nm, "err": str(e)[:80]}, None
        secs.append(s)
        if d is not None:
            sec_d[nm] = d
        log("[行业] %s → %s" % (nm, "有买点" if s.get("best") else ("无买点" if s.get("ok") else s.get("err"))))

    # ② 成分股
    all_rows, n_scan, n_below = [], 0, 0
    for nm in inds:
        swmap = TB.load_sw_index_map()
        code = swmap.get(nm) or swmap.get(TB.strip_roman(nm))
        if not code:
            continue
        # 申万成分（本地缓存）优先，取不到再用东财板块成分
        by_l1, by_l2, by_l3, _m = TB.load_sw()
        mem_codes = by_l2.get(nm) or by_l1.get(nm) or []
        names = {}
        try:
            nm_map = TB.name_map()
        except Exception:
            nm_map = {}
        cand = []
        if mem_codes:
            for c in mem_codes:
                cand.append({"code": c.split(".")[0], "ts_code": c, "name": nm_map.get(c, "")})
        else:
            try:
                cand = board_members(code, cap=a.cap)
            except Exception as e:
                log("  成分股取数失败 %s %s" % (nm, e))
        log("[%s] 成分股 %d 只，开始扫" % (nm, len(cand)))

        rows = []
        hist = TB._today_hist()
        sub = cand[:a.cap]
        with ThreadPoolExecutor(max_workers=4) as ex:
            res = list(ex.map(lambda m: fetch_stock(m, hist), sub))
        for m, d in res:
            n_scan += 1
            if d is None:
                continue
            try:
                s = analyse_stock(m.get("ts_code") or m["code"], m["name"], nm, d)
            except Exception as e:
                log("  analyse fail %s %s" % (m["code"], str(e)[:60]))
                continue
            if not s:
                continue
            if not s.get("above_vwap"):
                n_below += 1
                continue
            s["_d"] = d
            rows.append(s)
        log("  [%s] 站上 VWAP −1σ：%d 只（有缠论买点 %d 只）"
            % (nm, len(rows), len([r for r in rows if r.get("wk") or r.get("d3")])))
        all_rows += rows

    # 去重（影视院线 ⊂ 传媒，两行业会重叠）
    seen, uniq = set(), []
    for r in all_rows:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        uniq.append(r)
    all_rows = uniq

    # ③ 只保留「有买点」的（周线任意买点 或 日线三买；均线版买点作为补充档）
    withfx = [r for r in all_rows if r.get("wk") or r.get("d3")]
    withma = [r for r in all_rows if not (r.get("wk") or r.get("d3")) and r.get("ma")]
    ranked = sorted(withfx, key=stock_rank_key)
    picks = ranked[:a.top]
    if len(picks) < a.top:
        picks += sorted(withma, key=stock_rank_key)[:a.top - len(picks)]

    txt = build_text(secs, picks, n_scan, n_below, a.top)
    print(txt)

    imgs = []
    if a.chart:
        tag = "_".join(inds)[:20]
        # 行业指数图
        for s in secs:
            if not s.get("ok"):
                continue
            try:
                p = TB.chart_sector("industry", s["name"], s["code"])
                if p:
                    imgs.append(p)
                    log("[图] 行业 %s → %s" % (s["name"], p))
            except Exception as e:
                log("[图] 行业 %s 失败 %s" % (s["name"], str(e)[:60]))
        imgs += draw_pages(picks, tag, per_page=a.per_page)
        txt += "\n\n📎 图（%d 张）：\n" % len(imgs) + "\n".join(os.path.basename(x) for x in imgs)
        print("\n[图] " + " ".join(imgs), file=sys.stderr)

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        log("[存档]", a.out)
    if a.json:
        json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"),
                   "sectors": [{k: v for k, v in s.items() if k != "buys"} for s in secs],
                   "n_scan": n_scan, "n_below_vwap": n_below,
                   "picks": [{k: v for k, v in s.items() if k != "_d"} for s in picks],
                   "imgs": imgs}, open(a.json, "w"), ensure_ascii=False, indent=1, default=str)
        log("[json]", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
