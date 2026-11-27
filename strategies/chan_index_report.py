#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基指数 · 缠论多级别报告（日线 / 60分钟 / 周线）
- 复用 xq_industry.chan 的标准口径：包含处理 → 分型 → 笔 → 中枢(ZG/ZD) → 背驰
- 在此之上显式判定：一买 / 二买 / 三买（只做多口径） + 顶背驰/跌破中枢等风险信号
- 分型版（缠论结构）与 趋势浪子版（多倍均线）口径同时给出，供交叉验证

数据：
  60min  → bin/minute_hub.get_kline (腾讯/新浪 minute kline)
  日线    → tushare index_daily
  周线    → 日线 resample W-FRI
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, math, socket, time
from datetime import datetime

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd
# ── MACD 口径：直接复用 chan_smc_index 的常量（10/20/5），勿硬编码 ──
from chan_smc_index import MACD_FAST, MACD_SLOW, MACD_SIGNAL  # noqa: E402

# ─────────────────── 缠论核心（xq_industry 口径） ───────────────────
def merge_bars(bars):
    out = []
    for h, l in bars:
        if not out:
            out.append([h, l]); continue
        ph, pl = out[-1]
        if (h <= ph and l >= pl) or (h >= ph and l <= pl):
            up = len(out) < 2 or out[-1][0] >= out[-2][0]
            if up:
                out[-1] = [max(ph, h), max(pl, l)]
            else:
                out[-1] = [min(ph, h), min(pl, l)]
        else:
            out.append([h, l])
    return out


def fractals(mb):
    fr = []
    for i in range(1, len(mb) - 1):
        a, b, c = mb[i - 1], mb[i], mb[i + 1]
        if b[0] > a[0] and b[0] > c[0] and b[1] > a[1] and b[1] > c[1]:
            fr.append((i, 1, b[0]))
        elif b[0] < a[0] and b[0] < c[0] and b[1] < a[1] and b[1] < c[1]:
            fr.append((i, -1, b[1]))
    return fr


def build_bi(mb):
    """分型 → 笔（顶底交替 + 极值过滤 + 最小间隔3）"""
    fr = fractals(mb)
    bi = []
    for idx, t, px in fr:
        if not bi:
            bi.append([t, px, idx])
        elif bi[-1][0] == t:
            if (t == 1 and px > bi[-1][1]) or (t == -1 and px < bi[-1][1]):
                bi[-1] = [t, px, idx]
        else:
            if abs(idx - bi[-1][2]) >= 3:
                bi.append([t, px, idx])
    return bi


def segs_from_bi(mb, bi):
    """每笔 = dict(up, from, to, lo, hi, i0, i1)  mb 下标"""
    segs = []
    for k in range(len(bi) - 1):
        a, b = bi[k], bi[k + 1]
        segs.append({"up": b[0] == 1, "from": a[1], "to": b[1],
                     "lo": min(a[1], b[1]), "hi": max(a[1], b[1]),
                     "i0": a[2], "i1": b[2]})
    return segs


def macd_series(close, f=MACD_FAST, s=MACD_SLOW, sig=MACD_SIGNAL):
    """MACD 序列：默认 10/20/5（与 chan_smc_index 同口径），柱 = 2×(DIF-DEA)"""
    c = pd.Series(close).astype(float)
    dif = c.ewm(span=f, adjust=False).mean() - c.ewm(span=s, adjust=False).mean()
    dea = dif.ewm(span=sig, adjust=False).mean()
    return (dif.values, dea.values, (2 * (dif - dea)).values)


def chan_full(bars, closes):
    """bars=[(h,l)] on merged timeline; closes = 原始序列对齐(按位置近似用 mb 长度)
    为保持与 xq_industry 一致，直接在 merged bars 上用 (h,l) 近似 close。"""
    mb = merge_bars(bars)
    if len(mb) < 12:
        return {"ok": False, "reason": "K线不足"}
    bi = build_bi(mb)
    if len(bi) < 4:
        return {"ok": False, "reason": "笔不足"}
    segs = segs_from_bi(mb, bi)

    close_approx = np.array([(h + l) / 2 for h, l in mb], dtype=float)
    dif, dea, hist = macd_series(close_approx)

    # ── 中枢：从最近往前找第一组连续三笔重叠 ──
    zs = None
    zs_i = None
    for i in range(len(segs) - 3, -1, -1):
        w = segs[i:i + 3]
        zg = min(x["hi"] for x in w)
        zd = max(x["lo"] for x in w)
        if zg > zd:
            zs = (round(zd, 2), round(zg, 2))
            zs_i = i
            break

    last = segs[-1]
    d = "上升" if last["up"] else "下降"
    cur = float(mb[-1][0])

    # ── 背驰（笔幅度 + MACD 力度） ──
    def amp(s):
        return abs(s["to"] - s["from"])

    def macd_power(s, up):
        a, b = max(0, s["i0"] - 1), min(len(hist) - 1, s["i1"])
        if b <= a:
            return 0.0
        seg = hist[a:b]
        return float(seg.max() if up else -seg.min())

    div_last = None
    ups = [s for s in segs if s["up"]]
    dns = [s for s in segs if not s["up"]]
    if d == "上升" and len(ups) >= 2:
        a, b = ups[-2], ups[-1]
        if b["to"] > a["to"] and (amp(b) < amp(a) or macd_power(b, True) < macd_power(a, True)):
            div_last = "顶背驰（新高但力度减弱）"
    if d == "下降" and len(dns) >= 2:
        a, b = dns[-2], dns[-1]
        if b["to"] < a["to"] and (amp(b) < amp(a) or macd_power(b, False) < macd_power(a, False)):
            div_last = "底背驰（新低但力度减弱）"

    # ── 买点判定（只做多） ──
    buys = []
    dn_div = []          # 所有「下降笔 + 底背驰」= 一买候选（低点）
    for k, s in enumerate(segs):
        if s["up"]:
            continue
        same = [j for j in range(k) if not segs[j]["up"]]
        if not same:
            continue
        p = segs[same[-1]]
        if s["to"] < p["to"] and (amp(s) < amp(p) or macd_power(s, False) < macd_power(p, False)):
            dn_div.append(k)
    # 一买：最近的一个下降笔底背驰
    if dn_div:
        k = dn_div[-1]
        buys.append(("一买", round(segs[k]["to"], 2)))
    # 二买：一买之后的第一个下降笔，低点高于一买低点
    if dn_div:
        k0 = dn_div[-1]
        for k in range(k0 + 1, len(segs)):
            if not segs[k]["up"] and segs[k]["to"] > segs[k0]["to"]:
                buys.append(("二买", round(segs[k]["to"], 2)))
                break
    # 三买：向上离开中枢后回调不破 ZG
    if zs:
        leaves = [k for k in range(zs_i + 3, len(segs) - 1)
                  if segs[k]["up"] and segs[k]["hi"] > zs[1]]
        for k in leaves:
            r = segs[k + 1]
            if r["lo"] > zs[1]:
                buys.append(("三买", round(r["lo"], 2)))
                break

    # ── 风险信号（顶背驰 / 跌破中枢 / 二卖三卖） ──
    risks = []
    if zs:
        if cur < zs[0]:
            risks.append("跌破中枢下沿 ZD=%.2f（结构走坏）" % zs[0])
        if d == "下降" and last["to"] < zs[0]:
            risks.append("下降笔离开中枢向下")
    if div_last and "顶背驰" in div_last:
        risks.append(div_last)

    # ── 状态 ──
    state = "震荡"
    if zs:
        if cur > zs[1]:
            state = "上升趋势：离开中枢向上（三买结构区）" if d == "上升" else "中枢上方但最近一笔转下，警惕回落"
        elif cur < zs[0]:
            state = "下降趋势：跌破中枢向下（趋势破坏）" if d == "下降" else "跌破中枢下沿，结构走坏"
        else:
            state = "中枢震荡（方向未定，等突破）"
    else:
        state = "单边上升笔" if d == "上升" else "单边下降笔"

    # ── 关键位（用于算距离） ──
    last_low = last["to"] if not last["up"] else (segs[-2]["to"] if len(segs) >= 2 else last["lo"])
    last_high = last["to"] if last["up"] else (segs[-2]["to"] if len(segs) >= 2 else last["hi"])
    # 一买低点 = 最近一个「下降笔 + 底背驰」的低点
    y1_low = None
    if dn_div:
        y1_low = round(segs[dn_div[-1]]["to"], 2)

    P = cur
    dist = {}
    if zs:
        dist["to_zg"] = round((zs[1] - P) / P * 100, 2)   # 正=还需上涨多少%到中枢上沿
        dist["to_zd"] = round((P - zs[0]) / P * 100, 2)   # 正=还有多少%才跌破中枢下沿
    dist["bi_low"] = round(last_low, 2)
    dist["bi_high"] = round(last_high, 2)
    dist["y1_low"] = y1_low
    if y1_low:
        dist["to_y1"] = round((P - y1_low) / P * 100, 2)  # 正=现价在一买低点之上

    return {"ok": True, "dir": d, "cur": round(cur, 2), "zs": zs,
            "state": state, "div": div_last or "", "n_bi": len(bi),
            "buys": buys[-4:], "risks": risks,
            "last_bi": round(last["to"], 2),
            "zg": zs[1] if zs else None, "zd": zs[0] if zs else None,
            "dist": dist}


# ─────────────────── 趋势浪子口径（多倍均线） ───────────────────
def ma_state(df):
    c = df["close"]
    out = {}
    for n in (10, 20, 40, 80, 160):
        out[f"ma{n}"] = float(c.rolling(n, min_periods=1).mean().iloc[-1])
    last = float(c.iloc[-1])
    m40, m160 = out["ma40"], out["ma160"]
    if last > m40 and last > m160:
        out["trend"] = "多头"
    elif last < m40 and last < m160:
        out["trend"] = "空头"
    else:
        out["trend"] = "震荡"
    out["close"] = last
    return out


# ─────────────────── 数据获取 ───────────────────
IDX = [
    ("沪深300",  "000300.SH", "sh000300"),
    ("中证500",  "000905.SH", "sh000905"),
    ("中证1000", "000852.SH", "sh000852"),
    ("创业板指", "399006.SZ", "sz399006"),
    ("科创50",   "000688.SH", "sh000688"),
]


def fetch_daily(ts_code):
    import tushare_init as ti
    pro = ti.get_pro()
    for k in range(3):
        try:
            df = pro.index_daily(ts_code=ts_code, start_date="20220101")
            if df is not None and len(df) > 100:
                df = df.rename(columns={"trade_date": "day"})
                df["day"] = pd.to_datetime(df["day"])
                return df.sort_values("day").reset_index(drop=True)
        except Exception as e:
            time.sleep(3)
    return None


def resample_w(df):
    d = df.set_index("day")
    o = d["open"].resample("W-FRI").first()
    h = d["high"].resample("W-FRI").max()
    l = d["low"].resample("W-FRI").min()
    c = d["close"].resample("W-FRI").last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna().reset_index()


def fetch_60(sh_code):
    import minute_hub as mh
    return mh.get_kline(sh_code, "60", n=400, kind="index", cache=True)


def bars_of(df):
    return list(zip(df["high"].astype(float).values, df["low"].astype(float).values))


def analyse(name, ts_code, sh_code):
    out = {"name": name, "code": ts_code, "levels": {}}
    # 日线
    dd = fetch_daily(ts_code)
    if dd is not None:
        out["levels"]["日线"] = {"chan": chan_full(bars_of(dd), dd["close"].values),
                                 "ma": ma_state(dd), "n": len(dd),
                                 "last": str(dd["day"].iloc[-1].date()),
                                 "close": float(dd["close"].iloc[-1])}

    # 60min
    h = fetch_60(sh_code)
    if h is not None and len(h) > 60:
        out["levels"]["60分"] = {"chan": chan_full(bars_of(h), h["close"].values),
                                 "ma": ma_state(h), "n": len(h),
                                 "last": str(h["day"].iloc[-1]),
                                 "close": float(h["close"].iloc[-1])}
    return out


def fmt(res):
    """只讲「还差多少」：距二买/三买/风险位的百分比 + 机会/风险标记"""
    L = []
    day = res["levels"].get("日线", {})
    P = day.get("close", 0)
    L.append("=" * 76)
    L.append("【%s %s】最新收盘 %.2f" % (res["name"], res["code"], P))
    for lv in ("60分", "日线"):
        d = res["levels"].get(lv)
        if not d:
            L.append("  [%s] 数据不足" % lv); continue
        ck, ma = d["chan"], d["ma"]
        if not ck.get("ok"):
            L.append("  [%s] 缠论: %s" % (lv, ck.get("reason"))); continue
        zs = ck.get("zs")
        di = ck.get("dist", {})
        L.append("  [%s] 趋势=%s | 中枢=%s" % (lv, ma["trend"],
                 ("%.2f ~ %.2f" % zs) if zs else "无"))
        # 三买距离
        if zs:
            t3 = di.get("to_zg")
            if t3 is not None:
                if t3 > 0:
                    L.append("     三买：还差 +%.2f%%（需站上中枢上沿 %.2f）" % (t3, zs[1]))
                else:
                    L.append("     三买：已站上中枢上沿 %.2f（超出 %.2f%%），等回调不破 ZG → 三买" % (zs[1], -t3))
        else:
            L.append("     三买：暂无中枢，无参照")
        # 二买距离（以「一买低点」为确认基准）
        y1 = di.get("y1_low")
        ty = di.get("to_y1")
        if y1 is not None and ty is not None:
            if ty >= 0:
                L.append("     二买：确认基准=一买低点 %.2f；现价已高于它 %+.2f%%（等一笔回调不破 %.2f → 二买）" % (y1, ty, y1))
            else:
                L.append("     二买：确认基准=一买低点 %.2f；现价还在它下方 %.2f%%（等止跌站回 %.2f 之上 → 二买）" % (y1, -ty, y1))
        else:
            L.append("     二买：暂无底背驰（未出一买），无确认基准")
        # 风险距离
        if zs:
            tzd = di.get("to_zd")
            if tzd is not None:
                if tzd > 0:
                    L.append("     风险：距跌破中枢下沿 %.2f 还有 -%.2f%%" % (zs[0], tzd))
                else:
                    L.append("     风险：已跌破中枢下沿 %.2f（低 %.2f%%）→ 结构走坏" % (zs[0], -tzd))
        if ck.get("div"):
            L.append("     背驰：%s" % ck["div"])
        # 机会 / 风险标记
        opp, rk = [], []
        if ck.get("div") and "底背驰" in (ck.get("div") or ""):
            opp.append("底背驰已现（新低力度减弱）")
        if ck.get("buys"):
            opp.append("买点信号 " + "/".join("%s@%.2f" % (t, p) for t, p in ck["buys"]))
        if zs and P < zs[0]:
            rk.append("已在日/60中枢下方，反弹到 ZD 有压力")
        if ck.get("div") and "顶背驰" in (ck.get("div") or ""):
            rk.append("顶背驰")
        if rk:
            L.append("     ⚠️风险：" + "；".join(rk))
        if opp:
            L.append("     ⭐机会：" + "；".join(opp))
        if not opp and not rk:
            L.append("     ⭐机会：暂无（无背驰、无买点）｜⚠️风险：无背驰")
    return "\n".join(L)


if __name__ == "__main__":
    only = sys.argv[1:] or None
    res_all = []
    for name, ts_code, sh_code in IDX:
        if only and name not in only:
            continue
        try:
            r = analyse(name, ts_code, sh_code)
            res_all.append(r)
            print(fmt(r), flush=True)
        except Exception as e:
            import traceback
            print("ERR", name, traceback.format_exc()[:400], flush=True)
    with open(os.path.join(WS, ".cache/chan_index_%s.json" % datetime.now().strftime("%Y%m%d_%H%M")), "w", encoding="utf-8") as f:
        json.dump(res_all, f, ensure_ascii=False, default=str, indent=1)
