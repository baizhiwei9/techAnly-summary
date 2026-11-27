# -*- coding: utf-8 -*-
"""A股两融图：融资/融券余额 + MACD + 两融账户数 + 维持担保比例
====================================================================
数据源（四级降级，只读不写库）：
  Tier1 AKShare  stock_margin_account_info()  一份拿全：两市融资/融券余额、
                融资买入额、个人/机构投资者数量、有负债投资者数量、维持担保比例
  Tier2 Tushare 代理 ttx.dailyfetch.top  margin 接口（只有余额，账户数据缺）

用法：
  python3 misc/engine/margin_chart.py                      # 近 250 个交易日
  python3 misc/engine/margin_chart.py --days 500 --out ./x.png
  python3 misc/engine/margin_chart.py --json               # 只打摘要不画图

口径备忘（2026-11-20）：
  · 融资余额单位「亿元」，为沪深两市合计（交易所汇总口径，含北交所）
  · 个人投资者数量单位「万户」；有融资融券负债的投资者数量单位「名」
  · MACD 参数 (10,20,5)，与趋势浪子体系一致；作用于「融资余额」序列
  · 全流程纯 Python，0 token
"""
import os, sys, json, argparse, socket

# 容器 IPv6 不通会静默卡死，必须先禁
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as _fm
for _f in ["/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
           "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]:
    if os.path.exists(_f):
        try:
            _fm.fontManager.addfont(_f)
            matplotlib.rcParams["font.sans-serif"] = [_fm.FontProperties(fname=_f).get_name(),
                                                      "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            pass
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

RED, GRN, BLUE, ORANGE, GRAY = "#d62728", "#2ca02c", "#1f77b4", "#ff7f0e", "#7f7f7f"
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 10, 20, 5


# ---------------- 取数 ----------------
def _from_akshare():
    import akshare as ak
    df = ak.stock_margin_account_info()
    if df is None or df.empty:
        return None
    d = pd.DataFrame({
        "dt": pd.to_datetime(df["日期"].astype(str), errors="coerce"),
        "rzye": pd.to_numeric(df["融资余额"], errors="coerce"),      # 亿元
        "rqye": pd.to_numeric(df["融券余额"], errors="coerce"),      # 亿元
        "rzmre": pd.to_numeric(df["融资买入额"], errors="coerce"),   # 亿元
        "n_person": pd.to_numeric(df["个人投资者数量"], errors="coerce"),  # 万户
        "n_inst": pd.to_numeric(df["机构投资者数量"], errors="coerce"),    # 名
        "n_debt": pd.to_numeric(df["有融资融券负债的投资者数量"], errors="coerce"),  # 名
        "n_trade": pd.to_numeric(df["参与交易的投资者数量"], errors="coerce"),      # 名
        "ratio": pd.to_numeric(df["平均维持担保比例"], errors="coerce"),   # %
    }).dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    d.attrs["source"] = "akshare:stock_margin_account_info"
    return d


def _from_tushare(days=1200):
    """降级：只有余额，账户类字段为空。"""
    try:
        sys.path.insert(0, "/opt/coze/.fuse_drive/dataHub_三级体系/options/config")
        from credentials import TS_PROXY_TOKEN
        import tushare as ts
        pro = ts.pro_api(TS_PROXY_TOKEN)
        try:
            pro._DataApi__http_url = "https://ttx.dailyfetch.top/"
        except Exception:
            pass
        end = pd.Timestamp.today()
        start = end - pd.Timedelta(days=int(days * 1.7) + 30)
        df = pro.margin(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
        if df is None or df.empty:
            return None
        df = df[df["exchange_id"].isin(["SSE", "SZSE", "BSE"])]
        g = df.groupby("trade_date").agg(
            rzye=("rzye", "sum"), rqye=("rqye", "sum"), rzmre=("rzmre", "sum")).reset_index()
        d = pd.DataFrame({
            "dt": pd.to_datetime(g["trade_date"], format="%Y%m%d", errors="coerce"),
            "rzye": g["rzye"] / 1e8, "rqye": g["rqye"] / 1e8, "rzmre": g["rzmre"] / 1e8,
        }).sort_values("dt").reset_index(drop=True)
        for c in ("n_person", "n_inst", "n_debt", "n_trade", "ratio"):
            d[c] = np.nan
        d.attrs["source"] = "tushare:margin(proxy)"
        return d
    except Exception as e:
        print("[warn] tushare 降级失败: %s: %s" % (type(e).__name__, e))
        return None


def fetch(days=250):
    d = None
    try:
        d = _from_akshare()
    except Exception as e:
        print("[warn] akshare 取数失败: %s: %s" % (type(e).__name__, e))
    if d is None or d.empty:
        d = _from_tushare(days)
    if d is None or d.empty:
        raise SystemExit("[error] 两融数据全部取数失败，不出图")
    src = d.attrs.get("source", "")
    d = d.tail(int(days)).reset_index(drop=True)
    d.attrs["source"] = src
    return d


# ---------------- 指标 ----------------
def macd(series, fast=MACD_FAST, slow=MACD_SLOW, sig=MACD_SIGNAL):
    dif = series.ewm(span=fast, adjust=False).mean() - series.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=sig, adjust=False).mean()
    return dif, dea, 2 * (dif - dea)


def summarize(d):
    last = d.iloc[-1]
    out = {
        "date": last["dt"].strftime("%Y-%m-%d"),
        "rzye_yi": round(float(last["rzye"]), 1),
        "rzye_wan_yi": round(float(last["rzye"]) / 10000, 3),
        "rqye_yi": round(float(last["rqye"]), 1),
        "rzrqye_yi": round(float(last["rzye"] + last["rqye"]), 1),
        "rzmre_yi": None if pd.isna(last["rzmre"]) else round(float(last["rzmre"]), 1),
        "source": d.attrs.get("source", ""),
        "window": "%s ~ %s (%d bars)" % (d["dt"].iloc[0].strftime("%Y-%m-%d"),
                                         last["dt"].strftime("%Y-%m-%d"), len(d)),
    }
    # 分位（近窗口内）
    def pct(col):
        s = pd.to_numeric(d[col], errors="coerce").dropna()
        if len(s) < 20 or pd.isna(last[col]):
            return None
        return round(float((s <= last[col]).mean() * 100), 1)
    out["rzye_pct"] = pct("rzye")
    out["ratio"] = None if pd.isna(last["ratio"]) else round(float(last["ratio"]), 2)
    out["ratio_pct"] = pct("ratio")
    out["n_person_wan"] = None if pd.isna(last["n_person"]) else round(float(last["n_person"]), 1)
    out["n_debt_wan"] = None if pd.isna(last["n_debt"]) else round(float(last["n_debt"]) / 10000, 1)
    out["n_trade_wan"] = None if pd.isna(last["n_trade"]) else round(float(last["n_trade"]) / 10000, 1)
    # 融资余额 20 日变化
    if len(d) > 20:
        prev = float(d["rzye"].iloc[-21])
        out["rzye_chg20_yi"] = round(float(last["rzye"]) - prev, 1)
        out["rzye_chg20_pct"] = round((float(last["rzye"]) / prev - 1) * 100, 2)
    dif, dea, hist = macd(d["rzye"].astype(float))
    out["macd_dif"] = round(float(dif.iloc[-1]), 2)
    out["macd_dea"] = round(float(dea.iloc[-1]), 2)
    out["macd_hist"] = round(float(hist.iloc[-1]), 2)
    out["macd_state"] = ("金叉·多头" if dif.iloc[-1] > dea.iloc[-1] else "死叉·空头")
    # 交叉最近发生日
    cross = None
    for i in range(len(d) - 1, 0, -1):
        if (dif.iloc[i] - dea.iloc[i]) * (dif.iloc[i - 1] - dea.iloc[i - 1]) < 0:
            cross = d["dt"].iloc[i].strftime("%Y-%m-%d")
            break
    out["macd_last_cross"] = cross
    return out


# ---------------- 画图 ----------------
def draw(d, out_png, title_tag=""):
    n = len(d)
    x = np.arange(n)
    dt = d["dt"]
    rzye = d["rzye"].astype(float)
    rqye = d["rqye"].astype(float)
    ma20 = rzye.rolling(20).mean()
    ma60 = rzye.rolling(60).mean()
    dif, dea, hist = macd(rzye)
    sm = summarize(d)

    fig = plt.figure(figsize=(14.5, 13.2), facecolor="white")
    gs = GridSpec(4, 1, height_ratios=[2.4, 1.0, 1.25, 1.05], hspace=0.10,
                  left=0.065, right=0.945, top=0.945, bottom=0.045)

    # ── 面板1：融资余额 + 融券余额 ──
    ax1 = fig.add_subplot(gs[0])
    ax1.plot(x, rzye / 10000, color=RED, lw=1.9, label="融资余额(万亿)")
    ax1.plot(x, ma20 / 10000, color=ORANGE, lw=1.1, ls="--", label="MA20")
    ax1.plot(x, ma60 / 10000, color=BLUE, lw=1.1, ls="--", label="MA60")
    ax1.set_ylabel("融资余额（万亿元）", fontsize=10.5)
    ax1.grid(alpha=0.25, ls=":")
    ax1b = ax1.twinx()
    ax1b.plot(x, rqye, color=GRN, lw=1.0, alpha=0.55, label="融券余额(亿)")
    ax1b.set_ylabel("融券余额（亿元）", color=GRN, fontsize=10)
    ax1b.tick_params(axis="y", colors=GRN)
    ax1.set_title("%s A股两融全景 · 融资/融券余额 + MACD(10,20,5) + 两融账户 + 维持担保比例\n"
                  "数据至 %s ｜ 融资余额 %.2f万亿（近%d日分位%.1f%%）｜ 两融余额 %.2f万亿 ｜ 来源 %s"
                  % (title_tag, sm["date"], sm["rzye_yi"] / 10000, n,
                     (sm["rzye_pct"] if sm["rzye_pct"] is not None else 0.0),
                     sm["rzrqye_yi"] / 10000, sm["source"]),
                  fontsize=12.5, fontweight="bold", loc="left", pad=12)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1b.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=9, ncol=4, framealpha=0.9)
    # 最新值标注
    ax1.annotate("%.2f万亿" % (rzye.iloc[-1] / 10000), xy=(n - 1, rzye.iloc[-1] / 10000),
                 xytext=(-8, 14), textcoords="offset points", ha="right",
                 fontsize=10, color=RED, fontweight="bold",
                 bbox=dict(boxstyle="round,pad=0.22", fc="white", ec=RED, alpha=0.92, lw=0.9))

    # ── 面板2：MACD ──
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    bw = 0.62
    ax2.bar(x, hist.values, color=[RED if v >= 0 else GRN for v in hist.values],
            width=bw, alpha=0.65, label="MACD柱")
    ax2.plot(x, dif.values, color=ORANGE, lw=1.2, label="DIF")
    ax2.plot(x, dea.values, color=BLUE, lw=1.2, label="DEA")
    ax2.axhline(0, color=GRAY, lw=0.7)
    ax2.set_ylabel("MACD(10,20,5)\n融资余额动量", fontsize=9.5)
    ax2.grid(alpha=0.22, ls=":")
    ax2.legend(loc="upper left", fontsize=8.6, ncol=3, framealpha=0.9)
    ax2.annotate("%s ｜ 最近交叉 %s" % (sm["macd_state"], sm["macd_last_cross"] or "—"),
                 xy=(0.995, 0.06), xycoords="axes fraction", ha="right", fontsize=9.2,
                 color=(RED if "多头" in sm["macd_state"] else GRN), fontweight="bold")

    # ── 面板3：两融账户数 ──
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    if d["n_person"].notna().any():
        ax3.plot(x, d["n_person"].astype(float), color=BLUE, lw=1.7, label="个人投资者数量(万户)")
        ax3.set_ylabel("个人投资者（万户）", color=BLUE, fontsize=10)
        ax3.tick_params(axis="y", colors=BLUE)
        ax3.grid(alpha=0.22, ls=":")
        ax3b = ax3.twinx()
        ax3b.plot(x, d["n_debt"].astype(float) / 10000, color=RED, lw=1.5,
                  label="有融资融券负债的投资者(万人)")
        ax3b.set_ylabel("有负债投资者（万人）", color=RED, fontsize=10)
        ax3b.tick_params(axis="y", colors=RED)
        h3, l3 = ax3.get_legend_handles_labels()
        h4, l4 = ax3b.get_legend_handles_labels()
        ax3.legend(h3 + h4, l3 + l4, loc="upper left", fontsize=9, framealpha=0.9)
        if not pd.isna(d["n_person"].iloc[-1]):
            ax3.annotate("个人 %.1f万户 ｜ 有负债 %.1f万人"
                         % (d["n_person"].iloc[-1], d["n_debt"].iloc[-1] / 10000),
                         xy=(0.995, 0.10), xycoords="axes fraction", ha="right",
                         fontsize=9.2, color=GRAY, fontweight="bold")
    else:
        ax3.text(0.5, 0.5, "账户数据不可用（降级数据源无此字段）", ha="center", va="center",
                 transform=ax3.transAxes, fontsize=11, color=GRAY)

    # ── 面板4：平均维持担保比例 ──
    ax4 = fig.add_subplot(gs[3], sharex=ax1)
    if d["ratio"].notna().any():
        _rs = d["ratio"].astype(float).dropna()
        ax4.plot(x, d["ratio"].astype(float), color="#8e44ad", lw=1.6, label="平均维持担保比例(%)")
        for _y, _lab, _c in ((300, "300 安全线", GRAY), (250, "250 警戒线", ORANGE)):
            ax4.axhline(_y, color=_c, lw=0.9, ls="--", alpha=0.85)
            ax4.annotate(_lab, xy=(0.008, _y), xycoords=("axes fraction", "data"),
                         xytext=(0, 3), textcoords="offset points",
                         fontsize=8.2, color=_c, va="bottom", fontweight="bold")
        ax4.set_ylim(min(float(_rs.min()) - 10, 242), max(float(_rs.max()) + 10, 306))
        ax4.set_ylabel("维持担保比例（%）", fontsize=10)
        ax4.grid(alpha=0.22, ls=":")
        ax4.legend(loc="upper left", fontsize=8.8, framealpha=0.9)
        if not pd.isna(d["ratio"].iloc[-1]):
            ax4.annotate("%.1f%%（近%d日分位%.1f%%）"
                         % (d["ratio"].iloc[-1], n,
                            (sm["ratio_pct"] if sm["ratio_pct"] is not None else 0.0)),
                         xy=(0.995, 0.88), xycoords="axes fraction", ha="right",
                         fontsize=9.8, color="#8e44ad", fontweight="bold",
                         bbox=dict(boxstyle="round,pad=0.24", fc="white",
                                   ec="#8e44ad", alpha=0.92, lw=0.9))
    else:
        ax4.text(0.5, 0.5, "维持担保比例不可用", ha="center", va="center",
                 transform=ax4.transAxes, fontsize=11, color=GRAY)

    # X 轴刻度（只给最下面一个）
    for ax in (ax1, ax2, ax3):
        ax.tick_params(labelbottom=False)
    step = max(1, n // 12)
    ticks = list(range(0, n, step))
    ax4.set_xticks(ticks)
    ax4.set_xticklabels([dt.iloc[i].strftime("%y-%m-%d") for i in ticks], fontsize=9)
    for ax in (ax1, ax2, ax3, ax4):
        ax.set_xlim(-1, n)
        for s in ("top",):
            ax.spines[s].set_visible(False)

    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    fig.savefig(out_png, dpi=130, facecolor="white")
    plt.close(fig)
    return out_png, sm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true", help="只输出摘要 JSON，不画图")
    a = ap.parse_args()

    d = fetch(a.days)
    sm = summarize(d)
    if a.json:
        print(json.dumps(sm, ensure_ascii=False, indent=2))
        return 0
    out = a.out or os.path.join(os.getcwd(), "margin_chart_%s.png"
                                % pd.Timestamp.today().strftime("%Y%m%d"))
    p, sm = draw(d, out)
    print(json.dumps(sm, ensure_ascii=False, indent=2))
    print("[ok] 图已生成: %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
