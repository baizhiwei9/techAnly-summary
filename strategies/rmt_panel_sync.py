#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RMT 面板增量同步：~/.cache/rmt_panel/close_YYYYMMDD.csv（全市场逐日收盘）

为什么要有这个
--------------
`charts/rmt_rc.py` 原来用 DataHub `fetch_stock_close_panel` 按股票拉 1812 只，
baostock 串行 + 四源轮询实测 350s 才跑 110/453 只，跑一次要十几分钟，日报顶不住。
tushare `pro.daily(trade_date=...)` 一次给全市场 5500+ 行、单日 ~10s，
按「日」拉比按「股」拉快一个数量级，而且天然能增量（只补缺的日期）。

口径
----
· 只存 ts_code / close / pct_chg / vol（和 bin/five_index_topo.py 的 topo5 面板同口径）
· 前复权不一致问题：这里存的是**未复权收盘**，RMT 用**收益率**，
  单日除权会在收益率上出现跳空 → 对相关矩阵有轻微污染，但成分股除权日分散、影响有限；
  需要严格时用 --adjust（跳过，走 DataHub 前复权），默认不开。
· 默认保留最近 --keep 个交易日文件（默认 500），够 60 日窗口 + 420 天分位基准。

用法
----
    python3 strategies/rmt_panel_sync.py                # 补齐最近 500 个交易日
    python3 strategies/rmt_panel_sync.py --days 260     # 只补最近 260 个
    python3 strategies/rmt_panel_sync.py --workers 5    # 并发源数（默认 5，tushare token 全局 10 IP 限制）
"""
import os
import socket
import sys
import glob
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# 全局硬规矩：对外接口强制 IPv4（容器 IPv6 不通会静默卡死）
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

WS = os.path.expanduser("~/.openclaw/workspace")
PANEL_DIR = os.environ.get("RMT_PANEL_DIR") or os.path.expanduser("~/.cache/rmt_panel")


def _pro():
    sys.path.insert(0, os.path.join(WS, "bin"))
    from tushare_init import get_pro
    return get_pro()


def sync(days=500, workers=5, panel_dir=PANEL_DIR, keep=500, verbose=True):
    """补齐最近 days 个交易日的全市场收盘 CSV，返回新增文件数。"""
    os.makedirs(panel_dir, exist_ok=True)
    pro = _pro()
    end = datetime.now().strftime("%Y%m%d")
    start = datetime.now().replace(year=datetime.now().year - 3).strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
    all_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    want = all_days[-days:]

    todo = [d for d in want
            if not (os.path.exists(os.path.join(panel_dir, "close_%s.csv" % d))
                    and os.path.getsize(os.path.join(panel_dir, "close_%s.csv" % d)) > 1000)]
    if verbose:
        print("[rmt-panel] 目标 %d 日，缺 %d 日，并发 %d" % (len(want), len(todo), workers),
              flush=True)

    def one(d):
        fp = os.path.join(panel_dir, "close_%s.csv" % d)
        last = ""
        for _try in range(3):
            try:
                df = pro.daily(trade_date=d)
                if df is None or df.empty:
                    return (d, 0, "empty")
                df[["ts_code", "close", "pct_chg", "vol"]].to_csv(fp, index=False)
                return (d, len(df), "ok")
            except Exception as e:                                # noqa: BLE001
                last = str(e)[:70]
                time.sleep(1.5 * (_try + 1))
        return (d, 0, "ERR " + last)

    added, failed = 0, []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (d, n, st) in enumerate(ex.map(one, todo), 1):
            if st == "ok":
                added += 1
            else:
                failed.append((d, st))
            if verbose and (i % 10 == 0 or i == len(todo)):
                print("[rmt-panel] %d/%d  +%d  失败%d  %.0fs"
                      % (i, len(todo), added, len(failed), time.time() - t0), flush=True)

    # 修剪旧文件
    fs = sorted(glob.glob(os.path.join(panel_dir, "close_*.csv")))
    for f in fs[:max(0, len(fs) - keep)]:
        try:
            os.remove(f)
        except Exception:
            pass

    if verbose:
        print("[rmt-panel] 完成：新增 %d，失败 %d，总耗时 %.0fs"
              % (added, len(failed), time.time() - t0), flush=True)
        for d, st in failed[:5]:
            print("   ! %s %s" % (d, st), flush=True)
    return added


def load_panel(panel_dir=PANEL_DIR, days=500):
    """读面板 → 宽表 DataFrame(index=日期 str, columns=ts_code, values=close)。"""
    import pandas as pd
    fs = sorted(glob.glob(os.path.join(panel_dir, "close_*.csv")))[-days:]
    if not fs:
        raise RuntimeError("面板为空：%s（先跑 bin/rmt_panel_sync.py）" % panel_dir)
    rows = []
    for f in fs:
        d = os.path.basename(f).replace("close_", "").replace(".csv", "")
        try:
            df = pd.read_csv(f, usecols=["ts_code", "close"])
        except Exception:
            continue
        if df.empty:
            continue
        s = df.drop_duplicates("ts_code").set_index("ts_code")["close"]
        s.name = d
        rows.append(s)
    out = pd.concat(rows, axis=1).T.sort_index()
    out.index.name = "trade_date"
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=500)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--keep", type=int, default=500)
    ap.add_argument("--dir", default=PANEL_DIR)
    a = ap.parse_args()
    sync(days=a.days, workers=a.workers, panel_dir=a.dir, keep=a.keep)
