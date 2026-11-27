#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「大盘技术拆解日报」的纯文本报告转成 Markdown（带图片）
=====================================================================
用途：日报正文（.cache/market_tech_YYYYMMDD_HHMM.txt）是给飞书卡片用的纯文本，
      看着乱。这个脚本把它转成排版好的 Markdown，图片用本地相对路径引用，
      方便直接看 / 直接分享。

用法：
  python3 strategies/tech_report_md.py                     # 用最新一份缓存报告
  python3 strategies/tech_report_md.py --src .cache/market_tech_20261023_2017.txt
  python3 strategies/tech_report_md.py --charts            # 顺便出图并嵌入
  python3 strategies/tech_report_md.py --md-only           # 只转文字，不动图

输出：memory/tech_reports/tech_YYYYMMDD.md
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, re, glob, argparse
from datetime import datetime

WS = os.environ.get("OPENCLAW_WORKSPACE") or (__import__("ocsecrets").workspace())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT_DIR = os.path.join(WS, "memory/tech_reports")
CHART_DIR = os.path.join(WS, ".cache/charts")


def parse_pcr(lines):
    """成交量 PCR 段 → markdown 表格"""
    rows = []
    for ln in lines:
        m = re.match(r"\s*(\S+)\s*[：:]\s*([\d.]+)\s*｜\s*近60日分位\s*([\d.]+)%（([^）]+)）\s*｜\s*较前日\s*([+\-][\d.]+)\s*(↑|↓)?", ln)
        if m:
            name, val, pct, lvl, chg, arrow = m.groups()
            rows.append((name, val, pct + "%", lvl, chg + (" " + arrow if arrow else "")))
    if not rows:
        return ""
    md = ["## 📊 一、成交量 PCR（分指数 · 5日均 · 剔除当月合约）", "",
          "| 指数 | PCR | 近60日分位 | 水平 | 较前日 |",
          "|---|---|---|---|---|"]
    for r in rows:
        md.append("| %s | %s | %s | %s | %s |" % r)
    md.append("")
    md.append("> 口径：近5个交易日认沽量之和 ÷ 认购量之和，剔除当月合约；数据 = 上/深交所 ETF 期权 + 中金所股指期权")
    md.append("")
    return "\n".join(md)


def _bullets(body):
    """把一段多行文本转成 markdown 列表
    ⚠️ 跳过「⚠️ 二、」「🎯 一、」这类小标题残片（正则容易把它们带进来）
    """
    out = []
    for ln in (body or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^[⚠️🎯📋📐🖼]+\s*[一二三四五六七八九十]+、", s):
            continue
        if s in ("⚠️ 二、", "🎯 一、"):
            continue
        # 去掉自带的序号，避免 markdown 里出现 "- 1."
        s = re.sub(r"^(\d+)\.\s*", "", s)
        out.append("- " + s)
    return out


def parse_tech(text):
    """技术拆解段 → markdown"""
    md = []
    # ── 口径说明
    md += ["## 📋 二、口径说明", ""]
    md += ["> **级别**：小时线60m（60分钟K线）+ 日线",
           ">",
           "> **版本**：缠论·分型版 = 包含处理 → 顶底分型 → 笔 → 中枢(ZG/ZD) → 背驰",
           ">",
           "> 全篇只用这一个版本，不含均线版 / 趋势浪子版。", ""]

    # ── 机会榜（标题行带【分型版·缠论结构】，不能只吃到行尾）
    m = re.search(r"有机会的指数[^\n]*\n(.*?)(?=有风险的指数)", text, re.S)
    md += ["## 🎯 三、有机会的指数（🌙 = 缠论买点，月亮越多越强）", ""]
    if m and m.group(1).strip():
        md += _bullets(m.group(1))
    else:
        md.append("- 无")
    md.append("")

    # ── 风险榜
    m = re.search(r"有风险的指数[^\n]*\n(.*?)(?=（本榜单口径|══════)", text, re.S)
    md += ["## ⚠️ 四、有风险的指数（⭐ = 缠论卖点，星星越多越重）", ""]
    if m and m.group(1).strip():
        md += _bullets(m.group(1))
    else:
        md.append("- 无")
    md.append("")

    # ── 榜单脚注
    m = re.search(r"（本榜单口径[^）]*）", text)
    if m:
        md += ["> " + m.group(0).strip("（）"), ""]

    # ── 各指数明细
    blocks = re.findall(r"══════ 【([^】]+)】收 ([\d.]+) ══════\n(.*?)(?=══════ 【|\Z)", text, re.S)
    md += ["## 📐 五、各指数明细", ""]
    for name, close, body in blocks:
        md += ["### %s · 收 %s" % (name, close), ""]
        in_fence = False
        for ln in body.splitlines():
            s = ln.strip()
            if not s:
                continue
            is_art = ("┤" in s) or ("┄" in s and "●" in s)
            if s.startswith("小时线示意图"):
                md += ["", "**" + s + "**", "", "```"]
                in_fence = True
                continue
            if in_fence and not is_art and not s.startswith("─"):
                md += ["```", ""]
                in_fence = False
            if is_art:
                md.append(ln.rstrip())      # ASCII 图保持原样（不 strip 左缩进）
                continue
            if s.startswith("日线近40日") or s.startswith("数据："):
                md.append("- " + s)
            elif s.startswith("── 缠论") or s.startswith("── SMC"):
                md += ["", "**" + s.replace("──", "").strip(" ─") + "**", ""]
            elif s.startswith("【分型版") or s.startswith("SMC") and "：" in s:
                md.append("- " + s)
            elif s.startswith("破位：") or s.startswith("机会：") or s.startswith("风险："):
                md.append("  - " + s)
            else:
                md.append("- " + s)
        if in_fence:
            md += ["```"]
        md.append("")
    return "\n".join(md)


def embed_charts(md_txt, d8=None):
    """把生成的图以 markdown 图片语法插进去"""
    if not os.path.isdir(CHART_DIR):
        return md_txt
    d8 = d8 or datetime.now().strftime("%Y%m%d")

    def pick(pattern, want_day=None):
        """优先用报告当天的图，没有再用最新一张"""
        cands = glob.glob(os.path.join(CHART_DIR, pattern))
        if not cands:
            return None
        if want_day:
            exact = [c for c in cands if want_day in os.path.basename(c)]
            if exact:
                return sorted(exact, key=os.path.getmtime, reverse=True)[0]
        return sorted(cands, key=os.path.getmtime, reverse=True)[0]

    pano_h1 = pick("panorama_h1_*.png", d8)
    pano_day = pick("panorama_day_*.png", d8)
    if not (pano_h1 or pano_day):
        return md_txt

    md = [md_txt, "", "---", "", "## 🖼 六、图形（程序生成 · 0 token）", ""]

    # 全景图
    for f, title in ((pano_h1, "五指数全景 · 小时线60m"),
                     (pano_day, "五指数全景 · 日线")):
        if f:
            md += ["### " + title, "",
                   "![%s](%s)" % (title, os.path.relpath(f, OUT_DIR)), ""]

    # 单指数图：**只取报告当天的**
    singles = sorted([f for f in glob.glob(os.path.join(CHART_DIR, "chan_*.png"))
                      if d8 in os.path.basename(f)], key=os.path.getmtime)
    if singles:
        md += ["### 单指数明细", ""]
        for f in singles:
            base = os.path.basename(f)
            lv = "小时线60m" if "_h1_" in base else "日线"
            nm = base.replace("chan_", "").split("_")[0]
            md += ["**%s · %s**" % (nm, lv), "",
                   "![%s](%s)" % (nm, os.path.relpath(f, OUT_DIR)), ""]
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="源 txt 报告；默认取最新一份")
    ap.add_argument("--charts", action="store_true", help="顺便检查/嵌入图片")
    ap.add_argument("--md-only", action="store_true", help="只转文字")
    a = ap.parse_args()

    src = a.src
    if not src:
        cands = sorted(glob.glob(os.path.join(WS, ".cache/market_tech_*.txt")),
                       key=os.path.getmtime, reverse=True)
        if not cands:
            print("找不到报告 txt，先跑 python3 bin/market_tech_daily.py --no-push")
            return 1
        src = cands[0]
    txt = open(src, encoding="utf-8").read()
    print("[src]", src)

    # 拆两段：报告里有两个分隔线，第一个之前是 PCR，之后全部是技术拆解
    sep = "─" * 30
    if sep in txt:
        pcr_txt, tech_txt = txt.split(sep, 1)
    else:
        pcr_txt, tech_txt = "", txt

    mtime = datetime.fromtimestamp(os.path.getmtime(src))
    d = mtime.strftime("%Y-%m-%d")

    head = ["# 🧭 大盘技术拆解 · %s" % d, "",
            "**级别**：小时线60m + 日线　｜　**版本**：缠论·分型版　｜　**生成时间**：%s"
            % mtime.strftime("%Y-%m-%d %H:%M"), "",
            "---", ""]
    md = "\n".join(head) + parse_pcr(pcr_txt.splitlines()) + "\n" + parse_tech(tech_txt)
    if not a.md_only:
        md = embed_charts(md, mtime.strftime("%Y%m%d"))

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "tech_%s.md" % mtime.strftime("%Y%m%d"))
    open(out, "w", encoding="utf-8").write(md)
    print("[OK]", out)
    print("[size] %d 字符" % len(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
