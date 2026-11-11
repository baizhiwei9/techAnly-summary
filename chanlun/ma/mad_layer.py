#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAD 均线层（独立模块，不挂 tech_summary 主报告）
=====================================================================
口径与历史「MAD 均线指选股」的均线比价核心一致：

    MAD          = MA20 / MA200 - 1        （短中期均线对长期均线的比价，越大越强）
    通过条件     = 收盘 > MA40  且  MA40 五日斜率 > 0
    排名         = 按 MAD 降序（MAD 只做强弱排序，不参与是否通过的判定）

「比价 + 站上中期均线 + 中期均线向上」三件事合起来，才是这个口径想表达的东西：
只站上 MA40 但 MA40 还在下行 = 反弹，不算通过；MAD 再高但价格跌破 MA40 = 已走弱，也不算通过。

历史实现里 MA20/MA40/MA200 用的是「收盘价 × 复权因子」；本模块默认取**后复权**日线：
  个股 → 新浪/腾讯后复权行情；ETF → 新浪分红序列自建复权因子（分红再投资口径）；
  指数 → 无复权概念，直接走 datahub。取不到后复权时才退回不复权，并在输出里标注实际口径。

用法：
    python3 bin/mad_layer.py --target 600519
    python3 bin/mad_layer.py --pool 沪深300,中证500,科创50,创业板
    python3 bin/mad_layer.py --target 600519 --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# ── 全局硬规矩：禁 IPv6（容器 IPv6 不通会静默卡死）──
import socket

_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

def _repo_root(start):
    """向上探测仓库根：同时含 bin/ 与 core/ 的那一层（2026-11-12 目录整理后可任意层级）。"""
    d = os.path.dirname(os.path.abspath(start))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "bin")) and os.path.isdir(os.path.join(d, "core")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


WS = os.environ.get("TECH_WS") or _repo_root(__file__)
# 顺序有讲究：最后插入的优先级最高 → bin/ 放最前 = 最低优先级，
# 保证 import 命中真实目录，而不是 bin/ 里的兼容 shim。
for _sd in ("bin", "", "core", "chanlun/fractal", "chanlun/ma", "chanlun/multiscale",
            "chanlun/multiscale/vendor", "report", "misc", "wavelet", "smc"):
    _p = os.path.join(WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import datahub as DH  # noqa: E402

# ---------------- 参数（与历史实现一致） ----------------
MA_SHORT, MA_MID, MA_LONG = 20, 40, 200
SLOPE_LAG = 5

ALIAS = {
    "沪深300": "SH510300", "中证500": "SH510500", "中证1000": "SH512100",
    "科创50": "SH588000", "创业板": "SZ159915", "上证50": "SH510050",
    "恒生科技": "SH513180",
}
DEFAULT_POOL = ["沪深300", "中证500", "中证1000", "科创50", "创业板", "上证50", "恒生科技"]


def to_symbol(target):
    t = str(target).strip()
    if t in ALIAS:
        return ALIAS[t], False, t
    if re.fullmatch(r"\d{6}\.(SH|SZ|CSI)", t.upper()):
        return t.upper(), True, t.upper()
    if re.fullmatch(r"(sh|sz)\d{6}", t, re.I):
        return t.upper(), False, t.upper()
    if re.fullmatch(r"\d{6}", t):
        return ("SH" if t[0] in "569" else "SZ") + t, False, t
    raise SystemExit("无法识别的标的：%s（支持中文宽基名 / sh510300 / 600519 / 000905.SH）" % target)


def _tencent_fq(sym, days, kind="hfq"):
    """腾讯复权 K 线（kind=hfq/qfq）；返回标准 rows 或 None

    注意：腾讯对 ETF 的 hfq 实际是「复权净值」（含分红再投资，量纲与价格不同），
    所以 ETF 不取 hfq，改用 qfq —— 价格前复权与价格后复权只差一个常数，
    MA20/MA40/MA200 的比值与「收盘站上 MA40」的判定完全等价。
    """
    import json as _json
    import urllib.request as _ur

    code = sym.lower()
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,%s"
           % (code, max(int(days), 60), kind))
    try:
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        with _ur.urlopen(req, timeout=20) as r:
            js = _json.loads(r.read().decode("utf-8", "ignore"))
        node = (js.get("data") or {}).get(code) or {}
        arr = node.get(kind + "day") or node.get("day") or []
        rows = []
        for it in arr:
            if len(it) < 6:
                continue
            d = str(it[0]).replace("-", "")
            # 腾讯顺序为 [date, open, close, high, low, volume]
            rows.append([d, float(it[1]), float(it[3]), float(it[4]), float(it[2]), float(it[5])])
        if len(rows) < 60:
            return None
        return rows
    except Exception:  # noqa: BLE001
        return None


def _ak_hfq(sym, days):
    """后复权日线（新浪优先，东财兜底）；返回标准 rows 或 None

    为什么要后复权：MA20/MA40/MA200 是同一序列不同窗口的均值，
    只要区间内发生除权除息，不复权价会出现跳空，短均线被瞬间压低/抬高，
    MAD 与「站上 MA40」的判定都会被污染。后复权把历史价格按累计因子放大，
    序列内部的相对位置才是真实收益关系。
    """
    try:
        import akshare as ak
    except Exception:  # noqa: BLE001
        return None
    import datetime as _dt

    sina = sym.lower()
    code = sym[2:]
    end = _dt.date.today().strftime("%Y%m%d")
    start = (_dt.date.today() - _dt.timedelta(days=int(days * 1.6) + 120)).strftime("%Y%m%d")

    def _std(df):
        if df is None or len(df) == 0:
            return None
        cols = {c.lower(): c for c in df.columns}
        dcol = cols.get("date") or cols.get("日期")
        if dcol is None:
            return None
        out = pd.DataFrame({
            "dt": pd.to_datetime(df[dcol]),
            "open": pd.to_numeric(df[cols.get("open", "开盘")], errors="coerce"),
            "high": pd.to_numeric(df[cols.get("high", "最高")], errors="coerce"),
            "low": pd.to_numeric(df[cols.get("low", "最低")], errors="coerce"),
            "close": pd.to_numeric(df[cols.get("close", "收盘")], errors="coerce"),
        })
        vcol = cols.get("volume") or cols.get("成交量")
        out["volume"] = pd.to_numeric(df[vcol], errors="coerce") if vcol else 0.0
        out = out.dropna(subset=["close"]).sort_values("dt")
        if len(out) < 60:
            return None
        return [[r.dt.strftime("%Y%m%d"), r.open, r.high, r.low, r.close, r.volume]
                for r in out.itertuples()]

    # ① 新浪：个股与 ETF 都支持 hfq（历史实测最稳）
    try:
        d = ak.stock_zh_a_daily(symbol=sina, adjust="hfq")
        rows = _std(d)
        if rows:
            return rows
    except Exception:  # noqa: BLE001
        pass
    # ② 东财：ETF 走基金接口，个股走 A 股接口
    try:
        if code[:2] in ("51", "15", "16", "50", "58"):
            d = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start,
                                    end_date=end, adjust="hfq")
        else:
            d = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start,
                                   end_date=end, adjust="hfq")
        return _std(d)
    except Exception:  # noqa: BLE001
        return None


def _etf_hfq(sym, days):
    """ETF 后复权：用新浪分红序列自建复权因子（分红再投资口径）

    后复权定义：hfq_t = raw_t × ∏_{分红日 ≤ t}(1 + 每份分红 / 除息前收盘)
    —— 历史价保持原样，除息之后的价格按累计分红因子放大，除息跳空被抹平。
    窗口之前的分红事件缺少对应价格无法折算，会造成一个常数缩放；
    MAD 是均线比值、站上 MA40 是价格与均线比较，常数缩放不影响结论。
    """
    try:
        import akshare as ak
    except Exception:  # noqa: BLE001
        return None
    try:
        rows = DH.daily(sym, days=days, layers=("web",))
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    d = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
    d["dt"] = pd.to_datetime(d["dt"], format="%Y%m%d", errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["close"]).sort_values("dt").reset_index(drop=True)
    if len(d) < 60:
        return None
    try:
        dv = ak.fund_etf_dividend_sina(symbol=sym.lower())
        dv["日期"] = pd.to_datetime(dv["日期"])
        dv = dv.sort_values("日期").reset_index(drop=True)
        per = dv["累计分红"].diff()
        per.iloc[0] = dv["累计分红"].iloc[0]
        events = [(r["日期"], float(r["per"])) for _, r in dv.assign(per=per).iterrows()
                  if float(r["per"]) > 0]
    except Exception:  # noqa: BLE001
        return None
    if not events:
        return None

    px = d.set_index("dt")["close"]
    fac = np.ones(len(d))
    for i, t in enumerate(d["dt"]):
        f = 1.0
        for ed, amt in events:
            if ed <= t:
                p = px.asof(ed)
                if pd.notna(p) and p > 0:
                    f *= 1.0 + amt / float(p)
        fac[i] = f
    for c in ("open", "high", "low", "close"):
        d[c] = d[c] * fac
    return [[r.dt.strftime("%Y%m%d"), r.open, r.high, r.low, r.close, r.volume]
            for r in d.itertuples()]

def load(target, days=600, adjust="hfq"):
    """取日线；默认后复权。指数无复权概念，直接走 datahub。

    返回 (df, name)；df.attrs["adjust"] 记录实际口径（hfq / raw）。
    """
    sym, is_idx, name = to_symbol(target)
    rows, used = None, "raw"
    if adjust == "hfq" and not is_idx:
        code = sym[2:]
        if code[:2] in ("51", "15", "16", "50", "58"):   # ETF/LOF：分红因子自建后复权
            rows = _etf_hfq(sym, days)
            if rows:
                used = "hfq"
        else:                                            # 个股：价格后复权
            rows = _ak_hfq(sym, days)
            if not rows:
                rows = _tencent_fq(sym, days, "hfq")
            if rows:
                used = "hfq"
    if not rows:
        try:
            rows = DH.index_daily(sym, days=days) if is_idx else DH.daily(sym, days=days)
        except ImportError:
            # 本机没装 akshare/tushare 时，直接走 web 源（腾讯/东财），不依赖 L1/L2
            rows = DH.daily(sym, days=days, layers=("web",))
    if not rows:
        raise RuntimeError("取数失败：%s" % sym)
    d = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
    d["dt"] = pd.to_datetime(d["dt"], format="%Y%m%d", errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["close"]).sort_values("dt").reset_index(drop=True)
    d.attrs["adjust"] = used
    return d, name


def layer(df):
    """MAD 均线单标的一层；返回 dict"""
    c = pd.Series(df["close"].values, index=pd.to_datetime(df["dt"])).dropna()
    n = len(c)
    if n < MA_LONG:
        return {"status": "insufficient_history", "obs": n,
                "mad": None, "close": round(float(c.iloc[-1]), 4) if n else None,
                "ma20": None, "ma40": None, "ma200": None,
                "above_ma40": None, "ma40_up_lag5": None, "pass_filter": False,
                "reason": "历史不足 %d 根（现 %d 根）" % (MA_LONG, n)}

    ma20 = float(c.iloc[-MA_SHORT:].mean())
    ma40 = float(c.iloc[-MA_MID:].mean())
    ma200 = float(c.iloc[-MA_LONG:].mean())
    ma40_lag = float(c.iloc[-(MA_MID + SLOPE_LAG):-SLOPE_LAG].mean())
    px = float(c.iloc[-1])

    mad = ma20 / ma200 - 1.0
    above = bool(px > ma40)
    up = bool(ma40 > ma40_lag)
    passed = above and up

    if not above:
        reason = "收盘跌破 MA40（%.2f < %.2f）" % (px, ma40)
    elif not up:
        reason = "MA40 五日斜率向下（%.2f → %.2f）" % (ma40_lag, ma40)
    else:
        reason = "站上 MA40 且 MA40 向上，MAD=%.2f%%" % (mad * 100)

    return {"status": "ok", "obs": n, "last_date": str(c.index[-1].date()),
            "close": round(px, 4), "ma20": round(ma20, 4), "ma40": round(ma40, 4),
            "ma200": round(ma200, 4), "mad": round(mad, 6),
            "mad_pct": round(mad * 100, 2),
            "close_over_ma40_pct": round((px / ma40 - 1.0) * 100, 2),
            "ma40_slope_pct_lag5": round((ma40 / ma40_lag - 1.0) * 100, 2),
            "above_ma40": above, "ma40_up_lag5": up,
            "pass_filter": passed, "reason": reason}


def scan(pool, days=600):
    """批量跑（默认宽基池），按 MAD 降序返回明细表"""
    recs = []
    for t in pool:
        try:
            df, name = load(t, days)
            r = layer(df)
        except Exception as e:  # noqa: BLE001
            recs.append({"target": t, "status": "error", "reason": str(e)})
            continue
        r["target"] = name
        r["adjust"] = df.attrs.get("adjust", "raw")
        recs.append(r)
    ok = [r for r in recs if r.get("status") == "ok"]
    bad = [r for r in recs if r.get("status") != "ok"]
    ok.sort(key=lambda x: x["mad"], reverse=True)
    for i, r in enumerate(ok, 1):
        r["rank"] = i
    return ok + bad


def main():
    ap = argparse.ArgumentParser(description="MAD 均线层（独立模块）")
    ap.add_argument("--target", help="单标的：中文宽基名 / sh510300 / 600519 / 000905.SH")
    ap.add_argument("--pool", help="批量标的，逗号分隔；不给 --target 时默认跑 7 只宽基")
    ap.add_argument("--n", type=int, default=600, help="取多少根日线（默认 600）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    a = ap.parse_args()

    if a.target:
        df, name = load(a.target, a.n)
        r = layer(df)
        r["target"] = name
        r["adjust"] = df.attrs.get("adjust", "raw")
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print("=" * 62)
            print("MAD 均线  %s   数据截至 %s   复权：%s" %
                  (name, r.get("last_date", "-"),
                   {"hfq": "后复权", "qfq": "前复权（与后复权等价）", "raw": "不复权"}.get(r["adjust"], r["adjust"])))
            print("=" * 62)
            print("结论      %s" % ("通过" if r.get("pass_filter") else "未通过"))
            print("依据      %s" % r["reason"])
            for k, lab in (("close", "收盘"), ("ma20", "MA20"), ("ma40", "MA40"), ("ma200", "MA200"),
                           ("mad_pct", "MAD(%)"), ("close_over_ma40_pct", "距MA40(%)"),
                           ("ma40_slope_pct_lag5", "MA40斜率(%)")):
                print("%-10s %s" % (lab, r.get(k)))
            print("=" * 62)
        return

    pool = [x.strip() for x in a.pool.split(",") if x.strip()] if a.pool else DEFAULT_POOL
    rows = scan(pool, a.n)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    print("=" * 78)
    print("MAD 均线 · 批量（按 MAD 降序，通过条件：收>MA40 且 MA40 五日斜率>0）")
    print("复权：后复权（个股有复权行情，ETF 用分红序列自建因子；指数不复权）")
    print("=" * 78)
    print("%-3s %-10s %-9s %-8s %-8s %-9s %-6s %s" %
          ("#", "标的", "收盘", "MAD%", "距MA40%", "MA40斜率%", "通过", "说明"))
    for r in rows:
        if r.get("status") != "ok":
            print("%-3s %-10s %s" % ("-", r["target"], r["reason"]))
            continue
        print("%-3d %-10s %-9.3f %-8.2f %-8.2f %-9.2f %-6s %s" %
              (r["rank"], r["target"], r["close"], r["mad_pct"],
               r["close_over_ma40_pct"], r["ma40_slope_pct_lag5"],
               "是" if r["pass_filter"] else "否", r["reason"]))
    print("=" * 78)
    print("通过 %d / %d" % (sum(1 for r in rows if r.get("pass_filter")), len(rows)))


if __name__ == "__main__":
    main()