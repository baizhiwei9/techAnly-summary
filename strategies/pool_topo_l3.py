#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔬 备选/弃选池 · 行业三级下钻（申万 L1 → L2 → L3 → 个股）

大佬 2026-11-03 21:59 原话：
  「个股的行业拓扑的这个图，能不能再往下细挖下子？特别是那个行业。
    比如说你这个是消费行业，能不能挖到其他的？比如消费里边有白酒，有牛奶、乳业这种。
    还有什么其他的？就能不能再往下挖一级行业？然后再往下往下看看。如果能的话，
    那你重新发个图我看看」

现状问题
--------
`bin/stock_pool.py` 的行业来自 **TuShare stock_basic.industry**（东财口径，约 100 个细类），
是「平铺的一层」：白酒、食品、乳制品、红黄酒 全都并列，看不出谁属于谁。
所以「消费」下面到底有白酒 / 乳品 / 调味品 / 软饮料……这些东西，图上看不出来。

本脚本做法
----------
用**申万 2021 三级分类**（TuShare `index_member_all`，已缓存 `.cache/sw_member_all.json`，
5922 只票的 L1/L2/L3 全映射）把池内每只票挂到 三级链条 上，然后画：

  ① 申万一级行业树图（面积=池内股数，颜色=净看好）
  ② 大消费链下钻：食品饮料 / 美容护理 / 商贸零售 / 社会服务 / 家用电器 / 纺织服饰
     → L2 分组 → L3 小格 → 个股（含看好/看空次数）
  ③ 二级行业横条（Top 22，看好/看空堆叠）
  ④ 三级行业横条（Top 22）

用法
----
  python3 strategies/pool_topo_l3.py                      # 用当天池 json，出图 + 推 QQ/飞书
  python3 strategies/pool_topo_l3.py --date 2026-11-03 --no-send
  python3 strategies/pool_topo_l3.py --focus 食品饮料      # 指定下钻的一级行业
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

POOL_DIR = os.path.join(WS, ".cache/stock_pool")
SW_MEMBER = os.path.join(WS, ".cache/sw_member_all.json")
OUT_DIR = os.path.join(WS, ".cache/stock_pool")
QQ_TARGET = os.environ.get("QQ_TARGET", "qqbot:c2c:D3056302911060130E3DEEC57C618084")

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

# 大消费口径（申万一级）
CONSUMER = {"食品饮料", "美容护理", "商贸零售", "社会服务", "家用电器", "纺织服饰", "农林牧渔"}
# 大科技
TECH = {"电子", "计算机", "通信", "传媒", "电力设备", "国防军工"}
# 大金融
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


def load_sw():
    if not os.path.exists(SW_MEMBER):
        raise SystemExit("缺少 %s，先跑一次申万映射回补（index_classify + index_member_all）" % SW_MEMBER)
    return json.load(open(SW_MEMBER, encoding="utf-8"))


def load_pool(date):
    p = os.path.join(POOL_DIR, "pool_%s.json" % date.replace("-", ""))
    if not os.path.exists(p):
        raise SystemExit("找不到池文件 %s" % p)
    return json.load(open(p, encoding="utf-8"))


def build(pool, sw, min_single=1):
    """池内每只票 → 补齐 L1/L2/L3 链条"""
    rows = []
    for r in pool.get("all") or []:
        c = ts_code(r["code"])
        m = sw.get(c) or {}
        if not m:
            continue
        bull = int(r.get("bull") or 0)
        bear = int(r.get("bear") or 0)
        if bull + bear < min_single:
            continue
        rows.append({
            "code": r["code"], "name": r["name"], "em_ind": r.get("industry") or "未分类",
            "l1": m["l1"], "l2": m["l2"], "l3": m["l3"],
            "bull": bull, "bear": bear, "total": bull + bear,
            "big": BIG.get(m["l1"], "其他"),
        })
    return rows


def _color(net, total):
    """净看好比例 → 颜色（绿=看多为主，红=看空为主，灰=均衡）"""
    if total <= 0:
        return "#9e9e9e"
    r = net / float(total)
    if r >= 0.6:
        return "#2e8b57"
    if r >= 0.2:
        return "#7fbf7f"
    if r <= -0.6:
        return "#b03a2e"
    if r <= -0.2:
        return "#d98b83"
    return "#c9c9c9"


def make_chart(rows, date, focus=None, out_png=None, l1_only=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import squarify
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

    out_png = out_png or os.path.join(OUT_DIR, "pool_l3_%s.png" % date.replace("-", ""))

    # ── 汇总
    by_l1 = defaultdict(lambda: {"bull": 0, "bear": 0, "n": 0, "stocks": []})
    by_l2 = defaultdict(lambda: {"bull": 0, "bear": 0, "n": 0, "stocks": [], "l1": ""})
    by_l3 = defaultdict(lambda: {"bull": 0, "bear": 0, "n": 0, "stocks": [], "l1": "", "l2": ""})
    for r in rows:
        for d, k in ((by_l1, r["l1"]), (by_l2, (r["l1"], r["l2"])), (by_l3, (r["l1"], r["l2"], r["l3"]))):
            d[k]["bull"] += r["bull"]
            d[k]["bear"] += r["bear"]
            d[k]["n"] += 1
            d[k]["stocks"].append(r)
            if isinstance(k, tuple):
                d[k]["l1"] = k[0]
                if len(k) == 3:
                    d[k]["l2"] = k[1]

    fig = plt.figure(figsize=(20, 14.4), dpi=120)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], hspace=0.22, wspace=0.14)

    # ─── ① 申万一级行业（横向条：长度=池内股数，绿/红=净看好，右侧标 看好/看空）
    ax1 = fig.add_subplot(gs[0, 0])
    items = sorted(by_l1.items(), key=lambda kv: -kv[1]["n"])
    ys = list(range(len(items)))[::-1]
    for y_, (k, v) in zip(ys, items):
        net = v["bull"] - v["bear"]
        col = _color(net, v["bull"] + v["bear"])
        ax1.barh(y_, v["n"], color=col, height=0.72, edgecolor="white", linewidth=0.6)
        ax1.text(v["n"] + 0.15, y_, "  %d只  看好%d/看空%d" % (v["n"], v["bull"], v["bear"]),
                 va="center", ha="left", fontproperties=FP, fontsize=9.2,
                 color="#2e8b57" if net > 0 else ("#b03a2e" if net < 0 else "#666"))
    ax1.set_yticks(ys)
    ax1.set_yticklabels(["%s｜%s" % (BIG.get(k, "其他"), k) for k, _v in items],
                        fontproperties=FP, fontsize=10)
    ax1.set_xlim(0, max(v["n"] for _k, v in items) * 1.9)
    ax1.set_xlabel("池内股数", fontproperties=FP, fontsize=10)
    ax1.grid(axis="x", alpha=0.25)
    ax1.set_title("① 申万一级行业 · 池内分布（绿=看多为主 / 红=看空为主 / 灰=均衡）",
                  fontproperties=FPB, fontsize=14, pad=10)

    # ─── ② 指定一级行业的下钻树（按显示宽度预算截断，保证不超框）
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_axis_off()
    focus_l1 = focus if (focus and focus in by_l1) else items[0][0]
    if not focus:
        focus = "大消费" if any(l in CONSUMER for l in by_l1) else None

    def _dw(s_):
        """显示宽度（CJK=2 列）"""
        return sum(2 if ord(c) > 0x2E80 else 1 for c in str(s_))

    def _fit(s_, budget):
        out, n = "", 0
        for c in str(s_):
            cw = 2 if ord(c) > 0x2E80 else 1
            if n + cw > budget - 1:
                return out + "…"
            out += c
            n += cw
        return out

    # focus 可以是申万一级名，也可以是「大消费/大科技/大金融」
    if focus and focus in ("大消费", "大科技", "大金融"):
        focus_set = [l for l in (CONSUMER if focus == "大消费"
                                 else TECH if focus == "大科技" else FIN) if l in by_l1]
        focus_set.sort(key=lambda l: -by_l1[l]["n"])
        focus_name = focus
    else:
        focus_set = [focus_l1]
        focus_name = focus_l1

    MAXROW = 30
    BUDGET = 88
    lines = []
    for l1 in focus_set:
        v1 = by_l1[l1]
        lines.append((0, "【%s】%s · 池内 %d 只 · 看好 %d / 看空 %d"
                      % (BIG.get(l1, "其他"), l1, v1["n"], v1["bull"], v1["bear"]), "h"))
        l2s = sorted([(k[1], v) for k, v in by_l2.items() if k[0] == l1],
                     key=lambda kv: -kv[1]["n"])
        for l2, v2 in l2s:
            if len(lines) >= MAXROW:
                break
            lines.append((1, "%s（%d只 看好%d/看空%d）"
                          % (l2, v2["n"], v2["bull"], v2["bear"]), "l2"))
            l3s = sorted([(k[2], v) for k, v in by_l3.items()
                          if k[0] == l1 and k[1] == l2], key=lambda kv: -kv[1]["n"])
            for l3, v3 in l3s:
                if len(lines) >= MAXROW:
                    break
                names = "、".join("%s(%d/%d)" % (s_["name"], s_["bull"], s_["bear"])
                                 for s_ in sorted(v3["stocks"],
                                                 key=lambda x: -(x["bull"] - x["bear"])))
                lines.append((2, _fit("%s：%s" % (l3, names), BUDGET - 4), "l3"))
        if len(lines) >= MAXROW:
            lines.append((0, "…（其余行业略，见左侧一级行业图）", "l3"))
            break
    fs = 11.0 if len(lines) <= 16 else (10.0 if len(lines) <= 22 else 9.0)
    step = min(0.058, 0.95 / max(len(lines), 1))
    y = 0.975
    for lvl, txt, kind in lines:
        if kind == "h":
            ax2.text(0.005, y, _fit(txt, BUDGET), transform=ax2.transAxes,
                     fontproperties=FPB, fontsize=fs + 1.8, color="#1b4f72", va="top")
            y -= step * 1.2
        elif kind == "l2":
            ax2.text(0.018, y, _fit("└ " + txt, BUDGET), transform=ax2.transAxes,
                     fontproperties=FPB, fontsize=fs, color="#1e8449", va="top")
            y -= step
        else:
            ax2.text(0.045, y, _fit("· " + txt, BUDGET), transform=ax2.transAxes,
                     fontproperties=FP, fontsize=fs - 1.2, color="#333", va="top")
            y -= step * 0.92
    ax2.set_title("② 下钻「%s」：一级 › 二级 › 三级 › 个股（括号=看好/看空次数）" % focus_name,
                  fontproperties=FPB, fontsize=14, pad=10)

    # ─── ③ 二级行业横条
    ax3 = fig.add_subplot(gs[1, 0])
    l2s = sorted(by_l2.items(), key=lambda kv: -(kv[1]["n"] + 0.1 * (kv[1]["bull"] + kv[1]["bear"])))[:22]
    l2s = sorted(l2s, key=lambda kv: kv[1]["n"])
    ypos = range(len(l2s))
    ax3.barh(list(ypos), [v["bull"] for _k, v in l2s], color="#2e8b57", label="看好次数")
    ax3.barh(list(ypos), [-v["bear"] for _k, v in l2s], color="#b03a2e", label="看空次数")
    ax3.set_yticks(list(ypos))
    ax3.set_yticklabels(["%s·%s" % (k[0], k[1]) for k, _v in l2s], fontproperties=FP, fontsize=9.5)
    ax3.axvline(0, color="#555", lw=0.8)
    ax3.legend(prop=FP, fontsize=10, loc="lower right")
    ax3.set_title("③ 申万二级行业 · Top22（绿=看好次数，红=看空次数）",
                  fontproperties=FPB, fontsize=14, pad=8)
    ax3.grid(axis="x", alpha=0.25)

    # ─── ④ 三级行业横条
    ax4 = fig.add_subplot(gs[1, 1])
    l3s = sorted(by_l3.items(), key=lambda kv: -(kv[1]["n"] + 0.1 * (kv[1]["bull"] + kv[1]["bear"])))[:22]
    l3s = sorted(l3s, key=lambda kv: kv[1]["n"])
    ypos = range(len(l3s))
    ax4.barh(list(ypos), [v["bull"] for _k, v in l3s], color="#2e8b57", label="看好次数")
    ax4.barh(list(ypos), [-v["bear"] for _k, v in l3s], color="#b03a2e", label="看空次数")
    ax4.set_yticks(list(ypos))
    ax4.set_yticklabels(["%s·%s·%s" % (k[0][:2], k[1][:4], k[2]) for k, _v in l3s],
                        fontproperties=FP, fontsize=9)
    ax4.axvline(0, color="#555", lw=0.8)
    ax4.legend(prop=FP, fontsize=10, loc="lower right")
    ax4.set_title("④ 申万三级行业 · Top22（最细一层，白酒Ⅲ/乳品/调味发酵品Ⅲ…）",
                  fontproperties=FPB, fontsize=14, pad=8)
    ax4.grid(axis="x", alpha=0.25)

    fig.suptitle("个股备选池 / 弃选池 · 行业三级下钻（申万 2021 · L1→L2→L3→个股）— %s" % date,
                 fontproperties=FPB, fontsize=20, y=0.975)
    fig.text(0.5, 0.012,
             "口径：行业分类 = 申万 2021 三级（TuShare index_member_all，%d 只票全映射）；"
             "括号内为群内提及次数（看好/看空）。绿色=看多为主，红色=看空为主，灰色=多空均衡。"
             % len(rows),
             ha="center", fontproperties=FP, fontsize=10.5, color="#555")
    fig.tight_layout(rect=(0, 0.028, 1, 0.955))
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)
    return out_png


def fmt_text(rows, date):
    by_l1 = defaultdict(lambda: {"bull": 0, "bear": 0, "n": 0})
    by_l3 = defaultdict(lambda: {"bull": 0, "bear": 0, "n": 0, "stocks": []})
    for r in rows:
        for d, k in ((by_l1, r["l1"]), (by_l3, (r["l1"], r["l2"], r["l3"]))):
            d[k]["bull"] += r["bull"]
            d[k]["bear"] += r["bear"]
            d[k]["n"] += 1
            if isinstance(k, tuple):
                d[k]["stocks"].append(r)
    L = ["🔬 行业三级下钻 · %s（申万 2021）" % date, ""]
    L.append("【一级 → 二级 → 三级（池内股数）】")
    for l1, v1 in sorted(by_l1.items(), key=lambda kv: -kv[1]["n"]):
        net = v1["bull"] - v1["bear"]
        mk = "🌙" if net > 0 else ("⭐" if net < 0 else "")
        L.append("%s %s（%d只 看好%d/看空%d）%s" % (BIG.get(l1, "其他"), l1, v1["n"], v1["bull"], v1["bear"], mk))
        for (a, l2, l3), v3 in sorted(by_l3.items(), key=lambda kv: (-kv[1]["n"], kv[0][2])):
            if a != l1:
                continue
            names = "、".join("%s(%d/%d)" % (s["name"], s["bull"], s["bear"])
                             for s in sorted(v3["stocks"], key=lambda x: -(x["bull"] - x["bear"])))
            L.append("   └ %s › %s：%s" % (l2, l3, names))
    L.append("")
    L.append("（括号内为群内提及：看好次数/看空次数）")
    return "\n".join(L)


def push_qq(img, text):
    try:
        import push_image
        return push_image.send_qq(img, text)
    except Exception as e:
        print("qq push err", str(e)[:120], file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--focus", default="")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--text-only", action="store_true")
    a = ap.parse_args()
    date = a.date or __import__("datetime").date.today().strftime("%Y-%m-%d")
    pool = load_pool(date)
    rows = build(pool, load_sw())
    if not rows:
        raise SystemExit("池内股票没有申万映射，检查 .cache/sw_member_all.json")
    txt = fmt_text(rows, date)
    print(txt)
    if a.text_only:
        return
    png = make_chart(rows, date, focus=a.focus or None)
    print("chart ->", png)
    if not a.no_send:
        push_qq(png, "🔬 行业三级下钻 · %s" % date)


if __name__ == "__main__":
    main()
