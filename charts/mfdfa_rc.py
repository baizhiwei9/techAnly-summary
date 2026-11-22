#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mfdfa_rc · 多分形谱三栏监控图（走势 + MF-DFA Δα + R_cascade 波动率级联比）
====================================================================
三栏堆叠，一图看完「走势 / 多分形 / 波动率级联」：
  ① 走势图     收盘价（近 lookback 交易日）
  ② MF-DFA Δα  多分形谱宽度滚动序列，复用 charts.phase_mfdfa.rolling_delta_alpha
  ③ R_cascade  波动率级联比 σ20/σ60，直接调 misc/engine/rcascade.py::compute()（唯一实现）

口径（唯一权威，不另写一套）：
  MF-DFA Δα : charts/phase_mfdfa.mfdfa_delta_alpha（scales=[20,30,50,80,125], q=[-4..4]）
  R_cascade : misc/engine/rcascade.compute（σ20/σ60，短窗 20 / 长窗 60）
              阈值/状态读 rcascade 模块常量 THR_HIGH/THR_LOW/THR_DEEP

用法：
  python3 charts/mfdfa_rc.py --code 000688 --typ index_sh
  python3 charts/mfdfa_rc.py --code 000688 --lookback 250 --out /tmp/x.png
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403  (sys.path / net_guard / 中文字体 / Agg)
from charts.phase_mfdfa import rolling_delta_alpha  # noqa: E402  MF-DFA Δα 唯一实现

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402


# ============================================================================
# 取数：R_cascade / 收盘价 都走 misc/engine/rcascade.py（唯一实现）
# ============================================================================

def _load_rc():
    """加载 misc/engine/rcascade.py 模块。失败返回 None。"""
    fp = os.path.join(WS, "misc", "engine", "rcascade.py")
    if not os.path.exists(fp):
        print("[mfdfa_rc] 找不到 rcascade.py（%s）" % fp, flush=True)
        return None
    try:
        if "rcascade" in sys.modules:
            return sys.modules["rcascade"]
        spec = importlib.util.spec_from_file_location("rcascade", fp)
        RC = importlib.util.module_from_spec(spec)
        sys.modules["rcascade"] = RC
        spec.loader.exec_module(RC)
        return RC
    except Exception as ex:                                       # noqa: BLE001
        print("[mfdfa_rc] 加载 rcascade.py 失败：%s" % str(ex)[:100], flush=True)
        return None


def _rc_state(rc_mod, v):
    """R_cascade 状态（阈值/口径读 rcascade 模块，不另写）。"""
    if pd.isna(v):
        return "数据不足"
    if v > rc_mod.THR_HIGH:
        return "🔴 危险区 >1.2（小尺度波动失控）"
    if v < rc_mod.THR_DEEP:
        return "🟣 低波动陷阱 <0.75"
    if v < rc_mod.THR_LOW:
        return "🟢 安全区 <0.8（能量集中大尺度）"
    return "🟡 中性带 0.8~1.2"


# ============================================================================
# 绘图：①走势 / ②MF-DFA Δα / ③R_cascade
# ============================================================================

def plot_panel(code="000688", typ="index_sh", lookback=250, out=None):
    """绘制三栏多分形谱监控图。

    code     : 6 位指数代码（默认 000688 科创50）
    typ      : 数据源类型（index_sh / index_sz / sw / sector_ind / sector_con）
    lookback : 近 N 个交易日窗口（默认 250 ≈ 一年）
    """
    RC = _load_rc()
    if RC is None:
        raise SystemExit("取数失败：加载 misc/engine/rcascade.py 失败")

    d = RC.compute(typ, str(code))     # DataFrame[dt, ohlc, r, RC, vol20, vol60]，index=datetime
    if d is None or len(d) < 60:
        raise SystemExit("取数失败：%s(%s) 数据不足" % (typ, code))

    win = d.iloc[-lookback:].copy()
    end = win.index[-1]

    # ② MF-DFA Δα：全历史算，再截近 lookback 窗口
    da = rolling_delta_alpha(d["close"].astype(float), N=250)
    t0 = win.index[0]
    da_win = da[(da.index >= t0) & (da.index <= end)].dropna()

    rc_win = win["RC"]
    v20_win = win["vol20"]
    v60_win = win["vol60"]

    thr_da = float(da_win.quantile(0.9))
    lv_da = float(da.iloc[-1])
    lv_rc = float(rc_win.iloc[-1])
    rc_state = _rc_state(RC, lv_rc)

    # ---------- 画图 ----------
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 11.5), sharex=True,
                                        gridspec_kw={"height_ratios": [1.0, 1.0, 1.05]})

    # ① 走势：收盘价
    ax1.plot(win.index, win["close"].values, color="#1f3b73", lw=1.8,
             label="%s 收盘价" % code)
    ax1.set_ylabel("收盘价（点）", fontsize=13)
    ax1.set_title("① 走势 · 近 %d 交易日" % lookback, fontsize=15, pad=10)
    ax1.grid(alpha=0.25)
    ax1.legend(loc="upper left", fontsize=11)

    # ② MF-DFA 多分形 Δα
    ax2.plot(da_win.index, da_win.values, color="#c62828", lw=1.6,
             label="MF-DFA 滚动 Δα（N=250）")
    ax2.axhline(thr_da, color="#ef6c00", ls="--", lw=1.4, label="90分位阈值 %.3f" % thr_da)
    for i in np.where(da_win.values > thr_da)[0]:
        ax2.axvline(da_win.index[i], color="#c62828", ls=":", lw=1.0, alpha=0.8)
    ax2.set_ylabel("Δα（多分形谱宽）", fontsize=13, color="#c62828")
    ax2.set_title("② MF-DFA 多分形谱宽 Δα · scales=[20,30,50,80,125] · q=[-4..4]",
                  fontsize=14, pad=10)
    ax2.grid(alpha=0.25)
    ax2.legend(loc="upper left", fontsize=11)
    ax2.annotate("最新 Δα=%.3f" % lv_da, xy=(da_win.index[-1], lv_da),
                 xytext=(8, 8), textcoords="offset points",
                 color="#c62828", fontsize=11, fontweight="bold")

    # ③ R_cascade 波动率级联比
    ax3.plot(rc_win.index, rc_win.values, color="#4da3ff", lw=1.7,
             label="R_cascade = σ20/σ60")
    ax3.axhline(RC.THR_HIGH, color="#ff4d4f", lw=1.2, ls="--")
    ax3.axhline(RC.THR_LOW, color="#52c41a", lw=1.2, ls="--")
    ax3.axhline(RC.THR_DEEP, color="#b37feb", lw=1.0, ls="-.")
    ax3.axhline(1.0, color="#888", lw=0.8, ls=":")
    ax3.fill_between(rc_win.index, RC.THR_HIGH, rc_win.clip(lower=RC.THR_HIGH).values,
                     where=(rc_win.values > RC.THR_HIGH), color="#ff4d4f", alpha=0.12)
    ax3.fill_between(rc_win.index, rc_win.clip(upper=RC.THR_DEEP).values, RC.THR_DEEP,
                     where=(rc_win.values < RC.THR_DEEP), color="#b37feb", alpha=0.12)
    ax3.set_ylabel("R_cascade", fontsize=13, color="#1f5fbf")
    ax3.set_title("③ R_cascade 波动率级联比 · σ20/σ60（短窗20/长窗60）", fontsize=14, pad=10)
    ax3.grid(alpha=0.25)
    ax3.legend(loc="upper left", fontsize=11)
    ymin = min(0.55, float(rc_win.min()) - 0.05)
    ymax = max(1.45, float(rc_win.max()) + 0.05)
    ax3.set_ylim(ymin, ymax)
    ax3.text(rc_win.index[-1], RC.THR_HIGH, " %.1f 危险" % RC.THR_HIGH,
             color="#ff4d4f", va="bottom", ha="right", fontsize=9.5)
    ax3.text(rc_win.index[-1], RC.THR_LOW, " %.1f 安全 " % RC.THR_LOW,
             color="#52c41a", va="top", ha="right", fontsize=9.5)
    ax3.text(rc_win.index[-1], RC.THR_DEEP, " %.2f 低波动陷阱 " % RC.THR_DEEP,
             color="#b37feb", va="top", ha="right", fontsize=9.5)
    ax3.annotate("最新 R=%.3f" % lv_rc, xy=(rc_win.index[-1], lv_rc),
                 xytext=(8, 8), textcoords="offset points",
                 color="#1f5fbf", fontsize=11, fontweight="bold")

    ax3.xaxis.set_major_locator(mdates.MonthLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax3.set_xlabel("日期", fontsize=12)

    fig.suptitle("%s · ① 走势 / ② MF-DFA 多分形 Δα / ③ R_cascade 波动率级联比\n"
                 "数据截至 %s" % (code, end.strftime("%Y-%m-%d")), fontsize=15, y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out = out or os.path.join(WS, ".cache", "mfdfa_rc_%s.png" % code)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)

    print("窗口: %s ~ %s" % (win.index[0].strftime("%Y%m%d"), end.strftime("%Y%m%d")))
    print("最新 Δα = %.4f | 90分位阈值 = %.4f | 全历史分位 = %.1f%%" %
          (lv_da, thr_da, float((da <= lv_da).mean() * 100)))
    print("最新 R_cascade = %.3f | %s" % (lv_rc, rc_state))
    print("vol20 = %.1f%% | vol60 = %.1f%%" %
          (float(v20_win.iloc[-1]), float(v60_win.iloc[-1])))
    print("图 →", out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="多分形谱三栏监控图（走势 + MF-DFA Δα + R_cascade）")
    ap.add_argument("--code", default="000688", help="6 位指数代码（默认 000688 科创50）")
    ap.add_argument("--typ", default="index_sh",
                    help="数据源类型：index_sh / index_sz / sw / sector_ind / sector_con")
    ap.add_argument("--lookback", type=int, default=250, help="近 N 个交易日窗口（默认 250 ≈ 一年）")
    ap.add_argument("--out", default=None, help="输出 PNG 路径（默认 .cache/mfdfa_rc_<code>.png）")
    args = ap.parse_args(argv)
    plot_panel(code=args.code, typ=args.typ, lookback=args.lookback, out=args.out)


if __name__ == "__main__":
    main()
