#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同花顺板块/个股 K 线取数（东财的替代源，库内共享）

2026-11-12 大佬定：「能不能不用东财？新浪同花顺不都可以吗，东财一直封」

东财 push2his 的毛病：
  · IPv4 全废，只走 IPv6
  · 稍微多请求就限流封 IP（实测压测 100 次后被封 20 分钟以上）
  · 盘中扫 100 个板块 + 下钻个股，必然触发

同花顺的优势：
  · 板块列表：`https://q.10jqka.com.cn/thshy/` 140 个行业板块（含代码+名称）
  · 板块 K线：`https://d.10jqka.com.cn/v6/line/48_<code>/01/last.js`
             返回全部历史日线（实测半导体 11479 根），字段 `日期,开,高,低,收,量,额`
  · 概念板块：`https://q.10jqka.com.cn/gn/` 885xxx 系列
  · 个股 K线：`https://d.10jqka.com.cn/v6/line/hs_<code>/01/last.js`

用法：
    from ths_hub import board_list, board_kline, stock_kline
    boards = board_list("行业")          # [{"code":"881121","name":"半导体"}, ...]
    ks = board_kline("881121")           # [{"date","open","high","low","close","vol","amt"}, ...]
    ks = stock_kline("600519")           # 个股日线
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

# 网络出口策略（默认 IPv4；东财白名单走 v6）
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from net_guard import install as _net_install
    _net_install()
except Exception:
    pass

H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://q.10jqka.com.cn/",
}

# 板块列表缓存（一次会话内复用，避免重复抓页面）
_CACHE_BOARDS = {}
_CACHE_KLINE = {}
KLINE_TTL = float(os.environ.get("THS_KLINE_TTL", "600"))   # 10 分钟

TIMEOUT = float(os.environ.get("THS_TIMEOUT", "12"))
RETRIES = int(os.environ.get("THS_RETRIES", "3"))
SLEEP_BETWEEN = float(os.environ.get("THS_SLEEP", "0.12"))   # 请求间隔，避免被限流


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _get(url, **kw):
    """带重试的 GET（同花顺比东财温和，但也要有礼貌）"""
    last = None
    for i in range(RETRIES):
        try:
            r = requests.get(url, headers=H, timeout=TIMEOUT, **kw)
            if r.status_code == 200 and r.text:
                return r.text
            last = "HTTP %s" % r.status_code
        except Exception as e:
            last = "%s %s" % (type(e).__name__, str(e)[:60])
        time.sleep(1.0 * (i + 1))
    raise RuntimeError("fetch fail %s: %s" % (url.split("?")[0], last))


# ────────────────────────── 板块列表 ──────────────────────────
def board_list(kind="行业", force=False):
    """板块列表

    kind: "行业" / "概念"
    返回 [{"code": "881121", "name": "半导体"}, ...]
    """
    if kind in _CACHE_BOARDS and not force:
        return _CACHE_BOARDS[kind]

    if kind == "行业":
        # 行业页：/thshy/detail/code/881121/半导体
        txt = _get("https://q.10jqka.com.cn/thshy/")
        rows = re.findall(r'/thshy/detail/code/(\d{6})/[^>]*>\s*([^\s<]{2,14})\s*<', txt)
    elif kind == "概念":
        # ⚠️ 概念页表格里的链接码是「行业分类码」(30xxxx)，**不能用来取 K 线**；
        #    真正的板块码(885xxx)藏在隐藏域 gnSection 里：
        #      <input type="hidden" id="gnSection" value='{"2":{"platecode":"885333","platename":"移动支付",...}}'>
        txt = _get("https://q.10jqka.com.cn/gn/")
        rows = []
        for m in re.finditer(r'id="gnSection"\s+value=\s*\'([^\']+)\'', txt):
            try:
                sec = json.loads(m.group(1))
            except Exception:
                continue
            for _, v in sec.items():
                pc, pn = v.get("platecode"), v.get("platename")
                if pc and pn:
                    rows.append((pc, pn))
        if not rows:
            # 兜底：整页找 platecode/platename 对
            rows = re.findall(r'"platecode":"(\d{6})","platename":"([^"]{2,20})"', txt)
    else:
        raise ValueError("kind 只认 行业 / 概念")

    out, seen = [], set()
    for code, name in rows:
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name.strip()})
    if not out:
        raise RuntimeError("同花顺板块列表解析失败：%s" % kind)
    _CACHE_BOARDS[kind] = out
    log("[ths] %s板块 %d 个" % (kind, len(out)))
    return out


# ────────────────────────── K 线 ──────────────────────────
def _parse_klines(payload):
    """同花顺 last.js → [{date,open,high,low,close,vol,amt}, ...]

    data 格式（分号分隔，每行逗号分隔）：
        20260417,开,高,低,收,量,额,,,,0
    """
    m = re.search(r"\((\{.*\})\)\s*;?\s*$", payload.strip(), re.S)
    if not m:
        raise RuntimeError("last.js 格式无法解析")
    j = json.loads(m.group(1))
    raw = j.get("data") or ""
    out = []
    for line in raw.split(";"):
        a = line.split(",")
        if len(a) < 6:
            continue
        d = a[0].strip()
        if len(d) != 8 or not d.isdigit():
            continue
        try:
            out.append({
                "date": "%s-%s-%s" % (d[:4], d[4:6], d[6:8]),
                "open": float(a[1]),
                "high": float(a[2]),
                "low": float(a[3]),
                "close": float(a[4]),
                "vol": float(a[5]) if a[5] else 0.0,
                "amt": float(a[6]) if len(a) > 6 and a[6] else 0.0,
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["date"])
    return out


def _fetch_klines(prefix, code, years_back=6, force=False):
    """按年接口拼全量日线

    为什么不用 last.js：只给 140 根（约半年），周线分析要 ~180 周 = 900 根日线。
    为什么不用 all.js：它是压缩格式（dates 只有月日 / price 是分隔编码），解析易错。

    按年接口 <PREFIX><code>/01/<YYYY>.js 是标准 CSV 格式，直接拼：
        data = "20260102,开,高,低,收,量,额,,,,0;20260103,..."
    """
    import datetime
    this_year = datetime.date.today().year
    all_ks, seen = [], set()
    for y in range(this_year, this_year - years_back - 1, -1):
        url = ("https://d.10jqka.com.cn/v6/line/%s_%s/01/%d.js"
               % (prefix, code, y))
        try:
            txt = _get(url)
            ks = _parse_klines(txt)
        except Exception as e:
            log("[ths] %s %s %d年失败 %s" % (prefix, code, y, str(e)[:50]))
            continue
        for k in ks:
            if k["date"] in seen:
                continue
            seen.add(k["date"])
            all_ks.append(k)
        time.sleep(SLEEP_BETWEEN)
        # 拿到今年（最新年）就够判「有没有新数据」；继续往前的年份补历史
    all_ks.sort(key=lambda x: x["date"])
    if not all_ks:
        raise RuntimeError("同花顺 %s_%s 取不到任何 K 线" % (prefix, code))
    return all_ks


def board_kline(code, force=False, years_back=6):
    """板块指数日线（同花顺 48_<code>）"""
    key = "bk:%s" % code
    c = _CACHE_KLINE.get(key)
    if c and not force and (time.time() - c[0]) < KLINE_TTL:
        return c[1]
    ks = _fetch_klines("48", code, years_back=years_back, force=force)
    _CACHE_KLINE[key] = (time.time(), ks)
    return ks


def stock_kline(code, force=False, years_back=6):
    """个股日线（同花顺 hs_<code>）

    code: 6 位股票代码（600519 / 000001 / 300750）
    """
    key = "st:%s" % code
    c = _CACHE_KLINE.get(key)
    if c and not force and (time.time() - c[0]) < KLINE_TTL:
        return c[1]
    ks = _fetch_klines("hs", code, years_back=years_back, force=force)
    _CACHE_KLINE[key] = (time.time(), ks)
    return ks


# ────────────────────────── 板块成分股 ──────────────────────────
def board_members(code, kind="行业", force=False):
    """板块成分股（同花顺板块详情页，无需东财）

    ⚠️ 只有**行业板块**能拿到（实测白酒 19 只全部解析成功）；
       概念板块详情页成分是 ajax 动态加载，暂不支持 → 返回空，由调用方决定是否退东财。
    """
    key = "mem:%s" % code
    c = _CACHE_KLINE.get(key)
    if c and not force and (time.time() - c[0]) < KLINE_TTL:
        return c[1]
    if kind != "行业":
        return []
    url = "https://q.10jqka.com.cn/thshy/detail/code/%s/" % code
    txt = _get(url)
    pairs = re.findall(
        r'stockpage\.10jqka\.com\.cn/(\d{6})/?"[^>]*>([^<]{2,12})</a>', txt)
    seen = {}
    for cd, nm in pairs:
        nm = nm.strip()
        if not nm or nm.isdigit():
            continue
        seen[cd] = nm
    out = [{"code": k, "name": v} for k, v in seen.items()]
    _CACHE_KLINE[key] = (time.time(), out)
    time.sleep(SLEEP_BETWEEN)
    log("[ths] 板块 %s 成分股 %d 只" % (code, len(out)))
    return out


def board_code_by_name(name, kind="行业"):
    """板块名 → 同花顺板块代码（885xxx/881xxx）

    用于：申万行业名（如「半导体」「白酒」）转同花顺板块码取 K 线。
    ⚠️ 名称可能有细微差异（申万叫「白酒Ⅱ」，同花顺叫「白酒」）→ 做包含匹配兜底。
    """
    if not name:
        return None
    name = name.strip()
    try:
        bs = board_list(kind)
    except Exception:
        return None
    # 1) 精确
    for b in bs:
        if b["name"] == name:
            return b["code"]
    # 2) 去掉罗马数字后缀再比
    import re as _re
    base = _re.sub(r"[ⅠⅡⅢⅣⅤIV]+$", "", name).strip()
    for b in bs:
        if b["name"] == base:
            return b["code"]
    # 3) 互相包含（取最短的，避免「半导体」命中「半导体设备」）
    cands = [b for b in bs if base and (base in b["name"] or b["name"] in base)]
    if cands:
        cands.sort(key=lambda x: len(x["name"]))
        return cands[0]["code"]
    return None


# ────────────────────────── 自检 ──────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="同花顺取数自检")
    ap.add_argument("--kind", default="行业", choices=["行业", "概念"])
    ap.add_argument("--code", default="")
    ap.add_argument("--stock", default="")
    a = ap.parse_args()

    if a.stock:
        ks = stock_kline(a.stock)
        print("个股 %s：%d 根，%s ~ %s" % (a.stock, len(ks), ks[0]["date"], ks[-1]["date"]))
        print("  最后:", ks[-1])
    elif a.code:
        ks = board_kline(a.code)
        print("板块 %s：%d 根，%s ~ %s" % (a.code, len(ks), ks[0]["date"], ks[-1]["date"]))
        print("  最后:", ks[-1])
    else:
        bs = board_list(a.kind)
        print("%s板块 %d 个，前 10：" % (a.kind, len(bs)))
        for b in bs[:10]:
            print("  %s %s" % (b["code"], b["name"]))
        if bs:
            ks = board_kline(bs[0]["code"])
            print("\n首个板块 %s(%s)：%d 根，末根 %s"
                  % (bs[0]["name"], bs[0]["code"], len(ks), ks[-1]["date"]))
