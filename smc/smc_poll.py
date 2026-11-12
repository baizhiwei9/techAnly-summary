#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 30 分钟轮询器（每 30 分钟判断一次；只在交易时段活跃）
================================================================
crontab:  */30 * * * 1-5  .../smc/smc_poll.py  >> .../smc/cache/poll.log 2>&1

⚠️ crontab 用 */30 覆盖全天，但**脚本内部逐品种卡交易时段**：
  - 非交易时段：对应品种直接跳过；**全部品种都休市 → 立即退出（不执行）**
  - 夜盘（贵金属 21:00-02:30 / 有色 21:00-01:00 / 农产品 21:00-23:00）照常执行
  - 凌晨 02:30 之后到早盘之前 → 静默跳过

流程：
  1) 取当前时间（Asia/Shanghai），逐品种判断是否在**它自己的**交易时段
  2) 仅对开市品种刷新 60m 数据并重推演 → 增量落库
  3) 有新增量事件 → 发 多尺度交易群(飞书) + Coze 群
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smc_config import INSTRUMENTS, sessions_of, level_of
import smc_engine, smc_db


def _hm(s):
    h, m = s.split(":")
    return time(int(h), int(m))


def in_window(now_t, win):
    a, b = _hm(win[0]), _hm(win[1])
    if a <= b:
        return a <= now_t <= b
    return now_t >= a or now_t <= b     # 跨夜


def is_open(it, now=None):
    """该标的是否处于自己的交易时段。"""
    now = now or datetime.now()
    # 夜盘跨夜：周五 23:00 后的夜盘属于周一凌晨之前，此处按「自然日 weekday」判断
    if now.weekday() >= 5:
        # 周六凌晨仍可能有周五夜盘尾段（贵金属到 02:30）
        if now.weekday() == 5 and it.get("kind") == "fut":
            t = now.time()
            return any(in_window(t, w) for w in sessions_of(it) if w[0] > w[1])
        return False
    t = now.time()
    return any(in_window(t, w) for w in sessions_of(it))


def open_instruments(now=None):
    return [it for it in INSTRUMENTS if is_open(it, now)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略交易时段（调试用）")
    ap.add_argument("--dry", action="store_true", help="只判断时段，不取数")
    a = ap.parse_args()

    now = datetime.now()
    op = INSTRUMENTS if a.force else open_instruments(now)
    closed = [it["name"] for it in INSTRUMENTS if it not in op]
    print(f"--- {now:%Y-%m-%d %H:%M:%S} 开市 {len(op)}/{len(INSTRUMENTS)} 个标的 ---", flush=True)
    if not op:
        print("非交易时段（全部休市），退出不执行", flush=True)
        return
    if closed:
        print(f"  休市中跳过: {'/'.join(closed)}", flush=True)
    if a.dry:
        print(f"  开市: {'/'.join(it['name'] for it in op)}", flush=True)
        return

    # ⚠️ 2026-10-07 修复：原版 poll 自己复刻了一套落库逻辑，与 smc_engine.run_one
    #    重复且长期不同步（改级别时两处都要改）。现改为直接调用 smc_engine.run_one，
    #    单一实现、单一真相。股票/ETF=日线(day)、期货=小时(h1) 由 run_one 内部按类别分派。
    c = smc_db.conn()
    smc_db.upsert_instruments(c, INSTRUMENTS)
    # ⚠️ 只推送「今天」的事件：早于今日 00:00 的一律静默标记为已通知。
    #   即使数据窗口变化导致回测重算、旧事件被重新生成，也绝不会补推历史。
    today0 = datetime.now().strftime("%Y-%m-%dT00:00:00")
    new = 0
    for it in op:
        tf = level_of(it)
        try:
            out, err = smc_engine.run_one(c, it, tf=tf, refresh=True,
                                          baseline_before=today0)
            if err:
                print(f"  {it['name']} {err}，跳过", flush=True)
                continue
            n = len(out["new_events"])
            new += n
            if n:
                print(f"  {it['name']}（{tf}）新增 {n} 事件", flush=True)
        except Exception as e:
            print(f"  {it['name']} 异常: {str(e)[:100]}", flush=True)
    c.close()
    print(f"新事件 {new} 条", flush=True)
    if new:
        import smc_notify
        smc_notify.flush()


if __name__ == "__main__":
    main()
