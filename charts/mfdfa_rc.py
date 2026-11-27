#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mfdfa_rc · MF-DFA / λ1·λ2 监控图（**只画图，不实现任何算法**）
=====================================================================
大佬 2026-11-27：「直接用原来的代码，只不过你处理画图的接口，不要重复代码，
我记得原来就有 mfdfa 代码」

所以这里的分工是死的：
  · MF-DFA 算法  → `charts/phase_mfdfa.py`（`mfdfa_delta_alpha` /
                    `rolling_delta_alpha`，唯一实现，本文件只 import）
  · λ1/λ2 算法   → `charts/rmt_rc.py`（`series_for` / `slope_frame`，唯一实现）
  · 收盘价取数   → `misc/engine/rcascade.py::compute`（唯一实现）
  · 本文件       → **只有画图**

2026-11-27 之前这里有 `plot_panel()` 和 `plot_from_series()` 两份**几乎逐行相同**
的画图代码（都画「①走势 ②Δα ③λ1/λ2」），改一处得改两处。
现在统一成唯一一份 `render()`：

    取数包装  plot_panel(按代码自己取数)      ─┐
                                              ├→ render(close, da, lam, ...)  ← 唯一画图
    序列直绘  plot_from_series(别人算好的序列) ─┘

`plot_panel` / `plot_from_series` 签名保持不变，老的调用方（日报、告警邮件、
CLI）一行都不用改。

用法：
  python3 charts/mfdfa_rc.py --code 000688 --typ index_sh
  python3 charts/mfdfa_rc.py --code 000688 --lookback 250 --out /tmp/x.png
  python3 charts/mfdfa_rc.py --code 000688 --no-lam --with-rc
"""
from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403  (sys.path / net_guard / 中文字体 / Agg)

import matplotlib.dates as mdates  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

try:                                                        # MF-DFA Δα 唯一实现
    from charts.phase_mfdfa import rolling_delta_alpha       # noqa: E402
except Exception as _mfdfa_ex:                              # noqa: BLE001
    # 库不可用时 Δα 栏留空，**不另写一套 MF-DFA**（避免两套口径）
    rolling_delta_alpha = None
    print("[mfdfa_rc] ⚠️ charts/phase_mfdfa.py 不可用（%s）→ Δα 栏留空"
          % str(_mfdfa_ex)[:80], flush=True)

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════ 唯一画图实现 ══════════════════════════════
def render(out, close, da=None, lam=None, name=None, label=None,
           lookback=None, da_p90=None, rc=None, rc_mod=None, title_extra=None):
    """**唯一**画图函数：① 走势 ② MF-DFA Δα ③ λ1/λ2 ［④ R_cascade］。

    Args:
        out       : 输出 png 路径
        close     : pd.Series 收盘价（index = datetime 或 YYYYMMDD）
        da        : pd.Series MF-DFA Δα；None → 不出 Δα 栏
        lam       : DataFrame 含 l1/l2/ma1/ma2/s1/s2；None → 不出 λ 栏
        name      : 标题里的标的名（默认取 label 或 close.name）
        label     : 额外标注（如 6 位代码），拼进副标题
        lookback  : 只画最近 N 个点
        da_p90    : Δα 的 P90 阈值（None → 用 da 自身 quantile(0.9)）
        rc        : pd.Series R_cascade；给值才画第 ④ 栏
        rc_mod    : rcascade 模块（提供 THR_HIGH/THR_LOW/THR_DEEP）
        title_extra: 追加到副标题的说明

    Returns: out 路径
    """
    close = pd.Series(close).dropna()
    if not len(close):
        raise ValueError("render: close 为空")
    if lookback:
        close = close.iloc[-lookback:]

    ci = _as_dt(close.index)
    t0, t1 = ci[0], ci[-1]

    # ── 各栏数据裁剪到 close 的日期范围内（宽度 × 长度对齐）
    da_w = di_w = None
    if da is not None and len(da):
        da = pd.Series(da).dropna()
        di_full = _as_dt(da.index)
        m = (di_full >= t0) & (di_full <= t1)
        if m.any():
            da_w, di_w = da.values[m], di_full[m]
    if da_p90 is None and da_w is not None and len(da_w):
        da_p90 = float(np.quantile(da_w, 0.9))

    lam_w = li_w = None
    if lam is not None and len(lam):
        lam = lam.copy()
        li_full = _as_dt(lam.index)
        m = (li_full >= t0) & (li_full <= t1)
        if m.any():
            lam_w, li_w = lam[m], li_full[m]

    rc_w = None
    if rc is not None and len(rc):
        rc = pd.Series(rc)
        ri_full = _as_dt(rc.index)
        m = (ri_full >= t0) & (ri_full <= t1)
        if m.any():
            rc_w = rc[m]

    # ── 排版
    parts = [("走势", 1.0)]
    if da_w is not None:
        parts.append(("Δα", 1.0))
    if lam_w is not None:
        parts.append(("λ", 1.0))
    if rc_w is not None:
        parts.append(("R", 1.05))
    n = len(parts)
    fig, axes = plt.subplots(n, 1, figsize=(13.5, 3.6 * n), sharex=True,
                             squeeze=False,
                             gridspec_kw={"height_ratios": [h for _, h in parts]})
    axes = [a[0] for a in axes]
    k = 0

    # ① 走势
    ax = axes[k]; k += 1
    ax.plot(ci, close.values, color="#1f3b73", lw=1.7,
            label="%s 收盘价" % (label or ""))
    ax.set_ylabel("收盘价", fontsize=12)
    ax.set_title("① 走势 · 近 %d 交易日" % len(close), fontsize=13, pad=8)
    ax.grid(alpha=0.25)

    # ② MF-DFA Δα
    if da_w is not None:
        ax = axes[k]; k += 1
        ax.plot(di_w, da_w, color="#c62828", lw=1.6, label="MF-DFA 滚动 Δα（N=250）")
        ax.axhline(da_p90, color="#ef6c00", ls="--", lw=1.3,
                   label="P90 = %.3f" % da_p90)
        for i in np.where(da_w > da_p90)[0]:
            ax.axvline(di_w[i], color="#c62828", ls=":", lw=1.0, alpha=0.75)
        ax.set_ylabel("Δα", fontsize=12, color="#c62828")
        ax.set_title("② MF-DFA 多分形谱宽 Δα · scales=[20,30,50,80,125] · q=[-4..4]",
                     fontsize=12, pad=8)
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left", fontsize=10)
        ax.annotate("最新 Δα=%.3f" % da_w[-1], xy=(di_w[-1], da_w[-1]),
                    xytext=(6, 6), textcoords="offset points",
                    color="#c62828", fontsize=10.5, fontweight="bold")

    # ③ λ1 / λ2 + MA5
    if lam_w is not None:
        ax = axes[k]; k += 1
        for col, color, lab, ls in (("l1", "#c62828", "λ1", "-"),
                                    ("l2", "#1f5fbf", "λ2", "-"),
                                    ("ma1", "#ef6c00", "λ1 MA5", "--"),
                                    ("ma2", "#4da3ff", "λ2 MA5", "--")):
            if col in lam_w.columns:
                ax.plot(li_w, lam_w[col].values, color=color,
                        lw=1.5 if ls == "-" else 1.4, ls=ls,
                        alpha=0.95 if ls == "-" else 0.9, label=lab)
        ax.set_ylabel("λ（特征值）", fontsize=12)
        ax.set_title("③ λ1 / λ2 · 60 日滚动相关矩阵前两个特征值（虚线 = MA5）",
                     fontsize=12, pad=8)
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left", fontsize=9.5, ncol=2)
        try:
            r = lam_w.iloc[-1]
            d1 = "↑" if r["s1"] > 0 else ("↓" if r["s1"] < 0 else "→")
            d2 = "↑" if r["s2"] > 0 else ("↓" if r["s2"] < 0 else "→")
            ax.annotate("λ1=%.2f(MA5 %.2f, %+.3f%s) ｜ λ2=%.2f(MA5 %.2f, %+.3f%s)"
                        % (r["l1"], r["ma1"], r["s1"], d1,
                           r["l2"], r["ma2"], r["s2"], d2),
                        xy=(0.005, 0.03), xycoords="axes fraction", fontsize=9,
                        bbox=dict(boxstyle="round,pad=0.28", fc="#f6f6f6", ec="#ddd"))
        except Exception:                                    # noqa: BLE001
            pass

    # ④ R_cascade（仅显式给 rc 时才画；2026-11-24 起默认口径已换成 Δα）
    if rc_w is not None:
        ax = axes[k]; k += 1
        ax.plot(_as_dt(rc_w.index), rc_w.values, color="#4da3ff", lw=1.7,
                label="R_cascade = σ20/σ60")
        if rc_mod is not None:
            for lvl, color, ls, txt in (
                    (getattr(rc_mod, "THR_HIGH", None), "#ff4d4f", "--", "危险"),
                    (getattr(rc_mod, "THR_LOW", None), "#52c41a", "--", "安全"),
                    (getattr(rc_mod, "THR_DEEP", None), "#b37feb", "-.", "低波动陷阱")):
                if lvl is not None:
                    ax.axhline(lvl, color=color, lw=1.2, ls=ls,
                               label="%.2f %s" % (lvl, txt))
        ax.axhline(1.0, color="#888", lw=0.8, ls=":")
        ax.set_ylabel("R_cascade", fontsize=12, color="#1f5fbf")
        ax.set_title("④ R_cascade 波动率级联比 · σ20/σ60（短窗20/长窗60）",
                     fontsize=12, pad=8)
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left", fontsize=10)
        ax.annotate("最新 R=%.3f" % rc_w.values[-1], xy=(0.005, 0.03),
                    xycoords="axes fraction", color="#1f5fbf",
                    fontsize=10, fontweight="bold")

    # ── x 轴 / 总标题
    last = axes[k - 1]
    last.xaxis.set_major_locator(mdates.MonthLocator())
    last.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    last.set_xlabel("日期", fontsize=11)

    head = name or label or ""
    sub = " / ".join(t for t, _ in parts)
    title = "%s · %s\n数据截至 %s" % (head, sub, pd.Timestamp(t1).strftime("%Y-%m-%d"))
    if title_extra:
        title += " ｜ %s" % title_extra
    fig.suptitle(title, fontsize=13.5, y=0.997)
    fig.tight_layout(rect=[0, 0, 1, 0.972])

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    fig.savefig(out, dpi=140, facecolor="white")
    plt.close(fig)
    return out


def _as_dt(idx):
    """index → DatetimeIndex（兼容 YYYYMMDD 字符串 / int / datetime）。"""
    s = [_d8safe(x) for x in idx]
    if all(s):
        return pd.to_datetime(s, format="%Y%m%d")
    return pd.to_datetime(idx)


def _d8safe(x):
    t = str(x).replace("-", "").replace("/", "")[:8]
    return t if len(t) == 8 and t.isdigit() else ""


# ══════════════════════════════════ 两个薄包装（保持旧签名）════════════════════════
def plot_from_series(name, close, da, lam, out, lookback=None, da_p90=None,
                     lam_win=None):
    """用**已算好的序列**出图（日报/邮件复用，避免重复取数与重算）。

    签名与 2026-11-27 之前完全一致；内部转调唯一的 `render()`。
    """
    return render(out, close, da=da, lam=(lam_win if lam_win is not None else lam),
                  name=name, lookback=lookback, da_p90=da_p90)


def plot_panel(code="000688", typ="index_sh", lookback=250, out=None,
               no_lam=False, with_rc=False):
    """按代码自己取数后出图（CLI 用）。内部同样转调 `render()`。

    code     : 6 位指数代码
    typ      : 数据源类型（index_sh / index_sz / sw / sector_ind / sector_con）
    lookback : 近 N 个交易日
    no_lam   : True = 不画 λ1/λ2 栏
    with_rc  : True = 追加 R_cascade 栏（2026-11-24 起默认口径是 Δα，故默认 False）
    """
    from charts import rmt_rc as RRC

    RC = _load_rc()
    if RC is None:
        raise SystemExit("取数失败：加载 misc/engine/rcascade.py 失败")
    d = RC.compute(typ, str(code))
    if d is None or len(d) < 60:
        raise SystemExit("取数失败：%s(%s) 数据不足" % (typ, code))

    win = d.iloc[-lookback:].copy()
    close = win["close"].astype(float)

    # ② Δα：算法在 phase_mfdfa（唯一实现），这里只调
    da = None
    if rolling_delta_alpha is not None:
        full_da = rolling_delta_alpha(d["close"].astype(float), N=250)
        if full_da is not None and len(full_da):
            da = full_da

    # ③ λ1/λ2：算法在 rmt_rc（唯一实现）
    lam = None
    if not no_lam:
        try:
            full = RRC.series_for(_name_from_code(code) or code, win=60, verbose=False)
            if full is not None and len(full):
                lam = RRC.slope_frame(full, ma=RRC.SLOPE_MA)
        except Exception as e:                               # noqa: BLE001
            print("[mfdfa_rc] λ 序列不可用（%s）→ 跳过 λ 栏" % str(e)[:80], flush=True)

    out = out or os.path.join(WS, ".cache", "mfdfa_rc_%s.png" % code)
    p = render(out, close, da=da, lam=lam, name=str(code), label=str(code),
               rc=(win["RC"] if with_rc else None), rc_mod=(RC if with_rc else None))

    # ── 控制台摘要（保持老版输出习惯）
    print("窗口: %s ~ %s" % (win.index[0].strftime("%Y%m%d"),
                             win.index[-1].strftime("%Y%m%d")))
    if da is not None and len(da):
        s = da.dropna()
        v = float(s.iloc[-1])
        print("最新 Δα = %.4f | 90分位 = %.4f | 全历史分位 = %.1f%%"
              % (v, float(s.quantile(0.9)), float((s <= v).mean() * 100)))
    if lam is not None and len(lam):
        r = lam.iloc[-1]
        print("最新 λ1=%.4f (MA5=%.4f, 斜率=%+.4f) | λ2=%.4f (MA5=%.4f, 斜率=%+.4f)"
              % (r["l1"], r["ma1"], r["s1"], r["l2"], r["ma2"], r["s2"]))
    if with_rc and "RC" in win.columns:
        print("最新 R_cascade = %.3f" % float(win["RC"].iloc[-1]))
    print("图 →", p)
    return p


# ══════════════════════════════════════ 取数辅助 ══════════════════════════════════
def _load_rc():
    """加载 misc/engine/rcascade.py（收盘价 / R_cascade 的唯一实现）。失败返回 None。"""
    fp = os.path.join(WS, "misc", "engine", "rcascade.py")
    if not os.path.exists(fp):
        print("[mfdfa_rc] 找不到 rcascade.py（%s）" % fp, flush=True)
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("rcascade_mfdfa", fp)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    except Exception as e:                                   # noqa: BLE001
        print("[mfdfa_rc] 加载 rcascade.py 失败：%s" % str(e)[:100], flush=True)
        return None


def _name_from_code(code):
    """6 位代码 → rmt_rc.TARGETS 里的宽基名；找不到返回 None。"""
    try:
        from charts import rmt_rc as RRC
    except Exception:                                        # noqa: BLE001
        return None
    c = str(code).upper()
    for nm, (tcode, etf) in RRC.TARGETS.items():
        if c in (str(tcode).upper(), str(etf).upper(), str(tcode).split(".")[0]):
            return nm
    return None


# ══════════════════════════════════════ CLI ══════════════════════════════════════
def main(argv=None):
    ap = argparse.ArgumentParser(description="MF-DFA / λ1·λ2 监控图（只画图）")
    ap.add_argument("--code", default="000688", help="6 位指数代码（默认 000688 科创50）")
    ap.add_argument("--typ", default="index_sh",
                    help="数据源类型：index_sh / index_sz / sw / sector_ind / sector_con")
    ap.add_argument("--lookback", type=int, default=250, help="近 N 个交易日")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-lam", action="store_true", help="不画 λ1/λ2 栏")
    ap.add_argument("--with-rc", action="store_true",
                    help="追加 R_cascade 栏（默认不画，口径已被 Δα 取代）")
    a = ap.parse_args(argv)
    return 0 if plot_panel(a.code, a.typ, a.lookback, a.out,
                           a.no_lam, a.with_rc) else 1


if __name__ == "__main__":
    sys.exit(main())
