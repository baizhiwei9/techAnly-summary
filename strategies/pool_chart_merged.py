#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🖼 个股池 · 合并大图（把三张图合成一张，2026-11-03 22:09 大佬要求）

大佬原话：
  「你把这个任务能不能修改一下呢？就是把跟刚才那个图合了一合一下…之前看多、看空
    不都是二级行业？还是一级行业了？还有概念，概念那个不要动。然后我希望你加到那个，
    如果你二级行业了，有看多看空的，你可以下钻到三级行业，就是更精细一些了。
    如果你好的话，然后你合并一下子呗，重新出个图。重新出个图的话，就依次把这几张图都合一下子」

合并内容（3 行 × 3 列）
-----------------------
行1 看多·备选池：① 行业分布（东财口径，与原图一致）② 概念分布（**原样不动**）③ 资金结构 TDA/UMAP
行2 看空·弃选池：④ 行业分布 ⑤ 概念分布（**原样不动**）⑥ 资金结构 TDA/UMAP
行3 行业三级下钻：⑦ 看多池 二级›三级›个股  ⑧ 看空池 二级›三级›个股  ⑨ 全池 申万三级 Top20

行业口径说明（回答大佬的问题）
------------------------------
· 原图的「行业」= TuShare `stock_basic.industry`（东财口径，约 100 个**平铺**细类，
  等价于申万的二级~三级之间，不是严格一级也不是二级）。所以原图里"白酒"和"食品"并列。
· 本图行1/行2 保持原口径（与历史图可比），行3 另用**申万 2021 三级**做真正的层级下钻，
  才能看到「食品饮料 › 白酒Ⅱ › 白酒Ⅲ」「食品饮料 › 饮料乳品 › 乳品」。

用法
----
  python3 strategies/pool_chart_merged.py --date 2026-11-03            # 出图 + 推 QQ
  python3 strategies/pool_chart_merged.py --date 2026-11-03 --no-send  # 只出图
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import datetime
import json
import os
import sys
from collections import Counter, defaultdict

WS = os.path.expanduser("~/.openclaw/workspace")
sys.path.insert(0, os.path.join(WS, "bin"))

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
POOL_DIR = os.path.join(WS, ".cache/stock_pool")
SW_MEMBER = os.path.join(WS, ".cache/sw_member_all.json")

CONSUMER = {"食品饮料", "美容护理", "商贸零售", "社会服务", "家用电器", "纺织服饰", "农林牧渔"}
TECH = {"电子", "计算机", "通信", "传媒", "电力设备", "国防军工"}
FIN = {"银行", "非银金融", "房地产"}
BIG = {}
for _s in CONSUMER:
    BIG[_s] = "大消费"
for _s in TECH:
    BIG[_s] = "大科技"
for _s in FIN:
    BIG[_s] = "大金融"
BIG.update({"医药生物": "医药", "基础化工": "周期", "钢铁": "周期", "有色金属": "周期",
            "煤炭": "周期", "石油石化": "周期", "建筑材料": "周期", "建筑装饰": "周期",
            "机械设备": "制造", "汽车": "制造", "轻工制造": "制造", "公用事业": "公用",
            "交通运输": "公用", "环保": "公用", "综合": "其他"})


def ts_code(c):
    c = str(c).strip().zfill(6)
    if c[0] in "69":
        return c + ".SH"
    if c[0] in "48":
        return c + ".BJ"
    return c + ".SZ"


def dw(s):
    """显示宽度（CJK=2）"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in str(s))


def fit(s, budget):
    out, n = "", 0
    for c in str(s):
        cw = 2 if ord(c) > 0x2E80 else 1
        if n + cw > budget - 1:
            return out + "…"
        out += c
        n += cw
    return out


def build_rows(pool, sw):
    """池内每只票 → 补 L1/L2/L3 + 大分类"""
    out = []
    for r in pool.get("all") or []:
        m = sw.get(ts_code(r["code"])) or {}
        if not m:
            continue
        bull, bear = int(r.get("bull") or 0), int(r.get("bear") or 0)
        out.append({**r, "l1": m["l1"], "l2": m["l2"], "l3": m["l3"],
                    "bull": bull, "bear": bear, "big": BIG.get(m["l1"], "其他")})
    return out


# ═══════════════════════════════════════════════════ 画图
def make(rows_all, pick, avoid, struct, topo_pick, topo_avoid, date, out_png, cmap, SP):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.font_manager import FontProperties
    for f in (FONT, FONT_B):
        try:
            font_manager.fontManager.addfont(f)
        except Exception:
            pass
    FP = FontProperties(fname=FONT)
    FPB = FontProperties(fname=FONT_B)
    plt.rcParams["axes.unicode_minus"] = False

    swmap = {r["code"]: r for r in rows_all}

    fig = plt.figure(figsize=(26, 23), dpi=100)
    gs = fig.add_gridspec(3, 3, wspace=0.34, hspace=0.36,
                          left=0.055, right=0.985, top=0.945, bottom=0.045)

    # ─────────── 行1/行2：行业 + 概念 + 拓扑（原图口径，概念不动）
    def agg(rows, key):
        if key == "industry":
            return Counter(r["industry"] for r in rows)
        return SP.weighted_concepts(rows, cmap, min_stocks=1)

    def draw_bar(ax, rows, key, title, color):
        cnt = agg(rows, key)
        ranked = cnt.most_common(20)
        if key == "concept":
            shared = [(k, v) for k, v in ranked if v >= 2][:12]
            singles = [(k, v) for k, v in ranked if v < 2][:3]
            top = shared + singles if shared else ranked[:6]
        else:
            top = ranked[:12]
        ax.set_title(title, fontproperties=FPB, fontsize=17, pad=11)
        if not top:
            ax.text(0.5, 0.5, "（无数据）", ha="center", va="center",
                    fontproperties=FP, fontsize=18, color="#888")
            ax.axis("off")
            return
        labels = [k for k, _ in top][::-1]
        vals = [v for _, v in top][::-1]
        cols = [color if v >= 2 else "#c9c9c9" for v in vals]
        ax.barh(range(len(vals)), vals, color=cols, alpha=0.9, height=0.62)
        ax.set_yticks(range(len(vals)))
        lab2 = []
        for k in labels:
            m = (struct or {}).get(k)
            lab2.append("%s  %s·%s" % (k, m["dir"], m["state"]) if m else k)
        ax.set_yticklabels(lab2, fontproperties=FP, fontsize=12.5)
        ax.set_xlabel("只数", fontproperties=FP, fontsize=12)
        ax.tick_params(axis="x", labelsize=11)
        ax.grid(axis="x", ls="--", alpha=0.3)
        mx = max(vals)
        for i, v in enumerate(vals):
            ax.text(v + mx * 0.015, i, str(v), va="center", fontproperties=FP, fontsize=12)
        ax.set_xlim(0, mx * 1.22)
        for i, k in enumerate(labels):
            m = (struct or {}).get(k)
            if not m:
                continue
            arrow = "↑" if m["dir"] == "涨" else "↓"
            cc = "#d9534f" if m["state"] == "聚集" else "#3f7fbf"
            ax.text(mx * 1.20, i, "%s%s" % (arrow, m["state"]), ha="right", va="center",
                    fontproperties=FPB, fontsize=12, color=cc)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)

    def draw_topo(ax, t, label, upcolor, dncolor):
        ax.set_title("%s — 资金结构（TDA/UMAP）" % label, fontproperties=FPB, fontsize=17, pad=11)
        if not t or not t.get("ok"):
            ax.text(0.5, 0.5, "拓扑不可算\n" + ((t or {}).get("why") or "（无数据）"),
                    ha="center", va="center", fontproperties=FP, fontsize=14, color="#888")
            ax.axis("off")
            return
        p = t["pool"]
        ss = p["series"]
        xs = [s["d"][5:] for s in ss]
        ax.plot(xs, [s["ar"] * 100 for s in ss], "o-", color="#1f77b4", lw=2.4, ms=6,
                label="吸收比率 AR(%)")
        ax.plot(xs, [s["spread"] * 100 for s in ss], "s--", color="#ff7f0e", lw=2.0, ms=5,
                label="UMAP 离散度")
        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels(xs, fontproperties=FP, fontsize=10, rotation=30)
        ax.tick_params(axis="y", labelsize=10)
        ax.legend(prop=FP, fontsize=11, loc="best", framealpha=0.9)
        ax.grid(ls="--", alpha=0.25)
        for s_ in ("top", "right"):
            ax.spines[s_].set_visible(False)
        ok = p["votes"] >= 1
        color = upcolor if ok else dncolor
        txt = ("当日 %+.2f%% / 超额 %+.2f%%  胜率 %.0f%%\n"
               "ΔAR %+.2fct ｜ ΔH0 %+.2f ｜ Δ离散 %+.2f\n"
               "TDA %s ｜ UMAP %s ｜ %s·%s"
               % (p["ret_today"], p["resid_today"], p["pct_pos"],
                  p["d_ar"] * 100, p["d_h0"], p["d_spread"] * 100,
                  "过" if p["tda_ok"] else "否", "过" if p["umap_ok"] else "否",
                  p["verdict"], p["strength"]))
        ax.text(0.02, 0.02, txt, transform=ax.transAxes, fontproperties=FP, fontsize=11.0,
                color=color, va="bottom", ha="left", zorder=5,
                bbox=dict(boxstyle="round,pad=0.35", fc="#f7f7f7", ec=color, lw=1.3, alpha=0.94))

    ax_i1 = fig.add_subplot(gs[0, 0])
    ax_c1 = fig.add_subplot(gs[0, 1])
    ax_t1 = fig.add_subplot(gs[0, 2])
    ax_i2 = fig.add_subplot(gs[1, 0])
    ax_c2 = fig.add_subplot(gs[1, 1])
    ax_t2 = fig.add_subplot(gs[1, 2])
    draw_bar(ax_i1, pick, "industry", "① 看多·备选池 — 行业分布（%d 只）" % len(pick), "#2e8b57")
    draw_bar(ax_c1, pick, "concept", "② 看多·备选池 — 概念分布（%d 只）" % len(pick), "#3aa76d")
    draw_topo(ax_t1, topo_pick, "③ 看多·备选池", "#1d7a45", "#a03a2e")
    draw_bar(ax_i2, avoid, "industry", "④ 看空·弃选池 — 行业分布（%d 只）" % len(avoid), "#b03a2e")
    draw_bar(ax_c2, avoid, "concept", "⑤ 看空·弃选池 — 概念分布（%d 只）" % len(avoid), "#c05a4e")
    draw_topo(ax_t2, topo_avoid, "⑥ 看空·弃选池", "#1d7a45", "#a03a2e")

    # ─────────── 行3：申万三级下钻
    def draw_tree(ax, codes, title, color, budget=66, maxrow=26):
        ax.set_axis_off()
        ax.set_title(title, fontproperties=FPB, fontsize=17, pad=11)
        by2 = defaultdict(list)
        for c in codes:
            r = swmap.get(c)
            if r:
                by2[(r["l1"], r["l2"])].append(r)
        if not by2:
            ax.text(0.5, 0.5, "（无数据）", ha="center", va="center",
                    fontproperties=FP, fontsize=16, color="#888")
            return
        lines = []
        for (l1, l2), rs in sorted(by2.items(), key=lambda kv: -len(kv[1])):
            if len(lines) >= maxrow:
                lines.append("…（其余略）")
                break
            b = sum(x["bull"] for x in rs)
            s = sum(x["bear"] for x in rs)
            lines.append("▶ %s › %s（%d 只，看好%d/看空%d）" % (l1, l2, len(rs), b, s))
            by3 = defaultdict(list)
            for x in rs:
                by3[x["l3"]].append(x)
            for l3, xs in sorted(by3.items(), key=lambda kv: -len(kv[1])):
                if len(lines) >= maxrow:
                    break
                names = "、".join("%s(%d/%d)" % (x["name"], x["bull"], x["bear"])
                                 for x in sorted(xs, key=lambda z: -(z["bull"] - z["bear"])))
                lines.append("   · %s：%s" % (l3, names))
        fs = 11.2 if len(lines) <= 16 else (10.4 if len(lines) <= 21 else 9.6)
        step = min(0.062, 0.96 / max(len(lines), 1))
        y = 0.975
        for i, t in enumerate(lines):
            is_l2 = t.startswith("▶")
            ax.text(0.008 if is_l2 else 0.032, y, fit(t, budget),
                    transform=ax.transAxes,
                    fontproperties=FPB if is_l2 else FP,
                    fontsize=fs + (0.8 if is_l2 else -0.9),
                    color=color if is_l2 else "#333", va="top")
            y -= step * (1.05 if is_l2 else 0.94)

    ax_7 = fig.add_subplot(gs[2, 0])
    ax_8 = fig.add_subplot(gs[2, 1])
    ax_9 = fig.add_subplot(gs[2, 2])
    draw_tree(ax_7, [r["code"] for r in pick],
              "⑦ 看多·备选池 — 二级 › 三级 › 个股（申万2021）", "#1d7a45")
    draw_tree(ax_8, [r["code"] for r in avoid],
              "⑧ 看空·弃选池 — 二级 › 三级 › 个股（申万2021）", "#a03a2e")

    # ⑨ 全池申万三级 Top20（看好/看空堆叠）
    l3agg = defaultdict(lambda: {"b": 0, "s": 0, "n": 0})
    for r in rows_all:
        k = "%s›%s›%s" % (r["l1"], r["l2"], r["l3"])
        l3agg[k]["b"] += r["bull"]
        l3agg[k]["s"] += r["bear"]
        l3agg[k]["n"] += 1
    items = sorted(l3agg.items(), key=lambda kv: -(kv[1]["b"] + kv[1]["s"]))[:20]
    items = items[::-1]
    ax_9.set_title("⑨ 全池 · 申万三级行业 Top20（提及次数，绿=看好 / 红=看空）",
                   fontproperties=FPB, fontsize=17, pad=11)
    if items:
        ys = range(len(items))
        ax_9.barh(list(ys), [v["b"] for _k, v in items], color="#2e8b57", label="看好次数")
        ax_9.barh(list(ys), [-v["s"] for _k, v in items], color="#b03a2e", label="看空次数")
        ax_9.set_yticks(list(ys))
        ax_9.set_yticklabels([fit(k.replace("›", "›"), 30) for k, _v in items],
                             fontproperties=FP, fontsize=10.5)
        ax_9.axvline(0, color="#555", lw=0.9)
        ax_9.legend(prop=FP, fontsize=11, loc="lower right")
        ax_9.grid(axis="x", ls="--", alpha=0.3)
        mx = max(max(v["b"], v["s"]) for _k, v in items)
        for i, (k, v) in enumerate(items):
            ax_9.text(max(v["b"], 0) + mx * 0.02, i, "%d只" % v["n"], va="center",
                      fontproperties=FP, fontsize=9.5, color="#666")
        ax_9.set_xlim(-mx * 1.18, mx * 1.35)
        for sp in ("top", "right"):
            ax_9.spines[sp].set_visible(False)

    fig.suptitle("个股备选池 / 弃选池 — 行业·概念·资金结构·申万三级下钻 合并图 — %s" % date,
                 fontproperties=FPB, fontsize=23, y=0.977)
    fig.text(0.5, 0.014,
             "行1/行2 行业=TuShare stock_basic.industry（东财口径，约100个平铺细类，介于申万二~三级之间），与历史图口径一致；"
             "概念区口径未改动（按池内共有股票数加权）。行3 改用申万2021三级分类做真正层级下钻。"
             "备选=看好≥4次且占比≥60%，弃选=看空≥3次且占比≥60%；标签后缀=行业/概念结构状态（↑涨/↓跌 + 聚集/扩散）。",
             ha="center", fontproperties=FP, fontsize=11.5, color="#555")
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)
    return out_png


# ═══════════════════════════════════════════════════ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--no-send", action="store_true")
    a = ap.parse_args()
    date = a.date or datetime.date.today().strftime("%Y-%m-%d")
    stamp = date.replace("-", "")
    pj = os.path.join(POOL_DIR, "pool_%s.json" % stamp)
    if not os.path.exists(pj):
        raise SystemExit("找不到池文件 %s" % pj)
    pool = json.load(open(pj, encoding="utf-8"))
    sw = json.load(open(SW_MEMBER, encoding="utf-8"))
    rows_all = build_rows(pool, sw)

    import stock_pool as SP
    cmap = SP.load_concepts()

    # 结构状态（与 stock_pool 同口径，纯程序 0 token）
    struct = {}
    try:
        import struct_tag as ST
        ind_set = {r.get("industry") for r in (pool["pick"] + pool["avoid"]) if r.get("industry")}
        inds, cons = set(), set()
        for rows_ in (pool["pick"], pool["avoid"]):
            for k, _ in Counter(r.get("industry") for r in rows_).most_common(16):
                inds.add(k)
            for k, _ in SP.weighted_concepts(rows_, cmap, min_stocks=1).most_common(20):
                if any(k in k2 for k2 in SP.LOW_VALUE_TAGS) or SP.LIST_TAG.search(k):
                    continue
                if k in ind_set:
                    inds.add(k)
                else:
                    cons.add(k)
        struct = ST.run(date, sorted(x for x in inds if x), sorted(cons))
        print("[struct] %d labels" % len(struct))
    except Exception as e:
        print("[struct] fail", str(e)[:160], file=sys.stderr)

    out_png = os.path.join(POOL_DIR, "pool_merged_%s.png" % stamp)
    make(rows_all, pool["pick"], pool["avoid"], struct,
         pool.get("topo_pick"), pool.get("topo_avoid"), date, out_png, cmap, SP)
    print("merged ->", out_png, os.path.getsize(out_png) // 1024, "KB")

    if not a.no_send:
        import push_image
        ok = push_image.send_qq(out_png, "个股池合并图 · %s（行业+概念+拓扑+申万三级下钻）" % date)
        print("qq", ok)


if __name__ == "__main__":
    main()
