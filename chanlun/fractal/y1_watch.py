#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一买预备 · 「可能有一买机会」探测（MA40 向上口径）
================================================================================
大佬 2026-10-26 要求（原话）：
  「大盘技术拆解，还有那个还有那个行业的技术拆解，就是用缠论那个。
    如果要出现一买的机会，并且 MA40 是向上，你也要提示可能有一买机会出现。
    然后图的时候，你在一买的机会那块画一个圈，或者标一个月亮。」

口径（两层，别混）：
  · **一买（已成立）** = 下跌笔创新低 + 底背驰（MACD 力度衰减）
      → 由 chan_smc_index.chan_read 给出（buy_list 里的 "一买"）
  · **一买预备（可能有一买机会）** = 还没成一买，但条件快齐了，且 **MA40 向上**：
      A. 未破前低型：现价还在「前一段下跌笔低点」上方 4% 内（回踩到前低附近），
         且力度已在衰减（当前下跌笔弱于前一段，或 MACD 柱连续收缩）
         → 再往下破一点 + 力度不放大 = 一买
      B. 已破前低型：现价刚跌破前低 4% 以内，但底背驰还没确认
         → 力度能衰减下来就是一买
  · 已经有**新鲜一买**的不再报「预备」（同一件事不说两遍）

对外：
  ma_state(closes)                    → MA40 状态 dict
  y1_prepare(ck, closes, price=None)  → 一买预备 dict（ok=True = 要提示）
  tag(pre, mark="🌙", short=False)    → 一行提示文字
"""
import numpy as np

MA_N = 40          # 均线周期
MA_SLOPE = 5       # 斜率比较跨度（今日 MA40 vs 5 根前 MA40）
NEAR_PCT = 4.0     # 现价距前低多少 % 以内算「在前低附近」
SHRINK_BARS = 3    # MACD 柱连续收缩几根算「力度衰减」


def ma_state(closes, n=MA_N, slope=MA_SLOPE):
    """MA40 状态：数值 / 是否向上 / 现价偏离度（%）"""
    try:
        c = np.asarray([float(x) for x in closes], float)
    except Exception:
        return {"ok": False}
    if len(c) < n + slope:
        return {"ok": False}
    ma = float(c[-n:].mean())
    prev = float(c[-n - slope:-slope].mean())
    return {"ok": True, "ma": round(ma, 2), "ma_up": bool(ma > prev),
            "chg": round(ma - prev, 3),
            "dev": round(float((c[-1] - ma) / ma * 100.0), 2)}


def y1_prepare(ck, closes, price=None, near_pct=None):
    """一买预备判定。ck = chan_smc_index.chan_read() 的返回

    返回 dict（ok=True 表示要提示「可能有一买机会」）：
      ok / ma_up / ma40 / prev_low / cur_low / dist / weak / shrink / mode('A'|'B') / price
      dist = 现价距**前低**的百分比（正=还在前低上方，负=已破前低）
    """
    near = NEAR_PCT if near_pct is None else float(near_pct)
    ms = ma_state(closes)
    out = {"ok": False, "ma_up": ms.get("ma_up"), "ma40": ms.get("ma"),
           "ma_dev": ms.get("dev"), "mode": None}
    if not ms.get("ok") or not ck or not ck.get("ok"):
        return out
    if ck.get("last_up"):
        return out                       # 上升笔中，不谈一买
    for b in (ck.get("buy_list") or []):  # 已有新鲜一买 → 不报预备
        if b.get("tag") == "一买" and b.get("fresh"):
            return out
    lows = ck.get("dn_lows") or []
    pws = ck.get("dn_pw") or []
    if len(lows) < 2:
        return out
    cur_low = float(lows[-1])             # 本段下跌笔低点
    prev_low = float(lows[-2])            # 前一段下跌笔低点
    P = float(price if price is not None else (ck.get("cur") or closes[-1]))
    dist = (P - prev_low) / prev_low * 100.0        # 相对前低
    weak = bool(len(pws) >= 2 and pws[-2] > 0 and pws[-1] < pws[-2])
    hist = ck.get("hist_tail") or []
    shrink = False
    if len(hist) >= SHRINK_BARS:
        a = [abs(float(x)) for x in hist[-SHRINK_BARS:]]
        shrink = all(a[i + 1] < a[i] for i in range(len(a) - 1))
    mode = None
    if 0 < dist <= near and (weak or shrink):
        mode = "A"                        # 未破前低，回踩到前低附近且力度已弱
    elif -near <= dist <= 0 and cur_low <= prev_low:
        mode = "B"                        # 刚破前低，等背驰确认
    ok = bool(mode) and bool(ms.get("ma_up"))
    out.update({"ok": ok, "prev_low": round(prev_low, 2), "cur_low": round(cur_low, 2),
                "dist": round(dist, 2), "weak": weak, "shrink": shrink,
                "mode": mode, "price": round(P, 2)})
    return out


def tag(pre, mark="🌙", short=False):
    """一行中文提示；short=True 给表格单元格用"""
    if not pre or not pre.get("ok"):
        return ""
    d = pre.get("dist")
    if short:
        return "%s可能一买(MA40↑)" % mark
    tail = "刚破前低，等背驰确认" if pre.get("mode") == "B" else "回踩前低 %.2f，力度已弱" % (pre.get("prev_low") or 0)
    return "%s可能一买（MA40↑ %.2f｜%s｜距前低 %+.1f%%）" % (
        mark, pre.get("ma40") or 0, tail, d if d is not None else 0)


def ma_note(pre):
    """真一买的 MA40 注脚（MA40↑ 更靠得住）"""
    if not pre or not pre.get("ma_up"):
        return ""
    return "（MA40↑ 更靠得住）"


def is_buy_mark(pre):
    """配图用：是否画月亮圈"""
    return bool(pre and pre.get("ok"))


# ─────────── 配图：在「一买机会」那里画圈 + 月亮 ───────────
_MOON_FP = None
_CJK_FP = None


def _fp_of(paths):
    import os
    from matplotlib import font_manager
    for p in paths:
        if os.path.exists(p):
            try:
                return font_manager.FontProperties(fname=p)
            except Exception:
                continue
    return None


def moon_font():
    """月亮 ☾(U+263E) 的字形：中文字体（wqy-microhei / Noto CJK）都没有，
    用 matplotlib 自带的 DejaVuSans（实测有字形），单独给月亮文字用。"""
    global _MOON_FP
    if _MOON_FP is None:
        import os
        import matplotlib
        _MOON_FP = _fp_of([os.path.join(os.path.dirname(matplotlib.__file__),
                                        "mpl-data/fonts/ttf/DejaVuSans.ttf")])
    return _MOON_FP


def cjk_font():
    """中文标签字体（固定用中文字体文件，不依赖调用方的 rcParams）"""
    global _CJK_FP
    if _CJK_FP is None:
        _CJK_FP = _fp_of(["/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                          "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"])
    return _CJK_FP


def mark_on_ax(ax, bi, pre, start, nview, moon="☾", label="可能一买(MA40↑)"):
    """在 K 线图上标「可能有一买机会」：绿圈圈住前低 + 月亮标签

    ax   : matplotlib Axes
    bi   : chan_chart.chan_geometry() 的 geo["bi"]（[{"orig","type","price"}]）
    pre  : y1_watch.y1_prepare() 的返回（pre["ok"] 才画）
    start: 视图起点原始索引（x = orig - start）
    nview: 视图内 K 线根数
    → 画成功返回 True
    """
    if not pre or not pre.get("ok"):
        return False
    lvl = pre.get("prev_low")
    if not lvl:
        return False
    cand = [p for p in (bi or []) if p.get("type") == -1]   # 底分型
    if not cand:
        return False
    pt = min(cand, key=lambda p: abs(p["price"] - lvl))
    x = pt["orig"] - start
    if not (-1 <= x <= nview):
        return False
    # 圈：空心大圆（圈住前低那根底）
    ax.scatter([x], [pt["price"]], s=780, marker="o",
               facecolors="none", edgecolors="#0b6e0b",
               linewidths=2.4, zorder=12)
    # 内圈实心小点：定住位置
    ax.scatter([x], [pt["price"]], s=55, marker="o",
               color="#0b6e0b", zorder=12.5)
    # 标签：默认标下方；太贴底就翻到上方
    y0, y1 = ax.get_ylim()
    dy = -30 if pt["price"] - (y1 - y0) * 0.20 > y0 else 26
    va = "top" if dy < 0 else "bottom"
    # 月亮（DejaVu 字体单独渲染）+ 中文标签（中文字体）
    ax.annotate(moon, xy=(x, pt["price"]), xytext=(-round(len(label) * 2.6) - 8, dy),
                textcoords="offset points", ha="center", va=va,
                fontsize=17, color="#0b6e0b", fontproperties=moon_font(), zorder=13)
    ax.annotate(label, xy=(x, pt["price"]), xytext=(6, dy + (2 if dy < 0 else -2)),
                textcoords="offset points", ha="left", va=va,
                fontsize=10.5, color="#0b6e0b", fontweight="bold", zorder=13,
                fontproperties=cjk_font(),
                bbox=dict(boxstyle="round,pad=0.30", fc="#eaffea",
                          ec="#0b6e0b", alpha=0.95, lw=1.1))
    return True
