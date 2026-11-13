#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dh_bridge · 技术分析库 → data_hub 取数桥（**纯适配层，不含任何抓取逻辑**）
=====================================================================
大佬 2026-11-13：「techAnly 这个玩意你不要取数了，你就用 DataHub 来取数」。
本文件**只做三件事**：
  1. 惰性 import `optstack/data_hub`（失败静默，返回 None）
  2. 把 data_hub 的返回**字段名对齐**成技术库老口径（date→dt / vol→volume）
  3. 每个函数失败一律返回 None/[]，**绝不抛异常** → 调用方自然降级老链路

⚠️ 硬约束（别改坏）：
  · **不新建采集器、不注册 data_type、不改 data_hub 一个字节**
  · 算法/闸门/画图/输出格式一行不动，本文件只换取数来源
  · 每个调用点都保留原老链路做兜底（data_hub 挂了照常跑）

统一开关（与 tech_summary 一致）：`TECH_DATA_SOURCE=datahub|local`
  · datahub（默认）：优先 data_hub
  · local：强制走老链路（对比/排障用）

data_hub 现有口径备忘（2026-11-13 实测）：
  sector_kline     → DataFrame[date, open, high, low, close, vol, amt]（同花顺板块，1663 根）
  sector_members   → DataFrame[code, name]（**只有行业板块**，概念是 ajax 取不到）
  sector_list      → DataFrame[code, name, kind]（行业 90 / 概念 294）
  index_components → list[str] ts_code（中证官网 → tushare index_weight → web）
  stock_daily      → DataFrame[ts_code, trade_date, ..., vol, amount]（adjust 支持 qfq/hfq/""）
  index_daily      → DataFrame[trade_date, open, high, low, close, vol, ts_code]
  etf_daily        → DataFrame[trade_date, open, high, low, close, vol, amount]
  minute_bar       → kind="index"/"stock" 可用；kind="fut" 代码归一化有 bug（只 6 根）
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import threading

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

DATA_SOURCE = os.environ.get("TECH_DATA_SOURCE", "datahub").strip().lower()
if DATA_SOURCE not in ("datahub", "local"):
    DATA_SOURCE = "datahub"

_DH = None          # None=未尝试；False=不可用；模块=已加载
_QUIET_LOCK = threading.RLock()


def log(msg):
    print("[dh_bridge] %s" % msg, file=sys.stderr, flush=True)


def _pd():
    """惰性 import pandas（本模块在 import 期不依赖 pandas）"""
    import pandas as pd
    return pd


def _candidates():
    """含 data_hub 包的根目录候选（DATA_HUB_PATH > WS/optstack > 向上探测）"""
    out = []
    env = os.environ.get("DATA_HUB_PATH")
    if env:
        out.append(os.path.abspath(os.path.expanduser(env)))
    out.append(os.path.join(WS, "optstack"))
    out.append(WS)
    d = WS
    for _ in range(5):
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
        out.append(os.path.join(d, "optstack"))
        out.append(d)
    return out


def module():
    """惰性加载 data_hub；不可用返回 None（只打一行日志，绝不抛异常）"""
    global _DH
    if _DH is not None:
        return _DH or None
    if DATA_SOURCE != "datahub":
        _DH = False
        return None
    for base in _candidates():
        if not os.path.isdir(os.path.join(base, "data_hub")):
            continue
        try:
            if base not in sys.path:
                sys.path.insert(0, base)
            import importlib
            mod = importlib.import_module("data_hub")
            if not hasattr(mod, "get_data"):
                continue
            # data_hub 内部 logger 每条取数都打 INFO（扫 90 个板块 × 3 层 = 几百行），
            # 会淹掉技术库自己的日志 → 降到 WARNING。要看细节：TECH_DH_VERBOSE=1
            if os.environ.get("TECH_DH_VERBOSE", "0") not in ("1", "true", "yes"):
                try:
                    import logging as _lg
                    for _nm in ("data_hub", "data_hub.core", "data_hub.core.config"):
                        _lg.getLogger(_nm).setLevel(_lg.WARNING)
                    _lg.getLogger("data_hub").propagate = False
                except Exception:
                    pass
            # data_hub 导入时会把 getaddrinfo 改成「一刀切 IPv4」，
            # 技术库有 IPv6 白名单出口（东财行情族）→ 再幂等装一次 net_guard
            try:
                from net_guard import install as _ni
                _ni()
            except Exception:
                pass
            _DH = mod
            return mod
        except Exception as e:                                   # noqa: BLE001
            log("加载失败(%s): %s" % (base, str(e)[:90]))
    _DH = False
    log("不可用，降级老链路（DATA_HUB_PATH=%s）" % os.environ.get("DATA_HUB_PATH", "-"))
    return None


def enabled():
    return module() is not None


@contextlib.contextmanager
def _quiet():
    """把 data_hub 内部的 print 噪声挡掉（它有些模块直接 print 到 stdout，
    会污染技术库的报告输出）。只挡 print，不影响 logging(stderr)。"""
    if os.environ.get("TECH_DH_QUIET", "1") in ("0", "", "false", "no"):
        yield
        return
    with _QUIET_LOCK:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            yield


# ═══════════════════════ 归一化 ═══════════════════════
def norm_ohlcv(d, keep_amt=False):
    """任意源日线 → DataFrame[dt, open, high, low, close, volume] 升序（可带 vol/amt）

    兼容 date / trade_date / day / dt 四种日期列名；volume 缺失时取 vol。
    """
    try:
        import pandas as pd
    except Exception:                                            # noqa: BLE001
        return None
    if d is None or not hasattr(d, "columns") or len(d) == 0:
        return None
    d = d.copy()
    if "dt" not in d.columns:
        for c in ("trade_date", "date", "day", "dt"):
            if c in d.columns:
                d = d.rename(columns={c: "dt"})
                break
    if "dt" not in d.columns:
        return None
    s = d["dt"].astype(str).str.replace("-", "", regex=False).str[:8]
    d["dt"] = pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    if "volume" not in d.columns:
        d["volume"] = d["vol"] if "vol" in d.columns else 0.0
    for c in ("open", "high", "low", "close", "volume"):
        if c not in d.columns:
            d[c] = 0.0
        d[c] = pd.to_numeric(d[c], errors="coerce")
    cols = ["dt", "open", "high", "low", "close", "volume"]
    if keep_amt:
        for c in ("vol", "amt"):
            if c not in d.columns:
                d[c] = 0.0
            d[c] = pd.to_numeric(d[c], errors="coerce")
            cols.append(c)
    d = d[cols].dropna(subset=["dt", "close"]).sort_values("dt")
    d = d.drop_duplicates("dt").reset_index(drop=True)
    return d if len(d) else None


def _date8(dt):
    return str(dt).replace("-", "")[:8]


# ═══════════════════════ 板块（行业/概念）═══════════════════════
def sector_code(code):
    """'885431.TI' / '881121' / 885431 → '885431'（同花顺板块码）"""
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".")[0]
    return s


def sector_list(kind="行业"):
    """板块列表 → [{'code','name'}...]；失败 []"""
    mod = module()
    if mod is None:
        return []
    try:
        with _quiet():
            df = mod.get_data("sector_list", kind=kind)
    except Exception as e:                                       # noqa: BLE001
        log("sector_list(%s) 失败: %s" % (kind, str(e)[:80]))
        return []
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        c, n = str(r.get("code") or "").strip(), str(r.get("name") or "").strip()
        if c and n:
            out.append({"code": c, "name": n})
    return out


def board_code_by_name(name, kind="行业"):
    """板块名 → 同花顺板块码（复用 data_hub 的包含匹配，申万名也能对上）"""
    if not name:
        return None
    mod = module()
    if mod is None:
        return None
    try:
        from data_hub.collectors.stocks.sector import board_code_by_name as _bc
        with _quiet():
            return _bc(str(name).strip(), kind=kind)
    except Exception:                                            # noqa: BLE001
        pass
    try:
        with _quiet():
            return mod.sector_code_by_name(str(name).strip(), kind=kind)
    except Exception:                                            # noqa: BLE001
        return None


def sector_kline(code=None, name=None, kind="行业", years_back=6, period="daily", keep_amt=True):
    """板块K线 → DataFrame[dt,open,high,low,close,volume(,vol,amt)] 升序；失败 None"""
    mod = module()
    if mod is None:
        return None
    kw = dict(years_back=int(years_back), period=str(period))
    if code:
        c = sector_code(code)
        if not c:
            return None
        kw["code"] = c
    elif name:
        kw["name"] = str(name).strip()
        kw["kind"] = kind
    else:
        return None
    try:
        with _quiet():
            df = mod.get_data("sector_kline", **kw)
    except Exception as e:                                       # noqa: BLE001
        log("sector_kline(%s) 失败: %s" % (kw.get("code") or kw.get("name"), str(e)[:80]))
        return None
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return None
    return norm_ohlcv(df, keep_amt=keep_amt)


def sector_kline_rows(code=None, name=None, kind="行业", years_back=6, lmt=None):
    """板块K线 → [{date,open,high,low,close,vol,amt}...] 升序（老 datafeed/wyckoff 口径）"""
    d = sector_kline(code=code, name=name, kind=kind, years_back=years_back)
    if d is None or len(d) == 0:
        return []
    rows = []
    for r in d.itertuples():
        rows.append({"date": r.dt.strftime("%Y-%m-%d"),
                     "open": float(r.open), "high": float(r.high),
                     "low": float(r.low), "close": float(r.close),
                     "vol": float(getattr(r, "vol", 0.0) or 0.0),
                     "amt": float(getattr(r, "amt", 0.0) or 0.0)})
    if lmt:
        rows = rows[-int(lmt):]
    return rows


def sector_members(code=None, name=None, kind="行业"):
    """板块成分股 → [{'code': 6位, 'name': str}...]；**概念板块 data_hub 取不到 → []**"""
    mod = module()
    if mod is None:
        return []
    kw = {"kind": kind}
    if code:
        c = sector_code(code)
        if not c:
            return []
        kw["code"] = c
    elif name:
        kw["name"] = str(name).strip()
    else:
        return []
    try:
        with _quiet():
            df = mod.get_data("sector_members", **kw)
    except Exception as e:                                       # noqa: BLE001
        log("sector_members(%s) 失败: %s" % (kw.get("code") or kw.get("name"), str(e)[:80]))
        return []
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        c = str(r.get("code") or "").strip()
        if not c:
            continue
        out.append({"code": c.split(".")[0].zfill(6), "name": str(r.get("name") or "").strip()})
    return out


def stock_kline_rows_ths(code, years_back=6, lmt=None):
    """个股日线（data_hub 的同花顺个股源）→ [{date,open,...,vol,amt}...] 升序

    与老 datafeed/ths_hub 同一口径（同花顺 hs_ 接口），供盘中链路替换 datafeed 用。
    失败返回 []。
    """
    c6 = str(code or "").split(".")[0].strip()
    if not (len(c6) == 6 and c6.isdigit()):
        return []
    mod = module()
    if mod is None:
        return []
    try:
        with _quiet():
            df = mod.get_stock_kline_ths(c6, years_back=int(years_back))
    except Exception as e:                                       # noqa: BLE001
        log("stock_kline_ths(%s) 失败: %s" % (c6, str(e)[:80]))
        return []
    d = norm_ohlcv(df, keep_amt=True) if df is not None and len(df) else None
    if d is None:
        return []
    rows = []
    for r in d.itertuples():
        rows.append({"date": r.dt.strftime("%Y-%m-%d"),
                     "open": float(r.open), "high": float(r.high),
                     "low": float(r.low), "close": float(r.close),
                     "vol": float(getattr(r, "vol", 0.0) or 0.0),
                     "amt": float(getattr(r, "amt", 0.0) or 0.0)})
    if lmt:
        rows = rows[-int(lmt):]
    return rows


# ═══════════════════════ 指数 ═══════════════════════
def index_components(index_code):
    """指数成分股 → [ts_code...]；失败 []"""
    mod = module()
    if mod is None:
        return []
    try:
        with _quiet():
            d = mod.get_data("index_components", index_code=str(index_code).strip())
    except Exception as e:                                       # noqa: BLE001
        log("index_components(%s) 失败: %s" % (index_code, str(e)[:80]))
        return []
    if d is None:
        return []
    out = []
    if isinstance(d, (list, tuple)):
        out = [str(x) for x in d if x]
    else:
        try:
            col = "con_code" if "con_code" in d.columns else ("ts_code" if "ts_code" in d.columns else None)
            if col:
                out = [str(x) for x in d[col].tolist() if x]
        except Exception:                                        # noqa: BLE001
            out = []
    return out


def index_daily(ts_code, lookback=1200, start=None, end=None):
    """指数日线 → DataFrame[dt,open,high,low,close,volume]；失败 None

    ⚠️ 必须给 lookback / start（否则 akshare 只回 250 根）。"""
    mod = module()
    if mod is None:
        return None
    kw = {"ts_code": str(ts_code).strip()}
    if start:
        kw["start_date"] = _date8(start)
    elif lookback:
        kw["lookback"] = int(lookback)
    if end:
        kw["end_date"] = _date8(end)
    try:
        with _quiet():
            df = mod.get_data("index_daily", **kw)
    except Exception as e:                                       # noqa: BLE001
        log("index_daily(%s) 失败: %s" % (ts_code, str(e)[:80]))
        return None
    return norm_ohlcv(df)


def stock_daily(ts_code, start=None, end=None, lookback=None, adjust=""):
    """个股日线 → DataFrame[dt,open,high,low,close,volume]；失败 None

    adjust: '' 不复权 / 'qfq' 前复权 / 'hfq' 后复权
    ⚠️ tech_buypoint.load_daily 要**不复权**（和当日全市场快照对齐）"""
    mod = module()
    if mod is None:
        return None
    kw = {"ts_code": str(ts_code).strip(), "adjust": adjust or ""}
    if start:
        kw["start_date"] = _date8(start)
    elif lookback:
        try:
            import pandas as pd
            from datetime import datetime
            end8 = _date8(end) if end else datetime.now().strftime("%Y%m%d")
            start8 = (pd.Timestamp(end8) - pd.Timedelta(days=int(lookback * 1.7) + 30)).strftime("%Y%m%d")
            kw["start_date"] = start8
            kw["end_date"] = end8
        except Exception:                                        # noqa: BLE001
            pass
    if end and "end_date" not in kw:
        kw["end_date"] = _date8(end)
    try:
        with _quiet():
            df = mod.get_data("stock_daily", **kw)
    except Exception as e:                                       # noqa: BLE001
        log("stock_daily(%s) 失败: %s" % (ts_code, str(e)[:80]))
        return None
    return norm_ohlcv(df)


def etf_daily(ts_code, lookback=None, start=None, end=None):
    """ETF 日线 → DataFrame[dt,open,high,low,close,volume]；失败 None"""
    mod = module()
    if mod is None:
        return None
    kw = {"ts_code": str(ts_code).strip()}
    if start:
        kw["start_date"] = _date8(start)
    elif lookback:
        kw["lookback"] = int(lookback)
    if end:
        kw["end_date"] = _date8(end)
    try:
        with _quiet():
            df = mod.get_data("etf_daily", **kw)
    except Exception as e:                                       # noqa: BLE001
        log("etf_daily(%s) 失败: %s" % (ts_code, str(e)[:80]))
        return None
    return norm_ohlcv(df)


# ═══════════════════════ 分钟线 ═══════════════════════
def minute_bar(code, freq="60", n=400, kind="index"):
    """分钟K线 → DataFrame[dt,open,high,low,close,volume] 升序；失败 None

    Args:
        code: 已归一化代码（sh600519 / sh000905），或标准 ts_code（600519.SH）
        freq: '1'/'5'/'15'/'30'/'60'
        n: 目标根数
        kind: 'index' / 'stock' / 'fut' / 'auto'

    ⚠️ 2026-11-14 更新：data_hub 已修 `_norm_code` 的期货 bug（新增 `_norm_fut_code`，
    'IF0' 不再被补成 'shif0'），`kind='fut'` 现在走 `futures_zh_minute_sina`，实测
    IF0 60m 1023 根正常。老链路（minute_hub）保留作兜底。
    """
    mod = module()
    if mod is None:
        return None
    kw = {"freq": str(freq), "n": int(n), "kind": kind}
    c = str(code).strip()
    if str(kind).lower() == "fut":
        kw["ts_code"] = c                       # 期货：IF0 / AG0 / IF2609
    elif "." in c and not c.lower().startswith(("sh", "sz", "bj")):
        kw["ts_code"] = c
    else:
        kw["code"] = c.lower()
    try:
        with _quiet():
            df = mod.get_data("minute_bar", **kw)
    except Exception as e:                                       # noqa: BLE001
        log("minute_bar(%s,%s) 失败: %s" % (code, freq, str(e)[:80]))
        return None
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return None
    d = df.copy()
    if "dt" not in d.columns:
        for col in ("datetime", "trade_date", "date", "day"):
            if col in d.columns:
                d = d.rename(columns={col: "dt"})
                break
    if "dt" not in d.columns:
        return None
    d["dt"] = _pd().to_datetime(d["dt"], errors="coerce")
    if "volume" not in d.columns:
        d["volume"] = d["vol"] if "vol" in d.columns else 0.0
    for col in ("open", "high", "low", "close", "volume"):
        if col not in d.columns:
            d[col] = 0.0
        d[col] = _pd().to_numeric(d[col], errors="coerce")
    d = d[["dt", "open", "high", "low", "close", "volume"]].dropna(subset=["dt", "close"])
    d = d.drop_duplicates("dt").sort_values("dt").reset_index(drop=True)
    return d if len(d) else None


# ═══════════════════════ 股票名称 ═══════════════════════
_NAME_MAP = None


# ═══════════════════════ 2026-11-14 补的 5 个缺口 ═══════════════════════
# 这 5 个原来是「data_hub 没对应 data_type」的缺口，现在 data_hub 已补齐：
#   sector_spot / concept_members / sw_index_daily / futures_main_daily / etf_dividend
# 每个都保留老链路兜底（调用方自己决定，本模块只负责取，失败一律返回 None/[]）。

def sector_spot(kind="行业"):
    """板块实时快照 → DataFrame[code,name,price,pct_chg,vol,amt,kind]；失败 None

    ⚠️ 东财 BKxxxx 编码，与同花顺 881/885 **不是一套**。
       要跨源对齐（如同花顺板块 K 线 + 东财快照）请按**板块名称**匹配，
       可直接用 `sector_spot_one(name)`。
    """
    mod = module()
    if mod is None:
        return None
    try:
        with _quiet():
            df = mod.get_data("sector_spot", kind=str(kind))
    except Exception as e:                                       # noqa: BLE001
        log("sector_spot(%s) 失败: %s" % (kind, str(e)[:80]))
        return None
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return None
    return df


def sector_spot_one(name, kind="行业"):
    """按板块名取单个快照 → dict（code/name/price/pct_chg/vol/amt/kind）；失败 None"""
    mod = module()
    if mod is None or not name:
        return None
    try:
        from data_hub.collectors.stocks.sector_spot import get_sector_spot_one as _one
        with _quiet():
            return _one(str(name).strip(), kind=str(kind))
    except Exception as e:                                       # noqa: BLE001
        log("sector_spot_one(%s) 失败: %s" % (name, str(e)[:80]))
        return None


def concept_members(code=None, name=None, ths_code=None, kind="概念"):
    """概念板块成分股 → [{'code','name'}...]；失败 []

    取数（2026-11-14 定案）：data_hub `concept_members` ← **tushare `ths_member`**
      · 东财 push2 API 从本机完全不可达（网页 200 / API 全 RemoteDisconnected）
      · 同花顺概念详情页成分是 ajax 动态加载，直连 **HTTP 401**
      · tushare `ths_member` 可用（885431 云计算 1086 只）

    参数（优先级 ths_code > code > name）：
      ths_code 同花顺 885xxx / 885431.TI（**推荐**）
      code     同 ths_code（兼容老调用）
      name     板块名（在 sector_list("概念") 里精确/包含匹配出 885xxx）
    """
    mod = module()
    if mod is None:
        return []
    kw = {}
    if ths_code:
        kw["ths_code"] = str(ths_code).strip()
    elif code:
        kw["ths_code"] = str(code).strip()
    elif name:
        kw["name"] = str(name).strip()
    else:
        return []
    try:
        with _quiet():
            df = mod.get_data("concept_members", **kw)
    except Exception as e:                                       # noqa: BLE001
        log("concept_members(%s) 失败: %s"
            % (kw.get("ths_code") or kw.get("name"), str(e)[:80]))
        return []
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        c = str(r.get("con_code") or r.get("code") or "").strip()
        if c:
            out.append({"code": c.split(".")[0].zfill(6),
                        "name": str(r.get("con_name") or r.get("name") or "").strip()})
    return out


def concept_code_by_name(name, kind="概念"):
    """概念名 → 同花顺 885xxx.TI（供跨源对齐用）；失败 None"""
    mod = module()
    if mod is None or not name:
        return None
    try:
        from data_hub.collectors.stocks.concept_members import _code_by_name as _cb
        with _quiet():
            return _cb(str(name).strip()) or None
    except Exception:                                            # noqa: BLE001
        return None


def sw_index_daily(code, start=None, end=None):
    """申万行业指数日线 → DataFrame[dt,open,high,low,close,volume,amt]；失败 None

    Args:
        code: 801081 / 801081.SI
        start/end: YYYYMMDD 或 YYYY-MM-DD

    ⚠️ 返回对象的 `.attrs["proxy"]` 为 True 时代表 L3 同花顺代理口径
       （申万取不到时降级），报告里必须标注。
    """
    mod = module()
    if mod is None or not code:
        return None
    try:
        with _quiet():
            df = mod.get_data("sw_index_daily", code=str(code),
                              start_date=start, end_date=end)
    except Exception as e:                                       # noqa: BLE001
        log("sw_index_daily(%s) 失败: %s" % (code, str(e)[:80]))
        return None
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return None
    proxy = bool(getattr(df, "attrs", {}).get("proxy", False))
    d = df.rename(columns={"trade_date": "dt", "amount": "amt", "vol": "volume"})
    d["dt"] = _pd().to_datetime(d["dt"], format="%Y%m%d", errors="coerce")
    keep = [c for c in ("dt", "open", "high", "low", "close", "volume", "amt")
            if c in d.columns]
    d = d[keep].dropna(subset=["dt", "close"]).sort_values("dt").reset_index(drop=True)
    d.attrs["proxy"] = proxy
    d.attrs["source"] = "data_hub/sw_index_daily"
    return d if len(d) else None


def futures_main_daily(symbol, start=None, end=None):
    """期货主力连续日线 → DataFrame[dt,open,high,low,close,volume,oi]；失败 None

    Args:
        symbol: IF0 / AG0 / RB0（尾部 0 = 主力连续；纯字母会自动补 0）
        start/end: YYYYMMDD 或 YYYY-MM-DD
    """
    mod = module()
    if mod is None or not symbol:
        return None
    try:
        with _quiet():
            df = mod.get_data("futures_main_daily", symbol=str(symbol),
                              start_date=start, end_date=end)
    except Exception as e:                                       # noqa: BLE001
        log("futures_main_daily(%s) 失败: %s" % (symbol, str(e)[:80]))
        return None
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return None
    d = df.rename(columns={"trade_date": "dt", "vol": "volume"})
    d["dt"] = _pd().to_datetime(d["dt"], format="%Y%m%d", errors="coerce")
    keep = [c for c in ("dt", "open", "high", "low", "close", "volume", "oi")
            if c in d.columns]
    d = d[keep].dropna(subset=["dt", "close"]).sort_values("dt").reset_index(drop=True)
    d.attrs["source"] = "data_hub/futures_main_daily"
    return d if len(d) else None


def etf_dividend(ts_code):
    """ETF 分红序列 → DataFrame[dt, cum_dividend, per_dividend]；失败 None

    用于后复权因子：hfq_t = raw_t × ∏_{分红日≤t}(1 + 每份分红 / 除息前收盘)
    从未分红的 ETF → None（不是失败）。
    """
    mod = module()
    if mod is None or not ts_code:
        return None
    try:
        with _quiet():
            df = mod.get_data("etf_dividend", code=str(ts_code))
    except Exception as e:                                       # noqa: BLE001
        log("etf_dividend(%s) 失败: %s" % (ts_code, str(e)[:80]))
        return None
    if df is None or not hasattr(df, "columns") or len(df) == 0:
        return None
    d = df.rename(columns={"trade_date": "dt"})
    d["dt"] = _pd().to_datetime(d["dt"], format="%Y%m%d", errors="coerce")
    d = d.dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    return d if len(d) else None


def stock_name_map():
    """ts_code → 中文名（进程内缓存）；失败 {}"""
    global _NAME_MAP
    if _NAME_MAP is not None:
        return _NAME_MAP
    mod = module()
    if mod is None:
        _NAME_MAP = {}
        return _NAME_MAP
    try:
        with _quiet():
            df = mod.get_stock_list()
    except Exception as e:                                       # noqa: BLE001
        log("get_stock_list 失败: %s" % str(e)[:80])
        _NAME_MAP = {}
        return _NAME_MAP
    m = {}
    if df is not None and hasattr(df, "columns") and len(df):
        try:
            for c, n in zip(df["ts_code"].tolist(), df["name"].tolist()):
                if c and n:
                    m[str(c)] = str(n)
        except Exception:                                        # noqa: BLE001
            m = {}
    _NAME_MAP = m
    return m


def stock_name(ts_code):
    """单只中文名；失败 ''"""
    return stock_name_map().get(str(ts_code).strip(), "")
