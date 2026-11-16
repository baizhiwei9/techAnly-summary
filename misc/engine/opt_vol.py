#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期权波动率三条线：VIX / 偏度 / （配合 R_cascade 用）
=====================================================================
2026-11-16 大佬要求：「底下那坐标轴…还是双 Y 轴，一个是卡斯卡的，一个是偏度，
一个是那个 VIX，就这仨在一块。就是，还是不要 MACD 了」

口径（唯一实现，别在画图层另算一套）
------------------------------------
**主数据源：DataHub**（2026-11-16 大佬要求：「期权的数据应该也在 DataHub 上搞」）
  `data_hub.collectors.misc.option_metrics_history.get_option_metrics()`
  · VIX  = CBOE 分钟到期方差互换法（30 日历日插值），`calc_vix`
  · 偏度 = CBOE 偏度（100 中轴），`cboe_skew` 列
  · 带增量缓存 `~/.openclaw/workspace/.cache/option_metrics/`
    首次回补较慢（~31s/天），之后每天只补 1 天

备用（DataHub 不可用时）：工作区 IV 历史库 `.cache/opt_iv_hist.json`
结构：{ "OP510300.SH": { "20261116": { "9": {atm,p25,c25,F,S}, "37": {...} }, ... } }
       外层 key=日期，内层 key=该到期月的剩余天数 DTE（字符串）

两条序列：
  · **VIX**（我们自编的 30 天恒定期限隐含波动率）
      把每个到期月的 ATM IV 按 DTE 线性插值到 **30 天**（两侧都有腿才插值；
      否则取最接近 30 的那一腿，不做外推）。单位：波动率点（18.6 = 18.6%）。
      注：这不是 CBOE 那套方差互换口径，就是 ATM IV 的 30 天插值，图上标
      「30D ATM IV」更准确，但大佬习惯叫 VIX，标题里两个都写。
  · **偏度 Skew（CBOE 口径，100 中轴）**
      = 100 + 30 × (IV(25Δ Put) − ATM IV) / ATM IV
      取 **DTE 最接近 30** 的那一腿。100=中性、>110=偏贵/危险、>120=极危、<95 右偏。
      口径与本库 `data_hub.calc_cboe_skew_directional()` **完全一致**
      （阈值 120/110/105/95/80 就是按这个口径校准的，别自己改公式）。

为什么偏度能和 VIX 共用一根轴：两者都是「波动率点」，量纲一致；
R_cascade 是**比值**（0.6~1.4），必须单独一根轴 → 双 Y 轴。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

# 指数名 → 期权链代码（tushare opt_code）
IDX2OPT = {
    "沪深300": "OP510300.SH",
    "中证500": "OP510500.SH",
    "中证1000": "OP000852.SH",
    "创业板指": "OP159915.SZ",
    "科创50": "OP588000.SH",
    "上证50": "OP510050.SH",
}

# 备用数据源（只在 DataHub 不可用时启用；可用 OPT_VOL_SOURCE=legacy 强制走这条）
_HERE = os.path.dirname(os.path.abspath(__file__))
_WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(_HERE))
DEFAULT_HIST = os.environ.get("OPT_IV_HIST") or os.path.join(
    os.path.expanduser("~"), ".openclaw", "workspace", ".cache", "opt_iv_hist.json")
CACHE_DIR = os.environ.get("OPT_VOL_CACHE", os.path.join(
    os.path.expanduser("~"), ".cache", "opt_vol"))

TARGET_DTE = 30.0


def _moments_from_chain(recs, spot, T):
    """BKM 风险中性矩（复用 optstack 的唯一实现，别在这再写一套）。

    失败时返回空 dict —— 画图不能因为 BKM 挂掉整体失败。
    """
    try:
        _op = os.environ.get("OPTSTACK_DIR") or os.path.join(
            os.path.expanduser("~"), ".openclaw", "workspace", "optstack")
        if _op not in sys.path:
            sys.path.insert(0, _op)
        from options_utility.calc.cn.moments import moments_from_chain
        return moments_from_chain(recs, spot, T) or {}
    except Exception as e:                                       # noqa: BLE001
        print("[optvol] BKM 不可用：%s" % str(e)[:100], flush=True)
        return {}


def skew_index_of(bkm_skew):
    """BKM 风险中性偏度 → 近似 SKEW 指数（CBOE 式 100 − 10×skew）。"""
    if bkm_skew is None:
        return None
    try:
        return 100.0 - 10.0 * float(bkm_skew)
    except Exception:                                            # noqa: BLE001
        return None


def _legs_of(rec):
    """一天的记录 → [(dte, {atm,p25,c25,...}), ...]（按 dte 升序）。"""
    if not isinstance(rec, dict) or not rec:
        return []
    if "atm" in rec:                      # 老格式：单腿
        return [(int(TARGET_DTE), rec)]
    out = []
    for k, v in rec.items():
        try:
            out.append((int(float(k)), v))
        except Exception:                                    # noqa: BLE001
            continue
    return sorted(out, key=lambda x: x[0])


def vix_at(legs, target=TARGET_DTE):
    """按 DTE 线性插值到 30 天的 ATM IV；不满足插值条件时取最近腿（不外推）。"""
    pts = [(d, v["atm"]) for d, v in legs
           if isinstance(v, dict) and v.get("atm") is not None]
    if not pts:
        return np.nan
    for i in range(len(pts) - 1):
        (d1, a1), (d2, a2) = pts[i], pts[i + 1]
        if d1 <= target <= d2 and d2 > d1:
            w = (d2 - target) / (d2 - d1)
            return a1 * (1 - w) + a2 * w
    return min(pts, key=lambda x: abs(x[0] - target))[1]


def skew_at(legs, target=TARGET_DTE):
    """偏度（CBOE 口径，100 中轴）= 100 + 30×(IV25ΔPut − ATM IV)/ATM IV。

    老格式（只有 atm/p25/c25 三条）也能算；缺 p25 或 atm 返回 nan。
    """
    cand = [(d, v) for d, v in legs if isinstance(v, dict)]
    if not cand:
        return np.nan
    _d, v = min(cand, key=lambda x: abs(x[0] - target))
    p, a = v.get("p25"), v.get("atm")
    if p is None or a is None or a <= 0:
        return np.nan
    return 100.0 + 30.0 * (float(p) - float(a)) / float(a)


def series_from_hist(hist, opt_code):
    """单品种 → DataFrame(index=YYYYMMDD str, cols: vix, skew, atm_near, dte_near)。"""
    dmap = (hist or {}).get(opt_code) or {}
    rows, idx = [], []
    for day in sorted(dmap):
        legs = _legs_of(dmap[day])
        if not legs:
            continue
        near = min(legs, key=lambda x: abs(x[0] - TARGET_DTE))
        rows.append(dict(vix=vix_at(legs), skew=skew_at(legs),
                         atm_near=near[1].get("atm"), dte_near=near[0]))
        idx.append(str(day))
    if not rows:
        return pd.DataFrame(columns=["vix", "skew", "atm_near", "dte_near"])
    df = pd.DataFrame(rows, index=idx)
    df.index.name = "trade_date"
    return df


# ═══════════ 主路径：从 DataHub 期权指标缓存取数（2026-11-16 大佬要求）═══════════
# 「现在是需要期权相关的数据，你单独期权相关的数据，你用那个。我不知道你这个 DataHub
#   有没有期权相关的数据。我记得是 Options Utility 是做期权的策略的，
#   期权的数据应该也在 DataHub 上搞」
#
# DataHub 侧唯一入口：data_hub.collectors.misc.option_metrics_history.get_option_metrics
#   · 带增量缓存（~/.openclaw/workspace/.cache/option_metrics/）
#   · 指标口径：VIX = CBOE 分钟到期方差互换法；偏度 = CBOE 偏度（100 中轴）
#   · 列：trade_date, underlying, spot_price, atm_iv, put_25d_iv, call_25d_iv,
#         iv_skew, cboe_skew, vol_pcr, oi_pcr, amt_pcr, vix
#
# 本文件只做「指数名 → DataHub underlying」映射 + 列名整理，**不自己算指标**。

IDX2DH = {
    "沪深300": "510300",
    "中证500": "510500",
    "中证1000": "MO",
    "创业板指": "159915",
    "科创50": "588000",
    "上证50": "IO",
}


def _dh_metrics():
    """import DataHub 的期权指标入口（失败返回 None）。"""
    try:
        _op = os.environ.get("OPTSTACK_DIR") or os.path.join(
            os.path.expanduser("~"), ".openclaw", "workspace", "optstack")
        if _op not in sys.path:
            sys.path.insert(0, _op)
        from data_hub.collectors.misc.option_metrics_history import get_option_metrics
        return get_option_metrics
    except Exception as e:                                       # noqa: BLE001
        print("[optvol] DataHub 期权指标不可用：%s" % str(e)[:120], flush=True)
        return None


def series_from_datahub(name, start=None, end=None, verbose=False):
    """从 DataHub 取某指数的 VIX / 偏度（SKEW 指数口径），返回统一 DataFrame。

    列：vix（%）、skew（SKEW 指数，100 中轴）、atm_iv（%）、iv_skew（原始差值×100）
    """
    ul = IDX2DH.get(name)
    if ul is None:
        raise KeyError("没有 %s 的 DataHub 期权标的映射（可选：%s）"
                       % (name, "、".join(IDX2DH)))
    fn = _dh_metrics()
    if fn is None:
        return None
    import datetime as _dt
    end = end or _dt.date.today().strftime("%Y%m%d")
    start = start or (_dt.date.today() - _dt.timedelta(days=460)).strftime("%Y%m%d")
    df = fn(start, end, underlyings=[ul], verbose=verbose)
    if df is None or not len(df):
        return None
    df = df.copy()
    # 必须按日期排序后再取列（缓存合并后行序不保证），否则 values 会错位
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.sort_values("trade_date")
    out = pd.DataFrame(index=[str(x) for x in df["trade_date"]])
    out.index.name = "trade_date"
    out["vix"] = pd.to_numeric(df["vix"], errors="coerce").values
    # SKEW 指数：优先 DataHub 的 skew_index（BKM 风险中性偏度 → 100−10×skew，
    # 100 中轴、>110 危险）；老缓存没这列时退回 cboe_skew（该列被钳在 150，仅兜底）
    if "skew_index" in df.columns:
        out["skew"] = pd.to_numeric(df["skew_index"], errors="coerce").values
        try:
            _fallback = pd.to_numeric(df["cboe_skew"], errors="coerce").values
            out["skew"] = out["skew"].where(out["skew"].notna(), _fallback)
        except Exception:                                        # noqa: BLE001
            pass
    else:
        out["skew"] = pd.to_numeric(df["cboe_skew"], errors="coerce").values
    out["atm_iv"] = pd.to_numeric(df["atm_iv"], errors="coerce").values * 100.0
    out["iv_skew"] = pd.to_numeric(df["iv_skew"], errors="coerce").values * 100.0
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if verbose:
        _l = out.iloc[-1]
        print("[optvol] %s ← DataHub(%s) %d 天 %s~%s | VIX=%.1f SKEW=%.1f"
              % (name, ul, len(out), out.index[0], out.index[-1], _l["vix"], _l["skew"]),
              flush=True)
    return out


def series_for(name, hist_path=None, force=False, verbose=False):
    """取某指数的 VIX / 偏度序列（磁盘缓存，历史库变了才重算）。

    返回 DataFrame(index=YYYYMMDD str, cols: vix, skew, atm_near, dte_near)
    """
    # ① 主路径：DataHub（2026-11-16 大佬要求，期权数据统一从 DataHub 来）
    if os.environ.get("OPT_VOL_SOURCE", "datahub") != "legacy":
        try:
            dh_df = series_from_datahub(name, verbose=verbose)
            if dh_df is not None and len(dh_df):
                os.makedirs(CACHE_DIR, exist_ok=True)
                fp_dh = os.path.join(CACHE_DIR, "%s.csv" % name)
                dh_df.to_csv(fp_dh)
                return dh_df[["vix", "skew"]]
        except Exception as e:                                   # noqa: BLE001
            if verbose:
                print("[optvol] %s DataHub 取数失败，回退旧 IV 历史库：%s"
                      % (name, str(e)[:100]), flush=True)

    # ② 备用：旧 IV 历史库（.cache/opt_iv_hist.json + 25Δ delta 口径自算）
    code = IDX2OPT.get(name)
    if not code:
        raise KeyError("没有 %s 的期权链映射（可选：%s）" % (name, "、".join(IDX2OPT)))
    fp_hist = hist_path or DEFAULT_HIST
    if not os.path.exists(fp_hist):
        raise FileNotFoundError("找不到 IV 历史库：%s（先跑 bin/opt_iv_hist.py）" % fp_hist)

    os.makedirs(CACHE_DIR, exist_ok=True)
    fp = os.path.join(CACHE_DIR, "%s.csv" % name)
    mtime = os.path.getmtime(fp_hist)
    if os.path.exists(fp) and not force:
        try:
            old = pd.read_csv(fp, index_col=0)
            old.index = [str(x) for x in old.index]
            if abs(os.path.getmtime(fp) - mtime) < 1.0 and len(old):
                if verbose:
                    print("[optvol] %s 命中缓存" % name, flush=True)
                return old
        except Exception:                                    # noqa: BLE001
            pass

    with open(fp_hist, encoding="utf-8") as f:
        hist = json.load(f)
    df = series_from_hist(hist, code)
    # 若 25Δ-delta 口径的缓存存在，用它覆盖 vix/skew（更稳，翼宽自适应）
    if os.path.exists(SKEW_CACHE):
        try:
            dsk = skew_series(name)
            if len(dsk):
                dsk = dsk.dropna(how="all")
                for col in ("vix", "skew"):
                    if col in dsk.columns:
                        idx = df.index.union(dsk.index)
                        merged = df[col].reindex(idx)
                        d2 = dsk[col].reindex(idx)
                        df = df.reindex(idx)
                        df[col] = d2.where(d2.notna(), merged)
        except Exception:                                    # noqa: BLE001
            pass
    if not len(df):
        raise RuntimeError("%s（%s）在 IV 历史库里没有数据" % (name, code))
    df.to_csv(fp)
    os.utime(fp, (mtime, mtime))          # 用历史库 mtime 标记，便于判断新鲜度
    if verbose:
        last = df.iloc[-1]
        print("[optvol] %s %d 天 %s~%s | VIX=%.1f 偏度=%s"
              % (name, len(df), df.index[0], df.index[-1], last["vix"],
                 ("%.2f" % last["skew"]) if pd.notna(last["skew"]) else "NA"), flush=True)
    return df


def pct_rank(s, v, min_n=60):
    """历史分位（0~100）；样本不足返回 nan。"""
    s = s.dropna()
    if len(s) < min_n or not np.isfinite(v):
        return float("nan")
    return float((s <= v).mean() * 100)


# ═════════════ 偏度：按 delta 认 25Δ（比按固定翼宽稳） ═════════════
# 为什么不用「±1.25% 翼宽」：各品种行权价间距差别很大，
# 中证500（间距 0.1 元 / 4.5 元 ≈2.2%）、科创50（间距 0.05 元 / 1.28 元 ≈3.9%）
# 在 ±1.25% 里根本找不到腿 → 偏度动不动就是 None。
# 改成业界标准做法：对每个行权价反解 IV → 算 Black-76 delta →
# 取 |delta| 最接近 0.25 的 put / call 配对。落在 ±10% 内的才算，避免跑到远翼。
SKEW_TOL = 0.10          # 行权价相对现价的搜索范围（±10%）
SKEW_CACHE = os.path.join(CACHE_DIR, "_skew_delta.json")


def _ncdf(x):
    import math
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _blk(F, K, T_, s, cp, r=0.015):
    import math
    if T_ <= 0 or s <= 0:
        return max(0.0, (F - K) if cp == "C" else (K - F))
    d1 = (math.log(F / K) + 0.5 * s * s * T_) / (s * math.sqrt(T_))
    d2 = d1 - s * math.sqrt(T_)
    disc = math.exp(-r * T_)
    if cp == "C":
        return disc * (F * _ncdf(d1) - K * _ncdf(d2))
    return disc * (K * _ncdf(-d2) - F * _ncdf(-d1))


def _iv(px, F, K, T_, cp, r=0.015):
    import math
    if px <= 0 or F <= 0 or K <= 0 or T_ <= 0:
        return None
    intr = max(0.0, (F - K) if cp == "C" else (K - F)) * math.exp(-r * T_)
    if px <= intr * 1.001:
        return None
    lo, hi = 1e-4, 6.0
    for _ in range(70):
        m = (lo + hi) / 2
        if _blk(F, K, T_, m, cp, r) > px:
            hi = m
        else:
            lo = m
    v = (lo + hi) / 2
    return v if 0.01 < v < 6 else None


def _delta(F, K, T_, s, cp, r=0.015):
    """Black-76 delta（call 正、put 负），绝对值用于认 25Δ。"""
    import math
    if s <= 0 or T_ <= 0 or K <= 0:
        return None
    d1 = (math.log(F / K) + 0.5 * s * s * T_) / (s * math.sqrt(T_))
    disc = math.exp(-r * T_)
    return disc * _ncdf(d1) if cp == "C" else -disc * _ncdf(-d1)


def skew_one_day(contracts_df, opt_code, S, trade_date, target_dte=TARGET_DTE):
    """单个品种单日 → (vix_30d, skew_25d, dte_used)。数据不够返回 (nan, nan, None)。

    `contracts_df`: 该交易所当日 opt_daily 与 opt_basic 合并后的表
                    （需列：opt_code, maturity_date, exercise_price,
                      call_put, settle/close）
    """
    import datetime
    import math
    if S is None or not (S > 0):
        return dict(vix=np.nan, skew=np.nan, skew25=np.nan, dte=None,
                    p25=None, c25=None, atm=None)
    sub = contracts_df[contracts_df["opt_code"] == opt_code]
    if not len(sub):
        return dict(vix=np.nan, skew=np.nan, skew25=np.nan, dte=None,
                    p25=None, c25=None, atm=None)
    today = datetime.date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:]))

    # 每个到期月一个 leg：ATM IV（用于 VIX 插值）
    legs, skews = [], []
    for mat, g in sub.groupby("maturity_date"):
        mat = str(mat)
        try:
            exp = datetime.date(int(mat[:4]), int(mat[4:6]), int(mat[6:]))
        except Exception:                                    # noqa: BLE001
            continue
        dte = (exp - today).days
        if dte < 1 or dte > 400:
            continue
        Tn = dte / 365.0
        cps = {}
        for _, r in g.iterrows():
            try:
                K = float(r["exercise_price"])
            except Exception:                                # noqa: BLE001
                continue
            px = r.get("settle")
            if px is None or px != px or not px:
                px = r.get("close")
            try:
                px = float(px)
            except Exception:                                # noqa: BLE001
                continue
            if px <= 0 or K < S * (1 - SKEW_TOL) or K > S * (1 + SKEW_TOL):
                continue
            cps.setdefault(K, {})[str(r["call_put"])] = px
        Ks = sorted(k for k, v in cps.items() if "C" in v and "P" in v)
        if len(Ks) < 3:
            continue
        K0 = min(Ks, key=lambda k: abs(k - S))
        try:
            F = K0 + (cps[K0]["C"] - cps[K0]["P"]) * np.exp(0.015 * Tn)
        except Exception:                                    # noqa: BLE001
            continue
        # ATM IV（两边平均）
        a1 = _iv(cps[K0]["C"], F, K0, Tn, "C")
        a2 = _iv(cps[K0]["P"], F, K0, Tn, "P")
        vals = [v for v in (a1, a2) if v]
        if vals:
            legs.append((dte, float(np.mean(vals)) * 100))

        # 25Δ：把每个行权价的 IV 和 delta 都算出来，取最接近 0.25 的两边
        pbest = cbest = None       # (|delta-0.25|, iv小数)
        for k in Ks:
            for cp, bucket in (("P", "p"), ("C", "c")):
                if cp not in cps[k]:
                    continue
                v = _iv(cps[k][cp], F, k, Tn, cp)
                if not v:
                    continue
                dl = _delta(F, k, Tn, v, cp)
                if dl is None:
                    continue
                dist = abs(abs(dl) - 0.25)
                if bucket == "p":
                    if pbest is None or dist < pbest[0]:
                        pbest = (dist, v)
                else:
                    if cbest is None or dist < cbest[0]:
                        cbest = (dist, v)
        _atm_iv = float(np.mean(vals)) if vals else None
        if pbest and _atm_iv and pbest[0] < 0.13:
            # 25Δ 口径（备用）：100 + 30*(p25_iv - atm_iv)/atm_iv，量级只有 100~105
            cboe25 = 100.0 + 30.0 * (pbest[1] - _atm_iv) / _atm_iv
            # SKEW 指数（主用）：BKM 风险中性偏度 → 100 − 10×skew
            # 这是「偏度 1~120」那个量级的口径，量级 100~130，与 110/120 阈值匹配
            idx = None
            try:
                _recs = [dict(exercise_price=float(r["exercise_price"]),
                              call_put=str(r["call_put"]),
                              close=(r.get("settle") if (r.get("settle") is not None
                                     and r.get("settle") == r.get("settle")
                                     and r.get("settle")) else r.get("close")))
                        for _, r in g.iterrows()]
                _m = _moments_from_chain(_recs, S, Tn)
                if _m.get("skew") is not None:
                    idx = 100.0 - 10.0 * float(_m["skew"])
            except Exception:                                # noqa: BLE001
                idx = None
            skews.append((dte, idx, cboe25, pbest[1] * 100, _atm_iv * 100,
                          (cbest[1] * 100) if cbest else None))

    if not legs:
        return dict(vix=np.nan, skew=np.nan, skew25=np.nan, dte=None,
                    p25=None, c25=None, atm=None)
    # VIX：DTE 线性插值到 30 天
    legs = sorted(legs)
    vix = np.nan
    for i in range(len(legs) - 1):
        (d1, v1), (d2, v2) = legs[i], legs[i + 1]
        if d1 <= target_dte <= d2 and d2 > d1:
            w = (d2 - target_dte) / (d2 - d1)
            vix = v1 * (1 - w) + v2 * w
            break
    if not np.isfinite(vix):
        vix = min(legs, key=lambda x: abs(x[0] - target_dte))[1]
    # 偏度：DTE 最接近 30 的那一腿
    #   skew  = SKEW 指数（BKM → 100−10×skew，量级 100~130）← 画图用这个
    #   skew25= 25Δ 口径（100+30×(p25−atm)/atm，量级 100~105）← 备用
    skew = np.nan
    skew25 = np.nan
    dte_used = None
    p25 = c25 = atm_used = None
    if skews:
        _best = min(skews, key=lambda x: abs(x[0] - target_dte))
        dte_used, _idx, _s25, p25, atm_used, c25 = _best
        if _idx is not None:
            skew = float(_idx)
        if _s25 is not None:
            skew25 = float(_s25)
    return dict(vix=float(vix), skew=skew, skew25=skew25, dte=dte_used,
                p25=p25, c25=c25, atm=atm_used)


def build_skew_cache(names=None, days=260, verbose=True, force=False):
    """按 delta 口径重算偏度（+ VIX），落 `_skew_delta.json`，可断点续跑。

    只补缺的 (品种, 日期)，已有就跳过 → 日报每天多补 1~2 天很快。
    """
    import datetime
    import sys as _sys
    names = names or list(IDX2OPT)
    fp = SKEW_CACHE
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = {}
    if os.path.exists(fp) and not force:
        try:
            with open(fp, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:                                    # noqa: BLE001
            cache = {}

    # 交易日历 + 现价：走工作区 tushare 封装
    _ws = os.environ.get("TECH_WORKSPACE") or os.path.join(
        os.path.expanduser("~"), ".openclaw", "workspace")
    if os.path.join(_ws, "bin") not in _sys.path:
        _sys.path.insert(0, os.path.join(_ws, "bin"))
    import tushare_init as T

    end = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=int(days * 1.55))).strftime("%Y%m%d")
    cal = T.ts_call("trade_cal", exchange="SSE", start_date=start, end_date=end, is_open="1")
    days_list = sorted(str(x) for x in cal["cal_date"].tolist()) if cal is not None else []

    # 品种 → (交易所, 现价来源)
    spot_of = {}
    for n in names:
        code = IDX2OPT[n]
        ex = "CFFEX" if code.startswith("OP000") else ("SZSE" if code.endswith(".SZ") else "SSE")
        spot_of[n] = (code, ex)

    todo = [d for d in days_list
            if any(d not in (cache.get(n) or {}) for n in names)]
    if verbose:
        print("[skew] 待补 %d 个交易日（共 %d）" % (len(todo), len(days_list)), flush=True)

    import time
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()

    # ── 现价：一次拉全区间（原来每天每品种查一次 → 7×286 次调用，慢得要命）──
    spot_hist = {}
    _spot_ts = {"OP510300.SH": "510300.SH", "OP510500.SH": "510500.SH",
                "OP588000.SH": "588000.SH", "OP159915.SZ": "159915.SZ",
                "OP000852.SH": "000852.SH", "OP510050.SH": "510050.SH",
                "OP000300.SH": "000300.SH"}
    for code in {c for c, _ in spot_of.values()}:
        ts = _spot_ts.get(code)
        if not ts:
            continue
        api = "fund_daily" if ts[:2] in ("51", "15", "58") else "index_daily"
        try:
            df = T.ts_call(api, ts_code=ts, start_date=start, end_date=end)
            if df is not None and len(df):
                spot_hist[ts] = {str(r["trade_date"]): float(r["close"])
                                 for _, r in df.iterrows()}
        except Exception as e:                               # noqa: BLE001
            if verbose:
                print("[skew] 现价 %s 取数失败：%s" % (ts, str(e)[:70]), flush=True)

    # ── 合约静态表：串行拉一次（线程里共用只读）──
    basic = {}
    for ex in sorted({e for _, e in spot_of.values()}):
        try:
            basic[ex] = T.ts_call("opt_basic", exchange=ex)
        except Exception as e:                               # noqa: BLE001
            print("[skew] opt_basic %s 失败：%s" % (ex, str(e)[:70]), flush=True)
            basic[ex] = None
    if verbose:
        print("[skew] 现价 %d 标的 / 静态表 %d 交易所，用时 %.0fs"
              % (len(spot_hist), len(basic), time.time() - t0), flush=True)

    def one_day(d):
        """某一天：拉该日各交易所 opt_daily → 算各指数 (vix, skew, dte)。"""
        out = {}
        by_ex = {}
        for code, ex in set(spot_of.values()):
            if ex in by_ex:
                continue
            b = basic.get(ex)
            if b is None or not len(b):
                by_ex[ex] = None
                continue
            try:
                dd = T.ts_call("opt_daily", trade_date=d, exchange=ex)
            except Exception:                                # noqa: BLE001
                by_ex[ex] = None
                continue
            by_ex[ex] = b.merge(dd, on="ts_code") if dd is not None and len(dd) else None
        for n, (code, ex) in spot_of.items():
            df = by_ex.get(ex)
            ts = _spot_ts.get(code)
            S = (spot_hist.get(ts) or {}).get(d) if ts else None
            if df is None or S is None:
                continue
            r = skew_one_day(df, code, S, d)
            out[n] = {
                "vix": None if not np.isfinite(r["vix"]) else round(r["vix"], 3),
                "skew": None if not np.isfinite(r["skew"]) else round(r["skew"], 3),
                "skew25": None if not np.isfinite(r["skew25"]) else round(r["skew25"], 3),
                "p25": None if r["p25"] is None else round(r["p25"], 3),
                "c25": None if r["c25"] is None else round(r["c25"], 3),
                "atm": None if r["atm"] is None else round(r["atm"], 3),
                "dte": r["dte"]}
        return d, out

    nw = int(os.environ.get("OPT_VOL_WORKERS", "5"))
    done = 0
    with ThreadPoolExecutor(max_workers=nw) as pool:
        for d, out in pool.map(one_day, todo):
            for n, rec in out.items():
                cache.setdefault(n, {})[d] = rec
            done += 1
            if verbose and (done % 20 == 0 or done == len(todo)):
                print("[skew] %d/%d  %.0fs" % (done, len(todo), time.time() - t0), flush=True)
            if done % 40 == 0:
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False)

            with open(fp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    if verbose:
        for n in names:
            m = cache.get(n) or {}
            ok = sum(1 for v in m.values() if v.get("skew") is not None)
            print("[skew] %-8s %d 天，偏度有效 %d" % (n, len(m), ok), flush=True)
    return cache


def skew_series(name, cache=None):
    """从 `_skew_delta.json` 取某指数的 (vix, skew) 序列（index=YYYYMMDD str）。"""
    if cache is None:
        if not os.path.exists(SKEW_CACHE):
            return pd.DataFrame(columns=["vix", "skew"])
        with open(SKEW_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    m = cache.get(name) or {}
    if not m:
        return pd.DataFrame(columns=["vix", "skew"])
    ds = sorted(m)
    return pd.DataFrame(
        [{"vix": m[d].get("vix"), "skew": m[d].get("skew"),
          "skew25": m[d].get("skew25")} for d in ds],
        index=ds)
