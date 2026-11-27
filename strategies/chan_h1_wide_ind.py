#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📐 宽基 + 实盘行业 · 小时线监控（五大宽基中枢 / 实盘行业三买三卖）
=====================================================================
大佬 2026-10-29 21:24 口径（原话）：
  「加那个五大宽基的小时级监控……五大宽基是原来还是突破中枢和跌破中枢对不对？
   然后我的实盘任务是出现三买或者三卖提醒。日线级的和小时级的都提醒。发到飞书的私聊」
  「我那两个实盘里边那个行业，你要去重啊」

监控范围与口径（写死，别改）
  A. 五大宽基（沪深300/中证500/中证1000/创业板指/科创50）
       指标 = **中枢突破 / 跌破**（只这三态：中枢上方🌙 / 中枢下方⭐ / 中枢内→不打标）
       级别 = **小时线 60m + 日线**
       只在「位置刚变化」时推送（不心跳、不刷屏）
  B. 两个实盘（默尔根 / 亚丽）持仓股票所属行业
       ⚠️ **跨组合去重**：同一行业只出一行，标注属于哪个实盘（两个都有则合并写）
       指标 = **三买** 或 **一买＋MA10上穿MA20**（2026-10-29 22:15 大佬最终定稿：
              「只有三买，然后一买也可以。一买的话就是必须那个 MA10 上穿 MA20 均线才行」）
              —— **二买不认**
       级别 = **小时线 60m + 日线**
       只在「新出现买点」时推送（同一点不重复推）

数据源（2026-10-29 实测，申万 801xxx 分钟线四个源全拿不到，故行业改用同花顺一级行业）
  · 宽基 60m  → minute_hub（腾讯 mkline m60，L1）
  · 行业 60m  → 同花顺行业指数 `/v6/line/48_<code>/60/<year>.js`
             （akshare `stock_board_industry_name_ths()` 取名称→881xxx 映射，缓存
              `.cache/ths_ind_map.json`；同花顺 90 个一级行业）
  · 日线      → 宽基走 chan_smc_index.fetch_daily；行业走 weekly_zs.fetch_sw_daily（申万日线）
                且 60m 优先用同花顺自家日线（同源更一致），取不到再退申万

去重映射（申万二级/三级 → 同花顺一级）
  印制电路板 → 元件 ｜ 通信网络设备及器件 → 通信设备 ｜ 锂电池 → 电池
  电机Ⅱ → 电机 ｜ 游戏Ⅱ → 游戏 ｜ 其余同名直接对上
  → 默尔根 6 个 + 亚丽 12 个，去重后 **10 个行业**

推送：**飞书私聊**（<chat_id>，与机器人单聊）
     纯脚本 0 token；同一点不重复推（状态文件记忆）

用法：
  python3 strategies/chan_h1_wide_ind.py             # 检测 + 有变化就推
  python3 strategies/chan_h1_wide_ind.py --dry       # 只打印
  python3 strategies/chan_h1_wide_ind.py --test      # 强制推当前全量状态（调试）
  python3 strategies/chan_h1_wide_ind.py --baseline  # 只建基线不推（首次部署用）
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import json
import socket
import subprocess
import argparse
import datetime

# 容器 IPv6 不通 → 强制 IPv4（与全项目一致）
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

STATE_FILE = os.path.join(WS, "memory/chan_h1_wide_ind_state.json")
LOG_FILE = os.path.join(WS, ".cache/chan_h1_wide_ind.log")
THS_MAP_FILE = os.path.join(WS, ".cache/ths_ind_map.json")
CACHE_DIR = os.path.join(WS, ".cache/chan_h1")

FEISHU_CHAT = os.environ.get("CHAN_H1_CHAT") or os.environ.get("FEISHU_CHAT_DM") or os.environ.get("FEISHU_CHAT_ID", "")

UA_THS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
          "Referer": "https://q.10jqka.com.cn/"}

# ── 宽基 ──
WIDE = [("沪深300", "sh000300", "000300.SH"),
        ("中证500", "sh000905", "000905.SH"),
        ("中证1000", "sh000852", "000852.SH"),
        ("创业板指", "sz399006", "399006.SZ"),
        ("科创50", "sh000688", "000688.SH")]

# ── 申万(二级/三级) → 同花顺一级 去重映射 ──
SW2THS = {
    "印制电路板": "元件",
    "通信网络设备及器件": "通信设备",
    "锂电池": "电池",
    "电机Ⅱ": "电机",
    "电机": "电机",
    "游戏Ⅱ": "游戏",
    "游戏": "游戏",
    "元件": "元件", "通信设备": "通信设备", "医疗服务": "医疗服务",
    "半导体": "半导体", "电池": "电池", "化学制药": "化学制药",
    "生物制品": "生物制品", "互联网电商": "互联网电商",
    "本地生活服务Ⅱ": "旅游及酒店",   # 美团/锦江/首旅 系 → 同花顺一级「旅游及酒店」
}

ZPOS_TXT = {"in": "中枢内", "above": "中枢上方", "below": "中枢下方"}

# 三买新鲜度上限（多少根内算「新出现」）：日线 15 根≈3周，小时线 24 根≈4个交易日
TB_MAX_AGE = {"day": 15, "h1": 24}


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%F %T"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ═══════════════════ 数据层 ═══════════════════

def wide_60m(sh_code):
    """五大宽基 60 分钟线（腾讯 mkline，minute_hub L1）"""
    try:
        import minute_hub as mh
        df = mh.get_kline(sh_code, "60", n=420, kind="index",
                          cache=True, cache_ttl_min=10)
        if df is not None and len(df) > 60:
            return df.rename(columns={"day": "dt"})[["dt", "open", "high", "low", "close"]]
    except Exception as e:
        log("WARN wide_60m %s: %s" % (sh_code, str(e)[:90]))
    return None


def ths_map(refresh=False):
    """同花顺一级行业 名称→代码 映射（90 个）"""
    if not refresh and os.path.exists(THS_MAP_FILE):
        try:
            d = json.load(open(THS_MAP_FILE, encoding="utf-8"))
            if d:
                return d
        except Exception:
            pass
    try:
        from data_hub import ak as ak
        df = ak.stock_board_industry_name_ths()
        cols = list(df.columns)
        nc = "name" if "name" in cols else cols[0]
        cc = "code" if "code" in cols else cols[1]
        d = {str(r[nc]): str(r[cc]) for _, r in df.iterrows()}
        os.makedirs(os.path.dirname(THS_MAP_FILE), exist_ok=True)
        json.dump(d, open(THS_MAP_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        log("ths_map 刷新 %d 个行业" % len(d))
        return d
    except Exception as e:
        log("WARN ths_map: %s" % str(e)[:90])
        return {}


def _ths_minute(code, period="52"):
    """同花顺行业指数 K 线原始抓取（绕开 502：先试当年.js，再试去年.js，再试 last.js）

    ⚠️ 同花顺周期编码（2026-10-29 实测确认，非标准值，务必按此表用）：
        52 = 60 分钟线（每日 4 根，时间戳 10:30/11:30/14:00/15:00）★ 行业小时线用这个
        41/42 = 30 分钟线（每日 8 根）
        61/62 = 1 分钟线（每日 241 根）—— 千万别用，之前误把它当 60min，导致中枢宽度只有 0.1%
        01 = 日线 ｜ 11 = 周线 ｜ 91 = 季线
      前缀 48_ = 同花顺行业/概念指数
    """
    import re
    import time
    y = datetime.date.today().year
    # 顺序：当年.js（历史最深）→ 去年.js（跨年补）→ last.js（最新兜底）
    urls = ["https://d.10jqka.com.cn/v6/line/48_%s/%s/%d.js" % (code, period, y),
            "https://d.10jqka.com.cn/v6/line/48_%s/%s/%d.js" % (code, period, y - 1),
            "https://d.10jqka.com.cn/v6/line/48_%s/%s/last.js" % (code, period)]
    rows = []
    for url in urls:
        for _ in range(3):
            try:
                r = requests.get(url, headers=UA_THS, timeout=12)
                if r.status_code != 200 or len(r.text) < 200:
                    time.sleep(0.8)
                    continue
                m = re.search(r"\((.*)\)\s*$", r.text, re.S)
                if not m:
                    time.sleep(0.8)
                    continue
                d = json.loads(m.group(1))
                got = [x for x in (d.get("data") or "").split(";") if x]
                if got:
                    rows.extend(got)
                    break
            except Exception:
                time.sleep(0.8)
    if not rows:
        return None
    import pandas as pd
    recs = []
    for line in rows:
        p = line.split(",")
        if len(p) < 6:
            continue
        try:
            ts = p[0].strip()
            # 分钟线是 %Y%m%d%H%M（12位），日线是 %Y%m%d（8位），两套都吃
            fmt = "%Y%m%d%H%M" if len(ts) >= 12 else "%Y%m%d"
            t = pd.to_datetime(ts, format=fmt)
            o, h, l, c = float(p[1]), float(p[2]), float(p[3]), float(p[4])
        except Exception:
            continue
        recs.append({"dt": t, "open": o, "high": h, "low": l, "close": c})
    if not recs:
        return None
    df = pd.DataFrame(recs).drop_duplicates(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    return df


def ind_60m(ths_code):
    """行业 60 分钟线（同花顺 1 分钟线聚合而成）；磁盘缓存 30 分钟"""
    import time
    import pandas as pd
    os.makedirs(CACHE_DIR, exist_ok=True)
    fp = os.path.join(CACHE_DIR, "ths_%s_60m.csv" % ths_code)
    if os.path.exists(fp):
        try:
            if (time.time() - os.path.getmtime(fp)) / 60.0 <= 30:
                d = pd.read_csv(fp, parse_dates=["dt"])
                if len(d) > 60:
                    return d
        except Exception:
            pass
    d = _ths_minute(ths_code, "52")          # 52 = 同花顺真正的 60 分钟线
    if d is None or len(d) < 60:
        # 回退旧缓存
        if os.path.exists(fp):
            try:
                old = pd.read_csv(fp, parse_dates=["dt"])
                if len(old) > 60:
                    log("ind_60m %s 抓取失败 → 用旧缓存" % ths_code)
                    return old
            except Exception:
                pass
        return None
    try:
        d.to_csv(fp, index=False)
    except Exception:
        pass
    return d


def wide_daily(ts_code):
    try:
        import chan_smc_index as C
        return C.fetch_daily(ts_code)
    except Exception as e:
        log("WARN wide_daily %s: %s" % (ts_code, str(e)[:80]))
        return None


def ind_daily_ths(ths_code):
    """行业日线：优先同花顺自家（与 60m 同源），失败退申万"""
    d = _ths_minute(ths_code, "01")
    if d is not None and len(d) > 100:
        return d
    return None


# ═══════════════════ 缠论读取 ═══════════════════

def read_wide(bars):
    """宽基：只取中枢位置（zg/zd/zpos）+ 现价"""
    import chan_smc_index as C
    if bars is None or len(bars) < 30:
        return {"ok": False}
    r = C.chan_read(C.bars_of(bars), "hour" if False else "day")
    # 注：chan_read 第二参是 tag，仅用于标注；宽基按小时线喂 bars
    if not r.get("ok"):
        return {"ok": False}
    zs = r.get("zs")
    price = float(bars["close"].iloc[-1])
    if not zs:
        return {"ok": True, "zpos": None, "zg": None, "zd": None, "close": round(price, 2)}
    zg, zd = round(float(zs[1]), 2), round(float(zs[0]), 2)
    zpos = "above" if price > zg else ("below" if price < zd else "in")
    return {"ok": True, "zpos": zpos, "zg": zg, "zd": zd, "close": round(price, 2),
            "stage": r.get("stage")}


def third_buy_own(df):
    """自己实现的「三买」检测（入参：带 high/low/close 的 DataFrame）（不复用 chan_smc_index 的，因为那段是死代码 —— 2026-10-29 查实）

    ⚠️ 死代码原因：`chan_read` 里中枢是**从末尾倒着找**的：
         for i in range(len(segs)-3, -1, -1): ... zs_i = i; break
       → zs_i 永远 = len(segs)-3（或 -4），
       而三买窗口 `range(zs_i+3, len(segs)-1)` 恒为空区间 → 永不触发。
       实测：半导体/元件/电池/电机 4 个行业，窗口长度全 = 0。

    本函数口径（缠论标准三买）：
      遍历所有可能的中枢（连续 3 笔重叠区 ZG/ZD），对每个中枢向后看：
        ① 有一笔向上**离开**中枢（该笔 hi > ZG）
        ② 紧接一笔向下**回调不破 ZG**（该笔 lo > ZG）  → 三买成立，价位 = 该回调低点
      取**最新**成立的那个（bar 索引最大），并算 age（距现在多少根）
    返回 {"level": px, "age": n} 或 None
    """
    import chan_smc_index as C
    if df is None or len(df) < 40:
        return None
    mb = C.merge_bars(C.bars_of(df))
    if len(mb) < 12:
        return None
    bi = C.build_bi(mb)
    if len(bi) < 5:
        return None
    segs = C.segs_from_bi(bi)
    if len(segs) < 5:
        return None
    n_mb = len(mb)
    best = None
    # 遍历所有中枢起点 i（连续 3 笔）
    for i in range(0, len(segs) - 3):
        w = segs[i:i + 3]
        zg = min(x["hi"] for x in w)
        zd = max(x["lo"] for x in w)
        if zg <= zd:
            continue                      # 这三笔没有重叠 → 不构成中枢
        # 从中枢后第一笔开始往后找：向上离开 + 回调不破 ZG
        for k in range(i + 3, len(segs) - 1):
            up = segs[k]
            nxt = segs[k + 1]
            if not up["up"]:
                continue
            if up["hi"] <= zg:            # 没离开中枢，继续往后看
                continue
            if nxt["up"]:
                continue
            if nxt["lo"] > zg:            # 回调不破 ZG → 三买
                age = max(0, n_mb - nxt["i1"])
                cand = {"level": round(float(nxt["lo"]), 2), "age": age,
                        "zg": round(float(zg), 2), "zd": round(float(zd), 2)}
                if best is None or age < best["age"]:
                    best = cand
                break                     # 该中枢已判定，看下一个中枢
    return best


def ma_cross(bars, fast=10, slow=20, look=6):
    """MA(fast) 上穿 MA(slow) 检测：返回穿线发生在几根前（0=当根刚穿），没穿返回 None

    look = 只在最近 look 根内找，太久以前的金叉不算「新信号」
    """
    import pandas as pd
    if bars is None or len(bars) < slow + look + 2:
        return None
    c = pd.Series([float(x) for x in bars["close"].values])
    mf = c.rolling(fast).mean()
    ms = c.rolling(slow).mean()
    diff = (mf - ms).values
    n = len(diff)
    for k in range(1, min(look + 1, n)):
        i = n - k           # 当根往前第 k 根
        if i < 1:
            break
        if diff[i] > 0 and diff[i - 1] <= 0:
            return k - 1    # 0 = 当根刚上穿
    return None


def read_ind(bars, lv="day"):
    """行业买点（2026-10-29 22:15 大佬最终口径）：

      只认两种买点，**二买不要**：
        ① **三买** —— chan_read 的 buy_list 里有「三买」且未被跌破 1.5%
        ② **一买 + MA10 上穿 MA20** —— 光有一买不算，必须 MA10 同时/刚上穿 MA20
           （大佬原话：「一买的话就是必须那个 MA10 上穿 MA20 均线才行」）

    返回 out["sig"] = None | {"tag": "三买"|"一买", "level": px, "age": n, "cross": k}
      · 三买优先（更强）；三买不存在才看一买+金叉
    同时保留 zpos/zg/zd 供报告展示。
    """
    import chan_smc_index as C
    if bars is None or len(bars) < 30:
        return {"ok": False}
    r = C.chan_read(C.bars_of(bars), "day")
    if not r.get("ok"):
        return {"ok": False}
    price = float(bars["close"].iloc[-1])
    zs = r.get("zs")
    out = {"ok": True, "close": round(price, 2), "sig": None,
           "zg": None, "zd": None, "zpos": None, "stage": r.get("stage"),
           "ma_cross": None}
    if zs:
        zg, zd = round(float(zs[1]), 2), round(float(zs[0]), 2)
        out["zg"], out["zd"] = zg, zd
        out["zpos"] = "above" if price > zg else ("below" if price < zd else "in")

    # ── ① 三买（优先级最高；用自己实现的检测，chan_read 那段是死代码）──
    tb = third_buy_own(bars)   # bars 为 DataFrame
    if tb:
        # 三重有效性：① 新鲜度 ② 未跌破 1.5% ③ 现价仍在中枢 ZG 之上
        age_ok = (tb["age"] or 999) <= TB_MAX_AGE.get(lv, 15)
        not_broken = not (price < float(tb["level"]) * 0.985)
        above_zg = (tb.get("zg") is None) or (price > float(tb["zg"]))
        if age_ok and not_broken and above_zg:
            out["sig"] = {"tag": "三买", "level": tb["level"], "age": tb["age"],
                          "cross": None, "zg": tb.get("zg"), "zd": tb.get("zd")}
            return out

    # ── ② 一买 + MA10 上穿 MA20 ──
    # ✅ 2026-11-08 大佬确认：「这个可以保留，一买加金叉，可以」
    #    小时线 h1 与日线 day **都可以**用这条。
    #    注意区别：
    #      · 光秃秃的「一买」→ 小时线不认（噪音大，一买在中枢下方）
    #      · 「一买 + MA10 上穿 MA20」→ 两级别都认（多了均线金叉的确认，不是裸一买）
    yb = None
    for x in (r.get("buy_list") or []):
        if x.get("tag") != "一买":
            continue
        if yb is None or (x.get("age") or 999) < (yb.get("age") or 999):
            yb = x
    cross = ma_cross(bars, 10, 20, look=6)
    out["ma_cross"] = cross
    if yb and cross is not None:
        out["sig"] = {"tag": "一买", "level": round(float(yb["level"]), 2),
                      "age": yb.get("age"), "cross": cross}
    return out


# ═══════════════════ 实盘持仓刷新（程序化，不用手工） ═══════════════════

SNAP_PORTS = [("默尔根", os.path.join(WS, ".xueqiu/last_snapshot.json")),
              ("亚丽", os.path.join(WS, ".xueqiu/portfolios/yali/last_snapshot.json"))]


def _snap_is_today(fp):
    """快照是否今天抓的（按 mtime + 内容 ts 双重判断）"""
    if not os.path.exists(fp):
        return False
    try:
        mt = datetime.date.fromtimestamp(os.path.getmtime(fp))
        if mt != datetime.date.today():
            return False
        d = json.load(open(fp, encoding="utf-8"))
        ts = d.get("fetched_at") or d.get("ts") or d.get("updated_at")
        if ts:
            try:
                dt = datetime.date.fromtimestamp(int(ts))
                return dt == datetime.date.today()
            except Exception:
                pass
        return True
    except Exception:
        return False


def refresh_snapshots(force=False):
    """程序化抓取两个实盘最新持仓（雪球真实组合）。

    当天已抓过就跳过（避免每小时重复开浏览器）；--refresh 强制重抓。
    抓取失败不影响主流程：退回最近快照，并在推送里标注数据时间。
    """
    todo = [(lab, fp) for lab, fp in SNAP_PORTS if force or not _snap_is_today(fp)]
    if not todo:
        log("持仓快照已是当日（%s），跳过抓取" % "、".join(l for l, _ in SNAP_PORTS))
        return {}
    env = dict(os.environ)
    env["PATH"] = "/usr/local/bin:/usr/local/sbin:/usr/bin:/sbin:/bin:" + env.get("PATH", "")
    res = {}
    for lab, fp in todo:
        key = "moergen" if "yali" not in fp else "yali"
        try:
            r = subprocess.run(
                ["bash", os.environ.get("OPENCLAW_WORKSPACE","/root/.openclaw/workspace")+"/optstack/jobs/xq_daily.sh", "check", "--no-push"],
                capture_output=True, text=True, timeout=900, cwd=WS,
                env={**env, "XQ_PORTFOLIOS": key, "XQ_PUSH": ""})
            out = (r.stdout or "") + (r.stderr or "")
            ok = _snap_is_today(fp)
            res[lab] = ok
            log("刷新持仓 %s(%s)：%s %s" % (lab, key, "OK" if ok else "未更新",
                                          out.strip().splitlines()[-1][:90] if out.strip() else ""))
        except Exception as e:
            res[lab] = False
            log("刷新持仓 %s 失败：%s" % (lab, str(e)[:90]))
    return res


def snap_freshness():
    """返回 {label: 'YYYY-MM-DD HH:MM'}，用于推送里标注数据时间"""
    out = {}
    for lab, fp in SNAP_PORTS:
        try:
            out[lab] = datetime.datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%m-%d %H:%M")
        except Exception:
            out[lab] = "无"
    return out


# ═══════════════════ 实盘行业（去重） ═══════════════════

def portfolio_industries():
    """两个实盘持仓行业 → 去重后的 {ths_name: {pf: [label...], sw: [原始申万名], weight}}"""
    import weekly_zs as WZS
    import xq_portfolio as PF
    merged = {}
    for lab, f in WZS.PORTS:
        try:
            inds = WZS.holdings_industries(f)
        except Exception as e:
            log("WARN holdings_industries %s: %s" % (lab, str(e)[:80]))
            continue
        for sw_name, v in inds.items():
            ths = SW2THS.get(sw_name)
            if not ths:
                log("SKIP 行业未映射到同花顺: %s (%s)" % (sw_name, lab))
                continue
            o = merged.setdefault(ths, {"pf": [], "sw": set(), "weight": 0.0})
            if lab not in o["pf"]:
                o["pf"].append(lab)
            o["sw"].add(sw_name)
            o["weight"] += float(v.get("w") or 0)
    return merged


# ═══════════════════ 状态 ═══════════════════

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ═══════════════════ 扫描 ═══════════════════

def scan():
    """返回 (wide_items, ind_items)"""
    wide_items = []
    for name, sh, tsc in WIDE:
        for lv, lv_name in (("h1", "小时线"), ("day", "日线")):
            df = wide_60m(sh) if lv == "h1" else wide_daily(tsc)
            r = read_wide(df)
            wide_items.append({"name": name, "lv": lv, "lv_name": lv_name, **r})
    ind_items = []
    tmap = ths_map()
    inds = portfolio_industries()
    log("实盘行业去重后 %d 个: %s" % (len(inds), "、".join(sorted(inds))))
    for ths_name in sorted(inds):
        meta = inds[ths_name]
        code = tmap.get(ths_name)
        if not code:
            log("SKIP 同花顺无此行业: %s" % ths_name)
            for lv, lv_name in (("h1", "小时线"), ("day", "日线")):
                ind_items.append({"name": ths_name, "lv": lv, "lv_name": lv_name,
                                  "ok": False, "pf": meta["pf"], "weight": meta["weight"],
                                  "sw": sorted(meta["sw"])})
            continue
        for lv, lv_name in (("h1", "小时线"), ("day", "日线")):
            df = ind_60m(code) if lv == "h1" else ind_daily_ths(code)
            r = read_ind(df, lv)
            ind_items.append({"name": ths_name, "lv": lv, "lv_name": lv_name,
                              "pf": meta["pf"], "weight": round(meta["weight"], 2),
                              "sw": sorted(meta["sw"]), "code": code, **r})
    return wide_items, ind_items


# ═══════════════════ 信号判定 ═══════════════════

def wide_sig(item):
    """宽基签名：中枢位置（in 不打标）"""
    if not item.get("ok") or not item.get("zpos"):
        return None
    return item["zpos"]


def ind_sig(item):
    """行业签名：只认 三买 / 一买+MA10上穿MA20（2026-10-29 22:15 大佬定稿）

    三买 → "三买@<价>" ｜ 一买+金叉 → "一买@<价>" ｜ 都没有 → None（不推）
    """
    if not item.get("ok"):
        return None
    sg = item.get("sig")
    if not sg:
        return None
    return "%s@%.2f" % (sg["tag"], sg["level"])


def mark_of(sig, kind):
    if not sig:
        return ""
    if kind == "wide":
        return {"above": "🌙", "below": "⭐", "in": ""}.get(sig, "")
    return "🌙" if sig.startswith("三买") else ("⭐" if sig.startswith("三卖") else "")


# ═══════════════════ 渲染 + 推送 ═══════════════════

def build_text(hits, cur_wide, cur_ind, force=False):
    """渲染推送正文。hits=本次新变化，cur_*=当前全量状态"""
    L = []
    if force:
        L.append("**（调试：全量当前状态）**")

    # ── 本次新变化（放最前，最重要）──
    if hits and not force:
        L.append("**🔔 本次新变化**")
        for h in hits:
            who = ("（%s）" % "＋".join(h["pf"])) if h.get("pf") else ""
            if h["kind"] == "wide":
                newtxt = ("🌙 突破中枢" if h["new"] == "above" else "⭐ 跌破中枢")
                L.append("· 📐 宽基 %s%s %s：%s → **%s**" % (
                    h["name"], who, h["lv_name"],
                    ZPOS_TXT.get(h["old"] or "", "—"), newtxt))
            else:
                sg = h.get("sig") or {}
                if sg.get("tag") == "一买":
                    newtxt = "🌙 一买＋MA10上穿MA20 @%.2f" % sg.get("level", 0)
                else:
                    newtxt = "🌙 三买 @%.2f" % sg.get("level", 0)
                L.append("· 🏭 行业 %s%s %s：**%s**" % (
                    h["name"], who, h["lv_name"], newtxt))
        L.append("")

    # ── 一、五大宽基（中枢突破/跌破）──
    L.append("**━━ 一、五大宽基 · 中枢突破/跌破 ━━**")
    for it in cur_wide:
        if not it.get("ok"):
            L.append("· %s %s：数据不足" % (it["name"], it["lv_name"]))
            continue
        zp = it.get("zpos")
        if zp == "above":
            zt = "🌙 中枢上方（突破）"
        elif zp == "below":
            zt = "⭐ 中枢下方（跌破）"
        elif zp == "in":
            zt = "中枢内"
        else:
            zt = "—"
        L.append("· %s %s：%s ｜ ZG %s / ZD %s" % (
            it["name"], it["lv_name"], zt, it.get("zg"), it.get("zd")))

    # ── 二、实盘行业（三买 / 一买+MA10上穿MA20，已去重）──
    L.append("")
    L.append("**━━ 二、实盘持仓行业 · 三买 / 一买+MA10上穿MA20（跨组合已去重） ━━**")
    n_hit = 0
    for it in cur_ind:
        who = "＋".join(it.get("pf") or [])
        if not it.get("ok"):
            L.append("· %s（%s）%s：数据不足" % (it["name"], who, it["lv_name"]))
            continue
        sg = it.get("sig")
        if sg:
            n_hit += 1
            if sg["tag"] == "三买":
                t = "🌙 **三买** @%.2f（%s根前）" % (sg["level"], sg.get("age"))
            else:
                cr = sg.get("cross")
                ct = "当根金叉" if cr == 0 else ("%s根前金叉" % cr if cr is not None else "")
                t = "🌙 **一买** @%.2f（%s根前）＋ MA10上穿MA20（%s）" % (
                    sg["level"], sg.get("age"), ct)
        else:
            zt = {"above": "中枢上方", "below": "中枢下方",
                  "in": "中枢内"}.get(it.get("zpos"), "—")
            t = "无（%s，无三买/一买金叉）" % zt
        L.append("· %s（%s）%s：%s" % (it["name"], who, it["lv_name"], t))
    L.append("")
    L.append("▸ 共 %d 项，命中买点 **%d** 项" % (len(cur_ind), n_hit))

    L.append("")
    L.append("口径：宽基=中枢突破🌙/跌破⭐ ｜ 行业=**三买** 或 **一买＋MA10上穿MA20**（二买不算）")
    L.append("行业已跨组合去重：%d 个（印制电路板→元件、通信网络设备及器件→通信设备、锂电池→电池、电机Ⅱ→电机、游戏Ⅱ→游戏、本地生活服务Ⅱ→旅游及酒店）"
             % len({i["name"] for i in cur_ind}))
    fr = snap_freshness()
    L.append("持仓快照：%s" % "，".join("%s %s" % (k, v) for k, v in fr.items()))
    return "\n".join(L)


def push_feishu(text, title):
    """飞书私聊（interactive card）"""
    try:                              # 统一通知（data_hub.notify）优先
        import _notify
        _r = _notify.send_card(title, text)
        if _r.get("ok"):
            return True
        print("[notify] 统一库未就绪，回落原通道：%s" % _r.get("reason"))
    except Exception as _exc:
        print("[notify] 统一库异常，回落原通道：%s" % _exc)
    import urllib.request
    try:
        cfg = json.load(open(os.path.join(os.path.dirname(WS), "openclaw.json"), encoding="utf-8"))
        c = cfg["channels"]["feishu"]
        b = json.dumps({"app_id": c["appId"], "app_secret": c["appSecret"]}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=b, headers={"Content-Type": "application/json"})
        tok = json.loads(urllib.request.urlopen(req, timeout=20).read())["tenant_access_token"]
        card = {"config": {"wide_screen_mode": True},
                "header": {"template": "blue",
                           "title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": "📎 来自 OpenClaw.Deepseek"},
                             {"tag": "markdown", "content": text}]}
        body = json.dumps({"receive_id": FEISHU_CHAT, "msg_type": "interactive",
                           "content": json.dumps(card)}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=body, headers={"Authorization": "Bearer " + tok,
                                "Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        log("feishu push: code=%s msg=%s" % (r.get("code"), r.get("msg")))
        return r.get("code") == 0
    except Exception as e:
        log("feishu push ERR: %s" % str(e)[:150])
        return False


# ═══════════════════ 主流程 ═══════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不推送")
    ap.add_argument("--test", action="store_true", help="强制推当前全量状态")
    ap.add_argument("--baseline", action="store_true", help="只建基线不推送")
    ap.add_argument("--refresh", action="store_true", help="强制重抓两个实盘持仓（默认当天抓过就跳过）")
    args = ap.parse_args()

    # ① 先程序化刷新两个实盘持仓（当天抓过则跳过，失败退回最近快照）
    refresh_snapshots(force=args.refresh)
    fresh = snap_freshness()
    log("持仓快照时间：%s" % "，".join("%s=%s" % (k, v) for k, v in fresh.items()))

    st = load_state()
    prev = dict(st)
    wide_items, ind_items = scan()

    hits = []
    cur = {}
    # 宽基：位置变化
    for it in wide_items:
        k = "wide|%s|%s" % (it["name"], it["lv"])
        s = wide_sig(it)
        cur[k] = s
        if s is None:
            continue
        old = prev.get(k)
        if old is not None and old != s and s in ("above", "below"):
            hits.append({"kind": "wide", "name": it["name"], "lv_name": it["lv_name"],
                         "old": old, "new": s, "pf": []})
    # 行业：新出现 三买 / 一买+MA10上穿MA20
    for it in ind_items:
        k = "ind|%s|%s" % (it["name"], it["lv"])
        s = ind_sig(it)
        cur[k] = s
        if s is None:
            continue
        if s != prev.get(k):
            hits.append({"kind": "ind", "name": it["name"], "lv_name": it["lv_name"],
                         "old": prev.get(k), "new": s, "pf": it.get("pf") or [],
                         "sig": it.get("sig")})

    log("扫描完成：宽基 %d 项 / 行业 %d 项 / 命中 %d 条" % (len(wide_items), len(ind_items), len(hits)))

    if args.baseline:
        save_state(cur)
        log("已建基线（不推送），共 %d 个状态键" % len(cur))
        return 0

    force = args.test
    if not hits and not force:
        log("无新信号，不推送")
        return 0

    text = build_text(hits, wide_items, ind_items, force=force)
    print("\n" + text + "\n")
    if args.dry:
        log("--dry：不推送")
        save_state(cur)
        return 0

    now = datetime.datetime.now().strftime("%m-%d %H:%M")
    title = "📐 宽基+实盘行业 · 小时线监控 · %s" % now
    ok = push_feishu(text, title)
    if ok:
        save_state(cur)
        log("推送成功，状态已更新")
    else:
        log("推送失败，状态不更新（下次重试）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
