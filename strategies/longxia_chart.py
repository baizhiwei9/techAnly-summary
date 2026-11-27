#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🦞 龙虾缠论 · A/B 档（行业条件满足）K线合图
=====================================================================
大佬 2026-10-28 要求：「符合条件的这两档，报告上要带上图片……把它们变成一个图片，
然后每一个都是一个子图」

→ 一张 PNG，网格排布，每只股票一个子图：K线（后复权）+ MA20/40/80/160 + 标题。
   A/B 档通常只有几只，一格一只，一眼看完。

用法（作为库）：
    import longxia_chart as LC
    p = LC.build(panel, picks_detail, out_path=...)
    # picks_detail: [{'ts_code','name','industry','stage','ret60','pct',...}, ...]
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys

WS = "/root/.openclaw/workspace"

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

WS = "/root/.openclaw/workspace"

# 中文字体候选：环境变量 → 部署机常见路径 → 本机 fc-list 探测。逐个存在性检查，
# 只在「确实带中文字形」的字体里挑，避免 fc-match "WenQuanYi Micro Hei" 落到
# 纯拉丁字体（NotoSans-Regular.ttf）导致全图豆腐块。
_FONT_CANDIDATES = (
    os.environ.get("CJK_FONT") or "",
    os.path.expanduser("~/.fonts/NotoSansSC-Regular.ttf"),
    os.path.expanduser("~/.fonts/NotoSansCJK-Regular.ttc"),
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


def _fc_cjk_font():
    """fc-list 里第一个带中文字形（charset=4e2d）的字体文件，作为兜底探测。"""
    import subprocess
    try:
        out = subprocess.check_output(
            ["fc-list", ":charset=4e2d", "file"],
            stderr=subprocess.DEVNULL, text=True, timeout=5)
    except Exception:
        return ""
    for line in out.splitlines():
        p = line.split(":", 1)[0].strip()
        if p and os.path.exists(p):
            return p
    return ""


_FAM_OK = []
for _f in _FONT_CANDIDATES:
    if _f and os.path.exists(_f):
        try:
            font_manager.fontManager.addfont(_f)
            _FAM_OK.append(font_manager.FontProperties(fname=_f).get_name())
        except Exception:
            pass
if not _FAM_OK:
    _fc = _fc_cjk_font()
    if _fc:
        try:
            font_manager.fontManager.addfont(_fc)
            _FAM_OK.append(font_manager.FontProperties(fname=_fc).get_name())
        except Exception:
            pass
if not _FAM_OK:
    print("[longxia_chart] 警告：未找到中文字体，图中中文会变方块"
          "（设置 CJK_FONT 或安装 NotoSansCJK / Droid Sans Fallback）", file=sys.stderr)
plt.rcParams["font.sans-serif"] = _FAM_OK + ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

UP = "#d62728"      # 阳线 红
DN = "#2ca02c"      # 阴线 绿
MA_COLORS = {20: "#1f77b4", 40: "#ff7f0e", 80: "#9467bd", 160: "#7f7f7f"}
BARS = 120          # 每格显示多少根K线


def _plot_one(ax, sub, name, code, stage, ret60, pct, ind, n=BARS, tags=None):
    sub = sub.tail(n).reset_index(drop=True)
    if sub.empty:
        ax.text(0.5, 0.5, "无数据", ha="center", va="center")
        ax.set_axis_off()
        return
    x = np.arange(len(sub))
    o = sub["hfq_open"].values
    h = sub["hfq_high"].values
    l = sub["hfq_low"].values
    c = sub["hfq"].values
    up = c >= o
    # 影线
    ax.vlines(x, l, h, color=np.where(up, UP, DN), linewidth=0.7, alpha=0.9)
    # 实体
    body = np.abs(c - o)
    base = np.minimum(o, c)
    body = np.where(body < (h - l) * 0.02 + 1e-9, (h - l) * 0.02 + 1e-9, body)
    ax.bar(x, body, bottom=base, width=0.62,
           color=np.where(up, UP, DN), edgecolor=np.where(up, UP, DN), linewidth=0.4)
    # 均线
    for w in (20, 40, 80, 160):
        col = "ma%d" % w
        if col in sub.columns:
            ax.plot(x, sub[col].values, color=MA_COLORS[w], linewidth=0.85,
                    label="MA%d" % w, alpha=0.95)
    ax.set_xlim(-1, len(sub))
    ax.grid(alpha=0.18, linestyle=":")
    ax.tick_params(labelsize=6.5, length=2)
    ax.set_xticks([0, len(sub) // 2, len(sub) - 1])
    ax.set_xticklabels([sub["trade_date"].iloc[0][4:],
                        sub["trade_date"].iloc[len(sub) // 2][4:],
                        sub["trade_date"].iloc[-1][4:]])
    # 图内不用 emoji（字体缺失会变成方块），改用符号 ◀▶/▲▼
    sign = "▲涨" if pct > 0 else "▼跌"
    ax.set_title("%s %s｜%s档｜60日 %+.1f%% 当日 %+.2f%% %s"
                 % (name, code, stage, ret60 * 100, pct, sign),
                 fontsize=7.6, pad=3.5)
    # 大佬 2026-10-28：子图后边一定要加上「所属概念」
    tc = tags or []
    if tc:
        txt = "概念：" + " · ".join(tc)
        # 超过 2 个概念时折成两行（放在图内底部，不挤压标题）
        if len(txt) > 26 and len(tc) > 2:
            half = (len(tc) + 1) // 2
            txt = "概念：" + " · ".join(tc[:half]) + "\n　　　" + " · ".join(tc[half:])
        ax.text(0.005, 0.012, txt, transform=ax.transAxes, fontsize=6.2,
                color="#333333", va="bottom", ha="left",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#fffbe6",
                          edgecolor="#e0d8b0", linewidth=0.5, alpha=0.92))


def build(panel, picks, out_path=None, date=None, n=BARS,
          stages=("A", "B"), title=None):
    """panel: 含 hfq / hfq_open / hfq_high / hfq_low / ma20..ma160 / trade_date / ts_code
       picks: 明细 list（默认只用 stage in (A,B) 的；传 stages=None 用全部）
       picks[].concepts: 所属概念标签（大佬 2026-10-28 要求子图必须带概念）
    返回图片路径；无标或画图失败返回 None。
    """
    if stages is None:
        ab = list(picks)
    else:
        ab = [p for p in picks if str(p.get("stage")) in stages]
    if not ab:
        return None
    try:
        return _build(panel, ab, out_path, date, n, title)
    except Exception as e:
        print("[chart] 画图失败: %s" % e, flush=True)
        return None


def _build(panel, ab, out_path, date, n, title=None):
    m = len(ab)
    ncol = 2 if m <= 2 else (3 if m <= 9 else 4)
    nrow = int(np.ceil(m / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.6 * ncol, 2.55 * nrow + 0.55),
                             squeeze=False)
    fig.patch.set_facecolor("white")
    for i, p in enumerate(ab):
        ax = axes[i // ncol][i % ncol]
        sub = panel[panel["ts_code"] == p["ts_code"]].sort_values("trade_date")
        _plot_one(ax, sub, p["name"], p["ts_code"], p.get("stage", ""),
                  float(p.get("ret60", 0)), float(p.get("pct", 0)),
                  p.get("industry", ""), n, p.get("concepts") or [])
    for j in range(m, nrow * ncol):
        axes[j // ncol][j % ncol].set_axis_off()
    # 统一图例（只放一次，右上角整体）
    h, lb = axes[0][0].get_legend_handles_labels()
    if h:
        fig.legend(h, lb, loc="upper right", ncol=4, fontsize=7,
                   frameon=False, bbox_to_anchor=(0.995, 0.995))
    d = date or (panel["trade_date"].max() if len(panel) else "")
    # 2026-11-27：B 档实际从未出现（行业池不够大），标题不能写死 A/B
    _lab = "A/B 档" if any(str(p.get("stage")) == "B" for p in ab) else "A 档"
    fig.suptitle(title or ("龙虾缠论 · %s（行业条件满足）K线合图 · %s · 共 %d 只（后复权）"
                           % (_lab, _fmt(d), m)), fontsize=9.2, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.965), h_pad=1.0, w_pad=0.8)
    if out_path is None:
        out_path = os.path.join(WS, ".cache/longxia/ab_chart_%s.png" % (date or "latest"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=125, facecolor="white")
    plt.close(fig)
    print("[chart] %s合图 %d 只 → %s" % (_lab.replace(' ', ''), m, out_path), flush=True)
    return out_path


def _fmt(d):
    d = str(d)
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:]) if len(d) == 8 else d


if __name__ == "__main__":
    print(__doc__)
