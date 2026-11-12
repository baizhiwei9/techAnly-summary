#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FVG / OB 自测（不联网，只读 smc/cache 的磁盘缓存）
================================================================
验证三件事：
  1) FVG 缺口区间与原 K 线**可人工核对**（打印形成那 3 根 K 的开高低）；
  2) 状态机正确（回补进度 / mitigated / filled）；
  3) **无 look-ahead**：对同一个形成点 i，
       Frame.fvgs(i) 在 end=i 与 end=i+40 两次调用结果一致（形成时即可看到），
       且在「end < i」时看不到它。

用法：
  python3 smc/smc_fvg_selftest.py                    # 默认 sh510300 日线 + AU0 小时
  python3 smc/smc_fvg_selftest.py sh510300:1d AU0:60m
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd                      # noqa: E402
import smc_core as C                     # noqa: E402
from smc_config import CACHE             # noqa: E402


def load(code, freq):
    fn = os.path.join(CACHE, f"{code}_{freq}.csv")
    d = pd.read_csv(fn)
    d["dt"] = pd.to_datetime(d["dt"])
    return d.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)


def bar3(df, i):
    """打印形成 FVG 的 3 根 K（i-2 / i-1 / i）+ 缺口。"""
    s = df.iloc[i - 2:i + 1]
    out = []
    for k, r in s.iterrows():
        out.append(f"      #{k} {str(r['dt'])[:16]} O{r['open']:.4f} H{r['high']:.4f} "
                   f"L{r['low']:.4f} C{r['close']:.4f}")
    return out


def verify_fvg(df, z):
    """手工重算：bull 需 low[i] > high[i-2]；bear 需 high[i] < low[i-2]。"""
    i = z["idx"]
    lo_i, hi_i = float(df["low"].iloc[i]), float(df["high"].iloc[i])
    hi_2, lo_2 = float(df["high"].iloc[i - 2]), float(df["low"].iloc[i - 2])
    if z["dir"] == "bull":
        ok = lo_i > hi_2 and abs(z["bottom"] - hi_2) < 1e-9 and abs(z["top"] - lo_i) < 1e-9
        cond = f"low[{i}]={lo_i:.4f} > high[{i - 2}]={hi_2:.4f}"
    else:
        ok = hi_i < lo_2 and abs(z["top"] - lo_2) < 1e-9 and abs(z["bottom"] - hi_i) < 1e-9
        cond = f"high[{i}]={hi_i:.4f} < low[{i - 2}]={lo_2:.4f}"
    return ok, cond


def check_no_lookahead(fr, z, pad=40):
    """形成点 i 应「在 end=i 时就可见」，但「end<i 时不可见」。"""
    i = z["idx"]
    n = len(fr.df)
    j = min(n - 1, i + pad)
    a = [x for x in fr.fvgs(i) if x["idx"] == i]
    b = [x for x in fr.fvgs(j) if x["idx"] == i]
    pre = [x for x in fr.fvgs(i - 1) if x["idx"] == i]
    seen_now = bool(a)
    consistent = (bool(b) and a[0]["bottom"] == b[0]["bottom"] and a[0]["top"] == b[0]["top"])
    # 注意：同一个缺口在 end=i 时可能因 min_atr_ratio 被过滤，以「可见即一致」为准
    return dict(seen_at_i=seen_now, consistent=consistent, invisible_before=not pre,
                age_at_i=(a[0]["age"] if a else None), age_later=(b[0]["age"] if b else None))


def _find_zone(z, s):
    """从 signal 反查它在 zones 里的原始 zone dict（按 dir + 区间匹配）。"""
    pool = (z.get("fvgs") or []) + (z.get("obs") or [])
    for x in pool:
        if (x["dir"] == s["dir"] and abs(x["top"] - s["top"]) < 1e-9
                and abs(x["bottom"] - s["bottom"]) < 1e-9):
            if ("fill" in x) == (s["kind"] == "fvg"):
                return x
    return None


def verify_signal(df, s, zone, atr_val):
    """手工重算 confirm_zone 的四个条件，确认不是「假买点」。"""
    if zone is None:
        return False, "反查不到原始 zone"
    i = len(df) - 1
    win = C.CONF_WIN
    o, h, l, c = (df["open"].values.astype(float), df["high"].values.astype(float),
                  df["low"].values.astype(float), df["close"].values.astype(float))
    px = float(c[i])
    top, bottom = s["top"], s["bottom"]
    hh = float(h[i - win:i].max())
    ll = float(l[i - win:i].min())
    if s["dir"] == "bull":
        exp = {"yang": px > o[i], "up": px > c[i - 1], "newhigh": px > hh, "above_top": px >= top}
        broken = bool(zone.get("invalid")) if "fill" not in zone else (zone.get("fill", 0) >= 1.0)
        broken = broken or px < bottom
    else:
        exp = {"yin": px < o[i], "down": px < c[i - 1], "newlow": px < ll, "below_bottom": px <= bottom}
        broken = bool(zone.get("invalid")) if "fill" not in zone else (zone.get("fill", 0) >= 1.0)
        broken = broken or px > top
    same_conds = all(bool(exp[k]) == bool(v) for k, v in s["conds"].items())
    score = sum(1 for v in exp.values() if v)
    far = None
    if atr_val:
        far = ((px - top) / atr_val) if s["dir"] == "bull" else ((bottom - px) / atr_val)
    too_far = bool(far is not None and far > C.CONF_NEAR_ATR)
    # 重推状态机
    if s["dir"] == "bull":
        want = "broken" if broken else ("extended" if too_far else
                                        ("buy" if (s["retested"] and score >= s["need"]) else
                                         ("wait_up" if s["retested"] else "wait_retest")))
    else:
        want = "breakout" if broken else ("extended" if too_far else
                                          ("reject" if (s["retested"] and score >= s["need"]) else
                                           ("wait_up" if s["retested"] else "wait_retest")))
    ok = same_conds and score == s["score"] and want == s["state"] and bool(broken) == s["broken"]
    why = (f"4条={{{', '.join(f'{k}:{int(v)}' for k, v in exp.items())}}} score {score}/{s['score']} "
           f"| 未破={not broken} | 偏离={('%.2f' % far) if far is not None else 'n/a'}ATR "
           f"| 状态 {want}/{s['state']}")
    return ok, why


def run_one(code, freq, k=1):
    print("=" * 78)
    print(f"标的 {code} · {freq} · k={k}")
    df = load(code, freq)
    fr = C.Frame(df, k)
    z = fr.zones()
    print(f"  数据 {len(df)} 根：{str(df['dt'].iloc[0])[:16]} ~ {str(df['dt'].iloc[-1])[:16]}")
    print(f"  最新 #{z['i']} {z['dt']} 收盘 {z['close']:.4f} ATR14 {z['atr']:.4f}")
    fails = []

    # ── FVG ──
    print(f"\n  📐 FVG：未回补 {len(z['fvgs'])} 个（看涨 {sum(1 for x in z['fvgs'] if x['dir']=='bull')}"
          f" / 看跌 {sum(1 for x in z['fvgs'] if x['dir']=='bear')}）")
    checked = 0
    for x in z["fvgs"][:4]:
        d = "看涨" if x["dir"] == "bull" else "看跌"
        print(f"    [{d}] 区间 {x['bottom']:.4f} ~ {x['top']:.4f}  宽 {x['width']:.4f}"
              f"  强度 {x['strength']}×ATR  回补 {x['fill']*100:.1f}% ({x['state']})"
              f"  {x['age']}根前 距现价 {x['dist_pct']:+.2f}%")
        print("\n".join(bar3(df, x["idx"])))
        ok, cond = verify_fvg(df, x)
        print(f"      ✓ 核对：{cond} → {'通过' if ok else '❌不通过'}")
        if not ok:
            fails.append(f"FVG#{x['idx']} 区间核对失败")
        na = check_no_lookahead(fr, x)
        print(f"      ✓ 无未来函数：end=i 可见={na['seen_at_i']}（age={na['age_at_i']}）"
              f" / end<i 不可见={na['invisible_before']} / 后续一致={na['consistent']}")
        if not (na["seen_at_i"] and na["invisible_before"] and na["consistent"]):
            fails.append(f"FVG#{x['idx']} look-ahead 断言失败")
        checked += 1

    # ── OB ──
    print(f"\n  🧱 OB：有效 {len(z['obs'])} 个（看涨 {sum(1 for x in z['obs'] if x['dir']=='bull')}"
          f" / 看跌 {sum(1 for x in z['obs'] if x['dir']=='bear')}）")
    for x in z["obs"][:3]:
        d = "看涨" if x["dir"] == "bull" else "看跌"
        o = df.iloc[x["idx"]]
        b = df.iloc[x["break_idx"]]
        print(f"    [{d}] 区间 {x['bottom']:.4f} ~ {x['top']:.4f}"
              f"  OB K ({str(o['dt'])[:16]}) O{o['open']:.4f} C{o['close']:.4f}"
              f" {'阴线' if o['close'] < o['open'] else '阳线'}"
              f"  → 突破 {str(b['dt'])[:16]} C{b['close']:.4f}"
              f"  {x['age']}根前 回踩={x['touched']} 距现价 {x['dist_pct']:+.2f}%")
        ok = (o["close"] < o["open"]) if x["dir"] == "bull" else (o["close"] > o["open"])
        want = "阴线" if x["dir"] == "bull" else "阳线"
        print(f"      ✓ 核对：OB K 应为{want} → {'✓' if ok else '❌方向不符'}；"
              f"突破发生在 OB K 之后 {x['break_idx'] - x['idx']} 根 → {'✓' if x['break_idx'] > x['idx'] else '❌'}")
        if not (ok and x["break_idx"] > x["idx"]):
            fails.append(f"OB#{x['idx']} 核对失败")
        # OB 必须在突破那根之后才可出现
        if fr.obs(x["break_idx"] - 1):
            if any(y["idx"] == x["idx"] for y in fr.obs(x["break_idx"] - 1)):
                fails.append(f"OB#{x['idx']} 在突破前就可见（look-ahead）")

    # ── 播报文案 ──
    lines, _s = C.zones_text(z["fvgs"], z["obs"], px=z["close"])
    print("\n  播报文案：")
    for ln in lines:
        print("    " + ln)

    # ── 🎯 买卖点确认（不破 + 方向向上）──
    sigs = fr.signals()
    print(f"\n  🎯 买卖点确认：{len(sigs)} 条")
    atr_now = z.get("atr")
    for s in sigs:
        print(f"    {C.confirm_text(s, px=z['close'])}")
        zone = _find_zone(z, s)
        ok, why = verify_signal(df, s, zone, atr_now)
        print(f"      ✓ 核对：{why} → {'通过' if ok else '❌不通过'}")
        if not ok:
            fails.append(f"signal {s['kind']}@{s['bottom']} 判定不一致")
        # 无未来函数：同一 end 重复算必须完全一致
        again = C.confirm_zone(df, zone, end=z["i"], atr_val=atr_now)
        if again and (again["state"] != s["state"] or again["score"] != s["score"]):
            fails.append(f"signal {s['kind']}@{s['bottom']} 重复计算不一致")

    # ── 播报实际引用的那两个 zone，逐一对回原 K 线 ──
    print("\n  播报引用区核对：")
    for tag, arr in (("看涨FVG", [x for x in z["fvgs"] if x["dir"] == "bull"]),
                     ("看跌FVG", [x for x in z["fvgs"] if x["dir"] == "bear"])):
        if not arr:
            continue
        x = arr[0]
        ok, cond = verify_fvg(df, x)
        print(f"    {tag} {x['bottom']:.4f}~{x['top']:.4f} ({x['dt'][:16]})：{cond} → {'✓' if ok else '❌'}")
        if not ok:
            fails.append(f"{tag} 播报引用区核对失败")
    return checked, fails


def main():
    args = sys.argv[1:] or ["sh510300:1d", "AU0:60m"]
    total = 0
    allf = []
    for a in args:
        code, _, freq = a.partition(":")
        n, f = run_one(code, freq or "1d")
        total += n
        allf += f
    print("=" * 78)
    if allf:
        print(f"❌ 自测失败 {len(allf)} 项：")
        for x in allf:
            print("   -", x)
        return 1
    print(f"✅ 自测全部通过：核对了 {total} 个 FVG + 对应 OB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
