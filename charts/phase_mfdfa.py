#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""phase_mfdfa · 相变监控图 + MF-DFA 滚动 Δα 子图
====================================================================
在 charts/phase_transition.py 的 5 栏相变监控图下追加第 6 栏：
MF-DFA（多重分形去趋势波动分析）滚动 Δα（多分形谱宽度）。

原来的相变逻辑（取数 / 指标 / 前 5 栏绘图口径）不动，本模块只做三件事：
  ① mfdfa_delta_alpha()   单窗口 Δα（纯 numpy 手写循环，不引第三方 mfdfa 库）
  ② rolling_delta_alpha() 滚动 N=250 的 Δα 序列
  ③ plot_panel()          前 5 栏相变图 + 第 6 栏 Δα（红线 + 90 分位阈值橙虚线）

用法：
  python3 charts/phase_mfdfa.py --index 000688 --price-code 588000.SH
  python3 charts/phase_mfdfa.py --index 000688 --start 2026-06-01 --out /tmp/x.png
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403
from charts import phase_transition as _pt  # 复用原相变取数/指标/绘图口径

import numpy as np
import pandas as pd
import matplotlib.dates as mdates  # noqa: E402


# ============================================================================
# MF-DFA 滚动 Δα
# ============================================================================

def mfdfa_delta_alpha(r, scales=(20, 30, 50, 80, 125),
                      q_list=(-4, -3, -2, -1, 0, 1, 2, 3, 4)):
    """单窗口 MF-DFA 多分形谱宽度 Δα。

    输入：日收益率序列 r（log 收益率）。
    返回：max(α) - min(α)，其中 α(q) 由广义 Hurst 指数 h(q) 的中心差分给出。
    """
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    if r.size < max(scales) + 10:
        return np.nan

    X = np.cumsum(r - np.mean(r))  # 轮廓（profile）
    F_q = {q: [] for q in q_list}
    used_scales = []  # 实际参与拟合的标度（跳过无效标度后仍与 F_q 一一对应）

    for s in scales:
        nseg = len(X) // s
        if nseg < 1:
            continue
        segs = [X[i * s:(i + 1) * s] for i in range(nseg)]  # 不重叠切段，舍去尾部
        vars_ = []
        for seg in segs:
            x = np.arange(len(seg), dtype=float)
            coef = np.polyfit(x, seg, 1)        # 段内线性趋势
            Y = seg - np.polyval(coef, x)       # 去趋势残差
            vars_.append(np.mean(Y ** 2))
        vars_ = np.asarray(vars_, dtype=float)
        vars_ = vars_[vars_ > 0]
        if vars_.size == 0:
            continue
        used_scales.append(s)
        for q in q_list:
            if q == 0:
                F_q[q].append(np.exp(np.mean(np.log(vars_)) / 2.0))
            else:
                F_q[q].append(np.mean(vars_ ** (q / 2.0)) ** (1.0 / q))

    tau = {}
    for q, fq in F_q.items():
        if len(fq) < 2:
            continue
        ss = np.asarray(used_scales[:len(fq)], dtype=float)
        h = np.polyfit(np.log(ss), np.log(np.asarray(fq, dtype=float)), 1)[0]
        tau[q] = q * h - 1.0

    alphas = []
    for q in q_list:
        if (q - 1) in tau and (q + 1) in tau:
            alphas.append((tau[q + 1] - tau[q - 1]) / 2.0)  # 中心差分，dq=1
    if not alphas:
        return np.nan
    return float(max(alphas) - min(alphas))


def _as_dt_index(idx):
    """把 YYYYMMDD 字符串 / 整数 / 日期字符串索引统一成 DatetimeIndex。"""
    if isinstance(idx, pd.DatetimeIndex):
        return idx
    if pd.api.types.is_integer_dtype(idx):
        return pd.to_datetime(pd.Index(idx).astype(str), format="%Y%m%d")
    try:
        return pd.to_datetime(idx, format="%Y%m%d")
    except Exception:
        return pd.to_datetime(idx)


def rolling_delta_alpha(close, N=250):
    """对收盘价序列做滚动 MF-DFA Δα。

    返回 pandas Series（index=日期，value=Δα）。窗口取 r[i-N:i]，落点记在 date[i]。
    """
    px = close.astype(float)
    r = np.log(px).diff()
    idx = _as_dt_index(r.index)
    da, dates = [], []
    vals = r.values
    for i in range(N, len(r)):
        da.append(mfdfa_delta_alpha(vals[i - N:i]))
        dates.append(idx[i])
    return pd.Series(da, index=pd.DatetimeIndex(dates), dtype=float).dropna()


# ============================================================================
# 绘图：前 5 栏相变图 + 第 6 栏 Δα
# ============================================================================

def plot_panel(targets, start, end, out_path, da=None, da_window=250):
    """绘制 6 栏面板：原相变 5 栏 + MF-DFA 滚动 Δα 第 6 栏。

    da: pandas Series（index=日期，value=Δα）。若为 None 则用第一个标的的收盘价现算。
    """
    multi = len(targets) > 1
    fig, axes = plt.subplots(6, 1, figsize=(18, 31), sharex=True,
                             gridspec_kw={"height_ratios": [1.15, 1, 1, 1, 1, 1.1]})

    # ---------- ① 价格 ----------
    ax1 = axes[0]
    if multi:
        for k, t in enumerate(targets):
            px = t.price
            if px is None or px.empty:
                continue
            px = px[(px.index >= start) & (px.index <= end)]
            if px.empty:
                continue
            ax1.plot(pd.to_datetime(px.index, format="%Y%m%d"),
                     px["close"] / px["close"].iloc[0] * 100,
                     color=_pt.PALETTE[k % len(_pt.PALETTE)], lw=1.8, label=t.name)
        ax1.set_ylabel("归一化价格（起点=100）", fontsize=16)
        ax1.legend(loc="upper left", fontsize=13.5, ncol=2, framealpha=0.85)
        ax1.set_title("多标的归一化价格对比", fontsize=18, pad=26)
    else:
        t = targets[0]
        px = t.price[(t.price.index >= start) & (t.price.index <= end)] if t.price is not None else None
        if px is not None and not px.empty:
            idx = pd.to_datetime(px.index, format="%Y%m%d")
            ax1.plot(idx, px["close"], color="#1f3b73", lw=1.8, label="收盘价")
            ax1.set_ylabel("收盘价（元）", fontsize=16)
            if "vol" in px and px["vol"].notna().any():
                ax1b = ax1.twinx()
                ax1b.bar(idx, px["vol"], width=1.0, color="#8fb3e0", alpha=0.35, label="成交量")
                ax1b.set_ylabel("成交量（股）", fontsize=16)
        ax1.set_title("%s 价格与成交量 (%s - %s)" % (t.name, _pt._fmt(start), _pt._fmt(end)),
                      fontsize=18, pad=26)
    ax1.text(0.5, 1.005, "价格上行＝趋势偏多　|　价格下行、量能放大＝趋势偏空（价格不含相变判据）",
             transform=ax1.transAxes, fontsize=13, color="#444444", ha="center", va="bottom")
    ax1.grid(alpha=0.25)

    risk_note = ""
    if not multi and targets[0].metrics is not None and not targets[0].metrics.empty:
        m = targets[0].metrics
        ar_hi = m["ar1"] > max(0.6, float(m["ar1"].quantile(0.5)))
        risk = ((m["M"].diff(5) < 0) & (m["xi"].diff(5) < 0) & ar_hi).fillna(False).values
        segs = _pt.segments_by_pos(risk, m["date"].dt.to_pydatetime())
        for k, (s_, e_) in enumerate(segs):
            ax1.axvline(s_, color="0.45", ls="--", lw=1.2, alpha=0.9)
            ax1.axvline(e_, color="0.45", ls="--", lw=1.2, alpha=0.9)
            ax1.axvspan(s_, e_, color="0.85", alpha=0.25)
            if k == 0:
                ax1.text(s_, ax1.get_ylim()[1], " 风险预警区（集体瓦解）", color="0.3",
                         fontsize=11, va="top", ha="left")
        risk_note = "，风险预警区 %d 段" % len(segs)

    # ---------- ② M(t) + ξ(t) ----------
    ax2 = axes[1]
    ax2b = ax2.twinx()
    for k, t in enumerate(targets):
        m = t.metrics
        if m is None or m.empty:
            continue
        col = _pt.PALETTE[k % len(_pt.PALETTE)]
        ax2.plot(m["date"], m["M"], color=col, lw=2.0,
                 label=(t.name + " " if multi else "") + r"$M=\lambda_1/N$（左轴）")
        ax2b.plot(m["date"], m["xi"], color=col, lw=1.5, ls="--", alpha=0.85,
                  label=(t.name + " " if multi else "") + r"$\xi$（右轴）")
        if not multi:
            q90 = float(m["M"].quantile(0.9))
            for s_, e_ in _pt.segments_by_pos((m["M"] > q90).values, m["date"].dt.to_pydatetime()):
                ax2.axvspan(s_, e_, color="#d9c2ee", alpha=0.55)
            ax2.axhline(q90, color="#7b2d8e", ls=":", lw=1.1)
            ax2.text(m["date"].iloc[0], q90, " M 的 90 分位 %.3f（紫底=突破）" % q90,
                     color="#5c1f6b", fontsize=10.5, va="bottom")
    ax2.set_ylabel(r"$M = \lambda_1/N$（集体模式强度）", fontsize=16, color="#1f3b73")
    ax2.tick_params(axis="y", colors="#1f3b73")
    ax2b.set_ylabel(r"$\xi = \rho_{avg}/(1-\rho_{avg})$（共振强度）", fontsize=16, color="#1f77b4")
    ax2b.tick_params(axis="y", colors="#1f77b4")
    ax2.set_title("序参量 M(t) 与 关联长度 ξ(t)（M 左轴实线 / ξ 右轴虚线）", fontsize=18, pad=26)
    ax2.text(0.5, 1.005, "M 抬升且 ξ 抬升＝齐涨齐跌延续（偏多但脆弱）；M 与 ξ 同步回落＝集体模式瓦解（偏空）；两者近共线，须合并读",
             transform=ax2.transAxes, fontsize=13, color="#444444", ha="center", va="bottom")
    ax2.grid(alpha=0.25)
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2b.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=12,
               ncol=2 if multi else 1, framealpha=0.85)

    # ---------- ③ AR(1) ----------
    ax3 = axes[2]
    for k, t in enumerate(targets):
        m = t.metrics
        if m is None or m.empty:
            continue
        col = _pt.PALETTE[k % len(_pt.PALETTE)] if multi else "#7b2d8e"
        ax3.plot(m["date"], m["ar1"], color=col, lw=2.0,
                 label=t.name if multi else "AR(1) of M(t)")
        if not multi:
            q90 = float(m["ar1"].quantile(0.9))
            for s_, e_ in _pt.segments_by_pos((m["ar1"] > q90).values, m["date"].dt.to_pydatetime()):
                ax3.axvspan(s_, e_, color="#d9c2ee", alpha=0.55)
            ax3.axhline(q90, color="#7b2d8e", ls=":", lw=1.1)
            ax3.text(m["date"].iloc[0], q90, " 90 分位 %.2f（紫底=突破）" % q90,
                     color="#5c1f6b", fontsize=10.5, va="bottom")
    ax3.set_ylabel("AR(1) of M(t)", fontsize=16)
    ax3.set_title("临界慢化 AR(1) of M(t)（系统恢复变慢程度）", fontsize=18, pad=26)
    ax3.text(0.5, 1.005, "紫底＝突破本样本 90 分位，恢复最慢、结构最脆弱（偏空）；回落＝韧性恢复（偏多）",
             transform=ax3.transAxes, fontsize=13, color="#444444", ha="center", va="bottom")
    ax3.grid(alpha=0.25)
    if multi:
        ax3.legend(loc="upper left", fontsize=13, ncol=2, framealpha=0.85)

    # ---------- ④ 相变时序三指标（M1 AR(1) / M2 Δσ / M3 PSI）----------
    ax4 = axes[3]
    have_sig = (m is not None and not m.empty and "m1_ar" in m.columns) if not multi else True
    if not multi and have_sig:
        mm = m
        ax4.plot(mm["date"], mm["m1_ar"], color="#2e7d32", lw=1.8, label="M1 AR(1) 恢复力")
        ax4.axhline(0.6, color="#2e7d32", ls=":", lw=1.2)
        ax4.text(mm["date"].iloc[0], 0.6, " AR(1) 阈值 0.60", color="#2e7d32",
                 fontsize=10.5, va="bottom")
        ax4b = ax4.twinx()
        ax4b.plot(mm["date"], mm["m2_dsig"], color="#e65100", lw=1.2, alpha=0.85,
                  label="M2 Δσ（右轴）")
        ax4b.axhline(0, color="#e65100", lw=0.6, alpha=0.5)
        for s_, e_ in _pt.segments_by_pos((mm["chA"] == 1).values, mm["date"].dt.to_pydatetime()):
            ax4.axvspan(s_, e_, color="#ffcc80", alpha=0.5)
        ax4.set_ylabel("M1 AR(1)", fontsize=15, color="#2e7d32")
        ax4b.set_ylabel("M2 Δσ", fontsize=15, color="#e65100")
        ax4.set_title("相变时序通道A：M1 AR(1) 恢复力衰减（左轴） + M2 Δσ 方差放大（右轴）",
                      fontsize=18, pad=26)
        ax4.text(0.5, 1.005,
                 "橙底 = 通道A 亮（M1 且 M2 同时触发，抓广谱顶）　⚠️ 2026 年样本实测该通道零信息甚至反向",
                 transform=ax4.transAxes, fontsize=12.5, color="#8a4b00", ha="center", va="bottom")
        ax4.grid(alpha=0.25)
        lines1, labels1 = ax4.get_legend_handles_labels()
        lines2, labels2 = ax4b.get_legend_handles_labels()
        ax4.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=12,
                   framealpha=0.85)
    else:
        ax4.set_ylabel("M1 / M2", fontsize=15)
        ax4.set_title("相变时序通道A（多标的模式暂只画单标的）", fontsize=18, pad=26)
        ax4.grid(alpha=0.25)

    # ---------- ⑤ 通道B 吸收比率 λ1/N ----------
    ax5 = axes[4]
    if (not multi and m is not None and not m.empty
            and "m5_ar" in m.columns and m["m5_ar"].notna().any()):
        ax5.plot(m["date"], m["m5_ar"], color="#00695c", lw=1.8, label="M5 吸收比率 λ1/N（去共同因子）")
        ax5.plot(m["date"], m["m5_thr"], color="#b71c1c", ls="--", lw=1.2, label="120 日 P90 阈值")
        for s_, e_ in _pt.segments_by_pos((m["chB"] == 1).values, m["date"].dt.to_pydatetime()):
            ax5.axvspan(s_, e_, color="#80cbc4", alpha=0.5)
        ax5.set_ylabel("λ1/N", fontsize=15)
        ax5.set_title("相变截面通道B：吸收比率 λ1/N（Kritzman，去当日共同因子，含 MA60 方向约束）",
                      fontsize=18, pad=26)
        ax5.text(0.5, 1.005,
                 "青底 = 通道B 亮（破 120 日 P90 且收盘在 MA60 上，抓抱团顶）　"
                 "⚠️ 强同步/滞后指标，顶部当天常常不亮",
                 transform=ax5.transAxes, fontsize=12.5, color="#00564c", ha="center", va="bottom")
        ax5.legend(loc="upper left", fontsize=12, framealpha=0.85)
        ax5.grid(alpha=0.25)
    else:
        ax5.set_ylabel("λ1/N", fontsize=15)
        ax5.set_title("相变截面通道B：吸收比率（多标的模式暂只画单标的）", fontsize=18, pad=26)
        ax5.grid(alpha=0.25)

    # ---------- ⑥ MF-DFA 滚动 Δα ----------
    ax6 = axes[5]
    ax6.set_ylabel("Δα（多分形谱宽）", fontsize=15, color="#c62828")
    ax6.set_title("MF-DFA 滚动 Δα（窗口 N=%d，多分形谱宽 = 市场非单分形/多尺度涨落强度）" % da_window,
                  fontsize=18, pad=26)
    if not multi and targets[0].price is not None:
        # Δα 必须用「完整」价格历史算（滚动窗口要先预热 da_window 个交易日），
        # 算完再按绘图区间截取；先截价格再算会让第 6 栏永远为空。
        t = targets[0]
        if da is None and not t.price.empty:
            da = rolling_delta_alpha(t.price["close"], N=da_window)
        if da is not None and len(da) > 0:
            if not isinstance(da.index, pd.DatetimeIndex):
                da = pd.Series(da.values, index=_as_dt_index(da.index), dtype=da.dtype)
            t0 = pd.to_datetime(start, format="%Y%m%d")
            t1 = pd.to_datetime(end, format="%Y%m%d")
            da_win = da[(da.index >= t0) & (da.index <= t1)]
            da_win = da_win.dropna()
            if len(da_win) > 0:
                ax6.plot(da_win.index, da_win.values, color="#c62828", lw=1.6, label="Δα（滚动 %d 日）" % da_window)
                thr = float(da_win.quantile(0.9))
                ax6.axhline(thr, color="#ef6c00", ls="--", lw=1.4, label="90 分位阈值 %.3f" % thr)
                # 突破阈值处画红色竖虚线
                brk = (da_win.values > thr).astype(int)
                for s_, e_ in _pt.segments_by_pos((brk == 1), da_win.index.to_pydatetime(), gap=0, min_len=1):
                    ax6.axvline(s_, color="#c62828", ls=":", lw=1.0, alpha=0.8)
                ax6.text(0.5, 1.005,
                         "Δα 抬升＝多分形谱变宽、不同尺度涨落分化加剧（多尺度共振，常伴高波动/相变前兆）；"
                         "回落＝市场回归单分形（布朗式）。橙虚线=本样本 90 分位。",
                         transform=ax6.transAxes, fontsize=12.5, color="#444444",
                         ha="center", va="bottom")
                ax6.legend(loc="upper left", fontsize=12, framealpha=0.85)
            else:
                ax6.text(0.5, 0.5, "Δα 数据不足（滚动窗口 N=%d，需更长价格历史）" % da_window,
                         transform=ax6.transAxes, ha="center", fontsize=14)
        else:
            if t.price is None or t.price.empty:
                msg = "无价格数据，无法计算 Δα"
            else:
                msg = "Δα 数据不足（需 ≥%d 个交易日价格历史，当前 %d 日）" % (
                    da_window, len(t.price))
            ax6.text(0.5, 0.5, msg, transform=ax6.transAxes, ha="center", fontsize=14)
    else:
        ax6.text(0.5, 0.5, "多标的模式暂只画单标的 Δα", transform=ax6.transAxes, ha="center", fontsize=14)
    ax6.grid(alpha=0.25)

    axes[-1].xaxis.set_major_locator(mdates.MonthLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    axes[-1].set_xlabel("日期", fontsize=16)

    fig.suptitle("%s 相变监控面板 + MF-DFA Δα (%s - %s)" % ("、".join(t.name for t in targets),
                                                           _pt._fmt(start), _pt._fmt(end)),
                 fontsize=22, y=0.996)
    fig.text(0.5, 0.004,
             "前 5 栏＝相变监控（判读见原 phase_transition）；第 6 栏＝MF-DFA 滚动 Δα（窗口 %d，"
             "scales=[20,30,50,80,125]，q=[-4..4]）。\n"
             "Δα 只是结构状态描述，不是交易信号；突破 90 分位处画红色竖虚线。数据：DataHub。"
             % da_window,
             ha="center", va="bottom", fontsize=12.5, color="#333333", linespacing=1.6)
    fig.tight_layout(rect=[0, 0.045, 1, 0.985])
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white")
    plt.close(fig)
    return risk_note


# ============================================================================
# 入口（与 phase_transition.build 同构，仅绘图换成 6 栏）
# ============================================================================

def _mfdfa_start(start8, da_window):
    """价格取数起点：比绘图起点再往前约 da_window 个交易日（Δα 滚动窗口预热用）。"""
    days = int(da_window * 1.45) + 40
    return (pd.to_datetime(start8) - pd.Timedelta(days=days)).strftime("%Y%m%d")


def build(args):
    start8 = pd.to_datetime(args.start).strftime("%Y%m%d")
    end8 = pd.to_datetime(args.end).strftime("%Y%m%d")
    hist8 = (pd.to_datetime(args.start)
             - pd.Timedelta(days=int(args.window * 2.2) + 20)).strftime("%Y%m%d")
    # 相变指标只需 window+ar_window 预热；MF-DFA 的 Δα 还要额外 da_window 个交易日，
    # 价格单独从更早的 px_hist8 取，避免把成分股日线也拉长（取数成本）。
    px_hist8 = _mfdfa_start(start8, args.da_window)

    hub = _pt._ensure_datahub()
    font = _pt._ensure_cjk()
    print("[phase_mfdfa] DataHub=%s 字体=%s" % (hub or "-", font or "默认"))

    cache_dir = os.path.join(WS, ".cache", "phase_transition")
    os.makedirs(cache_dir, exist_ok=True)

    targets = _pt.build_targets(args)
    for t in targets:
        print("    标的 %s(%s) 成分股 %d 只" % (t.name, t.code, len(t.members)))

    from data_hub.collectors.stocks import get_stock_daily, get_index_daily
    for t in targets:
        if len(t.members) < 15:
            print("    %s: 有效成分股不足 15 只，跳过" % t.name)
            continue
        closes = _pt.fetch_closes(t.members, hist8, end8, args.max_members, cache_dir)
        if closes.shape[1] < 15:
            print("    %s: 可用日线 %d 只，跳过" % (t.name, closes.shape[1]))
            continue
        t.metrics = _pt.compute_metrics(closes, args.window, args.ar_window)
        if t.metrics.empty:
            print("    %s: 指标为空，跳过" % t.name)
            continue
        print("    %s: 成分股 %d 只，指标 %d 个交易日（%s ~ %s）" % (
            t.name, closes.shape[1], len(t.metrics),
            t.metrics["date"].iloc[0].date(), t.metrics["date"].iloc[-1].date()))

        px = None
        if args.price_code and len(targets) == 1:
            px = _pt._to_series(get_stock_daily(
                args.price_code, start_date=px_hist8, end_date=end8))
        if px is None or px.empty:
            try:
                px = _pt._to_series(get_index_daily(
                    t.code if "." in t.code else t.code + ".SH",
                    start_date=px_hist8, end_date=end8))
            except Exception:
                px = None
        if px is None or px.empty:
            eq = closes.mean(axis=1, skipna=True)
            px = pd.DataFrame({"close": eq, "vol": np.nan}).dropna(subset=["close"])
            if not px.empty:
                print("    %s: 指数日线不可用，图①改用成分股等权价格" % t.name)
        t.price = px

    targets = [t for t in targets if t.metrics is not None and not t.metrics.empty]
    if not targets:
        raise SystemExit("[phase_mfdfa] 没有可用标的，检查代码或数据源")

    out = args.out or os.path.join(
        WS, "reports", "charts", "phase_mfdfa",
        "相变监控_MFDFA_%s_%s_%s.png" % ("_".join(t.code for t in targets), start8, end8))
    note = plot_panel(targets, start8, end8, out, da_window=args.da_window)
    print("[phase_mfdfa] 输出 %s%s" % (out, note))

    for t in targets:
        m = t.metrics
        print("    %s: M %.3f->%.3f | ξ %.3f->%.3f | AR1 %.2f | λ1/λ2 %.2f | 谱熵 %.2f" % (
            t.name, m["M"].iloc[0], m["M"].iloc[-1], m["xi"].iloc[0], m["xi"].iloc[-1],
            m["ar1"].iloc[-1], m["lam_ratio"].iloc[-1], m["entropy"].iloc[-1]))
    return out


def main():
    ap = argparse.ArgumentParser(description="相变监控图 + MF-DFA 滚动 Δα 子图")
    ap.add_argument("--index", default="", help="宽基指数代码，逗号分隔，如 000688 或 000300,000905")
    ap.add_argument("--industry", default="", help="申万行业代码，逗号分隔")
    ap.add_argument("--sw-level", type=int, default=1)
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--window", type=int, default=60, help="滚动相关矩阵窗口（交易日）")
    ap.add_argument("--ar-window", type=int, default=30, help="AR(1) 滚动窗口（M 值个数）")
    ap.add_argument("--max-members", type=int, default=60)
    ap.add_argument("--price-code", default="", help="单标的模式下图①使用的价格/成交量代码，如 588000.SH")
    ap.add_argument("--da-window", type=int, default=250, help="MF-DFA 滚动窗口（交易日）")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if not a.index and not a.industry:
        ap.error("至少给 --index 或 --industry 之一")
    if a.end < a.start:
        ap.error("--end 不能早于 --start")
    build(a)


if __name__ == "__main__":
    main()