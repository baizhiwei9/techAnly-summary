#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R_cascade 波动率级联比（平滑版 std20/std60）
=====================================================================
2026-11-16 从工作区 `bin/rcascade_daily.py` **搬进本库**（大佬：「把这东西放在
techAnly 这个库里边，然后供这个脚本调用」）。工作区那份改成薄壳转发。

口径（唯一权威，别再另写一套）
--------------------------------
    r      = ln(close_t / close_t-1)
    R      = std(r, 20日) / std(r, 60日)      ← 短窗 20 / 长窗 60
    vol20  = std(r, 20) × √252 × 100
    vol60  = std(r, 60) × √252 × 100

⚠️ 历史上有过 5 日口径（`r² / 5日均r²`，即 scripts/kc50_cascade.py），
   2026-11-16 已按大佬要求删除，**不是本文件**。

阈值（正式报警只发两种）
--------------------------------
    🔴 > 1.2            小尺度波动失控     —— 须**连续 3 日**成立才正式报警
    🟢 < 0.8            能量集中在大尺度
    🟡 0.8 ~ 1.2        中性震荡（历史效率比仅 0.03，最无趋势）
    🟣 < 0.75 连续 5 日  低波动陷阱 / 单边末端 —— 也正式报警
    单日越线只算「预警」，报告里标出但**不发邮件**

标的（三指数）
--------------------------------
    科创50ETF 588000.SH ｜ 创业板指 399006.SZ ｜ 中证1000 000852.SH

数据来源
--------------------------------
① 优先 DataHub（`dh_bridge.etf_daily` / `index_daily`，多源并行面板）
② DataHub 不可用或数据更旧 → 回退 akshare 老链路
③ 两边都取到的，**取最后日期更新的那份**（DataHub 有时个别指数字段滞后）
→ 每条结果带 `source` / `last_date`，报告里可直接看出数据停在哪天

用法
--------------------------------
    python3 misc/engine/rcascade.py                       # 打印摘要（不出图）
    python3 misc/engine/rcascade.py --chart               # 同时出图
    python3 misc/engine/rcascade.py --chart --out x.png --json
    python3 misc/engine/rcascade.py --chart --email-alert # 正式报警就发邮件（带图）

  当库用（工作区 bin/rcascade_daily.py 就是这么调的）：
    import rcascade as RC
    data = {nm: RC.compute(typ, code) for nm, code, typ, lab in RC.TARGETS}
    d, rows, alerts, alerts_deep, warns = RC.build(data)
    RC.draw(data, png); print(RC.text_block(rows, d))
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# ── sys.path：仓库内各层（顺序有讲究，真实目录优先于 bin/ 兼容壳）────────────
_WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
for _sd in ("misc/core", "misc/engine", "chanlun/fractal", "chanlun/ma",
            "chanlun/multiscale", "smc", "misc", "wavelet", ""):
    _p = os.path.join(_WS, _sd)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# ── 网络出口：默认 IPv4，东财行情族走 IPv6（统一走 net_guard，别自己写）──────
try:
    from net_guard import install as _net_install
    _net_install()
except Exception:                                                # noqa: BLE001
    import socket as _socket
    _o = _socket.getaddrinfo
    _socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k)
                                           if r[0] == _socket.AF_INET]

import numpy as np                                               # noqa: E402
import pandas as pd                                              # noqa: E402

WS = _WS
OUT_DEFAULT = os.path.join(WS, ".cache", "rcascade_daily.png")

# (显示名, 代码, 数据源类型, 标签)
TARGETS = [
    ("科创50ETF", "588000", "etf", "科创50 ETF（588000）"),
    ("创业板指", "399006", "index_sz", "创业板指（399006）"),
    ("中证1000", "000852", "index_sh", "中证1000（000852）"),
]

# 阈值（唯一口径，工作区薄壳与大盘技术拆解都读这里）
THR_HIGH, THR_LOW, CONFIRM = 1.2, 0.8, 3
THR_DEEP, CONFIRM_DEEP = 0.75, 5

WIN_SHORT, WIN_LONG = 20, 60


# ═══════════════════════════ 取数 ═══════════════════════════
def _ts_code(code, typ):
    """6 位代码 → 标准 ts_code"""
    if typ == "etf" or typ == "index_sh":
        return code + ".SH"
    return code + ".SZ"


def _norm(df, cols=("dt", "open", "high", "low", "close")):
    """统一成 DataFrame[dt, open, high, low, close]，dt 为 datetime 升序"""
    if df is None or len(df) == 0:
        return None
    d = df.copy()
    if "date" in d.columns and "dt" not in d.columns:
        d = d.rename(columns={"date": "dt"})
    if "dt" not in d.columns:
        return None
    d["dt"] = pd.to_datetime(d["dt"])
    for c in ("open", "high", "low", "close"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["close"]).sort_values("dt").reset_index(drop=True)
    return d[["dt", "open", "high", "low", "close"]]


def _ensure_datahub_path():
    """DataHub 位置兜底（只设环境变量，不 import）。

    `dh_bridge._candidates()` 只看 `DATA_HUB_PATH` + 向上探 5 层 `optstack/`，
    而部署机上 DataHub 常在**工作区**（`~/.openclaw/workspace/optstack`），
    跟本库不是上下级 → 探不到。这里显式补候选。
    """
    if os.environ.get("DATA_HUB_PATH"):
        return
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, ".openclaw", "workspace", "optstack"),
                 os.path.join(home, "optstack"),
                 os.path.join(os.path.dirname(_WS), "optstack")):
        if os.path.isdir(os.path.join(cand, "data_hub")):
            os.environ["DATA_HUB_PATH"] = cand
            return


def _from_datahub(typ, code):
    """DataHub 路径（库内硬规矩：数据优先 DataHub）。失败/不可用返回 None。"""
    _ensure_datahub_path()
    try:
        import dh_bridge as D
    except Exception:                                            # noqa: BLE001
        return None
    if not D.enabled():
        return None
    try:
        if typ == "etf":
            df = D.etf_daily(_ts_code(code, typ), lookback=1200)
        else:
            df = D.index_daily(_ts_code(code, typ), lookback=1200)
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] datahub %s(%s): %s" % (typ, code, str(e)[:90]))
        return None
    return _norm(df)


def _from_akshare(typ, code):
    """akshare 老链路（兜底）。失败返回 None。"""
    import akshare as ak
    try:
        if typ == "etf":
            sym = ("sh" if code.startswith("5") else "sz") + code
            try:
                d = ak.fund_etf_hist_sina(symbol=sym)
                d = d[["date", "open", "high", "low", "close"]].copy()
            except Exception:                                    # noqa: BLE001
                d = ak.fund_etf_hist_em(symbol=code, period="daily",
                                        start_date="20200101", end_date="20991231",
                                        adjust="")
                d = d.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                                      "最低": "low", "收盘": "close"})
                d = d[["date", "open", "high", "low", "close"]].copy()
        else:
            prefix = "sz" if typ == "index_sz" else "sh"
            d = ak.stock_zh_index_daily(symbol=prefix + code)
            d = d[["date", "open", "high", "low", "close"]].copy()
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] akshare %s(%s): %s" % (typ, code, str(e)[:90]))
        return None
    return _norm(d)


def load(typ, code, prefer_datahub=True):
    """取日线：DataHub 与 akshare 都试一次，**取长历史 + 补最新**。

    合并规则（2026-11-16 定）：
      ① 以**行数更多**的那份为底（历史越长，「危险信号 N 次」「累计<0.75 N 次」
         这类历史计数才准；DataHub 的 etf/index_daily 只回约 1455 根≈6 年）
      ② 另一份里 **比底部最后一天更新** 的行，追加进去（补新鲜度）
    两边都取不到 → (None, None)。

    返回 (DataFrame, source_str)；source_str 形如 `akshare+datahub` 表示拼过。
    """
    got = []
    if prefer_datahub:
        d = _from_datahub(typ, code)
        if d is not None and len(d):
            got.append((d, "datahub"))
    d = _from_akshare(typ, code)
    if d is not None and len(d):
        got.append((d, "akshare"))
    if not got:
        return None, None
    if len(got) == 1:
        return got[0]
    got.sort(key=lambda x: len(x[0]), reverse=True)          # 长的在前
    base = got[0][0].copy()
    bsrc = got[0][1]
    other, osrc = got[1]
    last = base["dt"].iloc[-1]
    extra = other[other["dt"] > last]
    if len(extra):
        base = pd.concat([base, extra], ignore_index=True)
        base = base.drop_duplicates(subset=["dt"], keep="last")
        base = base.sort_values("dt").reset_index(drop=True)
        return base, "%s+%s" % (bsrc, osrc)
    return base, bsrc


def compute(typ, code, prefer_datahub=True):
    """算 R_cascade。返回 DataFrame[dt, ohlc, r, RC, vol20, vol60]，index=dt。

    额外写 `df.attrs["source"]`（datahub / akshare）
    """
    d, src = load(typ, code, prefer_datahub=prefer_datahub)
    if d is None:
        raise RuntimeError("取数失败：%s(%s)" % (typ, code))
    d = d.set_index("dt")
    r = np.log(d["close"] / d["close"].shift(1))
    d["r"] = r
    d["RC"] = r.rolling(WIN_SHORT).std() / r.rolling(WIN_LONG).std()
    d["vol20"] = r.rolling(WIN_SHORT).std() * np.sqrt(252) * 100
    d["vol60"] = r.rolling(WIN_LONG).std() * np.sqrt(252) * 100
    d.attrs["source"] = src
    return d


# ═══════════════════════════ 判定 ═══════════════════════════
def trig(V, thr, above=True, k=CONFIRM):
    """返回「连续 k 日成立」的起点日期（用于历史计数）"""
    cnt, res = 0, []
    for i, x in enumerate(V.values):
        ok = pd.notna(x) and (x > thr if above else x < thr)
        cnt = cnt + 1 if ok else 0
        if cnt == k:
            res.append(V.index[i])
    return res


def cur_ok(V, thr, above=True, k=CONFIRM):
    """当前尾部 k 日是否全满足（已处于确认状态）"""
    if len(V) < k:
        return False
    last = V.tail(k).values
    return bool(np.all(last > thr) if above else np.all(last < thr))


def cur_streak(V, thr, above=True):
    """当前尾部连续满足条件的天数"""
    n = 0
    for y in reversed(V.values.tolist()):
        ok = (y > thr) if above else (y < thr)
        if pd.isna(y) or not ok:
            break
        n += 1
    return n


def state_of(v):
    if pd.isna(v):
        return "数据不足", "—"
    if v > THR_HIGH:
        return "🔴危险区", ">1.2"
    if v < THR_LOW:
        return "🟢安全区", "<0.8"
    return "🟡中性带", "0.8~1.2"


def build(data):
    """data: {显示名: DataFrame} → (日期, rows, alerts, alerts_deep, warns)"""
    d = datetime.now().strftime("%Y-%m-%d")
    rows, alerts, alerts_deep, warns = [], [], [], []
    for nm, code, typ, lab in TARGETS:
        if nm not in data:
            continue
        p = data[nm]
        v = p.RC.dropna()
        if not len(v):
            continue
        lv = float(v.iloc[-1])
        last3 = [(x.strftime("%m-%d"), float(y)) for x, y in p.RC.tail(3).items()]
        last5 = [(x.strftime("%m-%d"), float(y)) for x, y in p.RC.tail(5).items()]
        hi = trig(v, THR_HIGH, True)
        lo_ = trig(v, THR_LOW, False)
        deep = trig(v, THR_DEEP, False, k=CONFIRM_DEEP)
        ds = cur_streak(v, THR_DEEP, False)
        st, rng = state_of(lv)
        confirmed = cur_ok(v, THR_HIGH, True, CONFIRM)
        deep_confirmed = cur_ok(v, THR_DEEP, False, CONFIRM_DEEP)
        warn = (lv > THR_HIGH) and not confirmed
        rows.append(dict(name=nm, label=lab, code=code,
                         close=float(p.close.iloc[-1]),
                         rc=lv, state=st, rng=rng, last3=last3, last5=last5,
                         vol20=float(p.vol20.iloc[-1]), vol60=float(p.vol60.iloc[-1]),
                         danger_n=len(hi), safe_n=len(lo_),
                         danger_last=hi[-1].strftime("%Y-%m-%d") if hi else "—",
                         safe_last=lo_[-1].strftime("%Y-%m-%d") if lo_ else "—",
                         confirmed=confirmed, warn=warn, deep_streak=ds,
                         deep_n=len(deep), below_n=int((v < THR_DEEP).sum()),
                         deep_last=deep[-1].strftime("%Y-%m-%d") if deep else "—",
                         deep_confirmed=deep_confirmed,
                         last_date=p.index[-1].strftime("%Y-%m-%d"),
                         source=p.attrs.get("source", "—")))
        if confirmed:
            alerts.append((nm, lv, True))
        elif warn:
            warns.append((nm, lv))
        if deep_confirmed:
            alerts_deep.append((nm, lv))
    return d, rows, alerts, alerts_deep, warns


def freshness_note(rows):
    """数据新鲜度提示：最后日期距今超过 3 自然日就提示（防止把旧信号当今天的新信号）"""
    if not rows:
        return ""
    last = max(x["last_date"] for x in rows)
    try:
        gap = (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days
    except Exception:                                            # noqa: BLE001
        return ""
    if gap <= 3:
        return ""
    stale = sorted({x["name"] for x in rows if x["last_date"] < last})
    extra = ("；%s 更旧" % "、".join(stale)) if stale else ""
    return ("⚠️ **数据新鲜度**：最新只到 **%s**（距今 %d 天）%s —— "
            "下面所有 R 值都是那天的，不是今天的新信号" % (last, gap, extra))


def alarm_tag(x):
    """行尾报警标识：正式报警（会发邮件）vs 预警（不发）"""
    if not x:
        return "—"
    t = []
    if x.get("confirmed"):
        t.append("🔴 报警 R>1.2(连续3日)")
    elif x.get("warn"):
        t.append("🟡 预警 R>1.2(单日·不发)")
    if x.get("deep_confirmed"):
        t.append("🟣 报警 R<0.75(连续5日)")
    elif x.get("deep_streak", 0) > 0:
        t.append("⚪ <0.75 进度 %d/5" % x["deep_streak"])
    return " · ".join(t) if t else "—"


def text_block(rows, d):
    """Markdown 文字段（大盘技术拆解日报直接贴这一段）"""
    trig_rows = [x for x in rows if x.get("confirmed") or x.get("deep_confirmed")]
    warn_rows = [x for x in rows if x.get("warn")]
    L = ["**📈 R_cascade 波动率级联比（平滑版 std%d/std%d）· %s**" %
         (WIN_SHORT, WIN_LONG, d), ""]
    fresh = freshness_note(rows)
    if fresh:
        L += [fresh, ""]
    if trig_rows:
        L.append("🚨 **本次触发（正式报警 → 发邮件）**：%s" % "；".join(
            "%s %s（%.3f）" % (x["name"], alarm_tag(x), x["rc"]) for x in trig_rows))
    else:
        L.append("✅ **本次触发**：无（未达连续 3 日 >1.2、也未连续 5 日 <0.75）")
    if warn_rows:
        L.append("⚠️ **预警未确认（不发邮件）**：%s" % "；".join(
            "%s %.3f 单日越线" % (x["name"], x["rc"]) for x in warn_rows))
    L.append("")
    L.append("| 指数 | 收盘 | R_cascade | 状态 | 20日波动 | 60日波动 | 数据截止 | 报警标识 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for x in rows:
        L.append("| %s | %.2f | **%.3f** | %s | %.1f%% | %.1f%% | %s | %s |" % (
            x["name"], x["close"], x["rc"], x["state"], x["vol20"], x["vol60"],
            x["last_date"], alarm_tag(x)))
    L.append("")
    for x in rows:
        s3 = " / ".join("%s %.3f" % (a, b) for a, b in x["last3"])
        L.append("- **%s** 最近3日：%s" % (x["name"], s3))
        L.append("  ｜危险信号 %d 次（最近 %s）｜安全信号 %d 次（最近 %s）" % (
            x["danger_n"], x["danger_last"], x["safe_n"], x["safe_last"]))
        s5 = " / ".join("%s %.3f" % (a, b) for a, b in x.get("last5", []))
        L.append("  ｜近5日：%s｜累计<0.75 %d 次（最近连续5日信号 %s）" % (
            s5, x.get("below_n", 0), x.get("deep_last", "—")))
    L.append("")
    L.append("> 口径：R = std(ln收益,%d日) ÷ std(ln收益,%d日)" % (WIN_SHORT, WIN_LONG))
    L.append("> 🔴>1.2 小尺度波动失控 / 🟢<0.8 能量集中在大尺度 / "
             "🟡0.8~1.2 中性震荡（此区间历史效率比仅0.03，最无趋势）")
    L.append("> ⭐ **发邮件的只有两种正式报警**：🔴 连续 3 日 >1.2   🟣 连续 5 日 <0.75")
    L.append("> 🟡 单日越线（未连 3 日）只算预警，报告里标出但不发邮件")
    return "\n".join(L)


# ═══════════════════════════ 出图 ═══════════════════════════
def draw(data, out=None):
    """三指数 × (K线+MA20 / R_cascade) 六面板图。返回 png 路径。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm

    out = out or OUT_DEFAULT
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    cjk = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
    fp = fm.FontProperties(fname=cjk)
    fp_t = fm.FontProperties(fname=cjk, size=14)
    fp_l = fm.FontProperties(fname=cjk, size=13)
    fp_s = fm.FontProperties(fname=cjk, size=9)

    names = [n for n, _, _, _ in TARGETS if n in data]
    start = pd.Timestamp("2026-01-01")
    plots = {n: data[n][data[n].index >= start].copy() for n in names}

    fig, axs = plt.subplots(max(2, 2 * len(names)), 1, figsize=(16.5, 5.2 * len(names)),
                            gridspec_kw={"height_ratios": [2.4, 1.4] * len(names),
                                         "hspace": 0.34})
    fig.patch.set_facecolor("#0e1117")
    UP, DN = "#e74c3c", "#2ecc71"

    for i, (nm, code, typ, lab) in enumerate([t for t in TARGETS if t[0] in data]):
        p = plots[nm]
        axp, axr = axs[i * 2], axs[i * 2 + 1]
        for a in (axp, axr):
            a.set_facecolor("#0e1117")
            for sp in a.spines.values():
                sp.set_color("#333")
            a.tick_params(colors="#aaa", labelsize=9)
            a.grid(True, ls=":", color="#2a2a2a", lw=0.7)

        n = len(p)
        x = np.arange(n)
        for k, (_, row) in enumerate(p.iterrows()):
            col = UP if row.close >= row.open else DN
            axp.plot([k, k], [row.low, row.high], color=col, lw=0.8, zorder=2)
            axp.add_patch(plt.Rectangle((k - 0.32, min(row.open, row.close)), 0.64,
                                        max(abs(row.close - row.open), 1e-9),
                                        facecolor=col, edgecolor=col, lw=0.5, zorder=3))
        ma20 = p.close.rolling(20).mean()
        axp.plot(x, ma20.values, color="#ffd666", lw=1.0, ls="--", alpha=0.85)
        axp.set_ylabel(lab, fontproperties=fp_l, fontsize=12, color="#eee")
        axp.set_xlim(-1, n)

        axr.plot(x, p.RC.values, color="#4da3ff", lw=1.7, zorder=3)
        axr.axhline(THR_HIGH, color="#ff4d4f", lw=1.2, ls="--")
        axr.axhline(THR_LOW, color="#52c41a", lw=1.2, ls="--")
        axr.axhline(THR_DEEP, color="#b37feb", lw=1.0, ls="-.")
        axr.axhline(1.0, color="#888", lw=0.8, ls=":")
        axr.scatter(x[-3:], p.RC.iloc[-3:].values, s=150, c="#ffd666",
                    edgecolors="#ff7a45", linewidths=1.4, zorder=5)
        axr.set_ylabel("R_cascade", fontproperties=fp_s, fontsize=11, color="#eee")
        lo = float(np.nanmin(p.RC.values)); hi = float(np.nanmax(p.RC.values))
        axr.set_ylim(min(0.55, lo - 0.05), max(1.45, hi + 0.05))
        axr.set_xlim(-1, n)
        lv = float(p.RC.iloc[-1])
        axr.text(n - 1, THR_HIGH, " 1.2 ", color="#ff7875", va="bottom", ha="right",
                 fontproperties=fp_s, fontsize=10)
        axr.text(n - 1, THR_LOW, " 0.8 ", color="#95de64", va="top", ha="right",
                 fontproperties=fp_s, fontsize=10)
        axr.text(n - 1, THR_DEEP, " 0.75 ", color="#b37feb", va="top", ha="right",
                 fontproperties=fp_s, fontsize=10)
        axr.text(n - 1, lv + 0.03, f" 最新 {lv:.3f} ", color="#ffd666", va="bottom",
                 ha="right", fontproperties=fp_s, fontsize=10.5)

        rc3 = p.RC.tail(3).values
        st = {"🔴危险区": "危险区>1.2", "🟢安全区": "安全区<0.8",
              "🟡中性带": "中性带0.8~1.2"}.get(state_of(lv)[0], "")
        tags = []
        if len(rc3) == 3 and np.all(rc3 > THR_HIGH):
            tags.append("▲报警 连3日>1.2")
        elif lv > THR_HIGH:
            tags.append("△预警 单日>1.2(不发)")
        rc5 = p.RC.tail(CONFIRM_DEEP).values
        if len(rc5) == CONFIRM_DEEP and np.all(rc5 < THR_DEEP):
            tags.append("▼报警 连5日<0.75")
        else:
            _ds = cur_streak(p.RC.dropna(), THR_DEEP, False)
            if _ds > 0:
                tags.append("▽<0.75 进度%d/5" % _ds)
        alarm = ("  " + " / ".join(tags)) if tags else ""
        axp.set_title("%s　R_cascade=%.3f　%s %s" % (nm, lv, st, alarm),
                      fontproperties=fp_t, fontsize=13.5, color="#f0f0f0",
                      loc="left", pad=6)

        ticks, labels, prev = [], [], None
        for k, dt in enumerate(p.index):
            if dt.month != prev:
                ticks.append(k); labels.append(dt.strftime("%y/%m")); prev = dt.month
        for a in (axp, axr):
            for t in ticks:
                a.axvline(t, color="#7c8794", lw=0.85, ls="--", alpha=0.5, zorder=0)
        axp.set_xticks(ticks); axp.set_xticklabels(labels)
        axr.set_xticks(ticks); axr.set_xticklabels(labels)

    fig.suptitle("R_cascade 波动率级联比（平滑版 std%d/std%d）· %s" %
                 (WIN_SHORT, WIN_LONG, " / ".join(names)),
                 fontproperties=fp, fontsize=17, color="#f5f5f5", y=0.997)
    fig.savefig(out, dpi=125, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out


# ═══════════════════════════ 邮件 ═══════════════════════════
def send_email(subject, body_md, img_path, ws=None):
    """发邮件（复用工作区 scripts/send_email.py；找不到就跳过，不抛异常）"""
    import subprocess
    ws = ws or WS
    script = os.path.join(ws, "scripts", "send_email.py")
    if not os.path.exists(script):
        print("[email] 找不到 %s，跳过" % script)
        return False
    cmd = [sys.executable, script, "--subject", subject, "--body", body_md, "--html"]
    if img_path and os.path.exists(img_path):
        cmd += ["--attach", img_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    print("[email]", r.returncode, (r.stdout or "")[-200:], (r.stderr or "")[-300:])
    return r.returncode == 0


def fetch_all(prefer_datahub=True):
    """取三个指数的数据 → {显示名: DataFrame}（失败的只打日志，不中断）"""
    data = {}
    for nm, code, typ, lab in TARGETS:
        try:
            data[nm] = compute(typ, code, prefer_datahub=prefer_datahub)
        except Exception as e:                                   # noqa: BLE001
            print("[ERR] %s(%s): %s" % (nm, code, str(e)[:150]))
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description="R_cascade 波动率级联比（std20/std60）")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--chart", action="store_true", help="出图")
    ap.add_argument("--no-chart", action="store_true",
                    help="兼容旧参数（默认就不出图，给了也不报错）")
    ap.add_argument("--email-alert", action="store_true",
                    help="有正式报警时发邮件（带图）")
    ap.add_argument("--force-email", action="store_true", help="无论是否越线都发邮件")
    ap.add_argument("--json", action="store_true", help="额外打印 JSON 摘要")
    ap.add_argument("--state", default=os.path.join(WS, ".cache", "rcascade_state.json"),
                    help="状态 JSON 落盘路径（默认 .cache/rcascade_state.json）")
    ap.add_argument("--local", action="store_true",
                    help="强制走 akshare 老链路（排障用，跳过 DataHub）")
    a = ap.parse_args(argv)

    data = fetch_all(prefer_datahub=not a.local)
    if not data:
        print("[ERR] 无任何数据")
        return 1
    missing = [n for n, _, _, _ in TARGETS if n not in data]
    if missing:
        print("[WARN] 缺数据:", missing)

    d, rows, alerts, alerts_deep, warns = build(data)

    img = None
    if a.chart:
        try:
            img = draw(data, a.out)
            print("[chart]", img)
        except Exception as e:                                   # noqa: BLE001
            import traceback; traceback.print_exc()
            print("[WARN] 出图失败:", str(e)[:150])

    tb = text_block(rows, d)
    print("\n" + tb)

    payload = {"date": d, "rows": rows, "alerts": alerts,
               "alerts_deep": alerts_deep, "warns": warns, "chart": img}
    try:
        os.makedirs(os.path.dirname(a.state) or ".", exist_ok=True)
        with open(a.state, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:                                       # noqa: BLE001
        print("[WARN] 状态落盘失败:", str(e)[:120])

    if a.json:
        print("\n--- JSON ---")
        print(json.dumps(payload, ensure_ascii=False, indent=1))

    # 只有两种正式报警才发邮件：🔴 连3日>1.2 / 🟣 连5日<0.75
    if a.email_alert or a.force_email:
        if alerts or alerts_deep:
            head = []
            for x in alerts:
                head.append("- 🔴 **%s** R=%.3f ｜ 连续 3 日 >1.2（正式报警）" % (x[0], x[1]))
            for x in alerts_deep:
                head.append("- 🟣 **%s** R=%.3f ｜ 连续 5 日 <0.75（正式报警）" % (x[0], x[1]))
            tags = []
            if alerts:
                tags.append("🔴连续3日>1.2")
            if alerts_deep:
                tags.append("🟣连续5日<0.75")
            names = "、".join([x[0] for x in alerts] + [x[0] for x in alerts_deep])
            subj = "🚨 R_cascade 正式报警 · %s · %s · %s" % (d, "/".join(tags), names)
            body = ("# 🚨 R_cascade 正式报警\n\n"
                    "**%s**  触发指数：\n\n%s\n\n---\n\n%s\n\n**图见附件。**"
                    % (d, "\n".join(head), tb))
            send_email(subj, body, img)
        elif a.force_email:
            subj = "✅ R_cascade 日报 · %s · 无正式报警" % d
            body = ("# ✅ R_cascade 日报\n\n"
                    "三个指数均未达到正式报警条件（连续3日>1.2 / 连续5日<0.75）。"
                    "\n\n---\n\n" + tb)
            send_email(subj, body, img)
        else:
            print("[email] 无正式报警，不发邮件")
    if warns:
        print("[warn] 仅预警（不发邮件）：%s" %
              "、".join("%s %.3f" % (x[0], x[1]) for x in warns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
