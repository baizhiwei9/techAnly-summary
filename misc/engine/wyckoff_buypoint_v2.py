#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中吸筹买点分析 v2 —— 威科夫吸筹 → 技术分析汇总四层过滤 → Top10 行业 → Top20 个股 → 出图

大佬 2026-11-10 口径（替换旧版 wyckoff_acc_buy_push.py）：
  ① **先用威科夫判吸筹**（口径完全沿用 bin/wyckoff_today_analyze.py，一个字没改）
     「你原先怎么判断吸筹还是判断吸筹？我只不过让你用这个技术分析汇总那几个东西来过滤一遍」
  ② 技术分析汇总四层过滤吸筹行业（周线一二三买 / 日线只认三买 / 均线版 / SMC / VWAP）
  ③ 选出 **Top 10 行业**（超过 10 个取前 10，不足 10 个就用剩下的全部）
  ④ 每个行业下钻个股：**必须站在 VWAP −1σ 以上**，按买点优先级，**每行业最多 2 只**，全市场 **Top 20**
  ⑤ 出图：**每只股票一个子图**，子图里画【缠论分型版 + VWAP 六轨】

全流程纯 Python，0 token。

用法：
  python3 bin/wyckoff_buypoint_v2.py                 # 出报告（文本）
  python3 bin/wyckoff_buypoint_v2.py --chart         # 额外出一张多子图 PNG
  python3 bin/wyckoff_buypoint_v2.py --push          # 推飞书卡片（含图）
  python3 bin/wyckoff_buypoint_v2.py --qq            # 顺带推 QQ 文字
  python3 bin/wyckoff_buypoint_v2.py --dry           # 只打印不推送
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
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
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "chanlun")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
# 顺序有讲究：最后插入的优先级最高 → bin/ 放最前 = 最低优先级，
# 保证 import 命中真实目录，而不是 bin/ 里的兼容 shim。
for _sd in ("bin", "", "misc/core", "chanlun/fractal", "chanlun/ma", "chanlun/multiscale",
            "chanlun/multiscale/vendor", "misc/engine", "misc", "wavelet", "smc",
            "misc/strategies", "misc/tools"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import chan_smc_index as C            # noqa: E402
import chan_ma_trend_v2 as M          # noqa: E402
import tech_summary as TS             # noqa: E402
import tech_buypoint as TB            # noqa: E402
import smc_core as S                  # noqa: E402
from tech_intraday_buypoint import (   # noqa: E402
    to_df, board_members, stock_kline, analyse_stock, stock_rank_key,
    weekly_buys, WEEK_BARS, STOCK_VWAP_SIGMA, QQ_ACCOUNT, _qq_target,
)

CACHE = os.path.join(WS, ".cache", "wyckoff_today")
RESULT = os.path.join(CACHE, "result.json")
HIST = os.path.join(CACHE, "hist")
CHART_DIR = os.path.join(WS, ".cache/charts")
os.makedirs(CHART_DIR, exist_ok=True)

TOP_IND = int(os.environ.get("WBP_TOP_IND", "10"))     # 最多 10 个行业
TOP_STK = int(os.environ.get("WBP_TOP_STK", "20"))     # 个股全市场 Top 20
PER_IND = int(os.environ.get("WBP_PER_IND", "2"))      # 每行业最多 2 只
STOCK_CAP = int(os.environ.get("WBP_STOCK_CAP", "40"))  # 每板块最多扫多少只（成交额降序）
SIGN = "📎 来自 OpenClaw.Deepseek"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ═════════════════════ ① 威科夫吸筹（沿用原口径，不重写）═════════════════════
def load_wyckoff():
    """读 .cache/wyckoff_today/result.json（由 wyckoff_today_data.py + wyckoff_today_analyze.py 生成）

    硬校验：必须是今天算的，否则报错不继续（大佬 2026-10-30 要求）
    """
    if not os.path.exists(RESULT):
        raise SystemExit("❌ 缺 %s，先跑 bin/wyckoff_today_data.py + wyckoff_today_analyze.py" % RESULT)
    d = json.load(open(RESULT, encoding="utf-8"))
    mt = datetime.fromtimestamp(os.path.getmtime(RESULT))
    today = datetime.now().strftime("%Y-%m-%d")
    if mt.strftime("%Y-%m-%d") != today:
        raise SystemExit("❌ result.json 是 %s 的结果，不是今天 %s → 中止（先重跑数据+分析）"
                         % (mt.strftime("%Y-%m-%d"), today))
    return d, mt.strftime("%Y-%m-%d %H:%M")


def accum_industries(d):
    """吸筹行业列表（威科夫口径，tag == '吸筹'），按涨幅降序"""
    rows = [r for r in (d.get("行业") or []) if r.get("tag") == "吸筹"]
    rows.sort(key=lambda r: -r.get("pct1d", 0))
    return rows


# ═════════════════════ ② 行业指数（同花顺 ths_daily）═════════════════════
_THS_MAP = None


def ths_index_map():
    """同花顺行业名 → 881xxx.TI（缓存）"""
    global _THS_MAP
    if _THS_MAP is not None:
        return _THS_MAP
    p = os.path.join(WS, ".cache/ths_industry_map.json")
    try:
        if time.time() - os.path.getmtime(p) < 7 * 86400:
            _THS_MAP = json.load(open(p, encoding="utf-8"))
            return _THS_MAP
    except Exception:
        pass
    from tushare_init import ts_call
    idx = ts_call("ths_index")
    m = {}
    if idx is not None and len(idx):
        for _, r in idx.iterrows():
            tp = str(r.get("type") or "")
            if tp not in ("I", "N", ""):
                continue
            nm = str(r["name"]).replace("(A股)", "").strip()
            m.setdefault(nm, {"code": r["ts_code"], "type": tp})
    try:
        json.dump(m, open(p, "w"), ensure_ascii=False)
    except Exception:
        pass
    _THS_MAP = m
    return m


def ths_daily(code, start="20230101"):
    """同花顺板块日线 → DataFrame[dt,open,high,low,close,volume]"""
    from tushare_init import ts_call
    d = ts_call("ths_daily", ts_code=code, start_date=start)
    if d is None or not len(d):
        return None
    d = d.rename(columns={"trade_date": "dt", "vol": "volume"})
    d["dt"] = pd.to_datetime(d["dt"].astype(str), format="%Y%m%d")
    for c in ("open", "high", "low", "close"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    if "volume" not in d.columns:
        d["volume"] = 0.0
    return d.sort_values("dt").reset_index(drop=True)[
        ["dt", "open", "high", "low", "close", "volume"]].dropna(subset=["close"]).reset_index(drop=True)


def analyse_industry(name, wk_row):
    """吸筹行业 + 技术分析汇总四层过滤

    周线：一买/二买/三买 都算（方向）｜日线：**只认三买**（入场时机）
    另外叠加：均线版买点 / SMC 买点 / VWAP MA40向上
    """
    mp = ths_index_map()
    info = mp.get(name)
    if not info:
        return {"name": name, "ok": False, "why": "同花顺无此行业指数"}
    d = ths_daily(info["code"])
    if d is None or len(d) < 120:
        return {"name": name, "ok": False, "why": "行业指数数据不足"}
    cl = d["close"].astype(float).values
    close = float(cl[-1])
    prev = float(cl[-2])
    pct = (close / prev - 1) * 100 if prev else 0.0

    wk_acc, _, _ = weekly_buys(d, close)
    best_wk = wk_acc[0] if wk_acc else None
    dtb = [x for x in TB.find_third_buys(d, cl) if x.get("accept")]
    dtb.sort(key=lambda x: (x["pri"], x.get("age", 999)))
    best_d = dtb[0] if dtb else None

    dd = M.add_ind(d)
    sigs = M.signals(dd)
    ma_buy = next((s for s in sigs if s["type"] == "买点"), None)
    ma_sell = next((s for s in sigs if s["type"] == "卖点"), None)
    align = M.ma_alignment(dd)

    sm = TS.smc_layer(d, 3)
    smc_buy = [s for s in sm["sigs"] if s["dir"] == "bull" and s["state"] == "buy"]
    smc_best = max(smc_buy, key=lambda s: s["score"]) if smc_buy else None

    vw = S.vwap_tracks(d)
    vw_dir = str(vw["ma_dir"][-1])
    vv, sg = float(vw["vwap"][-1]), float(vw["sigma"][-1])
    z = (close - vv) / sg if sg > 0 else 0.0
    vw_ok = (vw_dir == "up") and z <= 1.0

    tiers = []
    if best_wk or best_d:
        tiers.append(1)
    if ma_buy:
        tiers.append(2)
    if smc_best:
        tiers.append(3)
    if vw_ok:
        tiers.append(4)

    return {"name": name, "ok": True, "code": info["code"], "close": close, "pct": pct,
            "wk": best_wk, "d3": best_d, "both": bool(best_wk and best_d),
            "ma": ma_buy, "ma_sell": ma_sell, "align": align.get("state"),
            "smc": smc_best, "vw_dir": vw_dir, "vw_z": z,
            "tiers": tiers, "n_res": len(tiers),
            "tier": min(tiers) if tiers else 9,
            "wk_pct": wk_row.get("pct1d"), "wk_why": wk_row.get("why"),
            "wk_place": wk_row.get("pos"), "wk_ar": wk_row.get("ar"),
            "wk_netmain": wk_row.get("netmain"), "wk_lead": wk_row.get("lead")}


def ind_rank_key(r):
    """行业排序：周线买点(三买1>一买2>二买3) → 日线三买 → 共振层数 → tier"""
    w_pri = r["wk"]["pri"] if r.get("wk") else 9
    return (w_pri, 0 if r.get("d3") else 1, -r["n_res"], r["tier"])


# ═════════════════════ ③ 行业 → 个股 ═════════════════════
def ths_members(code, tries=4):
    """同花顺板块成分股（带重试：tushare 偶发返回空 = 被限流，不是真没成分）"""
    from tushare_init import ts_call
    m = None
    for i in range(tries):
        m = ts_call("ths_member", ts_code=code)
        if m is not None and len(m):
            break
        time.sleep(1.5 * (i + 1))
    if m is None or not len(m):
        log("    [warn] %s 成分股 4 次都空（疑似限流）" % code)
        return []
    return [{"code": r["con_code"], "name": (r.get("con_name") or "").replace(".SZ", "").replace(".SH", "").strip()}
            for _, r in m.iterrows()]


def scan_stocks(industries):
    """每个行业下钻个股：站上 VWAP −1σ → 按买点优先级；每行业最多 PER_IND 只"""
    all_picks = []
    for ind in industries:
        mem = ths_members(ind["code"])
        if not mem:
            ind["stocks"] = []
            log("  [个股] %s：无成分股" % ind["name"])
            continue
        # 先按成交额排序（用东财该行业的成分快照拿不到同花顺口径，直接按代码顺序扫，控量）
        picks, below, n_scan = [], 0, 0
        todo = mem[:STOCK_CAP]
        n_scan = len(todo)
        # 并发取 K 线（IO 密集，4 线程足矣；太多会被东财限流）
        from concurrent.futures import ThreadPoolExecutor

        def _fetch(m):
            for _ in range(2):
                try:
                    ks = stock_kline(m["code"], lmt=WEEK_BARS)
                    if ks:
                        return m, ks
                except Exception:
                    time.sleep(0.8)
            return m, None

        with ThreadPoolExecutor(max_workers=4) as ex:
            fetched = list(ex.map(_fetch, todo))
        for m, ks in fetched:
            if not ks:
                continue
            try:
                s = analyse_stock(m["code"], m["name"], ks)
            except Exception as e:
                log("    analyse fail %s %s" % (m["code"], str(e)[:40]))
                continue
            if not s:
                continue
            if not s.get("above_vwap"):
                below += 1
                continue
            s["ind"] = ind["name"]
            s["_ks"] = ks
            picks.append(s)
        picks.sort(key=stock_rank_key)
        ind["stocks"] = picks[:PER_IND]
        ind["n_member"] = len(mem)
        ind["n_below_vwap"] = below
        all_picks.extend(ind["stocks"])
        log("  [个股] %s：成分 %d，扫 %d，站上−1σ %d（被挡 %d）→ 取 %d"
            % (ind["name"], len(mem), n_scan, len(picks), below, len(ind["stocks"])))
    all_picks.sort(key=stock_rank_key)
    return all_picks


# ═════════════════════ ④ 出图：每只股票一个子图（缠论分型版 + VWAP）═════════════════════
def draw_stock_grid(picks, out_path, n_show=120):
    """一张图，每只股票一个子图：日线 K 线 + 缠论分型版（笔/中枢/买卖点）+ VWAP 六轨 + 量"""
    if not picks:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from chan_chart import chan_geometry
    from tech_summary import MOON, STAR, RED, GRN, BLUE, ORANGE, rank_buys, ma20_state

    n = len(picks)
    ncol = 2 if n > 1 else 1
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(9.6 * ncol, 4.4 * nrow))
    axes = np.atleast_1d(axes).ravel()

    for k, s in enumerate(picks):
        ax = axes[k]
        try:
            ks_ = s.get("_ks") or stock_kline(s["code"], lmt=WEEK_BARS)
            d = to_df(ks_)
        except Exception:
            ax.text(0.5, 0.5, "取数失败", ha="center", transform=ax.transAxes)
            ax.axis("off")
            continue
        total = len(d)
        start = max(0, total - n_show)
        view = d.iloc[start:].reset_index(drop=True)
        nv = len(view)
        x = np.arange(nv)
        o = view["open"].values.astype(float)
        h = view["high"].values.astype(float)
        l = view["low"].values.astype(float)
        c = view["close"].values.astype(float)
        prev_c = np.concatenate([[np.nan], c[:-1]])
        if start > 0:
            prev_c[0] = float(d["close"].iloc[start - 1])
        cols = [RED if (np.isfinite(prev_c[i]) and c[i] >= prev_c[i]) else GRN for i in range(nv)]

        bw = 0.62
        for i in range(nv):
            ax.vlines(x[i], l[i], h[i], color=cols[i], lw=0.8, zorder=3)
            lo_, hi_ = min(o[i], c[i]), max(o[i], c[i])
            if abs(hi_ - lo_) < 1e-9:
                hi_ = lo_ + max((h[i] - l[i]) * 1e-3, 1e-6)
            ax.add_patch(Rectangle((x[i] - bw / 2, lo_), bw, hi_ - lo_,
                                   facecolor=cols[i], edgecolor=cols[i], lw=0.4, zorder=4))

        # VWAP 六轨
        vw = S.vwap_tracks(d)
        for key, col, lw, ls, lab, al in (
                ("up3", "#8e0000", 1.1, "-", "+3σ", 1.0),
                ("up2", "#c0392b", 1.0, "--", "+2σ", 1.0),
                ("up1", "#e08b7a", 0.8, ":", "+1σ", 0.95),
                ("vwap", "#222222", 1.7, "-", "VWAP", 1.0),
                ("dn1", "#1e8449", 1.0, "--", "−1σ", 1.0),
                ("dn2", "#0b4f2c", 1.1, "-", "−2σ", 1.0)):
            v = vw[key][start:]
            if not np.all(np.isnan(v)):
                ax.plot(x, v, color=col, lw=lw, ls=ls, alpha=al, zorder=5,
                        label=lab if k == 0 else None)

        # 缠论分型版：笔 / 分型 / 中枢 / 买卖点
        geo = chan_geometry(d)
        bx, by = [], []
        for p in geo["bi"]:
            xi = p["orig"] - start
            if -2 <= xi <= nv + 2:
                bx.append(xi)
                by.append(p["price"])
        if len(bx) >= 2:
            ax.plot(bx, by, color=BLUE, lw=1.4, zorder=6, label="缠论笔" if k == 0 else None)
        for p in geo["bi"]:
            xi = p["orig"] - start
            if -1 <= xi <= nv:
                ax.scatter([xi], [p["price"]], s=16, zorder=7,
                           color=(RED if p["type"] == 1 else GRN),
                           marker=("v" if p["type"] == 1 else "^"))
        if geo["zs"] and geo["zs_x"]:
            zs0, zs1 = geo["zs"]
            x0 = max(geo["zs_x"][0] - start, -0.5)
            x1 = min(geo["zs_x"][1] - start, nv)
            if x1 > x0:
                ax.add_patch(Rectangle((x0, zs0), x1 - x0, zs1 - zs0,
                                       facecolor="#ffd24d", alpha=0.25,
                                       edgecolor="#b8860b", lw=1.2, zorder=3))
                ax.axhline(zs1, color="#b8860b", lw=0.7, ls="--", alpha=0.7, zorder=4)
                ax.axhline(zs0, color="#b8860b", lw=0.7, ls="--", alpha=0.7, zorder=4)

        # 买卖点（只画过闸门的）
        fx = C.chan_read(C.bars_of(d), "day")
        if fx.get("ok"):
            _cl = float(d["close"].iloc[-1])
            ok_buys = [b for b in rank_buys(fx.get("buy_list") or [], ma20_state(d), close=_cl)
                       if b["accept"]]
            for b in ok_buys:
                cand = [p for p in geo["bi"] if p["type"] == -1]
                if not cand:
                    continue
                pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
                xi = pt["orig"] - start
                if not (-1 <= xi <= nv):
                    continue
                ax.scatter([xi], [pt["price"]], s=260, marker=MOON, color="#0b6e0b",
                           edgecolor="#0b6e0b", lw=1.7, zorder=10)
                ax.annotate("%s %.2f" % (b["tag"], b["level"]), xy=(xi, pt["price"]),
                            xytext=(0, -26), textcoords="offset points", ha="center",
                            fontsize=7.5, color="#0b6e0b", fontweight="bold", zorder=11,
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#0b6e0b",
                                      alpha=0.92, lw=0.8))
            for b in (fx.get("sell_list") or []):
                cand = [p for p in geo["bi"] if p["type"] == 1]
                if not cand:
                    continue
                pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
                xi = pt["orig"] - start
                if not (-1 <= xi <= nv):
                    continue
                ax.scatter([xi], [pt["price"]], s=220, marker=STAR, color="#8b0000",
                           edgecolor="white", lw=1.0, zorder=10)

        # 标题：名称 代码 + 买点摘要 + VWAP 偏离
        bits = []
        if s.get("wk"):
            bits.append("周" + str(s["wk"]["tag"]))
        if s.get("d3"):
            bits.append("日三买")
        if not bits:
            bits.append("仅VWAP")
        pct = (c[-1] / prev_c[-1] - 1) * 100 if np.isfinite(prev_c[-1]) else 0.0
        ax.set_title("%s(%s) %+.2f%% ｜ %s ｜ VWAP %+.2fσ ｜ %s"
                     % (s["name"], s["code"], pct, "+".join(bits), s.get("vw_z", 0), s.get("ind", "")),
                     fontsize=9.5, loc="left", pad=5)
        ax.grid(True, ls=":", color="#dddddd", alpha=0.6)
        ax.tick_params(labelsize=7)
        if nv > 12:
            step = max(1, nv // 8)
            tks = list(range(0, nv, step))
            ax.set_xticks(tks)
            ax.set_xticklabels([pd.Timestamp(view["dt"].iloc[i]).strftime("%m-%d") for i in tks],
                               fontsize=7, rotation=0)
        if k == 0:
            ax.legend(loc="upper left", fontsize=7, ncol=4, framealpha=0.85)

    for j in range(n, len(axes)):
        axes[j].axis("off")
    fig.suptitle("盘中吸筹买点 · 个股结构图（缠论分型版 + VWAP 六轨）· %s"
                 % datetime.now().strftime("%Y-%m-%d %H:%M"), fontsize=13, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(out_path, dpi=115)
    plt.close(fig)
    return out_path


# ═════════════════════ ⑤ 报告 ═════════════════════
def _wk_txt(x):
    if not x:
        return "无"
    return "%s %.2f（%d周前，现价%+.1f%%）" % (
        x.get("tag") or TB.PRI_MARK.get(x["pri"], "?"), x["level"], x.get("age", 0), x.get("gap", 0))


def build_text(wk_dt, n_acc, inds, picks, rejected, n_ind_all):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    A = L.append
    A("📊 盘中吸筹买点分析 · %s" % now)
    A("口径：① 威科夫判吸筹（原始口径不变）→ ② 技术分析汇总四层过滤 → ③ Top10 行业 → ④ 每行业≤2只、全市场Top20 个股")
    A("级别：行业**周线一二三买都算**、**日线只认三买**｜个股须站在 **VWAP −1σ 以上**")
    A("数据：威科夫结果 %s ｜ 吸筹行业 %d 个，技术面过滤后 %d 个 → 取前 %d"
      % (wk_dt, n_acc, n_ind_all, len(inds)))
    A("")

    A("═══ 一、吸筹 + 技术面过闸的行业（%d 个）═══" % len(inds))
    for i, r in enumerate(inds, 1):
        A("%2d. %-10s %+6.2f%% ｜ 共振 %d层｜%s"
          % (i, r["name"], r.get("wk_pct") or 0, r["n_res"],
             "★周线+日线双共振" if r.get("both") else ("周线买点" if r.get("wk") else ("日线三买" if r.get("d3") else "-"))))
        if r.get("wk"):
            A("     └ 周线：%s" % _wk_txt(r["wk"]))
        if r.get("d3"):
            A("     └ 日线三买 %.2f（%d根前，现价%+.1f%%）"
              % (r["d3"]["level"], r["d3"].get("age", 0), r["d3"].get("gap", 0)))
        if not r.get("wk") and not r.get("d3"):
            A("     └ 缠论：周线无 / 日线三买无（靠均线版/SMC/VWAP 过闸）")
        if r.get("ma"):
            A("     └ 均线版：%s" % r["ma"]["kind"].split("（")[0])
        if r.get("smc"):
            A("     └ SMC：%s %.2f~%.2f（方向 %d/4）"
              % (r["smc"]["kind"].upper(), r["smc"]["bottom"], r["smc"]["top"], r["smc"]["score"]))
        A("     └ 威科夫：%s" % (r.get("wk_why") or "-"))
        st = r.get("stocks") or []
        if st:
            A("     └ 🌙 个股 %d 只：" % len(st))
            for s in st:
                A("        · %s(%s) %+.2f%% ｜ VWAP %+.2fσ ｜ %s"
                  % (s["name"], s["code"], s["pct"], s.get("vw_z", 0), _stk_tag(s)))
        else:
            A("     └ 🌙 个股：无（成分股里无一只站在 VWAP −1σ 以上）")
    A("")

    A("═══ 二、个股汇总（每行业≤%d 只，全市场 Top%d）═══" % (PER_IND, TOP_STK))
    if picks:
        for i, s in enumerate(picks[:TOP_STK], 1):
            A("%2d. %-8s(%s) %+6.2f%% ｜ %-10s ｜ VWAP %+.2fσ ｜ %s"
              % (i, s["name"], s["code"], s["pct"], s.get("ind", ""), s.get("vw_z", 0), _stk_tag(s)))
            ext = []
            if s.get("above_ma20"):
                ext.append("站上MA20")
            if s.get("above_ma160"):
                ext.append("站上MA160")
            if s.get("align") == "多头排列":
                ext.append("多头排列")
            if s.get("ma_sell"):
                ext.append("⚠️均线版卖点")
            if ext:
                A("      └ " + "、".join(ext))
    else:
        A("  无（没有个股站在 VWAP −1σ 以上）")
    A("")

    if rejected:
        A("═══ 三、吸筹但技术面被否的行业（%d 个）═══" % len(rejected))
        for r in rejected[:15]:
            A("  · %-10s %+6.2f%% ｜ %s" % (r["name"], r.get("wk_pct") or 0, r.get("why") or "无买点"))
        A("")

    A("⚠️ 威科夫判吸筹 + 技术结构过滤，不构成投资建议。")
    return "\n".join(L)


def _stk_tag(s):
    bits = []
    if s.get("wk"):
        bits.append("周" + s["wk"]["tag"] + "%.2f(%d周)" % (s["wk"]["level"], s["wk"].get("age", 0)))
    if s.get("d3"):
        bits.append("日三买%.2f(%d根)" % (s["d3"]["level"], s["d3"].get("age", 0)))
    if not bits:
        bits.append("无缠论买点")
    if s.get("both"):
        bits.append("★双共振")
    return " + ".join(bits)


def push_feishu(title, txt, imgs=None):
    ok, chat = TB.push_feishu(title, txt, imgs, None, "blue")
    log("[feishu]", "已推送" if ok else "推送失败", "→", chat)
    return ok


def push_qq(txt):
    tgt = _qq_target()
    if not tgt:
        log("[qq] 跳过：未配置 QQ target")
        return False
    cmd = ["openclaw", "message", "send", "--channel", "qqbot",
           "--target", tgt, "--message", txt]
    if QQ_ACCOUNT:
        cmd += ["--account", QQ_ACCOUNT]
    try:
        p = subprocess.run(cmd, cwd=WS, capture_output=True, text=True, timeout=180)
        tail = [x for x in (p.stdout or "").splitlines() if x.strip()][-1:] or [""]
        log("[qq] rc=%d %s" % (p.returncode, tail[0].strip()[:160]))
        return p.returncode == 0
    except Exception as e:
        log("[qq] ERR", str(e)[:160])
        return False


def main():
    global PER_IND, STOCK_CAP
    ap = argparse.ArgumentParser()
    ap.add_argument("--chart", action="store_true", help="出一张多子图 PNG（每只个股一个子图）")
    ap.add_argument("--push", action="store_true", help="推飞书卡片（含图）")
    ap.add_argument("--qq", action="store_true", help="顺带推 QQ 文字")
    ap.add_argument("--json", default="", help="结果落盘 json")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--top-ind", type=int, default=TOP_IND)
    ap.add_argument("--top-stk", type=int, default=TOP_STK)
    ap.add_argument("--per-ind", type=int, default=PER_IND)
    ap.add_argument("--stock-cap", type=int, default=STOCK_CAP)
    a = ap.parse_args()

    PER_IND, STOCK_CAP = a.per_ind, a.stock_cap

    d, wk_dt = load_wyckoff()
    acc = accum_industries(d)
    log("[威科夫] 吸筹行业 %d 个（结果时间 %s）" % (len(acc), wk_dt))

    inds, rejected = [], []
    for r in acc:
        try:
            x = analyse_industry(r["name"], r)
        except Exception as e:
            x = {"name": r["name"], "ok": False, "why": "分析异常 " + str(e)[:40]}
        if x.get("ok") and x["n_res"] > 0:
            inds.append(x)
        else:
            x["name"] = r["name"]
            x.setdefault("wk_pct", r.get("pct1d"))
            x.setdefault("why", x.get("why") or "四层全部无买点")
            rejected.append(x)
    inds.sort(key=ind_rank_key)
    n_ind_all = len(inds)
    inds = inds[:a.top_ind]          # 超过 10 个取前 10，不足就用全部
    log("[行业] 技术面过闸 %d 个 → 取 Top%d（实际 %d）" % (n_ind_all, a.top_ind, len(inds)))

    picks = scan_stocks(inds) if inds else []
    log("[个股] 共 %d 只（每行业≤%d，全市场取前 %d）" % (len(picks), a.per_ind, a.top_stk))
    picks = picks[:a.top_stk]

    txt = build_text(wk_dt, len(acc), inds, picks, rejected, n_ind_all)
    print(txt)

    img = None
    if a.chart or a.push:
        try:
            img = draw_stock_grid(picks, os.path.join(
                CHART_DIR, "wyckoff_buypoint_%s.png" % datetime.now().strftime("%Y%m%d_%H%M")))
            log("[图]", img)
        except Exception as e:
            log("[图] 失败", str(e)[:160])

    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"),
                   "wyckoff_result_at": wk_dt, "n_acc": len(acc),
                   "n_ind_pass": n_ind_all, "industries": [
                       {k: v for k, v in r.items() if k != "stocks"} | {"stocks": r.get("stocks") or []}
                       for r in inds],
                   "picks": [{kk: vv for kk, vv in p.items() if kk != "_ks"} for p in picks], "chart": img},
                  open(a.json, "w"), ensure_ascii=False, indent=1, default=str)
        log("[json]", a.json)

    if a.dry:
        return 0
    ok = False
    if a.push:
        ok = push_feishu("📊 盘中吸筹买点分析 · %s" % datetime.now().strftime("%m-%d %H:%M"),
                         txt, [img] if img else None) or ok
    if a.qq:
        ok = push_qq(txt) or ok
        # ⚠️ QQ 文字消息带不了图 → 图单独发一条（必须先复制到 ~/.openclaw/media/ 受信目录）
        if img and os.path.exists(img):
            try:
                sys.path.insert(0, os.path.join(WS, "bin"))
                import push_image
                _t = _qq_target()
                if _t:
                    push_image.QQ_TARGET = _t
                push_image.QQ_ACCOUNT = QQ_ACCOUNT        # ⚠️ 必须带 account，否则被静默拦截
                ok = push_image.push([img], text="📊 盘中吸筹买点 · 个股结构图（缠论分型版 + VWAP）",
                                     qq=True, fs=False) or ok
            except Exception as e:
                log("[qq图] 失败", str(e)[:160])
    return 0 if (ok or (not a.push and not a.qq)) else 1


if __name__ == "__main__":
    sys.exit(main())
