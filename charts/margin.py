#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股两融全景图（融资余额 + 融券 + 账户数 + 担保比例 + MACD）
=====================================================================
大佬 2026-11-20 要求：
  「我忘了是哪个任务里边有一个画两融的那个图。还有那个 MACD……
    把它这个两融的这个图放在技术分析汇总这个技能里面。」
  「这东西是要放在数据库里边，是要保存 3 个月的，也就是出图至少要 3 个月内的数据。」

数据层（2026-11-20 起，唯一入口）
---------------------------------
**只读 DataHub EDB**（`data_hub.edb`，category `cn_market`，SQLite 保留 120 天）：

    CN.EQ.MARGIN.BALANCE      融资余额（亿元）
    CN.EQ.MARGIN.SHORT        融券余额（亿元）
    CN.EQ.MARGIN.TOTAL        两融余额（派生 = BALANCE + SHORT）
    CN.EQ.MARGIN.BUY          融资买入额（亿元）
    CN.EQ.MARGIN.SHORTSELL    融券卖出额（亿元）
    CN.EQ.MARGIN.ACCT.INDIV   个人投资者（万名）
    CN.EQ.MARGIN.ACCT.INST    机构投资者（名）
    CN.EQ.MARGIN.ACCT.ACTIVE  参与交易投资者（万名）
    CN.EQ.MARGIN.ACCT.DEBT    有融资融券负债的投资者（万名）
    CN.EQ.MARGIN.COLLATERAL   担保物总价值（亿元）
    CN.EQ.MARGIN.RATIO        平均维持担保比例（%）

本脚本 **不再自己调 akshare**：库里没有对应日期就先 `edb.refresh` 补库，再读库出图。
落库保留期由 `store._PRUNABLE["cn_market"] = 120` 天控制（大佬要求 ≥3 个月）。

MACD 口径：**10/20/5**（本库全库统一口径，禁止 12/26/9），
      DIF = EMA10 − EMA20，DEA = EMA(DIF,5)，柱 = 2×(DIF−DEA)，作用在**融资余额**上。

面板（纵向 6 段，共享 x 轴）：
  ① 融资余额 + MA5/MA20 + MACD(10,20,5)（双轴）
  ② 融券余额
  ③ 融资买入额 vs 融资偿还额 + 单日净买入
  ④ 两融账户存量：个人投资者（万户）+ 有融资融券负债（万人）
       —— 大佬 2026-11-20 指定：这一格就要这两条线，单位统一成「万」同轴可比
  ⑤ 参与交易的投资者（万人）+ 机构投资者（万家，右轴）
  ⑥ 平均维持担保比例（%）+ 担保物总价值（万亿元，右轴）

用法：
  python3 charts/margin.py                     # 近 250 交易日（默认）
  python3 charts/margin.py --days 120          # 3 个月
  python3 charts/margin.py --start 2026-08-01
  python3 charts/margin.py --json              # 只回数值
  python3 charts/margin.py --refresh           # 强制先从源补库再出图
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys

# ---------- 强制 IPv4（容器 IPv6 不通，akshare 走 IPv6 会静默卡死） ----------
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k)
                                      if r[0] == socket.AF_INET]

from charts.common import *  # noqa: F401,F403,E402  (仓库根/sys.path/net_guard/Agg/中文字体)
from charts.common import WS  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 10, 20, 5   # 全库统一口径，勿改

_CN = dict(fontsize=9)

#: 面板要用的 EDB 序列 → 本地列名
_SID = {
    "bal":        "CN.EQ.MARGIN.BALANCE",
    "short":      "CN.EQ.MARGIN.SHORT",
    "buy":        "CN.EQ.MARGIN.BUY",
    "shortsell":  "CN.EQ.MARGIN.SHORTSELL",
    "n_indiv":    "CN.EQ.MARGIN.ACCT.INDIV",
    "n_inst":     "CN.EQ.MARGIN.ACCT.INST",
    "n_active":   "CN.EQ.MARGIN.ACCT.ACTIVE",
    "n_debt":     "CN.EQ.MARGIN.ACCT.DEBT",
    "collateral": "CN.EQ.MARGIN.COLLATERAL",
    "ratio":      "CN.EQ.MARGIN.RATIO",
}


# ============================ 数据层：只走 EDB ============================
def _edb():
    """加载 data_hub.edb；不可用时给出明确指引（不静默退回现场抓数）。"""
    cands = [os.environ.get("DATA_HUB_PATH") or "",
             os.path.join(WS, "optstack"), WS]
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "data_hub")):
            break
    try:
        from data_hub import edb  # noqa: WPS433
        return edb
    except Exception as exc:                                # noqa: BLE001
        raise SystemExit(
            "[margin] 无法加载 data_hub.edb（%s）。\n"
            "两融数据必须走 EDB 数据库，请检查 DATA_HUB_PATH 或 optstack 目录。" % exc)


def ensure_data(edb, days: int, start: str, refresh: bool) -> None:
    """确保库里有数据：库空 或 显式 --refresh 时先补库（落库仍受 120 天保留期约束）。"""
    tail = edb.get_latest(_SID["bal"])
    need = refresh or tail is None or tail[1] is None
    if not need:
        # 库里最新日期超过 5 个自然日也补一次（两融 T+1，容忍周末）
        try:
            last = pd.Timestamp(str(tail[0]))
            need = (pd.Timestamp.now().normalize() - last).days > 5
        except Exception:                                   # noqa: BLE001
            need = True
    if not need:
        return
    ids = [v for v in _SID.values()]
    from data_hub.edb import api as _api
    rep = _api.refresh(ids, force=True)
    if rep.get("failed"):
        print("[margin] 补库部分失败：%s" % rep["failed"], file=sys.stderr)


def fetch(days: int = 250, start: str = "", refresh: bool = False) -> pd.DataFrame:
    """从 EDB 读 10 条两融序列，拼成一张宽表（date 升序）。

    Args:
        days: 最近 N 个交易日；start 非空时忽略。
        start: 起始日 ``YYYY-MM-DD``。
        refresh: True 时强制先从数据源补库再读。

    Returns:
        DataFrame，列 ``d`` + :data:`_SID` 的 key。
    """
    edb = _edb()
    ensure_data(edb, days, start, refresh)

    frames = {}
    for key, sid in _SID.items():
        s = edb.get_series(sid)
        if s is None or not len(s):
            raise SystemExit("[margin] EDB 缺少序列 %s，先跑 --refresh" % sid)
        s = s.copy()
        s["date"] = s["date"].astype(str)
        frames[key] = s.set_index("date")["value"].astype(float)
    df = pd.DataFrame(frames).sort_index().reset_index()
    date_col = df.columns[0]
    df = df.rename(columns={date_col: "d"})
    df["d"] = pd.to_datetime(df["d"], errors="coerce")
    df = df.dropna(subset=["d"]).sort_values("d").reset_index(drop=True)

    # 派生：两融余额（用 EDB 的 TOTAL 更稳，缺了再自己加）
    try:
        tot = edb.get_series(_SID_BAL_TOTAL)
        if tot is not None and len(tot):
            tot = tot.copy()
            tot["date"] = tot["date"].astype(str)
            m = dict(zip(tot["date"], tot["value"].astype(float)))
            df["total"] = df["d"].dt.strftime("%Y-%m-%d").map(m)
        else:
            df["total"] = df["bal"] + df["short"]
    except Exception:                                       # noqa: BLE001
        df["total"] = df["bal"] + df["short"]
    df["total"] = df["total"].fillna(df["bal"] + df["short"])

    if start:
        df = df[df["d"] >= pd.Timestamp(start)].reset_index(drop=True)
    if days and len(df) > days:
        df = df.tail(days).reset_index(drop=True)
    return df


_SID_BAL_TOTAL = "CN.EQ.MARGIN.TOTAL"


def macd_series(s: pd.Series):
    """MACD(10,20,5)，与 chan_smc_index.macd_hist 同口径。"""
    dif = (s.ewm(span=MACD_FAST, adjust=False).mean()
           - s.ewm(span=MACD_SLOW, adjust=False).mean())
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    hist = 2 * (dif - dea)
    return dif, dea, hist


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    last, prev = df.iloc[-1], (df.iloc[-2] if len(df) > 1 else df.iloc[-1])
    dif, dea, hist = macd_series(df["bal"])

    def pct5(col):
        v = df[col].dropna()
        if len(v) < 6 or not v.iloc[-6]:
            return None
        return round((v.iloc[-1] / v.iloc[-6] - 1) * 100, 2)

    return {
        "date": last["d"].strftime("%Y-%m-%d"),
        "rzye": round(float(last["bal"]), 2),
        "rqye": round(float(last["short"]), 2),
        "rzrqye": round(float(last["total"]), 2),
        "rzye_d1": round(float(last["bal"] - prev["bal"]), 2),
        "rzye_w1_pct": pct5("bal"),
        "rzmre": round(float(last["buy"]), 2),
        "net_buy": round(float(last["bal"] - prev["bal"]), 2),   # 融资净买入 = Δ融资余额
        "n_indiv": round(float(last["n_indiv"]), 2),
        "n_inst": round(float(last["n_inst"]), 2),
        "n_debt": round(float(last["n_debt"]), 2),
        "n_active": round(float(last["n_active"]), 2),
        "ratio": round(float(last["ratio"]), 2),
        "macd_dif": round(float(dif.iloc[-1]), 2),
        "macd_dea": round(float(dea.iloc[-1]), 2),
        "macd_hist": round(float(hist.iloc[-1]), 2),
        "macd_hist_prev": round(float(hist.iloc[-2]), 2) if len(hist) > 1 else None,
        "bars": int(len(df)),
        "source": "edb:cn_market",
    }


# ============================ 出图 ============================
def plot(d: pd.DataFrame, out: str, title_extra: str = "") -> str:
    dif, dea, hist = macd_series(d["bal"])
    x = d["d"]
    fig = plt.figure(figsize=(15, 17))
    gs = fig.add_gridspec(6, 1, height_ratios=[3.0, 1.0, 1.4, 1.5, 1.1, 1.4],
                          hspace=0.10)

    UP, DN = "#c0392b", "#1e8449"

    # ---------- ① 融资余额 + MACD ----------
    ax1 = fig.add_subplot(gs[0])
    ax1.plot(x, d["bal"], color="#c0392b", lw=2.0, label="融资余额（亿）")
    ax1.plot(x, d["bal"].rolling(5).mean(), color="#e67e22", lw=1.1, ls="--", label="MA5")
    ax1.plot(x, d["bal"].rolling(20).mean(), color="#2c3e50", lw=1.1, ls=":", label="MA20")
    ax1b = ax1.twinx()
    cols = [UP if v >= 0 else DN for v in hist]
    ax1b.bar(x, hist, color=cols, alpha=0.45, width=0.85, label="MACD 柱")
    ax1b.plot(x, dif, color="#e67e22", lw=1.0, label="DIF")
    ax1b.plot(x, dea, color="#34495e", lw=1.0, label="DEA")
    ax1b.set_ylabel("MACD(10,20,5)", **_CN)
    ax1.set_ylabel("融资余额（亿元）", **_CN)
    ax1.set_title("A股两融全景 · %s ~ %s%s（融资余额 + MACD(10,20,5)）"
                  % (d["d"].iloc[0].strftime("%Y-%m-%d"),
                     d["d"].iloc[-1].strftime("%Y-%m-%d"),
                     (" · " + title_extra) if title_extra else ""),
                  fontsize=15, pad=12)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1b.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", ncol=5, fontsize=9, framealpha=0.9)
    ax1.grid(alpha=0.25)

    last = d.iloc[-1]
    ax1.annotate("%.0f亿" % last["bal"], xy=(last["d"], last["bal"]),
                 xytext=(-8, 10), textcoords="offset points",
                 fontsize=10, color="#c0392b", fontweight="bold")

    # ---------- ② 融券余额 ----------
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax2.fill_between(x, 0, d["short"], color="#8e44ad", alpha=0.40, lw=0)
    ax2.plot(x, d["short"], color="#6c3483", lw=1.3, label="融券余额（亿）")
    ax2.set_ylabel("融券余额\n（亿元）", **_CN)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(alpha=0.2)

    # ---------- ③ 融资买入 vs 偿还 + 净买入 ----------
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    repay = d["buy"] - d["bal"].diff().fillna(0)     # 买入 − Δ余额 = 偿还
    net = d["buy"] - repay
    ax3.bar(x, net, color=[UP if v >= 0 else DN for v in net],
            alpha=0.55, width=0.85, label="单日净买入（亿）")
    ax3.plot(x, d["buy"], color="#d35400", lw=1.0, label="融资买入额")
    ax3.plot(x, repay, color="#16a085", lw=1.0, label="融资偿还额")
    ax3.axhline(0, color="#555", lw=0.8)
    ax3.set_ylabel("买入/偿还\n净买入（亿元）", **_CN)
    ax3.legend(loc="upper left", ncol=3, fontsize=9)
    ax3.grid(alpha=0.2)

    # ---------- ④ 两融账户存量：个人投资者 + 有融资融券负债 ----------
    # 大佬 2026-11-20：「个人投资者数量、有融资融券负债的数量」放这一格。
    # 两者量级差 4 倍（847.9 万户 vs 198.8 万人），若共用一根轴、且轴范围被
    # 强行拉宽（之前下延到 100），两条线会各自压成一条几乎水平的直线，
    # 完全看不出形态 —— 所以用**双轴、各自自适应范围**（对齐大佬发来的图：
    # 左轴 ~780-850，右轴 ~185-205），颜色与左右轴一一对应。
    ax4 = fig.add_subplot(gs[3], sharex=ax1)
    ax4.plot(x, d["n_indiv"], color="#1f6fb4", lw=1.9,
             label="个人投资者数量（万户，左轴）")
    ax4.set_ylabel("个人投资者\n（万户）", **_CN, color="#1f6fb4")
    ax4.tick_params(axis="y", colors="#1f6fb4")

    ax4r = ax4.twinx()
    ax4r.plot(x, d["n_debt"], color="#c0392b", lw=1.9,
              label="有融资融券负债的投资者（万人，右轴）")
    ax4r.set_ylabel("有融资融券负债\n（万人）", **_CN, color="#c0392b")
    ax4r.tick_params(axis="y", colors="#c0392b")

    # 各自按自身数据留 8% 边距（不做跨轴共享范围，否则两条线都会被压平）
    def _tight(ax, s):
        lo, hi = float(s.min()), float(s.max())
        pad = (hi - lo) * 0.08 or max(abs(hi) * 0.01, 1.0)
        ax.set_ylim(lo - pad, hi + pad)
    _tight(ax4, d["n_indiv"])
    _tight(ax4r, d["n_debt"])

    h4, l4 = ax4.get_legend_handles_labels()
    h4r, l4r = ax4r.get_legend_handles_labels()
    ax4.legend(h4 + h4r, l4 + l4r, loc="upper left", ncol=2, fontsize=9.5,
               framealpha=0.9)
    ax4.grid(alpha=0.2)

    # 末端数值标注（对齐大佬发来的样式：「个人 847.9万户 | 有负债 198.8万人」）
    _li, _ld = d["n_indiv"].iloc[-1], d["n_debt"].iloc[-1]
    ax4.annotate("%.1f万户" % _li, xy=(x.iloc[-1], _li), xytext=(-6, 9),
                 textcoords="offset points", ha="right", fontsize=9,
                 color="#1f6fb4", fontweight="bold")
    ax4r.annotate("%.1f万人" % _ld, xy=(x.iloc[-1], _ld), xytext=(-6, -15),
                  textcoords="offset points", ha="right", fontsize=9,
                  color="#c0392b", fontweight="bold")
    ax4.text(0.012, 0.06, "个人 %.1f万户 ｜ 有负债 %.1f万人 ｜ 负债账户占比 %.1f%%"
             % (_li, _ld, _ld / _li * 100 if _li else 0),
             transform=ax4.transAxes, fontsize=10, color="#333",
             bbox=dict(facecolor="white", alpha=0.78, edgecolor="#ccc", lw=0.6))

    # ---------- ⑤ 两融账户活跃度：参与交易（万人）+ 机构（名，右轴）----------
    ax4b = fig.add_subplot(gs[4], sharex=ax1)
    ax4b.fill_between(x, 0, d["n_active"], color="#7f8c8d", alpha=0.30, lw=0)
    ax4b.plot(x, d["n_active"], color="#4d5656", lw=1.5,
              label="参与交易的投资者（万人）")
    ax4b.set_ylabel("参与交易\n（万人）", **_CN)
    ax4br = ax4b.twinx()
    ax4br.plot(x, d["n_inst"] / 1e4, color="#b9770e", lw=1.4, ls="--",
               label="机构投资者（万家，右轴）")
    ax4br.set_ylabel("机构投资者（万家）", **_CN)
    h4b, l4b = ax4b.get_legend_handles_labels()
    h4br, l4br = ax4br.get_legend_handles_labels()
    ax4b.legend(h4b + h4br, l4b + l4br, loc="upper left", ncol=2, fontsize=9)
    ax4b.grid(alpha=0.2)

    # ---------- ⑥ 平均维持担保比例 + 担保物 ----------
    ax5 = fig.add_subplot(gs[5], sharex=ax1)
    ax5.plot(x, d["ratio"], color="#c0392b", lw=1.8, label="平均维持担保比例（%）")
    ax5.axhline(150, color="#e67e22", ls="--", lw=1.0, label="150% 警戒")
    ax5.axhline(130, color="#c0392b", ls="--", lw=1.0, label="130% 平仓")
    ax5.set_ylabel("维持担保\n比例（%）", **_CN)
    ax5r = ax5.twinx()
    ax5r.fill_between(x, 0, d["collateral"] / 1e4, color="#5dade2", alpha=0.35, lw=0,
                      label="担保物总价值（万亿元，右轴）")
    ax5r.set_ylabel("担保物（万亿元）", **_CN)
    h5, l5 = ax5.get_legend_handles_labels()
    h5r, l5r = ax5r.get_legend_handles_labels()
    ax5.legend(h5 + h5r, l5 + l5r, loc="upper left", ncol=4, fontsize=9)
    ax5.grid(alpha=0.2)
    ax5.set_xlabel("")

    for ax in (ax1, ax2, ax3, ax4, ax4b, ax5):
        ax.tick_params(axis="x", labelrotation=0, labelsize=8)

    fig.text(0.5, 0.005,
             "数据源：DataHub EDB · cn_market（CN.EQ.MARGIN.*，只读数据库，保留 120 天）｜"
             "MACD 参数 10/20/5（本库统一口径）｜"
             "净买入 = 融资买入额 − 融资偿还额，偿还额 = 买入额 − Δ融资余额",
             ha="center", fontsize=8.5, color="#555")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    fig.savefig(out, dpi=130, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return out


def render_text(s: dict) -> str:
    if not s:
        return "【两融】无数据"
    arrow = "↑" if s["rzye_d1"] > 0 else ("↓" if s["rzye_d1"] < 0 else "→")
    h = s.get("macd_hist") or 0
    hp = s.get("macd_hist_prev") or 0
    mtag = ("红柱放大" if h > 0 and h > hp else "绿柱扩大" if h < 0 and h < hp
            else "红柱缩量" if h > 0 else "绿柱收敛")
    return "\n".join([
        "💰 A股两融（%s 口径 · EDB cn_market）" % s["date"],
        "· 融资余额 %.0f亿（日 %+.0f亿 %s｜5日 %s%%）" % (
            s["rzye"], s["rzye_d1"], arrow,
            ("%+.2f" % s["rzye_w1_pct"]) if s["rzye_w1_pct"] is not None else "—"),
        "· 融券余额 %.0f亿 ｜ 两融合计 %.0f亿" % (s["rqye"], s["rzrqye"]),
        "· 融资买入 %.0f亿 ｜ 当日净买入 %+.0f亿" % (s["rzmre"], s["net_buy"]),
        "· 两融账户：个人 %.1f万名 / 机构 %.0f名 / 有负债 %.1f万名 / 参与交易 %.1f万名" % (
            s["n_indiv"], s["n_inst"], s["n_debt"], s["n_active"]),
        "· 平均维持担保比例 %.1f%%" % s["ratio"],
        "· MACD(10,20,5)：DIF %.1f / DEA %.1f / 柱 %.1f（%s）" % (
            s["macd_dif"], s["macd_dea"], s["macd_hist"], mtag),
    ])


def main():
    ap = argparse.ArgumentParser(
        description="A股两融全景图（数据只读 EDB，融资余额/融券/账户/担保比例 + MACD10,20,5）")
    ap.add_argument("--days", type=int, default=250, help="最近 N 个交易日（默认 250；>=60 建议）")
    ap.add_argument("--start", default="", help="起始日 YYYY-MM-DD（给了就忽略 --days 上限）")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="强制先从数据源补库再出图")
    a = ap.parse_args()

    d = fetch(days=(0 if a.start else a.days), start=a.start, refresh=a.refresh)
    if d.empty:
        raise SystemExit("[margin] EDB 无数据")
    s = summary(d)
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return s

    out = a.out or os.path.join(WS, "reports", "charts", "margin",
                                "两融全景_%s.png" % d["d"].iloc[-1].strftime("%Y%m%d"))
    p = plot(d, out)
    print(render_text(s))
    print("[margin] 图 → %s" % p)
    return s


if __name__ == "__main__":
    main()
