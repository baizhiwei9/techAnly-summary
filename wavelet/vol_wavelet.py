#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🌊 小波多尺度成交量结构诊断（中长周期成交量确认增长）
=====================================================================
大佬 2026-10-28 指定的算法（阈值一字不改）：

6 层 DWT（默认 db4）→ D1≈2天 / D2≈4天 / D3≈8天 / D4≈16天 / D5≈32天 / D6≈64天 / A6>64天
  中长周期 = D4+D5+D6+A6（16 天以上）｜短期 = D1+D2+D3

硬阈值（铁律，不可改）：
  · P(D1+D2+D3) > 60%           → 淘汰（短期噪声主导）
  · P(D4..A6)     ≤ 55%         → 不通过
  · 单调性：最近 20 个 VOL_midlong 中「后点>前点」占比 ≥ 70%
  · 斜率：最近 30 点线性拟合 slope>0 且 R² ≥ 0.6
  · 相对位置：最新 VOL_midlong ≥ 最近 60 日均值 × 1.2
  · 脉冲反证：近 5 日内任一日原始量 ≥ 前 20 日均量 × 2，且同期 midlong 增幅 < 15%
              → 降级为「仅短期放量」，不得放入通过名单
  · 稳健性：db4 / sym6 / coif3 三组结论须一致；1 组相反=置信度中，≥2 组相反=低→淘汰
  · 样本 < 64 个交易日 → 提示「样本过短，16天以上周期不可靠」，置信度最高为「低」

实现细节（不改阈值，只保证三组小波基可比）：
  · 边界处理统一用 **periodization**（周期延拓）。原因：symmetric 模式下 6 层分解对
    coif3 要求 (18-1)*63+1 = 1072 根样本，300 日窗口需镜像垫 772 根，边界能量会把
    真实结构吃掉；periodization 对任意长度都能做 6 层分解，且三组小波基的能量分布
    天然可比（实测 db4/sym6/coif3 的 A6 占比相差 < 1 个百分点），稳健性校验才有意义。
  · 样本 < 64 个交易日按铁律提示「16 天以上周期不可靠」且置信度压到「低」。

用法（库）：
    import vol_wavelet as VW
    r = VW.diagnose(vol_array, name='贵州茅台', code='600519.SH')
命令行：
    python3 bin/vol_wavelet.py 600519,000001,300750
"""
import os
import sys
import json
import warnings
import numpy as np
import pywt


LEVEL = 6
WAVELETS = ("db4", "sym6", "coif3")
MAIN_WAVE = "db4"

TH_SHORT_KILL = 0.60      # 短期占比 > 60% 直接淘汰
TH_MID_KEEP = 0.55        # 中长占比必须 > 55%
TH_MONO = 0.70            # 近 20 点上行占比 ≥ 70%
TH_R2 = 0.60              # 斜率拟合 R² ≥ 0.6
TH_REL = 1.20             # 最新中长量 ≥ 60 日均 × 1.2
TH_PULSE = 2.00           # 单日量 ≥ 前 20 日均 × 2 视为脉冲
TH_PULSE_GROW = 0.15      # 同期 midlong 增幅 < 15% 才算「无趋势意义」
MIN_LEN = 64              # 样本下限（用户铁律口径）
WINDOW = 300              # 取数窗口（交易日）


# ---------------------------------------------------------------- 分解
MODE = "periodization"      # 边界延拓方式（见模块文档：保证三组小波基可比）


def decompose(vol, wavelet=MAIN_WAVE, level=LEVEL, mode=MODE):
    """返回 (coeffs, P, 0)。coeffs 顺序 [cA6, cD6, cD5, cD4, cD3, cD2, cD1]。"""
    v = np.asarray(vol, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        coeffs = pywt.wavedec(v, wavelet, level=level, mode=mode)
    eA = float(np.sum(coeffs[0] ** 2))
    eD = [float(np.sum(c ** 2)) for c in coeffs[1:]]      # D6..D1
    tot = eA + sum(eD)
    P = {}
    if tot > 0:
        P["A6"] = eA / tot
        for j, e in zip(range(level, 0, -1), eD):
            P["D%d" % j] = e / tot
    return coeffs, P, 0


# 周期分组（大佬 2026-10-28 20:53：「中期或者长期都可以」）
#   coeffs 顺序 = [cA6, cD6, cD5, cD4, cD3, cD2, cD1]
#   · MID_GROUP  中期 16~32 个交易日 = D4 + D5（含低频底噪 cA6，保证是量级而非振荡）
#   · LONG_GROUP 长期 >32 个交易日  = D6 + cA6
# 原口径 mid_all = D4+D5+D6+cA6 仍保留（只作参考，不作判定）
MID_GROUP = (0, 2, 3)
LONG_GROUP = (0, 1)
ALL_GROUP = (0, 1, 2, 3)


def midlong(coeffs, wavelet=MAIN_WAVE, pad_n=0, n_orig=None, mode=MODE,
            keep=ALL_GROUP):
    """按周期组重构：只保留 keep 里的系数，逆变换后裁回原始长度。"""
    if isinstance(keep, str):
        keep = {"mid": MID_GROUP, "long": LONG_GROUP, "all": ALL_GROUP}[keep]
    c = [np.zeros_like(x) for x in coeffs]
    for i in keep:
        c[i] = coeffs[i]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ml = pywt.waverec(c, wavelet, mode=mode)
    if n_orig:
        ml = ml[:n_orig]
    return ml


# ---------------------------------------------------------------- 检验
def _fit(y):
    x = np.arange(len(y), dtype=float)
    if len(y) < 3:
        return 0.0, 0.0
    A = np.vstack([x, np.ones_like(x)]).T
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 0.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return float(coef[0]), r2


def _checks(ml, vol):
    out = {}
    # ① 单调性（最近 20 点，后点>前点占比）
    seg = ml[-20:] if len(ml) >= 20 else ml
    dif = np.diff(seg)
    out["mono"] = float(np.mean(dif > 0)) if len(dif) else 0.0
    out["mono_inc"] = int(np.sum(dif > 0))
    out["mono_n"] = int(len(dif))
    out["mono_days"] = int(len(seg))
    # ② 斜率 + R²（最近 30 点）
    seg30 = ml[-30:] if len(ml) >= 30 else ml
    slope, r2 = _fit(seg30)
    out["slope"] = slope
    out["r2"] = r2
    base = float(np.mean(ml[-60:])) if len(ml) >= 5 else float(np.mean(ml))
    out["slope_norm"] = slope / base if base > 0 else 0.0
    # ③ 相对位置
    mean60 = float(np.mean(ml[-60:])) if len(ml) >= 5 else float(np.mean(ml))
    out["mean60"] = mean60
    out["latest"] = float(ml[-1])
    out["rel"] = float(ml[-1]) / mean60 if mean60 > 0 else 0.0
    # ④ 脉冲反证（最近 5 日）
    pulse = []
    n = len(vol)
    for k in range(max(0, n - 5), n):
        prev20 = vol[max(0, k - 20):k]
        if len(prev20) < 10:
            continue
        b = float(np.mean(prev20))
        if b <= 0:
            continue
        if vol[k] >= b * TH_PULSE:
            grow = (ml[-1] / ml[k] - 1.0) if ml[k] > 0 else 9.99
            pulse.append({"idx": int(k), "ratio": float(vol[k] / b), "grow": float(grow)})
    out["pulse"] = pulse
    out["pulse_bad"] = [p for p in pulse if p["grow"] < TH_PULSE_GROW]
    return out


# ---------------------------------------------------------------- 单只诊断
def diagnose(vol, name="", code="", wavelets=WAVELETS):
    """完整诊断一只股票，返回 dict（不抛异常）。

    2026-10-28 20:53 大佬口径修正：「中期或者长期都可以」
      → 不再把 D4+D5+D6+A6 揉成一条曲线判（A6 的长期趋势会把中期波动抹平，
        单调性天然拉到 40% 一带，实测 20 只全 0 通过），改为**两个周期各自独立检验**：
        · 中期 MID  = D4 + D5（+cA6 作量级基准）= 16~32 个交易日
        · 长期 LONG = D6 + cA6 = 32 个交易日以上
        **任一周期四项全过 → 通过**（阈值不变：70%/0.6/1.2/2倍/55%/60%）
    """
    raw = list(vol)
    v = np.asarray([x for x in raw if x is not None and np.isfinite(float(x)) and float(x) >= 0],
                   dtype=float)
    r = {"code": code, "name": name, "n": int(len(v)), "pass": False,
         "confirm": None, "reason": "", "P": {}, "mono": None, "r2": None,
         "rel": None, "pulse_bad": [], "short_note": "", "wave_detail": {},
         "groups": {}}
    if len(v) < 32:
        r["reason"] = "样本不足（%d 个交易日），无法做 6 层小波分解" % len(v)
        r["confirm"] = "低"
        return r
    short = len(v) < MIN_LEN
    if short:
        r["short_note"] = "样本过短（%d < 64 个交易日），16 天以上周期不可靠" % len(v)

    GROUPS = (("中期", "mid"), ("长期", "long"))
    per = {}
    for w in wavelets:
        try:
            coeffs, P, _ = decompose(v, w)
            p_short = sum(P.get("D%d" % j, 0.0) for j in (1, 2, 3))
            p_mid = 1.0 - p_short
            g = {}
            for gname, gkey in GROUPS:
                ml = midlong(coeffs, w, 0, len(v), keep=gkey)
                chk = _checks(ml, v)
                ok = True
                if p_short > TH_SHORT_KILL:
                    ok = False
                elif p_mid <= TH_MID_KEEP:
                    ok = False
                elif chk["mono"] < TH_MONO:
                    ok = False
                elif not (chk["slope"] > 0 and chk["r2"] >= TH_R2):
                    ok = False
                elif chk["rel"] < TH_REL:
                    ok = False
                elif chk["pulse_bad"]:
                    ok = False
                g[gkey] = {"ok": ok, "chk": chk, "name": gname}
            per[w] = {"P": P, "p_short": p_short, "p_mid": p_mid, "g": g,
                      "ok": any(x["ok"] for x in g.values())}
        except Exception as e:
            per[w] = {"ok": False, "err": str(e)[:120], "P": {}, "g": {}}

    base = per.get(MAIN_WAVE) or {}
    if not base.get("P"):
        r["reason"] = "小波分解失败：%s" % base.get("err", "未知")
        r["confirm"] = "低"
        return r

    P = base["P"]
    r["P"] = {k: round(x * 100, 1) for k, x in P.items()}
    r["p_short"] = round(base["p_short"] * 100, 1)
    r["p_mid"] = round(base["p_mid"] * 100, 1)

    # 按「先中期后长期」挑一个作为报告主口径（各自记下四项数值）
    for gname, gkey in GROUPS:
        gg = (base["g"] or {}).get(gkey) or {}
        chk = gg.get("chk") or {}
        r["groups"][gkey] = {
            "name": gname, "ok": bool(gg.get("ok")),
            "mono": round((chk.get("mono") or 0) * 100, 1),
            "mono_inc": chk.get("mono_inc", 0), "mono_n": chk.get("mono_n", 0),
            "r2": round(chk.get("r2") or 0, 3),
            "slope_norm": round((chk.get("slope_norm") or 0) * 100, 3),
            "rel": round(chk.get("rel") or 0, 3),
            "pulse_bad": chk.get("pulse_bad") or [],
        }
    # 主口径 = 通过的那个；都不通过则取「四项过得更接近」的中期
    pick = None
    for gname, gkey in GROUPS:
        if r["groups"].get(gkey, {}).get("ok"):
            pick = gkey
            break
    if pick is None:
        pick = "mid"
    main = r["groups"].get(pick) or {}
    r["main"] = pick
    r["main_name"] = main.get("name", "")
    r["mono"] = main.get("mono")
    r["mono_inc"], r["mono_n"] = main.get("mono_inc", 0), main.get("mono_n", 0)
    r["r2"] = main.get("r2")
    r["slope_norm"] = main.get("slope_norm")
    r["rel"] = main.get("rel")
    baseg = (base["g"] or {}).get(pick) or {}
    chk = baseg.get("chk") or {}
    r["latest"], r["mean60"] = chk.get("latest"), chk.get("mean60")
    r["pulse_bad"], r["pulse_all"] = chk.get("pulse_bad") or [], chk.get("pulse") or []

    oks = [per[w].get("ok", False) for w in wavelets]
    n_ok, n_tot = sum(oks), len(oks)
    r["wave_detail"] = {w: bool(per[w].get("ok")) for w in wavelets}
    if n_ok == n_tot:
        conf = "高"
    elif n_ok == n_tot - 1:
        conf = "中"
    else:
        conf = "低"
    if short:
        conf = "低"          # 铁律：样本过短时置信度最高为「低」
    r["confirm"] = conf

    p_short, p_mid = base["p_short"], base["p_mid"]
    if p_short > TH_SHORT_KILL:
        r["reason"] = "短期噪声占 %.1f%%（>60%%），成交量由短期噪声主导" % (p_short * 100)
        return r
    if p_mid <= TH_MID_KEEP:
        r["reason"] = "中长动能占 %.1f%%（未过 55%%）" % (p_mid * 100)
        return r

    # 中期 / 长期 任一通过即可
    if any(r["groups"].get(k, {}).get("ok") for _, k in GROUPS):
        if conf == "低":
            r["reason"] = "稳健性结论分裂（%s）→ 淘汰" % str(r["wave_detail"])
            return r
        r["pass"] = True
        r["reason"] = "%s周期成交量确认增长" % r["main_name"]
        return r

    # 都没过 → 选「关口过得更少/更接近」的那个周期给原因（先看谁四项过得多）
    def _cnt(g):
        c = 0
        c += 1 if (g.get("mono") or 0) >= TH_MONO * 100 else 0
        c += 1 if ((g.get("slope_norm") or 0) > 0 and (g.get("r2") or 0) >= TH_R2) else 0
        c += 1 if (g.get("rel") or 0) >= TH_REL else 0
        c += 1 if not (g.get("pulse_bad") or []) else 0
        return c
    _mid = r["groups"].get("mid") or {}
    _lng = r["groups"].get("long") or {}
    m = _mid if _cnt(_mid) >= _cnt(_lng) else _lng
    pre = m.get("name", "")
    if m is _lng:
        if m.get("mono", 0) < TH_MONO * 100:
            r["reason"] = ("中期 %.0f%%上行、长期 %s%.0f%%上行，均未达 70%%"
                           % (_mid.get("mono", 0), pre, m.get("mono", 0)))
        elif m.get("pulse_bad"):
            p = m["pulse_bad"][0]
            r["reason"] = ("⚠️ 短期脉冲/对倒嫌疑：某日量为前20日均量 %.1f 倍，同期长期量仅增 %.1f%%（<15%%）"
                           % (p["ratio"], p["grow"] * 100))
        else:
            r["reason"] = "中期与长期均未确认增长"
        return r

    if m.get("mono", 0) < TH_MONO * 100:
        r["reason"] = ("中期 近20点上行占比 %.1f%%（%d/%d），未达 70%%"
                       % (m.get("mono", 0), m.get("mono_inc", 0), m.get("mono_n", 0)))
    elif not (m.get("slope_norm", 0) > 0 and m.get("r2", 0) >= TH_R2):
        r["reason"] = ("中期 30日斜率 %+.2f%%/日、R²=%.2f（需 slope>0 且 R²≥0.6）"
                       % (m.get("slope_norm", 0), m.get("r2", 0)))
    elif m.get("rel", 0) < TH_REL:
        r["reason"] = "中期 最新量/60日均 = %.2f（需 ≥1.2）" % m.get("rel", 0)
    elif m.get("pulse_bad"):
        p = m["pulse_bad"][0]
        r["reason"] = ("⚠️ 短期脉冲/对倒嫌疑：某日量为前20日均量 %.1f 倍，同期中期量仅增 %.1f%%（<15%%）"
                       % (p["ratio"], p["grow"] * 100))
    else:
        r["reason"] = "中期与长期均未确认增长"
    return r


# ---------------------------------------------------------------- 报告
def fmt_report(results):
    L = []
    passed = [r for r in results if r["pass"]]
    L.append("## 📋 通过筛选的股票（中长周期成交量确认增长）")
    if passed:
        L.append("| 代码 | 置信度 | 中长能量占比 | R² | 最新中长量/60日均 | 备注 |")
        L.append("|---|---|---|---|---|---|")
        for r in passed:
            L.append("| %s %s | %s | %.1f%% | %.2f | %.2f | %s |"
                     % (r["code"], r["name"], r["confirm"], r["p_mid"], r["r2"],
                        r["rel"], r["short_note"] or "—"))
    else:
        L.append("本次无股票通过筛选")
    L.append("")
    L.append("## ❌ 被过滤的股票及原因")
    L.append("| 代码 | 淘汰原因（一句话，引用具体数值） |")
    L.append("|---|---|")
    for r in results:
        if not r["pass"]:
            L.append("| %s %s | %s |" % (r["code"], r["name"], r["reason"]))
    L.append("")
    L.append("## ⚠️ 存疑提示")
    sus = [r for r in results if r.get("pulse_all")]
    if sus:
        for r in sus:
            ds = "、".join("第%d个点" % p["idx"] for p in r["pulse_all"])
            L.append("- %s %s：短期脉冲/对倒嫌疑（%s 放量 ≥2 倍前20日均量）"
                     % (r["code"], r["name"], ds))
    else:
        L.append("无")
    L.append("")
    L.append("## 📊 能量占比速览（供复核）")
    for r in results:
        P = r.get("P") or {}
        L.append("%s %s：D1 %.1f%% / D2 %.1f%% / D3 %.1f%% / D4 %.1f%% / D5 %.1f%% / D6 %.1f%% / A6 %.1f%%"
                 % (r["code"], r["name"], P.get("D1", 0), P.get("D2", 0), P.get("D3", 0),
                    P.get("D4", 0), P.get("D5", 0), P.get("D6", 0), P.get("A6", 0)))
    return "\n".join(L)
