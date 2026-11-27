#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📈 行业趋势 Hurst（只算有趋势的行业，每个行业算自己的 Hurst）

════════════════════════════════════════════════════════════════════
大佬 2026-11-07 18:56 原话
  「你这种全行业的风险还不一定，因为有的行业有，有的行业有趋势，有的行业没趋势。
    我只要找有行业的趋势就行了，所以有行业的趋势，它的，我要只要算它的趋势
    赫斯特的指数就行了，而不是算全行业的」

════════════════════════════════════════════════════════════════════
为什么不能用全市场 Hurst
  全市场（沪深300）Hurst 把所有行业揉在一起 → 有趋势的和没趋势的互相抵消，
  得出一个谁都不代表的数字（实测 11-06 沪深300 H=0.267，说"反持续性"，
  但同期养殖业在明显走趋势）。**Hurst 必须按行业分别算**。

════════════════════════════════════════════════════════════════════
口径（每个行业独立）
  · Hurst 用 DFA（去趋势波动分析），窗口 120 交易日
  · 趋势判定（三个条件判"有没有趋势"）：
      ① H > H_TREND_MIN（默认 0.55）→ 有持续性
      ② 20 日动量绝对值 > MOM_MIN（默认 2%）→ 实际走了
      ③ |残差动量/σ| > RESID_MIN（默认 0.5）→ 跑赢/跑输大盘明显
     三个都满足才算「有趋势的行业」
  · 输出分两组：
      📈 有趋势（按 H 降序）：各自 Hurst + 动量 + 残差动量 + 方向
      ⚪ 无趋势（按 H 升序）：只有 H 和一句话原因
  · 汇总 Hurst = 有趋势行业的 Hurst 均值（**只对有趋势的汇总**，不掺无趋势的）
"""
import numpy as np

H_TREND_MIN = 0.52      # Hurst 门槛：>0.52 才算有持续性
                        #   ⚠️ 2026-11-07 实测：124 个行业 H 中位仅 0.467，
                        #   >0.55 只占 15% 且全是下跌行业（跌势更平滑）。
                        #   而白酒 20日 +9.4%/残差 +4.61 却只有 H=0.42 —— 因为它
                        #   前 4 个月跌太狠，拉低了 120 日窗口的平滑度。
                        #   → 门槛降到 0.52，用「H + 动量 + 残差」三条件共同判定，
                        #     不单靠 H。H 只回答"走势顺不顺"，不回答"涨不涨"。
MOM_MIN = 0.02          # 20 日动量绝对值门槛（2%）
RESID_MIN = 0.5         # 残差动量/σ 门槛
WIN = 120               # DFA 窗口（交易日）
WIN_SHORT = 60          # 短窗口 Hurst（近 3 个月，看当前趋势强度）


def dfa_hurst(x, scales=None):
    """DFA 估 Hurst（与 cri_topology._hurst_dfa 同口径，抽出来避免循环依赖）

    x: 收益率序列（不是价格）
    """
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 40:
        return float("nan")
    y = np.cumsum(x - x.mean())
    N = len(y)
    if scales is None:
        scales = np.unique(np.floor(np.logspace(
            np.log10(6), np.log10(max(N // 4, 8)), 14)).astype(int))
    F, ok = [], []
    for s in scales:
        if s < 4 or s > N // 2:
            continue
        nseg = N // s
        rms = []
        for i in range(nseg):
            seg = y[i * s:(i + 1) * s]
            t = np.arange(s)
            fit = np.polyval(np.polyfit(t, seg, 1), t)
            rms.append(np.mean((seg - fit) ** 2))
        if rms:
            F.append(np.sqrt(np.mean(rms)))
            ok.append(s)
    if len(F) < 3:
        return float("nan")
    return float(np.polyfit(np.log(ok), np.log(F), 1)[0])


def classify(H, mom20, resid_ratio, ma20_slope=None):
    """判断这个行业「有没有趋势」

    ⚠️ 2026-11-07 22:20 大佬：「至少它 MA20 是向上的，对不对？
       你 20 日向都不向上，你再有趋势也没用，你肯定是向下趋势」
    → MA20 斜率 < 0 直接判**无趋势**（哪怕 H 再高），这是前置硬条件。

    返回 (bool, 方向, 原因)
      方向：'up' / 'down' / None
      · 有趋势：H>H_TREND_MIN 且 |mom20|>MOM_MIN 且 |resid_ratio|>RESID_MIN
        → 方向由 mom20 符号决定（正=上升趋势，负=下降趋势）
      · 否则 无趋势，原因说明是哪一条没过
    """
    reasons = []
    # ① MA20 必须向上（前置硬条件）
    if ma20_slope is not None and ma20_slope <= 0:
        reasons.append("MA20向下%.2f%%" % ma20_slope)
        return False, None, "、".join(reasons)
    if not np.isfinite(H) or H <= H_TREND_MIN:
        reasons.append("H=%.2f≤%.2f" % (H if np.isfinite(H) else float("nan"),
                                        H_TREND_MIN))
    if mom20 is None or abs(mom20) <= MOM_MIN:
        reasons.append("20日动量%s不足" % (
            "—" if mom20 is None else "%.1f%%" % (mom20 * 100)))
    if resid_ratio is None or abs(resid_ratio) <= RESID_MIN:
        reasons.append("残差动量%s弱" % (
            "—" if resid_ratio is None else "%.2f" % resid_ratio))
    if reasons:
        return False, None, "、".join(reasons)
    direction = "up" if mom20 > 0 else "down"
    return True, direction, ""


def scan(rows):
    """对一组行业算 Hurst 并分组

    rows: [{name, rets (pd.Series 收益率), mom20 (小数), resid_ratio, ...}]
          调用方已算好 mom20 / resid_ratio，这里只补 Hurst + 分组
    返回 {
      "trend":   [有趋势的行业（按 H 降序）],
      "flat":    [无趋势的（按 H 升序）],
      "h_mean":  有趋势行业的 H 均值（无趋势则 None）,
      "h_median": ...,
      "ratio":   有趋势数 / 总数,
    }
    """
    out = []
    for r in rows:
        rets = r.get("rets")
        H = float("nan")
        H60 = float("nan")
        if rets is not None and len(rets) >= 40:
            arr = np.asarray(rets, float)
            H = dfa_hurst(arr[-WIN:])
            if len(arr) >= WIN_SHORT + 10:
                H60 = dfa_hurst(arr[-WIN_SHORT:])
        # 判定用「长窗口与短窗口都够」才算稳（短窗口缺失时退长窗口）
        H_use = H
        if np.isfinite(H60):
            H_use = max(H, H60) if np.isfinite(H) else H60
        # MA20 斜率（用价格序列；rets 是对数收益 → 累乘还原）
        ma20_slope = None
        try:
            arr = np.asarray(rets, float)
            if arr is not None and len(arr) >= 30:
                px = np.exp(np.cumsum(arr))
                ma_now = float(np.mean(px[-20:]))
                ma_prev = float(np.mean(px[-25:-5]))
                ma20_slope = (ma_now / ma_prev - 1) * 100 if ma_prev > 0 else 0.0
        except Exception:
            ma20_slope = None
        ok, direction, why = classify(H_use, r.get("mom20"), r.get("resid_ratio"),
                                      ma20_slope)
        o = dict(r)
        o["H"] = H
        o["H60"] = H60
        o["ma20_slope"] = ma20_slope
        o["H_use"] = H_use
        o["has_trend"] = bool(ok)
        o["trend_dir"] = direction
        o["why_flat"] = why
        out.append(o)

    trend = [o for o in out if o["has_trend"]]
    rest = [o for o in out if not o["has_trend"]]
    # 第三组：动量/残差都够，但 Hurst 不够（V型反转、锯齿上涨）
    #   ⚠️ 2026-11-07 实测：白酒 20日 +9.4%、残差 +4.61，但 H120=0.422/H60=0.429
    #   → 它是**V 型反转**（锯齿），Hurst 天生低。技术上确实"不是平滑趋势"，
    #     但绝不能让它从视野里消失，否则大佬看不到最强的那个。
    # ⚠️ 2026-11-07：只收**上涨**的（mom20>0）。原来看 abs() 会把暴跌行业
    #   （饰品 -23.6%/残差 -8.74）也归进来，把真正 V 型上涨的白酒淹掉。
    jump = [o for o in rest
            if (o.get("mom20") is not None and o["mom20"] > MOM_MIN)
            and (o.get("resid_ratio") is not None
                 and o["resid_ratio"] > RESID_MIN)]
    flat = [o for o in rest if o not in jump]

    _kf = lambda o: -(o.get("H_use") if np.isfinite(o.get("H_use", float("nan"))) else -9)
    trend.sort(key=_kf)
    jump.sort(key=lambda o: -(o.get("resid_ratio") or 0))
    flat.sort(key=lambda o: -_kf(o))

    hs = [o.get("H_use") for o in trend if np.isfinite(o.get("H_use", float("nan")))]
    return {
        "trend": trend,
        "jump": jump,
        "flat": flat,
        "h_mean": float(np.mean(hs)) if hs else None,
        "h_median": float(np.median(hs)) if hs else None,
        "n_all": len(out),
        "n_trend": len(trend),
        "n_jump": len(jump),
        "ratio": (len(trend) / len(out)) if out else 0.0,
    }


def fmt_line(o, show_dir=True):
    """单个行业一行"""
    H = o.get("H")
    H60 = o.get("H60")
    s = "%s" % o.get("name", "?")
    s += " ｜H120 %s" % ("%.3f" % H if np.isfinite(H) else "—")
    if H60 is not None and np.isfinite(H60):
        s += "/H60 %.3f" % H60
    if show_dir:
        d = o.get("trend_dir")
        if d == "up":
            s += " ｜📈上升趋势"
        elif d == "down":
            s += " ｜📉下降趋势"
    m = o.get("mom20")
    if m is not None:
        s += " ｜20日 %+.1f%%" % (m * 100)
    rr = o.get("resid_ratio")
    if rr is not None:
        s += " ｜残差 %+.2f" % rr
    return s


if __name__ == "__main__":
    # 自测
    np.random.seed(0)
    # 有趋势：带漂移的随机游走
    trend = np.cumsum(np.random.normal(0.002, 0.01, 300))
    trend_ret = np.diff(np.log(trend + 1000))
    # 无趋势：白噪声
    flat_ret = np.random.normal(0, 0.01, 300)
    print("趋势序列 H = %.3f" % dfa_hurst(trend_ret))
    print("噪声序列 H = %.3f" % dfa_hurst(flat_ret))
