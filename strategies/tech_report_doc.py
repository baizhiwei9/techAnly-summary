#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大盘技术拆解 · 图文报告生成器（Markdown / HTML / PDF 三合一）
=====================================================================
把 20:15 日报的纯文本（.cache/market_tech_*.txt）+ 缠论K线图，
合成一份**图文一体**的文档：图直接嵌入文件内部（base64），
打开单个文件就能看全，不用带一堆散图。

输出：
  ① .md   —— 图片 base64 内嵌，单文件自包含（推荐先看这个）
  ② .html —— 同样内嵌，给 PDF 用 / 也可直接浏览器看
  ③ .pdf  —— 用 chromium 无头打印（环境已有）

用法：
  python3 strategies/tech_report_doc.py                       # 用最新一份报告
  python3 strategies/tech_report_doc.py --src .cache/market_tech_20261023_2017.txt
  python3 strategies/tech_report_doc.py --pdf                 # 同时出 PDF
  python3 strategies/tech_report_doc.py --pdf --push          # 出完推 QQ+飞书私聊
  python3 strategies/tech_report_doc.py --no-chart            # 不重新出图（复用已有图）
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, re, glob, base64, argparse, subprocess, shutil
from datetime import datetime

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT_DIR = os.path.join(WS, "memory/tech_reports")
CHART_DIR = os.path.join(WS, ".cache/charts")
SEP = "─" * 30


# ───────────────────────── 解析纯文本报告 ─────────────────────────
def parse_pcr(lines):
    """成交量 PCR → 结构化行"""
    rows = []
    for ln in lines:
        m = re.match(r"\s*(\S+)\s*[：:]\s*([\d.]+)\s*｜\s*近60日分位\s*([\d.]+)%"
                     r"（([^）]+)）\s*｜\s*较前日\s*([+\-][\d.]+)\s*(↑|↓)?", ln)
        if m:
            name, val, pct, lvl, chg, arrow = m.groups()
            rows.append({"name": name, "pcr": val, "pct": pct,
                         "level": lvl, "chg": chg, "arrow": arrow or ""})
    return rows


# ───────────────────────── 对齐工具 ─────────────────────────
def disp_w(s):
    """显示宽度：CJK 全角算 2 列（等宽字体下对齐要用这个，不能用 len）"""
    import unicodedata
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s, width, align="left"):
    """按显示宽度补空格"""
    s = str(s)
    gap = max(0, width - disp_w(s))
    if align == "right":
        return " " * gap + s
    if align == "center":
        l = gap // 2
        return " " * l + s + " " * (gap - l)
    return s + " " * gap


def md_table(headers, rows, aligns=None):
    """生成**源码层面真正对齐**的 markdown 表格（管道符竖排对齐）
    ⚠️ 中文按显示宽度算（CJK=2列），否则渲染器不处理时源码是歪的。
    """
    aligns = aligns or ["left"] * len(headers)
    cols = len(headers)
    widths = []
    for i in range(cols):
        w = disp_w(headers[i])
        for r in rows:
            w = max(w, disp_w(r[i] if i < len(r) else ""))
        widths.append(w)

    def line(cells, aligns_):
        parts = []
        for i, c in enumerate(cells):
            parts.append(" " + pad(c, widths[i], aligns_[i]) + " ")
        return "|" + "|".join(parts) + "|"

    sep = "|" + "|".join(
        (" " + ("-" * widths[i]) + " ") if aligns[i] == "left"
        else (" " + ("-" * (widths[i] - 1)) + ": ") if aligns[i] == "right"
        else (" :" + ("-" * (widths[i] - 2)) + ": ")
        for i in range(cols)) + "|"
    out = [line(headers, ["left"] * cols), sep]
    for r in rows:
        out.append(line([r[i] if i < len(r) else "" for i in range(cols)], aligns))
    return out


def norm_spark(s):
    """把走势条里的空格（=最低值，看不见）换成 ▁，并去掉多余尾随空格。
    原因：源 spark() 用的字符集首字符是空格 ' ▁▂▃▄▅▆▇█'，最低点变成空白，
    一条 41 字符的条看起来宽度不齐。换成 ▁ 后等宽可见、对齐。
    """
    if not s:
        return s
    s = s.strip()
    return s.replace(" ", "▁")


def parse_leaderboard(text, kind):
    """机会榜 / 风险榜。kind='opp'|'risk'
    返回 [{"stars":"🌙🌙🌙", "name":"沪深300", "level":"小时线60m",
           "tag":"一买", "price":"4115.07", "note":"📍现价就在买点上", "age":"21"}]
    """
    if kind == "opp":
        pat = r"有机会的指数[^\n]*\n(.*?)(?=有风险的指数)"
    else:
        pat = r"有风险的指数[^\n]*\n(.*?)(?=（本榜单口径|══════)"
    m = re.search(pat, text, re.S)
    if not m:
        return []
    items = []
    for ln in m.group(1).splitlines():
        s = ln.strip()
        if not s or s.startswith("⚠️") or s.startswith("🎯"):
            continue
        if "无新鲜" in s or "无新鲜卖点" in s:
            items.append({"empty": s})
            continue
        # 1. 🌙🌙🌙 沪深300 ｜小时线60m 一买 4115.07 ｜📍现价就在买点上 ｜21根K线前
        m2 = re.match(r"(?:\d+\.\s*)?([🌙⭐]+)\s*(\S+)\s*｜\s*(\S+)\s+(\S+?)\s+([\d.]+)"
                      r"\s*｜\s*([^｜]+?)\s*｜\s*(\d+)\s*根", s)
        if m2:
            stars, name, lv, tag, price, note, age = m2.groups()
            items.append({"stars": stars, "name": name, "level": lv, "tag": tag,
                          "price": price, "note": note.strip(), "age": age})
        else:
            items.append({"raw": s})
    return items


def parse_indices(text):
    """各指数明细 → [{name, close, spark, data, ladder[], chan[], smc{}}]"""
    out = []
    blocks = re.findall(r"══════ 【([^】]+)】收 ([\d.]+) ══════\n(.*?)(?=══════ 【|\Z)", text, re.S)
    for name, close, body in blocks:
        d = {"name": name, "close": close, "spark": "", "data": "",
             "ladder": [], "chan": [], "smc": []}
        in_ladder = False
        cur_smc = None
        for ln in body.splitlines():
            s = ln.strip()
            if not s:
                continue
            if s.startswith("日线近40日"):
                d["spark"] = s.split("：", 1)[-1].split(":", 1)[-1].strip()
                continue
            if s.startswith("数据："):
                d["data"] = s.replace("数据：", "").strip()
                continue
            if s.startswith("小时线示意图"):
                in_ladder = True
                continue
            if in_ladder:
                if "┤" in ln:
                    d["ladder"].append(ln.rstrip())
                    continue
                in_ladder = False
            if s.startswith("【分型版"):
                d["chan"].append(s)
                continue
            if s.startswith("SMC日") or s.startswith("SMC周"):
                cur_smc = {"title": s, "lines": []}
                d["smc"].append(cur_smc)
                continue
            if cur_smc is not None and (s.startswith("破位：") or s.startswith("机会：")
                                        or s.startswith("风险：")):
                cur_smc["lines"].append(s)
                continue
            if s.startswith("──"):
                continue
        out.append(d)
    return out


def split_report(txt):
    if SEP in txt:
        a, b = txt.split(SEP, 1)
        return a, b
    return "", txt


# ───────────────────────── 出图 ─────────────────────────
def build_charts(d8, force=False):
    """**每个级别合成一张图**（2026-10-24 用户最终口径）

    用户原话：「小时级别有买点的和有卖点的，你都合成一张图片……就发一次图片就行了。
    然后其他的级别……没有机会啊什么的，差多少，你都文字版都说明了，我就知道。
    所以你不要发太多图片，太乱」

    返回 {level: path}，level ∈ {h1, day}；某级别无信号则不出图。
    """
    import chan_smc_index as C
    import chan_signal_monitor as M

    out = {}
    try:
        import chan_panorama as PANO
    except Exception as e:
        print("[WARN] 拼图模块不可用:", str(e)[:150])
        return out

    for lv, key_lv in (("h1", "小时"), ("day", "日")):
        names, sigs = [], []
        for name, code in C.IDX:
            try:
                if lv == "h1":
                    df = M.fetch_60_fresh(C.IDX_SH[name])
                else:
                    df = M.fetch_daily_fresh(code)
                if df is None or len(df) < 60:
                    continue
                ck = C.chan_read(C.bars_of(df), "h1" if lv == "h1" else "day")
                if not ck.get("ok"):
                    continue
                P = float(df["close"].iloc[-1])
                sig = M.point_sig(ck, key_lv, P)[0]
                if sig:
                    names.append(name)
                    sigs.append("%s %s" % (name, sig))
            except Exception as e:
                print("[WARN] 判定失败 %s %s: %s" % (name, lv, str(e)[:100]))
        # 2026-10-24 定稿：两个级别**都出图**（日线没买点也出，当走势参考）
        if not names:
            print("[chart] %s 无买卖点 → 仍出走势参考图（全部指数）" % lv, flush=True)
            names = [x[0] for x in C.IDX]
            sigs = ["无买卖点"]
        path = out.get(lv) or os.path.join(CHART_DIR, "panorama_%s_%s.png" % (lv, d8))
        if force or not os.path.exists(path):
            try:
                PANO.draw_panorama(lv, n=90, out=path, names=names,
                                   title_extra="　｜　" + "、".join(sigs))
                print("[chart]", lv, "、".join(sigs), flush=True)
            except Exception as e:
                print("[WARN] 拼图 %s 失败: %s" % (lv, str(e)[:150]))
                continue
        if os.path.exists(path):
            out[lv] = path
    return out


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


# ───────────────────────── 生成 Markdown ─────────────────────────
def parse_chan_line(s):
    """【分型版·小时线60m】阶段=xx | 中枢=a~b | 买点 ... | 背驰=... → dict"""
    m = re.match(r"【分型版·([^】]+)】(.*)$", s)
    if not m:
        return None
    lv, rest = m.groups()
    d = {"level": lv, "stage": "", "zs": "-", "points": "-", "div": "-"}
    for part in rest.split("|"):
        p = part.strip()
        if p.startswith("阶段="):
            d["stage"] = p[3:]
        elif p.startswith("中枢="):
            d["zs"] = p[3:]
        elif p.startswith("买点") or p.startswith("卖点"):
            d["points"] = (d["points"] + "；" + p) if d["points"] != "-" else p
        elif p.startswith("背驰="):
            d["div"] = p[3:]
    return d


def make_md(d, pcr, opp, risk, indices, charts, src_name):
    """生成对齐规整的 Markdown（表格源码级对齐、刻度图等宽、分节一致）"""
    L = []
    # ═══ 抬头
    L += ["# 🧭 大盘技术拆解 · %s" % d, ""]
    L += md_table(
        ["项目", "内容"],
        [["级别", "小时线60m（60分钟K线） + 日线"],
         ["版本", "缠论·分型版（包含处理 → 顶底分型 → 笔 → 中枢ZG/ZD → 背驰）"],
         ["范围", "全篇仅此一个版本，不含均线版 / 趋势浪子版"],
         ["数据", indices[0]["data"] if indices else "-"],
         ["生成", "纯脚本生成 · 0 token · 源 %s" % src_name]])
    L += ["", "---", ""]

    # ═══ 一、PCR
    if pcr:
        L += ["## 📊 一、成交量 PCR", ""]
        rows = [[r["name"], r["pcr"], r["pct"] + "%", r["level"],
                 r["chg"] + (" " + r["arrow"] if r["arrow"] else "")] for r in pcr]
        L += md_table(["指数", "PCR", "60日分位", "水平", "较前日"], rows,
                      ["left", "right", "right", "left", "right"])
        L += ["", "> 近5个交易日认沽量之和 ÷ 认购量之和，剔除当月合约",
              ">",
              "> 数据 = 上/深交所 ETF 期权 + 中金所股指期权", ""]

    # ═══ 二、结论速览
    L += ["## 🎯 二、结论速览", ""]
    has_opp = opp and not (len(opp) == 1 and opp[0].get("empty"))
    has_risk = risk and not (len(risk) == 1 and risk[0].get("empty"))

    if has_opp:
        items = [it for it in opp if it.get("stars")]
        L += ["**🌙 有机会 · 缠论买点**", ""]
        if items:
            rows = [[it["stars"], it["name"], it["level"], it["tag"],
                     it["price"], it["note"], it["age"] + " 根"] for it in items]
            L += md_table(["强度", "指数", "级别", "买点", "价位", "状态", "出现"],
                          rows, ["left", "left", "left", "left", "right", "left", "right"])
        else:
            L += ["- " + (opp[0].get("empty") or opp[0].get("raw", ""))]
        L += [""]
    else:
        L += ["**🌙 有机会**：今日 5 指数 × 2 级别均无新鲜买点", ""]

    if has_risk:
        items = [it for it in risk if it.get("stars")]
        L += ["**⭐ 有风险 · 缠论卖点**", ""]
        if items:
            rows = [[it["stars"], it["name"], it["level"], it["tag"],
                     it["price"], it["note"], it["age"] + " 根"] for it in items]
            L += md_table(["强度", "指数", "级别", "卖点", "价位", "状态", "出现"],
                          rows, ["left", "left", "left", "left", "right", "left", "right"])
        else:
            L += ["- " + (risk[0].get("empty") or risk[0].get("raw", ""))]
        L += [""]
    else:
        L += ["**⭐ 有风险**：现价附近无新鲜卖点", ""]

    # ═══ 三、买卖点图（每个级别合成一张）
    L += ["---", "", "## 🖼 三、买卖点图（每个级别合成一张）", ""]
    shown = 0
    for lv, lv_txt in (("h1", "小时线60m"), ("day", "日线")):
        if lv in charts:
            L += ["### %s" % lv_txt, "",
                  "![%s](data:image/png;base64,%s)" % (lv_txt, b64(charts[lv])), ""]
            shown += 1
    if not shown:
        L += ["- 两个级别均无买卖点。", ""]
    L += ["> 小时线60m 与日线**各 1 张合图**；该级别有买卖点就只拼有信号的指数，"
          "没有买卖点则拼全部指数当走势参考。", ""]

    # ═══ 四、逐指数明细（统一五段式：走势 / 图 / 价位 / 缠论 / SMC）
    L += ["---", "", "## 📐 四、逐指数明细", ""]
    for idx in indices:
        L += ["### %s　收 **%s**" % (idx["name"], idx["close"]), ""]

        # ① 40日走势
        if idx["spark"]:
            L += ["**① 日线近40日走势**", "", "```",
                  norm_spark(idx["spark"]), "```", ""]

        # ② K线图：已按级别合成放在第三节，这里不重复嵌（避免同一张图出现两次）

        # ③ 关键价位（等宽对齐，统一列宽）
        if idx["ladder"]:
            L += ["**③ 关键价位**（🌙 真买点　⭐ 真卖点　┄ 参考线　● 现价）", ""]
            L += ["```"] + [l.rstrip() for l in idx["ladder"]] + ["```", ""]

        # ④ 缠论结论（表格，两级别并排）
        crows = []
        for c in idx["chan"]:
            pc = parse_chan_line(c)
            if pc:
                crows.append([pc["level"], pc["stage"], pc["zs"], pc["points"], pc["div"]])
        if crows:
            L += ["**④ 缠论·分型版结论**", ""]
            L += md_table(["级别", "阶段", "中枢", "买卖点", "背驰"], crows,
                          ["left", "left", "right", "left", "left"])
            L += [""]

        # ⑤ SMC（表格）
        if idx["smc"]:
            srows = []
            for sm in idx["smc"]:
                lv = "日线" if sm["title"].startswith("SMC日") else "周线"
                struct = sm["title"].split("：", 1)[-1].strip()
                bos = next((l.replace("破位：", "").strip() for l in sm["lines"]
                            if l.startswith("破位：")), "-")
                opps = next((l.replace("机会：", "").strip() for l in sm["lines"]
                             if l.startswith("机会：")), "-")
                rks = next((l.replace("风险：", "").strip() for l in sm["lines"]
                            if l.startswith("风险：")), "-")
                srows.append([lv, struct, bos, opps, rks])
            if srows:
                L += ["**⑤ SMC 结构**", ""]
                L += md_table(["级别", "结构", "破位", "机会", "风险"], srows,
                              ["left", "left", "left", "left", "left"])
                L += [""]

    # ═══ 尾注
    L += ["---", "",
          "> **口径**：级别 = 小时线60m + 日线　｜　版本 = 缠论·分型版　｜　"
          "🌙 = 买点（月亮越多越强）　⭐ = 卖点（星星越多越重）",
          ">",
          "> 本报告由纯脚本生成，0 token，不经过任何大模型。", ""]
    return "\n".join(L)


# ───────────────────────── 生成 HTML / PDF ─────────────────────────
HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>%(title)s</title>
<style>
@page { size: A4; margin: 14mm 12mm; }
body { font-family: "Noto Sans CJK SC","WenQuanYi Micro Hei",sans-serif;
       font-size: 11.5pt; line-height: 1.65; color: #222; max-width: 100%%; }
h1 { font-size: 21pt; border-bottom: 3px solid #1f4e79; padding-bottom: 8px; color: #1f4e79; }
h2 { font-size: 15pt; margin-top: 22px; border-left: 5px solid #1f4e79;
     padding-left: 10px; background: #f2f6fa; padding: 7px 10px; }
h3 { font-size: 12.5pt; margin-top: 18px; color: #1f4e79;
     border-bottom: 1px dashed #bbb; padding-bottom: 4px; }
table { border-collapse: collapse; width: 100%%; margin: 10px 0; font-size: 10.5pt; }
th { background: #1f4e79; color: #fff; padding: 7px 9px; text-align: left; }
td { border: 1px solid #ddd; padding: 6px 9px; }
tr:nth-child(even) td { background: #fafafa; }
blockquote { margin: 10px 0; padding: 8px 14px; background: #fffbe6;
             border-left: 4px solid #e8b400; color: #665500; font-size: 10.5pt; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 4px;
      padding: 10px; font-family: "Noto Sans Mono CJK SC",monospace;
      font-size: 9.8pt; line-height: 1.4; overflow-x: auto; white-space: pre; }
img { max-width: 100%%; border: 1px solid #ddd; border-radius: 4px;
      margin: 10px 0; display: block; }
ul { margin: 6px 0 6px 4px; } li { margin: 3px 0; }
hr { border: none; border-top: 1px solid #ddd; margin: 18px 0; }
.foot { color: #888; font-size: 9.5pt; }
</style></head><body>
%(body)s
</body></html>"""


def md_to_html_body(md, charts):
    """轻量 md → html（只覆盖本报告用到的语法，避免引入依赖）"""
    import html as H
    out = []
    lines = md.split("\n")
    i = 0
    in_code = False
    in_table = False

    def inline(t):
        t = H.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        return t

    while i < len(lines):
        ln = lines[i]
        # 图片（base64 行很长，单独处理）
        m = re.match(r"!\[([^\]]*)\]\(data:image/png;base64,(.+)\)$", ln.strip())
        if m:
            out.append('<img alt="%s" src="data:image/png;base64,%s">'
                       % (H.escape(m.group(1)), m.group(2)))
            i += 1
            continue
        if ln.strip().startswith("```"):
            if not in_code:
                out.append("<pre>")
                in_code = True
            else:
                out.append("</pre>")
                in_code = False
            i += 1
            continue
        if in_code:
            out.append(H.escape(ln))
            i += 1
            continue
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("|") and not in_table:
            # 收集整张表
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            out.append("<table>")
            for k, r in enumerate(rows):
                cells = [c.strip() for c in r.strip("|").split("|")]
                if k == 1 and all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    continue
                tag = "th" if k == 0 else "td"
                align = ""
                if k == 0:
                    align = ""
                out.append("<tr>" + "".join("<%s>%s</%s>" % (tag, inline(c), tag)
                                            for c in cells) + "</tr>")
            out.append("</table>")
            continue
        if s.startswith("# "):
            out.append("<h1>%s</h1>" % inline(s[2:]))
        elif s.startswith("## "):
            out.append("<h2>%s</h2>" % inline(s[3:]))
        elif s.startswith("### "):
            out.append("<h3>%s</h3>" % inline(s[4:]))
        elif s == "---":
            out.append("<hr>")
        elif s.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>%s</blockquote>" % "<br>".join(inline(b) for b in buf))
            continue
        elif re.match(r"^[-*]\s+", s):
            out.append("<ul>")
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                raw = lines[i]
                indent = len(raw) - len(raw.lstrip())
                txt = re.sub(r"^\s*[-*]\s+", "", raw)
                out.append("<li%s>%s</li>" % (' style="margin-left:18px"' if indent >= 2 else "",
                                              inline(txt)))
                i += 1
            out.append("</ul>")
            continue
        else:
            out.append("<p>%s</p>" % inline(s))
        i += 1
    if in_code:
        out.append("</pre>")
    return "\n".join(out)


def make_pdf(md_path, html_path, pdf_path, md, charts, title):
    body = md_to_html_body(md, charts)
    html_doc = HTML_TPL % {"title": title, "body": body}
    open(html_path, "w", encoding="utf-8").write(html_doc)
    print("[html]", html_path)
    try:
        subprocess.run(["chromium", "--headless", "--disable-gpu", "--no-sandbox",
                        "--no-pdf-header-footer",
                        "--print-to-pdf=" + pdf_path,
                        "file://" + os.path.abspath(html_path)],
                       capture_output=True, text=True, timeout=300)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 10000:
            print("[pdf]", pdf_path, "%.1f MB" % (os.path.getsize(pdf_path) / 1e6))
            return True
        print("[WARN] pdf 生成失败")
    except Exception as e:
        print("[WARN] chromium 失败:", str(e)[:150])
    return False


# ───────────────────────── 主流程 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None)
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--no-chart", action="store_true")
    ap.add_argument("--qq-only", action="store_true")
    ap.add_argument("--feishu-only", action="store_true")
    a = ap.parse_args()

    src = a.src
    if not src:
        cands = sorted(glob.glob(os.path.join(WS, ".cache/market_tech_*.txt")),
                       key=os.path.getmtime, reverse=True)
        if not cands:
            print("找不到报告 txt")
            return 1
        src = cands[0]
    txt = open(src, encoding="utf-8").read()
    mtime = datetime.fromtimestamp(os.path.getmtime(src))
    d = mtime.strftime("%Y-%m-%d")
    d8 = mtime.strftime("%Y%m%d")
    print("[src]", src, flush=True)

    pcr_txt, tech_txt = split_report(txt)
    pcr = parse_pcr(pcr_txt.splitlines())
    opp = parse_leaderboard(tech_txt, "opp")
    risk = parse_leaderboard(tech_txt, "risk")
    indices = parse_indices(tech_txt)
    print("[parse] PCR %d 行 ｜ 机会 %d ｜ 风险 %d ｜ 指数 %d 个"
          % (len(pcr), len(opp), len(risk), len(indices)), flush=True)

    charts = {}
    if not a.no_chart:
        charts = build_charts(d8)
    else:
        for k, p in build_charts(d8).items():
            if os.path.exists(p):
                charts[k] = p

    md = make_md(d, pcr, opp, risk, indices, charts, os.path.basename(src))
    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, "技术拆解_%s.md" % d8)
    open(md_path, "w", encoding="utf-8").write(md)
    print("[md]", md_path, "%.1f MB" % (os.path.getsize(md_path) / 1e6))

    html_path = os.path.join(OUT_DIR, "技术拆解_%s.html" % d8)
    pdf_path = os.path.join(OUT_DIR, "技术拆解_%s.pdf" % d8)
    pdf_ok = False
    if a.pdf:
        pdf_ok = make_pdf(md_path, html_path, pdf_path, md, charts, "大盘技术拆解 %s" % d)
    else:
        # 即使不出 PDF 也生成 HTML，方便直接浏览器看
        body = md_to_html_body(md, charts)
        open(html_path, "w", encoding="utf-8").write(
            HTML_TPL % {"title": "大盘技术拆解 %s" % d, "body": body})
        print("[html]", html_path)

    if a.push:
        files = [md_path] + ([pdf_path] if pdf_ok else [])
        try:
            import push_image as PI
            for f in files:
                ext = os.path.splitext(f)[1]
                txt_msg = "🧭 大盘技术拆解 · %s · 缠论分型版 · 小时线60m+日线" % d
                ok = PI.send_feishu_file(f, txt_msg) if not a.qq_only else False
                ok2 = PI.send_qq_file(f, txt_msg) if not a.feishu_only else False
                print("[push]", os.path.basename(f), "fs=", ok, "qq=", ok2)
        except Exception as e:
            print("[WARN] 推送失败:", str(e)[:200])

    return 0


if __name__ == "__main__":
    sys.exit(main())
