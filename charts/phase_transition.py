#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""phase_transition · 相变监控图（序参量 / 关联长度 / 临界慢化）
====================================================================
数据一律经 DataHub 统一入口获取（其内部 tushare > akshare > web 逐级降级），
本模块不直连任何单一数据源，也不在本地重算 DataHub 已有的口径。

指标（2026-11-19 扩充，算法在 charts/phase_signals.py）
-----------------------------------------------------------
截面口径（成分股日收益滚动相关矩阵特征分解）：
  M(t)  = λ1 / N                 序参量（最大特征值占比＝集体模式强度）
  ξ(t)  = ρ_avg / (1 - ρ_avg)    关联长度（跨股票共振强度）
  AR(1) = corr(M_t, M_{t-1})     临界慢化（滚动 ar_window 个 M 值）
  M5    = λ1 / N（去当日截面共同因子）  吸收比率 Kritzman —— 与 M 口径不同，别混

时序口径（等权组合收益，charts/phase_signals.py）：
  M1    = AR(1) of [log(等权价) − MA20]   恢复力衰减，阈值 > 0.6 连续 3 日
  M2    = σ20(t) − σ20(t−20)，对比 σ 自身的 20 日标准差   方差放大，阈值 3σ
  M3    = σ / λ²（λ 由 log ρ_k ~ k 斜率）   PSI 结构脆弱度，阈值破 120 日 P90

双通道：
  通道A = M1 AND M2   → 抓广谱顶（7 月型）
  通道B = M5 破 P90 且收盘 > MA60 → 抓抱团顶（10 月型）

注：λ1/λ2 与谱熵仍参与计算并打印，供命令行核对；不再单独出图（大佬 2026-11-17 定稿：
    删掉 RMT 那一栏，M 与 ξ 合并成一张双轴图）。

五栏结构（2026-11-19 由三栏扩充）：
  ① 价格 + 成交量（单标的）／归一化价格对比（多标的）
  ② M（左轴实线）与 ξ（右轴虚线）合并
  ③ AR(1) of M，与 ② 一并在突破 90 分位区间铺紫底
  ④ M1 AR(1) 恢复力 + M2 Δσ（双轴），橙底 = 通道A 亮
  ⑤ M5 吸收比率 λ1/N + 120 日 P90 阈值，青底 = 通道B 亮

用法：
  python3 charts/phase_transition.py --index 000688 --price-code 588000.SH
  python3 charts/phase_transition.py --index 000300,000905,000852
  python3 charts/phase_transition.py --industry 801010,801080 --start 2026-06-01
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
from datetime import datetime

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from charts.common import *  # noqa: F401,F403  (仓库根/sys.path/net_guard/Agg/中文字体)

import numpy as np
import pandas as pd
import matplotlib.dates as mdates  # noqa: E402


# ============================================================================
# 环境适配：DataHub 位置 + 中文字体兜底
# ============================================================================

def _ensure_datahub():
    """让 data_hub 及其依赖（akshare/tushare）可 import；返回 DataHub 代码库路径。

    优先环境变量 DATA_HUB_PATH / DATAHUB_HOME；其次按仓库相对位置找，
    最后退回本机已知位置。找不到时抛错，不静默降级到别的取数链路。
    """
    cands = []
    for env in ("DATA_HUB_PATH", "DATAHUB_HOME"):
        if os.environ.get(env):
            cands.append(os.environ[env])
    cands.extend([
        os.path.join(WS, "..", "datahub-three-tier"),
        os.path.join(WS, "..", "..", "projects", "datahub-three-tier"),
        "/workspace/projects/datahub-three-tier",
    ])
    dep_cands = [
        os.environ.get("DATAHUB_DEP_DIRS", ""),
        os.path.join(WS, "..", "datahub_deps"),
        os.path.join(WS, "..", "..", "datahub_deps"),
        "/workspace/datahub_deps",
        os.path.join(WS, "..", "py_deps"),
        "/workspace/py_deps",
    ]
    for d in dep_cands:
        if d and os.path.isdir(d) and d not in sys.path:
            sys.path.append(d)
    hub = ""
    for c in cands:
        if c and os.path.isdir(c) and c not in sys.path:
            sys.path.insert(0, c)
            hub = c
            break
    try:
        from data_hub.collectors.stocks import (get_index_components, get_stock_daily,  # noqa: F401
                                                get_index_daily)
        from data_hub.collectors.stocks.sw_industry import get_sw_members  # noqa: F401
        from data_hub import config as dh_cfg
    except Exception as e:  # 依赖缺失时明确失败，避免拿别的源顶替
        raise SystemExit("[phase] DataHub 不可用：%s（检查 DATA_HUB_PATH / DATAHUB_DEP_DIRS）" % e)
    dh_cfg.TIMEOUT_CONFIG.http_timeout = int(os.environ.get("DH_HTTP_TIMEOUT", "10"))
    dh_cfg.TIMEOUT_CONFIG.max_retries = int(os.environ.get("DH_MAX_RETRIES", "1"))
    return hub


def _ensure_cjk():
    """charts.common 注册的字体在本机可能不存在，这里补一个覆盖 ASCII/希腊/汉字的字体。

    ★ 不要用只有汉字的 fallback 字体（如 Droid Sans Fallback）：数字与字母会变成缺字方框。
    """
    cands = [os.environ.get("TECH_FONT", ""),
             "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf",
             os.path.join(WS, ".fonts", "NotoSansSC-Regular.ttf"),
             os.path.join(WS, ".fonts", "NotoSansSC-VF.ttf"),
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf"]
    for f in cands:
        if f and os.path.exists(f):
            try:
                font_manager.fontManager.addfont(f)
                name = font_manager.FontProperties(fname=f).get_name()
                matplotlib.rcParams["font.sans-serif"] = [name] + [
                    x for x in matplotlib.rcParams["font.sans-serif"] if x != name]
                matplotlib.rcParams["mathtext.fontset"] = "dejavusans"
                return name
            except Exception:
                continue
    return ""


# ============================================================================
# 指标口径
# ============================================================================

def _to_series(raw):
    """DataHub 日线 -> DataFrame(index=YYYYMMDD str, close, vol)。"""
    if raw is None or len(raw) == 0:
        return None
    df = raw.copy()
    dcol = "trade_date" if "trade_date" in df.columns else "date"
    df[dcol] = df[dcol].astype(str).str.replace("-", "", regex=False)
    df = df.drop_duplicates(dcol).sort_values(dcol).set_index(dcol)
    out = pd.DataFrame({"close": pd.to_numeric(df["close"], errors="coerce")})
    out["vol"] = pd.to_numeric(df.get("vol", np.nan), errors="coerce")
    return out.dropna(subset=["close"])


def _norm_members(obj):
    """成分股返回值统一成 6 位 A 股代码列表（兼容 list 与 DataFrame 多种列名）。"""
    if obj is None:
        return []
    if isinstance(obj, (list, tuple, set)):
        codes = list(obj)
    elif isinstance(obj, pd.DataFrame):
        col = next((c for c in ("con_code", "ts_code", "code", "stock_code")
                    if c in obj.columns), None)
        if col is None:
            return []
        codes = obj[col].tolist()
    else:
        return []
    out = []
    for c in codes:
        s = str(c).strip().upper().split(".")[0]
        for pre in ("SH", "SZ", "BJ"):
            if s.startswith(pre):
                s = s[len(pre):]
        if len(s) == 6 and s.isdigit() and s not in out:
            out.append(s)
    return out


def _ts_code(code6):
    if code6.startswith("6"):
        return "%s.SH" % code6
    if code6.startswith(("8", "4")):
        return "%s.BJ" % code6
    return "%s.SZ" % code6


def compute_metrics(closes, window, ar_window):
    """滚动 window 日相关矩阵 + 特征分解 -> 逐日指标表。"""
    px = closes.sort_index()
    ret = np.log(px.astype(float)).diff()
    rows = []
    for d in ret.index:
        win = [x for x in ret.index if x <= d][-window:]
        if len(win) < window:
            continue
        sub = ret.loc[win]
        sub = sub.loc[:, sub.notna().all(axis=0)]
        n = sub.shape[1]
        if n < 15:
            continue
        c = np.nan_to_num(np.corrcoef(sub.values, rowvar=False))
        eig = np.clip(np.sort(np.linalg.eigvalsh(c))[::-1], 0, None)
        lam1, lam2 = eig[0], (eig[1] if len(eig) > 1 else np.nan)
        rho = float(np.mean(c[np.triu_indices(n, k=1)]))
        p = eig / eig.sum() if eig.sum() > 0 else eig
        p = p[p > 0]
        rows.append({"date": d, "N": n, "M": lam1 / n,
                     "xi": rho / (1 - rho) if rho < 1 else np.nan,
                     "rho": rho, "lam_ratio": lam1 / lam2 if lam2 else np.nan,
                     "entropy": float(-(p * np.log(p)).sum()),
                     "PR": eig.sum() ** 2 / (eig ** 2).sum()})
    m = pd.DataFrame(rows)
    if m.empty:
        return m
    m["date"] = pd.to_datetime(m["date"], format="%Y%m%d")
    ar, mv = [], m["M"].values
    for i in range(len(mv)):
        if i < ar_window:
            ar.append(np.nan)
            continue
        w = mv[i - ar_window:i]
        a, b = w[:-1], w[1:]
        ar.append(float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else np.nan)
    m["ar1"] = ar

    # ===== 2026-11-19 新增：接入 charts.phase_signals 的相变四指标 =====
    # 原面板只有 M / xi / AR(1) of M（全是截面口径）。
    # 这里补齐时序口径三项 + 去因子吸收比率一项：
    #   M1 AR(1) 恢复力衰减   —— log(等权组合价)-MA20 残差的一阶自相关
    #   M2 dsig 方差放大      —— sigma20(t) - sigma20(t-20)，对比 sigma 自身的 20 日标准差
    #   M3 PSI 结构脆弱度     —— sigma / lambda^2，lambda 由 log(rho_k)~k 斜率给出
    #   M5 吸收比率 lambda1/N —— 先减当日截面均值（去共同因子）再算，与上面 M 不同口径
    try:
        from charts.phase_signals import (ar1_resilience, ar1_alert, sigma_delta,
                                          dsig_alert, ar_lambda, psi_fragility,
                                          psi_alert, absorption_ratio_series,
                                          absorption_alert)

        # 时序指标在**全样本**上算（需要历史热身），最后按 m["date"] 对齐切片，
        # 否则 m 只有 window 之后的行数，长度对不上（2026-11-19 踩过）。
        eq_series = px.mean(axis=1, skipna=True).ffill()
        eq_close_full = eq_series.to_numpy(float)
        eq_ret_full = np.diff(np.log(eq_close_full), prepend=np.nan)
        pos_of = {str(k): i for i, k in enumerate(eq_series.index)}
        tgt_pos = [pos_of.get(d.strftime("%Y%m%d"), -1) for d in m["date"]]
        if any(p_ < 0 for p_ in tgt_pos):
            raise ValueError("日期对不齐，跳过相变四指标")
        # 在全样本上算完再按目标日期取（保证 60 日窗口 + 120 日分位有足够热身）
        ai = np.asarray(tgt_pos, int)

        # ⚠️ 图侧口径保持原样：M1 用 resid（log价-MA20 残差）+ **persist 持续口径**，
        #    跟本机默认的 'ret' 不同前，别改。改图口径是另一件事，大佬没发话前不动。
        ar1_full = ar1_resilience(eq_ret_full, eq_close_full, "resid")
        ar1_a_full = ar1_alert(ar1_full, persist=True)
        m["m1_ar"] = ar1_full[ai]
        m["m1_alert"] = ar1_a_full[ai].astype(int)

        sig_f, sd_f, dsig_f = sigma_delta(eq_ret_full)
        ds_a_f = dsig_alert(dsig_f, sd_f)
        m["m2_sig"] = sig_f[ai]
        m["m2_dsig"] = dsig_f[ai]
        m["m2_alert"] = ds_a_f[ai].astype(int)

        lam_f = ar_lambda(eq_ret_full)
        psi_f = psi_fragility(sig_f, lam_f)
        psi_thr_f, psi_a_f = psi_alert(psi_f)
        m["m3_lam"] = lam_f[ai]
        m["m3_psi"] = psi_f[ai]
        m["m3_psi_thr"] = psi_thr_f[ai]
        m["m3_alert"] = psi_a_f[ai].astype(int)

        # M5：吸收比率（去当日截面共同因子，2026-11-02 本机口径）
        ret_panel = ret.dropna(axis=1, thresh=int(len(ret) * 0.9)).dropna()
        if ret_panel.shape[1] >= 15:
            abs_ser = absorption_ratio_series(ret_panel, win=window)
            date8 = [d.strftime("%Y%m%d") for d in m["date"]]
            # 收盘价直接用等权序列对齐（已在上面按 m 的日期切片）
            close_aligned = eq_close_full[ai]
            m5_a, m5_v, m5_thr = absorption_alert(abs_ser, date8, close_aligned)
            m["m5_ar"] = m5_v
            m["m5_thr"] = m5_thr
            m["m5_alert"] = m5_a

        # 双通道：A = M1 AND M2（抓广谱顶）| B = M5（抓抱团顶）
        if "m1_alert" in m and "m2_alert" in m:
            m["chA"] = (m["m1_alert"].astype(bool) & m["m2_alert"].astype(bool)).astype(int)
        if "m5_alert" in m:
            m["chB"] = m["m5_alert"].astype(int)
        if "chA" in m and "chB" in m:
            m["dual"] = ((m["chA"] == 1) | (m["chB"] == 1)).astype(int)
            m["dual_lv"] = np.where((m["chA"] == 1) & (m["chB"] == 1), 2,
                                    np.where(m["dual"] == 1, 1, 0)).astype(int)
    except Exception as _e:  # 相变四指标失败不能拖垮原面板
        print("    [phase] 相变四指标计算失败（M/xi/AR1 不受影响）: %s" % str(_e)[:100])

    return m


def segments_by_pos(mask, dates, gap=5, min_len=2):
    """把布尔序列折成连续区间，间隔 <=gap 的相邻段合并，丢弃过短碎片。"""
    idx = np.where(np.asarray(mask, dtype=bool))[0]
    if len(idx) == 0:
        return []
    groups = [[idx[0]]]
    for i in idx[1:]:
        if i - groups[-1][-1] - 1 <= gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    dates = list(dates)
    return [(dates[g[0]], dates[g[-1]]) for g in groups if len(g) >= min_len]


# ============================================================================
# 取数
# ============================================================================

class Target(object):
    """一个待分析的标的：宽基指数或行业，指标由成分股截面算出。"""

    def __init__(self, name, code, members, kind):
        self.name = name
        self.code = code
        self.members = members
        self.kind = kind
        self.metrics = None
        self.price = None


def fetch_closes(codes, start, end, max_members, cache_dir):
    from data_hub.collectors.stocks import get_stock_daily
    closes, ok = {}, []
    use = codes[:max_members] if max_members > 0 else codes
    for i, c in enumerate(use, 1):
        key = os.path.join(cache_dir, "px_%s_%s_%s.pkl" % (c, start, end))
        df = None
        if os.path.exists(key):
            try:
                with open(key, "rb") as f:
                    df = pickle.load(f)
            except Exception:
                df = None
        if df is None:
            try:
                df = _to_series(get_stock_daily(_ts_code(c), start_date=start, end_date=end))
            except Exception as e:
                print("    [%d/%d] %s 取数失败: %s" % (i, len(use), c, str(e)[:50]))
                df = None
            if df is not None:
                try:
                    with open(key, "wb") as f:
                        pickle.dump(df, f)
                except Exception:
                    pass
        if df is None or len(df) < 30:
            continue
        closes[c] = df["close"]
        ok.append(c)
    return pd.DataFrame(closes)


def build_targets(args):
    from data_hub.collectors.stocks import get_index_components
    from data_hub.collectors.stocks.sw_industry import get_sw_members, get_sw_classify
    targets = []
    for code in [x.strip() for x in (args.index or "").split(",") if x.strip()]:
        targets.append(Target("宽基 %s" % code, code,
                              _norm_members(get_index_components(code)), "index"))
    cls = None
    for code in [x.strip() for x in (args.industry or "").split(",") if x.strip()]:
        try:
            df = get_sw_members(code, level=args.sw_level)
        except Exception as e:
            print("  [行业] %s 成分股取数失败: %s" % (code, str(e)[:60]))
            df = None
        name = "行业 %s" % code
        try:
            if cls is None:
                cls = get_sw_classify(level=args.sw_level)
            hit = cls[cls.iloc[:, 0].astype(str).str.endswith(code)]
            if len(hit):
                name = str(hit.iloc[0, 1])
        except Exception:
            pass
        targets.append(Target(name, code, _norm_members(df), "industry"))
    return targets


# ============================================================================
# 绘图
# ============================================================================

PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
           "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]


def _fmt(d):
    return pd.to_datetime(d, format="%Y%m%d").strftime("%Y/%-m/%-d")


def plot_panel(targets, start, end, out_path):
    multi = len(targets) > 1
    # 2026-11-19：从 3 栏扩到 5 栏，后两栏是接入 charts.phase_signals 的相变四指标
    fig, axes = plt.subplots(5, 1, figsize=(18, 26), sharex=True,
                             gridspec_kw={"height_ratios": [1.15, 1, 1, 1, 1]})

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
                     color=PALETTE[k % len(PALETTE)], lw=1.8, label=t.name)
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
            if px["vol"].notna().any():
                ax1b = ax1.twinx()
                ax1b.bar(idx, px["vol"], width=1.0, color="#8fb3e0", alpha=0.35, label="成交量")
                ax1b.set_ylabel("成交量（股）", fontsize=16)
        ax1.set_title("%s 价格与成交量 (%s - %s)" % (t.name, _fmt(start), _fmt(end)),
                      fontsize=18, pad=26)
    ax1.text(0.5, 1.005, "价格上行＝趋势偏多　|　价格下行、量能放大＝趋势偏空（价格不含相变判据）",
             transform=ax1.transAxes, fontsize=13, color="#444444", ha="center", va="bottom")
    ax1.grid(alpha=0.25)

    risk_note = ""
    if not multi and targets[0].metrics is not None:
        m = targets[0].metrics
        ar_hi = m["ar1"] > max(0.6, float(m["ar1"].quantile(0.5)))
        risk = ((m["M"].diff(5) < 0) & (m["xi"].diff(5) < 0) & ar_hi).fillna(False).values
        segs = segments_by_pos(risk, m["date"].dt.to_pydatetime())
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
        col = PALETTE[k % len(PALETTE)]
        ax2.plot(m["date"], m["M"], color=col, lw=2.0,
                 label=(t.name + " " if multi else "") + r"$M=\lambda_1/N$（左轴）")
        ax2b.plot(m["date"], m["xi"], color=col, lw=1.5, ls="--", alpha=0.85,
                  label=(t.name + " " if multi else "") + r"$\xi$（右轴）")
        if not multi:
            q90 = float(m["M"].quantile(0.9))
            for s_, e_ in segments_by_pos((m["M"] > q90).values, m["date"].dt.to_pydatetime()):
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
        col = PALETTE[k % len(PALETTE)] if multi else "#7b2d8e"
        ax3.plot(m["date"], m["ar1"], color=col, lw=2.0,
                 label=t.name if multi else "AR(1) of M(t)")
        if not multi:
            q90 = float(m["ar1"].quantile(0.9))
            for s_, e_ in segments_by_pos((m["ar1"] > q90).values, m["date"].dt.to_pydatetime()):
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
    have_sig = ("m1_ar" in m.columns) if not multi else True
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
        for s_, e_ in segments_by_pos((mm["chA"] == 1).values, mm["date"].dt.to_pydatetime()):
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
    if not multi and "m5_ar" in m.columns and m["m5_ar"].notna().any():
        ax5.plot(m["date"], m["m5_ar"], color="#00695c", lw=1.8, label="M5 吸收比率 λ1/N（去共同因子）")
        ax5.plot(m["date"], m["m5_thr"], color="#b71c1c", ls="--", lw=1.2, label="120 日 P90 阈值")
        for s_, e_ in segments_by_pos((m["chB"] == 1).values, m["date"].dt.to_pydatetime()):
            ax5.axvspan(s_, e_, color="#80cbc4", alpha=0.5)
        ax5.set_ylabel("λ1/N", fontsize=15)
        ax5.set_title("相变截面通道B：吸收比率 λ1/N（Kritzman，去当日共同因子，含 MA60 方向约束）",
                      fontsize=18, pad=26)
        ax5.text(0.5, 1.005,
                 "青底 = 通道B 亮（破 120 日 P90 且收盘在 MA60 上，抓抱团顶）　"
                 "⚠️ 强同步/滞后指标，顶部当天常常不亮（科创50 20260630 顶部 AR=0.210 < 0.218）",
                 transform=ax5.transAxes, fontsize=12.5, color="#00564c", ha="center", va="bottom")
        ax5.legend(loc="upper left", fontsize=12, framealpha=0.85)
        ax5.grid(alpha=0.25)
    else:
        ax5.set_ylabel("λ1/N", fontsize=15)
        ax5.set_title("相变截面通道B：吸收比率（多标的模式暂只画单标的）", fontsize=18, pad=26)
        ax5.grid(alpha=0.25)

    axes[-1].xaxis.set_major_locator(mdates.MonthLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    axes[-1].set_xlabel("日期", fontsize=16)

    fig.suptitle("%s 相变监控面板 (%s - %s)" % ("、".join(t.name for t in targets),
                                              _fmt(start), _fmt(end)), fontsize=22, y=0.996)
    fig.text(0.5, 0.006,
             "判读总则（已按 2026-06~11 科创50 样本校正方向）：风险＝M↓ + ξ↓（集体模式瓦解）且 AR(1) 处相对高位；"
             "机会＝M↑ + ξ↑ 且谱熵抬升。\n"
             "M 与 ξ 近共线（ρ≈0.91），只能当一个信号读；紫底为对应指标突破 90 分位。数据：DataHub。\n"
             "⚠️ 2026-11-19 全样本实测：相变族（M1 AR(1)/Δσ/PSI/吸收比率）对 2026-07 大跌【无预警能力】——"
             "通道A 亮灯后 20 日均值 +1.6%、通道B 亮灯后 +0.3%（非重叠检验）。"
             "可作结构状态描述，不可当交易信号。真正提前的是位置类指标（dev60 / VWAP 偏离）。",
             ha="center", va="bottom", fontsize=13, color="#333333", linespacing=1.6)
    fig.tight_layout(rect=[0, 0.045, 1, 0.985])
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white")
    plt.close(fig)
    return risk_note


# ============================================================================
# 入口
# ============================================================================

def build(args):
    start8 = pd.to_datetime(args.start).strftime("%Y%m%d")
    end8 = pd.to_datetime(args.end).strftime("%Y%m%d")
    hist8 = (pd.to_datetime(args.start)
             - pd.Timedelta(days=int(args.window * 2.2) + 20)).strftime("%Y%m%d")

    hub = _ensure_datahub()
    font = _ensure_cjk()
    print("[phase] DataHub=%s 字体=%s" % (hub or "-", font or "默认"))

    cache_dir = os.path.join(WS, ".cache", "phase_transition")
    os.makedirs(cache_dir, exist_ok=True)

    targets = build_targets(args)
    for t in targets:
        print("    标的 %s(%s) 成分股 %d 只" % (t.name, t.code, len(t.members)))

    from data_hub.collectors.stocks import get_stock_daily, get_index_daily
    for t in targets:
        if len(t.members) < 15:
            print("    %s: 有效成分股不足 15 只，跳过" % t.name)
            continue
        closes = fetch_closes(t.members, hist8, end8, args.max_members, cache_dir)
        if closes.shape[1] < 15:
            print("    %s: 可用日线 %d 只，跳过" % (t.name, closes.shape[1]))
            continue
        t.metrics = compute_metrics(closes, args.window, args.ar_window)
        if t.metrics.empty:
            print("    %s: 指标为空，跳过" % t.name)
            continue
        print("    %s: 成分股 %d 只，指标 %d 个交易日（%s ~ %s）" % (
            t.name, closes.shape[1], len(t.metrics),
            t.metrics["date"].iloc[0].date(), t.metrics["date"].iloc[-1].date()))

        px = None
        if args.price_code and len(targets) == 1:
            px = _to_series(get_stock_daily(args.price_code, start_date=hist8, end_date=end8))
        if px is None or px.empty:
            try:
                px = _to_series(get_index_daily(
                    t.code if "." in t.code else t.code + ".SH",
                    start_date=hist8, end_date=end8))
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
        raise SystemExit("[phase] 没有可用标的，检查代码或数据源")

    out = args.out or os.path.join(
        WS, "reports", "charts", "phase_transition",
        "相变监控_%s_%s_%s.png" % ("_".join(t.code for t in targets), start8, end8))
    note = plot_panel(targets, start8, end8, out)
    print("[phase] 输出 %s%s" % (out, note))

    for t in targets:
        m = t.metrics
        print("    %s: M %.3f->%.3f | ξ %.3f->%.3f | AR1 %.2f | λ1/λ2 %.2f | 谱熵 %.2f" % (
            t.name, m["M"].iloc[0], m["M"].iloc[-1], m["xi"].iloc[0], m["xi"].iloc[-1],
            m["ar1"].iloc[-1], m["lam_ratio"].iloc[-1], m["entropy"].iloc[-1]))
    return out


def main():
    ap = argparse.ArgumentParser(description="相变监控图（序参量 / 关联长度 / 临界慢化，支持多标的叠加）")
    ap.add_argument("--index", default="", help="宽基指数代码，逗号分隔，如 000688 或 000300,000905")
    ap.add_argument("--industry", default="", help="申万行业代码，逗号分隔，如 801010,801080")
    ap.add_argument("--sw-level", type=int, default=1, help="申万行业级别，默认 1")
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--window", type=int, default=60, help="滚动相关矩阵窗口（交易日）")
    ap.add_argument("--ar-window", type=int, default=30, help="AR(1) 滚动窗口（M 值个数）")
    ap.add_argument("--max-members", type=int, default=60, help="每个标的取多少只成分股")
    ap.add_argument("--price-code", default="", help="单标的模式下图①使用的价格/成交量代码，如 588000.SH")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if not a.index and not a.industry:
        ap.error("至少给 --index 或 --industry 之一")
    if a.end < a.start:
        ap.error("--end 不能早于 --start")
    build(a)


if __name__ == "__main__":
    main()