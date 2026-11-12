#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中买点的数据获取层（多源，带限流保护）

2026-11-12 大佬定：「能不能不用东财？新浪同花顺不都可以吗，东财一直封」

各源能力对比（实测 2026-11-12）：

| 数据 | 同花顺 | 东财 | 结论 |
|---|---|---|---|
| 行业板块列表(90)   | ✅ thshy 页面 | ✅ clist | 两边都行 → **优同花顺** |
| 概念板块列表(294)  | ✅ gnSection   | ✅ clist | 两边都行 → **优同花顺** |
| 板块 K 线(全量1662根) | ✅ 按年接口    | ⚠️ 限流封 IP | **必须同花顺** |
| 个股 K 线          | ✅ hs_ 接口    | ⚠️ 限流封 IP | **必须同花顺** |
| 板块成分股         | ❌ 401 反爬    | ✅ clist     | 只能东财（低频） |

所以本模块的策略：
  · **所有 K 线走同花顺**（东财最容易封的就是这个，压测 100 次能封 20 分钟）
  · **成分股走东财**（每个板块只 1 次请求，100 个板块 = 100 次，可接受）
  · 全局限流：两次东财请求之间至少间隔 `DG_MIN_GAP` 秒；连续 429/000 达 `DG_BREAK` 次就熔断
"""
from __future__ import annotations

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, os.path.join(_here, "..", "engine"), os.path.join(_here, "..", "..")):
    _q = os.path.normpath(_p)
    if os.path.isdir(_q) and _q not in sys.path:
        sys.path.insert(0, _q)

try:
    from net_guard import install as _net_install
    _net_install()
except Exception:
    pass

try:
    import ths_hub as THS
except Exception as _e:      # pragma: no cover
    THS = None
    print("[datafeed] ths_hub 不可用: %s" % _e, file=sys.stderr)

# 东财（只用于成分股）
# ⭐ 2026-11-12 大佬定：「不要用东财了，获取其他的」
#    东财只作为「同花顺彻底拿不到时」的最后手段，默认关闭。
#    需要时：DF_EASTMONEY=1
USE_EASTMONEY = os.environ.get("DF_EASTMONEY", "0") not in ("0", "", "false", "no")
DG_MIN_GAP = float(os.environ.get("DG_MIN_GAP", "0.35"))   # 东财请求最小间隔（秒）
DG_BREAK = int(os.environ.get("DG_BREAK", "4"))            # 连续失败几次就熔断
_dg_last = [0.0]
_dg_fail = [0]
_dg_broken = [False]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _dg_throttle():
    """东财限流：保证最小间隔；熔断后直接抛错，不再撞墙"""
    if _dg_broken[0]:
        raise RuntimeError("东财已熔断（连续失败 %d 次），本次跳过" % _dg_fail[0])
    gap = time.time() - _dg_last[0]
    if gap < DG_MIN_GAP:
        time.sleep(DG_MIN_GAP - gap)
    _dg_last[0] = time.time()


def _dg_ok():
    _dg_fail[0] = 0


def _dg_bad():
    _dg_fail[0] += 1
    if _dg_fail[0] >= DG_BREAK and not _dg_broken[0]:
        _dg_broken[0] = True
        log("[datafeed] ⚠️ 东财连续失败 %d 次 → 熔断，后续只走同花顺" % _dg_fail[0])


# ══════════════════════ 板块列表 ══════════════════════
def board_list(kind="行业", eastmoney_fallback=None):
    """板块列表，优先同花顺；失败才退回东财

    返回 [{"code","name","src"}, ...]
      src = "ths"（同花顺 885xxx/881xxx）或 "dg"（东财 BKxxxx）
    """
    if THS is not None:
        try:
            bs = THS.board_list(kind)
            return [{"code": b["code"], "name": b["name"], "src": "ths"} for b in bs]
        except Exception as e:
            log("[datafeed] 同花顺%s列表失败 %s，退回东财" % (kind, str(e)[:60]))
    # 东财兜底默认关闭（2026-11-12 大佬：「不要用东财了」）
    # 需要临时启用：DF_EASTMONEY=1
    if eastmoney_fallback is None:
        eastmoney_fallback = os.environ.get("DF_EASTMONEY", "0") not in ("0", "", "false", "no")
    if not eastmoney_fallback:
        raise RuntimeError("板块列表全部来源失败（东财兜底已关闭）")
    # 东财兜底
    import wyckoff_today_fetch as F
    fs = "m:90 t:2" if kind == "行业" else "m:90 t:3"
    _dg_throttle()
    try:
        rows = F.board_list(fs)
        _dg_ok()
    except Exception:
        _dg_bad()
        raise
    return [{"code": r.get("f12"), "name": r.get("f14"), "src": "dg"} for r in rows
            if r.get("f12") and r.get("f14")]


# ══════════════════════ 板块 K 线 ══════════════════════
def board_kline(code, src="ths", years_back=6):
    """板块日线，统一返回 [{date,open,high,low,close,vol,amt}, ...]（升序）

    同花顺板块码是 885xxx/881xxx（6 位数字）；东财是 BKxxxx。
    """
    if src == "ths":
        if THS is None:
            raise RuntimeError("同花顺模块不可用")
        return THS.board_kline(code, years_back=years_back)
    # 东财兜底（仅当同花顺拿不到，且显式开启）
    if not USE_EASTMONEY:
        raise RuntimeError("板块 K 线取不到（东财兜底已关闭）")
    import wyckoff_today_fetch as F
    _dg_throttle()
    try:
        ks = F.kline(code, lmt=900)
        _dg_ok()
        return ks
    except Exception:
        _dg_bad()
        raise


# ══════════════════════ 个股 K 线 ══════════════════════
def stock_kline(code, years_back=6):
    """个股日线，同花顺优先（东财 K 线最容易被封）"""
    code6 = code.split(".")[0]
    if THS is not None:
        try:
            return THS.stock_kline(code6, years_back=years_back)
        except Exception as e:
            log("[datafeed] 同花顺个股 %s 失败 %s，退东财" % (code6, str(e)[:50]))
    if not USE_EASTMONEY:
        raise RuntimeError("个股 K 线取不到（东财兜底已关闭）")
    import wyckoff_today_fetch as F
    _dg_throttle()
    try:
        ks = F.kline(code6, lmt=900)
        _dg_ok()
        return ks
    except Exception:
        _dg_bad()
        raise


# ══════════════════════ 板块码映射（同花顺 → 东财 BK）══════════════════════
_DG_BOARD_CACHE = {}


def ths_to_dg_board(kind="行业"):
    """建立「板块名 → 东财 BK 码」映射（同花顺给 881xxx/885xxx，东财成分股接口要 BKxxxx）

    ⚠️ 只在下钻个股时用，且一次性建好缓存；同花顺成分股 401 反爬，只能用东财。
    """
    key = "map:%s" % kind
    if key in _DG_BOARD_CACHE:
        return _DG_BOARD_CACHE[key]
    import wyckoff_today_fetch as F
    fs = "m:90 t:2" if kind == "行业" else "m:90 t:3"
    _dg_throttle()
    try:
        rows = F.board_list(fs)
        _dg_ok()
    except Exception:
        _dg_bad()
        raise
    m = {}
    for r in rows:
        nm, code = r.get("f14"), r.get("f12")
        if nm and code:
            m[nm] = code
    _DG_BOARD_CACHE[key] = m
    log("[datafeed] 板块名→东财码 %d 条" % len(m))
    return m


# ══════════════════════ 板块成分股（只能东财）══════════════════════
def board_members(board_code, src="dg", cap=60, name=None, kind="行业"):
    """板块成分股（按成交额降序，流动性优先）

    ⚠️ 同花顺成分股有反爬（401），只能用东财；因此这里加了限流 + 熔断。
    东财板块码是 BKxxxx（不是 885xxx），调用前必须用 board_list 拿到的 dg 码。
    """
    # ⭐ 同花顺优先：行业板块详情页能直接解析成分股（20 只左右，够下钻用）
    if src == "ths" and THS is not None:
        try:
            mem = THS.board_members(board_code, kind=kind)
            if mem:
                return [{"code": x["code"], "name": x["name"], "pct": None,
                         "price": None, "amt": 0.0} for x in mem][:cap]
        except Exception as e:
            log("[datafeed] 同花顺成分股 %s 失败 %s" % (board_code, str(e)[:50]))

    if not USE_EASTMONEY:
        return []     # 东财关闭时，概念板块成分股拿不到（同花顺概念页是 ajax）

    # 同花顺 8xxxxx 码不能直接给东财用 → 按名称查 BK 码
    if src == "ths" or not str(board_code).upper().startswith("BK"):
        if name:
            try:
                m = ths_to_dg_board(kind)
                bk = m.get(name)
                if bk:
                    board_code = bk
                else:
                    log("[datafeed] 板块「%s」在东财找不到对应 BK 码，跳过成分股" % name)
                    return []
            except Exception as e:
                log("[datafeed] 板块名映射失败 %s" % str(e)[:50])
                return []
    import wyckoff_today_fetch as F
    _dg_throttle()
    try:
        d = F._get("/api/qt/clist/get",
                   dict(pn=1, pz=300, po=1, np=1, fltt=2, invt=2, fid="f6",
                        fs="b:%s" % board_code, fields="f2,f3,f6,f12,f14"),
                   tries=2, timeout=10)
        _dg_ok()
    except Exception:
        _dg_bad()
        raise
    out = []
    for r in (d.get("diff") or []):
        if not isinstance(r.get("f3"), (int, float)):
            continue
        out.append({"code": r["f12"], "name": r["f14"], "pct": float(r["f3"]),
                    "price": r.get("f2"), "amt": (r.get("f6") or 0) / 1e8})
    return out[:cap]


def status():
    return {"ths_available": THS is not None,
            "eastmoney_enabled": USE_EASTMONEY,
            "eastmoney_broken": _dg_broken[0],
            "eastmoney_fail_streak": _dg_fail[0]}


if __name__ == "__main__":
    import json
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    bs = board_list("行业")
    print("行业 %d 个（源=%s）" % (len(bs), bs[0]["src"] if bs else "?"))
    if bs:
        ks = board_kline(bs[0]["code"], src=bs[0]["src"])
        print("%s：%d 根，末根 %s" % (bs[0]["name"], len(ks), ks[-1]["date"]))
