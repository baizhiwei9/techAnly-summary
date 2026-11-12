#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 模拟交易引擎 (h1 级别)
================================================================
对每个标的：
  1) 数据引擎取 60m 母数据（1 年）
  2) 结构回测/推演（变体 A 子级转折为主）得到 状态 + 事件流
  3) 增量写库：新事件（开仓/平仓/减半/加回）入库并标记未通知
  4) 累积权益曲线入库

用法：
  python3 smc_engine.py                 # 全标的推演 + 落库
  python3 smc_engine.py --notify        # 落库后发增量通知
  python3 smc_engine.py --refresh       # 强制刷新行情
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smc_config import INSTRUMENTS, TRADE_TF, REPORT_DIR, INIT_CAPITAL, level_of, base_freq
import smc_data
import smc_core
import smc_db


def run_one(c, it, tf=None, variant="A", refresh=False, baseline_before=None, silent_all=False):
    """tf=None 时按标的类别自动选：股票/ETF=day（日线），期货=h1（小时）。

    silent_all：本次写入的事件全部标记为已通知（一次性历史重建用）。"""
    if tf is None:
        tf = level_of(it)
    base = base_freq(tf)                       # day→1d, h1→60m
    h1 = smc_data.get(it["code"], it["kind"], base, refresh=refresh)
    if h1 is None or len(h1) < 200:
        return None, "数据不足"
    ltf = h1.reset_index(drop=True) if tf in ("h1", "day") and base in ("60m", "1d") else smc_core.aggregate(h1, tf)
    frames = smc_core.build_frames(h1, level=tf)
    res = smc_core.run_backtest(ltf, frames, variant=variant, kind=it["kind"], cls=it.get("cls"))
    # 增量入库
    new_ev = smc_db.add_events(c, it["code"], it, tf, res["events"],
                               baseline_before=baseline_before, silent_all=silent_all)
    smc_db.add_trades(c, it["code"], it, tf, res["trades"])
    smc_db.prune_instrument(c, it["code"], tf, res["events"], res["trades"])
    smc_db.put_state(c, it["code"], tf, res, it)
    smc_db.put_equity(c, it["code"], tf, res["equity_curve"])
    smc_db.put_bar_signals(c, it["code"], tf, res)
    pos = res.get("pos")
    # FVG / OB 快照（仅供播报；不参与任何入场/离场判定）
    try:
        _fr = smc_core.Frame(ltf, 1)
        lv = _fr.zones()
        _sigs = _fr.signals()
        res["zones"] = dict(dt=lv["dt"], close=lv["close"], atr=lv["atr"],
                            fvg_bull=sum(1 for z in lv["fvgs"] if z["dir"] == "bull"),
                            fvg_bear=sum(1 for z in lv["fvgs"] if z["dir"] == "bear"),
                            ob_bull=sum(1 for z in lv["obs"] if z["dir"] == "bull"),
                            ob_bear=sum(1 for z in lv["obs"] if z["dir"] == "bear"),
                            nearest=(min([abs(z["dist_pct"]) for z in
                                          (lv["fvgs"][:3] + lv["obs"][:3]) if z.get("dist_pct") is not None],
                                         default=None)),
                            fvgs=lv["fvgs"][:5], obs=lv["obs"][:5], text=None,
                            signals=_sigs[:4],
                            sig_buy=sum(1 for s in _sigs if s["state"] == "buy"),
                            sig_reject=sum(1 for s in _sigs if s["state"] == "reject"))
        lines, _s = smc_core.zones_text(lv["fvgs"], lv["obs"], px=lv["close"])
        res["zones"]["text"] = " ｜ ".join(x.replace("📐 ", "").replace("🧱 ", "") for x in lines)
    except Exception as e:
        res["zones"] = dict(err=str(e)[:120])
    if pos and res.get("stop_px") is not None:
        smc_db.put_stop_state(c, it["code"], tf, pos.get("entry_px"), pos.get("peak"),
                              res["stop_px"], res["stop_kind"], res["stop_dist"], res["last_close"])
    else:
        smc_db.clear_stop_state(c, it["code"], tf)
    return dict(res=res, new_events=new_ev), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default=None, help="留空则按类别自动（股票/ETF=day, 期货=h1）")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()

    c = smc_db.conn()
    smc_db.upsert_instruments(c, INSTRUMENTS)
    tot_new = 0
    print(f"=== SMC 模拟交易引擎 · {datetime.now():%Y-%m-%d %H:%M} · 级别=股票/ETF:day(日线) · 期货:h1(小时) ===")
    for it in INSTRUMENTS:
        tf = a.tf or level_of(it)
        out, err = run_one(c, it, tf=tf, refresh=a.refresh)
        if err:
            print(f"  {it['name']:10s} {err}")
            continue
        r = out["res"]
        n = len(out["new_events"])
        tot_new += n
        s = smc_core.summarize(r)
        pos = r.get("pos")
        hold = "空仓" if not r["units"] else f"持仓@{pos['entry_px']:.4g}"
        zz = r.get("zones") or {}
        zs = (f" FVG{zz.get('fvg_bull',0)}/{zz.get('fvg_bear',0)}"
              f" OB{zz.get('ob_bull',0)}/{zz.get('ob_bear',0)}") if zz and not zz.get("err") else ""
        print(f"  {it['name']:10s} {tf} 权益={s['final_equity']:>10,.0f} 收益={s['total_ret']:+6.2f}% "
              f"交易={s['n_trades']:>2d} 胜率={s['win_rate']:>5.1f}% 回撤={s['max_dd']:+6.2f}% | {hold} | 新事件 {n}{zs}")
    smc_db.set_meta(c, "last_run", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(f"\n新事件合计 {tot_new} 条")
    c.close()

    if a.notify:
        import smc_notify
        smc_notify.flush()


if __name__ == "__main__":
    main()
