#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地模拟交易数据库 (SQLite)
================================================================
表：
  positions  当前/历史持仓
  trades     已平仓交易（含减半/加回明细 json）
  events     增量事件流（开仓/平仓/减半/加回）—— 通知引擎按此发增量消息
  equity     每日权益曲线
  signals    去重用的已消费信号(SUB摆动高点 dt)
  meta       运行元信息
"""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime

from smc_config import DB_PATH

DDL = """
CREATE TABLE IF NOT EXISTS instruments(
  code TEXT PRIMARY KEY, name TEXT, cls TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT, name TEXT, cls TEXT, kind TEXT, tf TEXT,
  etype TEXT, dt TEXT, px REAL, units REAL, reason TEXT,
  pnl REAL, pnl_pct REAL, extra TEXT,
  notified INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT, name TEXT, tf TEXT, entry_dt TEXT, entry_px REAL,
  exit_dt TEXT, exit_px REAL, reason TEXT, pnl REAL, pnl_pct REAL,
  cash_out REAL, cash_in REAL, adds TEXT, xch TEXT);
CREATE TABLE IF NOT EXISTS state(
  code TEXT, tf TEXT, state TEXT, entry_dt TEXT, entry_px REAL, units REAL,
  cash REAL, last_dt TEXT, last_close REAL, updated TEXT,
  PRIMARY KEY(code,tf));
CREATE TABLE IF NOT EXISTS equity(
  code TEXT, tf TEXT, dt TEXT, equity REAL, PRIMARY KEY(code,tf,dt));
CREATE TABLE IF NOT EXISTS signals(
  code TEXT, tf TEXT, key TEXT, created TEXT, PRIMARY KEY(code,tf,key));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS bar_signals(
  code TEXT, tf TEXT, dt TEXT, tier TEXT, reason TEXT, px REAL, created TEXT,
  PRIMARY KEY(code,tf,dt));
CREATE TABLE IF NOT EXISTS stop_state(
  code TEXT, tf TEXT, state TEXT, entry_px REAL, peak REAL, stop_price REAL,
  stop_kind TEXT, dist_pct REAL, last_close REAL, updated TEXT,
  PRIMARY KEY(code,tf));
"""


def conn():
    c = sqlite3.connect(DB_PATH)
    c.executescript(DDL)
    return c


def upsert_instruments(c, instruments):
    c.executemany("INSERT OR REPLACE INTO instruments(code,name,cls,kind) VALUES(?,?,?,?)",
                  [(i["code"], i["name"], i["cls"], i["kind"]) for i in instruments])
    c.commit()


def put_state(c, code, tf, res, it):
    pos = res.get("pos")
    c.execute("""INSERT OR REPLACE INTO state
        (code,tf,state,entry_dt,entry_px,units,cash,last_dt,last_close,updated)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (code, tf, res["state"], (pos or {}).get("entry_dt"), (pos or {}).get("entry_px"),
               res["units"], res["cash"], res["last_dt"], res["last_close"],
               datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    c.commit()


def put_equity(c, code, tf, curve):
    rows = [(code, tf, str(r["dt"])[:19], float(r["equity"])) for _, r in curve.iterrows()]
    c.executemany("INSERT OR REPLACE INTO equity(code,tf,dt,equity) VALUES(?,?,?,?)", rows)
    c.commit()


def last_event_id(c):
    r = c.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()
    return int(r[0])


def existing_event_keys(c, code, tf):
    rs = c.execute("SELECT etype,dt FROM events WHERE code=? AND tf=?", (code, tf)).fetchall()
    return {(a, b) for a, b in rs}


def add_events(c, code, it, tf, events):
    """写入新事件，返回新写入的事件（不含已存在）。"""
    keys = existing_event_keys(c, code, tf)
    new = []
    for e in events:
        k = (e["kind"], e["dt"])
        if k in keys:
            continue
        keys.add(k)
        c.execute("""INSERT INTO events(code,name,cls,kind,tf,etype,dt,px,units,reason,pnl,pnl_pct,extra,notified,created)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
                  (code, it["name"], it["cls"], it["kind"], tf, e["kind"], e["dt"], e.get("px"),
                   e.get("units"), e.get("reason"), e.get("pnl"), e.get("pnl_pct"),
                   json.dumps({k2: v for k2, v in e.items() if k2 not in
                               ("kind", "dt", "px", "units", "reason", "pnl", "pnl_pct")}, ensure_ascii=False),
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        e["_new"] = True
        new.append(e)
    c.commit()
    return new


def add_events(c, code, it, tf, events, baseline_before=None, silent_all=False):
    """写入新事件，返回新写入的事件（不含已存在）。

    silent_all：全部标记 notified=1（一次性重建历史时用，不补推）。
    baseline_before：早于该时间戳（字符串）的事件标记 notified=1。"""
    keys = existing_event_keys(c, code, tf)
    new = []
    for e in events:
        k = (e["kind"], e["dt"])
        if k in keys:
            continue
        keys.add(k)
        hist = 1 if (silent_all or (baseline_before and str(e["dt"]) < str(baseline_before))) else 0
        c.execute("""INSERT INTO events(code,name,cls,kind,tf,etype,dt,px,units,reason,pnl,pnl_pct,extra,notified,created)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (code, it["name"], it["cls"], it["kind"], tf, e["kind"], e["dt"], e.get("px"),
                   e.get("units"), e.get("reason"), e.get("pnl"), e.get("pnl_pct"),
                   json.dumps({k2: v for k2, v in e.items() if k2 not in
                               ("kind", "dt", "px", "units", "reason", "pnl", "pnl_pct")}, ensure_ascii=False),
                   hist, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        e["_new"] = True
        new.append(e)
    c.commit()
    return new


def prune_instrument(c, code, tf, events, trades):
    """删除该标的不再出现在回测结果里的陈旧事件/交易（保持 DB 与回测一致）。"""
    ek = {(e["kind"], str(e["dt"])) for e in events}
    for eid, etype, dt in c.execute("SELECT id,etype,dt FROM events WHERE code=? AND tf=?", (code, tf)).fetchall():
        if (etype, str(dt)) not in ek:
            c.execute("DELETE FROM events WHERE id=?", (eid,))
    tk = {(str(t["entry_dt"]), str(t["exit_dt"])) for t in trades if t.get("exit_dt")}
    for tid, ed, xd in c.execute("SELECT id,entry_dt,exit_dt FROM trades WHERE code=? AND tf=?", (code, tf)).fetchall():
        if (str(ed), str(xd)) not in tk:
            c.execute("DELETE FROM trades WHERE id=?", (tid,))
    c.commit()


def add_trades(c, code, it, tf, trades):
    have = {(r[0], r[1]) for r in
            c.execute("SELECT entry_dt,exit_dt FROM trades WHERE code=? AND tf=?", (code, tf)).fetchall()}
    n = 0
    for t in trades:
        if not t.get("exit_dt"):
            continue
        if (t["entry_dt"], t["exit_dt"]) in have:
            continue
        c.execute("""INSERT INTO trades(code,name,tf,entry_dt,entry_px,exit_dt,exit_px,reason,pnl,pnl_pct,cash_out,cash_in,adds,xch)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (code, it["name"], tf, t["entry_dt"], t["entry_px"], t["exit_dt"], t["exit_px"],
                   t["reason"], t["pnl"], t["pnl_pct"], t["cash_out"], t["cash_in"],
                   json.dumps(t.get("adds", []), ensure_ascii=False), json.dumps(t.get("xch_events", []), ensure_ascii=False)))
        n += 1
    c.commit()
    return n


def set_meta(c, k, v):
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
    c.commit()


def get_meta(c, k, default=None):
    r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def unread_events(c, limit=100):
    return c.execute("""SELECT id,code,name,cls,kind,tf,etype,dt,px,units,reason,pnl,pnl_pct
                        FROM events WHERE notified=0 ORDER BY id LIMIT ?""", (limit,)).fetchall()


def unread_events_full(c, limit=200):
    """带 extra（entry_dt/entry_px）的未读事件，供富格式通知。
    只取**开仓/平仓**（含减半 XCH / 加回满仓 这些仓位变动）—— 用户 2026-10-07 要求：
    只发开仓和平仓，不发空仓/待机类消息。"""
    return c.execute("""SELECT id,code,name,cls,kind,tf,etype,dt,px,units,reason,pnl,pnl_pct,extra
                        FROM events
                        WHERE notified=0 AND etype IN ('开仓','平仓','减半(XCH)','加回满仓')
                        ORDER BY id LIMIT ?""", (limit,)).fetchall()


def close_log(c, limit=40):
    """最近的平仓记录（供「平仓逻辑」查询）。"""
    return c.execute("""SELECT name,code,kind,tf,dt,px,reason,pnl,pnl_pct
                        FROM events WHERE etype='平仓' ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()


def recent_events(c, limit=8):
    """最近的开/平/减/加事件（不分已读未读），供「最近成交」展示。"""
    return c.execute("""SELECT code,name,cls,kind,tf,etype,dt,px,units,reason,pnl,pnl_pct,extra
                        FROM events
                        WHERE etype IN ('开仓','平仓','减半(XCH)','加回满仓')
                        ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()


def mark_notified(c, ids):
    c.executemany("UPDATE events SET notified=1 WHERE id=?", [(i,) for i in ids])
    c.commit()


def put_bar_signals(c, code, tf, res):
    """写入最新一根 K 线的信号诊断（先清旧）。"""
    c.execute("DELETE FROM bar_signals WHERE code=? AND tf=?", (code, tf))
    last = res.get("last_dt")
    rows = [(code, tf, s["dt"], s.get("tier"), s.get("reason"), s.get("px"),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            for s in res.get("signals", []) if s.get("dt") == last]
    if rows:
        c.executemany("""INSERT OR REPLACE INTO bar_signals(code,tf,dt,tier,reason,px,created)
                        VALUES(?,?,?,?,?,?,?)""", rows)
    c.commit()


def put_stop_state(c, code, tf, entry_px, peak, stop_price, stop_kind, dist_pct, last_close):
    c.execute("""INSERT OR REPLACE INTO stop_state
                 (code,tf,state,entry_px,peak,stop_price,stop_kind,dist_pct,last_close,updated)
                 VALUES(?,?,'hold',?,?,?,?,?,?,?)""",
              (code, tf, entry_px, peak, stop_price, stop_kind, dist_pct, last_close,
               datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    c.commit()


def clear_stop_state(c, code, tf):
    c.execute("DELETE FROM stop_state WHERE code=? AND tf=?", (code, tf))
    c.commit()


def get_stop_state(c, code, tf):
    return c.execute("""SELECT stop_price,stop_kind,dist_pct,peak,entry_px,last_close
                        FROM stop_state WHERE code=? AND tf=?""", (code, tf)).fetchone()


def get_bar_signals(c, code=None, tf=None):
    if code:
        return c.execute("""SELECT code,tf,dt,tier,reason,px,created FROM bar_signals
                            WHERE code=? ORDER BY dt DESC""", (code,)).fetchall()
    return c.execute("""SELECT code,tf,dt,tier,reason,px,created FROM bar_signals
                        ORDER BY created DESC""").fetchall()
