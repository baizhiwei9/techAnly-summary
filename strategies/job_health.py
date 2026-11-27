#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务健康看门狗（连续失败/数据停滞 → 报警）
================================================
为什么需要（2026-11-27 大佬要求）
--------------------------------
大佬原话：「如果程序工作失败的话，应该报警……另外发现连续几天失败的话要报警。
我这不都看不（见）」

两个真实事故驱动：
  ① 宽基 ETF 净申赎：tushare 返回残缺被当缓存永久采用，数据停在 20261118，
     日报却一直写「全部未触发」→ 预警形同虚设，没人发现。
  ② RMT λ1/λ2 告警：2026-11-25 库改版删了函数，`rmt_rc_alert.py` 每天
     ImportError 直接退出，连续两天无人知晓。

设计
----
  · 每个任务一个 key（如 `wide_etf_flow` / `rmt_slope`），
    每次跑完调一次 `report(key, ok, detail=...)`。
  · **同一自然日只计一次**（重跑不重复计数）。
  · 连续失败达到 `FAIL_ALERT_DAYS`（默认 2 天）→ 发一封告警邮件 + 飞书，
    之后同一告警窗口 24h 内不重复发（防止天天刷屏）。
  · 恢复成功（ok=True）→ 若之前处于告警态，发一封「已恢复」，并清零。
  · 状态文件：`.cache/job_health_state.json`（只留 90 天）

用法
----
    from job_health import report
    report('wide_etf_flow', ok=False, detail='数据停在 20261118（滞后 6 个交易日）')

    # 数据新鲜度辅助：给定「最新数据日」和「应当的最新交易日」
    from job_health import stale_trade_days
    n = stale_trade_days('20261118', '20261126')   # → 6
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta

# AGENTS.md 硬规矩：容器 IPv6 不通，强制 IPv4
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(WS, 'bin')
if BIN not in sys.path:
    sys.path.insert(0, BIN)
STATE_FP = os.environ.get('JOB_HEALTH_STATE',
                          os.path.join(WS, '.cache', 'job_health_state.json'))
SEND_EMAIL = os.path.join(WS, 'scripts', 'send_email.py')
FEISHU_HOOK = os.environ.get(
    'JOB_HEALTH_FEISHU_HOOK',
    __import__("ocsecrets").feishu_hook())

FAIL_ALERT_DAYS = int(os.environ.get('JOB_HEALTH_FAIL_DAYS', '2'))
KEEP_DAYS = 90

# 任务中文名（告警文案用；未登记的用 key 本身）
NAMES = {
    'wide_etf_flow': '五大宽基 ETF 净申赎监控',
    'rmt_slope': 'RMT λ1/λ2 斜率 + 分位告警',
    'market_tech_daily': '大盘技术拆解日报',
}


# ───────────────────────── 状态读写 ─────────────────────────
def _load():
    try:
        with open(STATE_FP, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save(st):
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime('%Y%m%d')
    st = {k: v for k, v in st.items()
          if str(v.get('last_date') or '') >= cutoff or v.get('fail_days')}
    try:
        os.makedirs(os.path.dirname(STATE_FP), exist_ok=True)
        tmp = STATE_FP + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, STATE_FP)
    except Exception as e:
        print('[job-health] 状态写入失败: %s' % str(e)[:100], file=sys.stderr)


# ───────────────────────── 交易日差 ─────────────────────────
def _trade_calendar():
    """交易日历 dict{YYYYMMDD: 1交易/0休市}。

    优先级（**不硬依赖宿主机 scripts**，大佬 2026-11-27：「不用依赖外部的脚本」）：
      1. DataHub 自带的 core/trade_calendar.py（optstack 在 sys.path 上就有）
      2. 宿主机 bin/trade_day.py（老环境）
      3. 都没有 → None（调用方退化为工作日估算）
    """
    # 1) DataHub 内部日历
    for probe in ('optstack', os.path.join(WS, 'optstack')):
        try:
            if probe not in sys.path:
                sys.path.insert(0, probe)
            from data_hub.core import trade_calendar as TC   # type: ignore
            cal = TC.cal_days()
            if cal:
                return {str(k): int(v or 0) for k, v in cal.items()}
        except Exception:
            pass
    # 2) 宿主机 bin/trade_day.py
    try:
        if BIN not in sys.path:
            sys.path.insert(0, BIN)
        import trade_day as TD
        cal = TD.cal_days()
        if cal:
            return {str(k): int(v or 0) for k, v in cal.items()}
    except Exception:
        pass
    return None


def stale_trade_days(latest_d8, expect_d8=None):
    """「最新数据日」落后「应到交易日」几个交易日。算不出返回 None。"""
    try:
        exp = expect_d8
        cal = _trade_calendar()
        if cal:
            days = sorted(k for k, v in cal.items() if v == 1)
            if not exp:
                today = datetime.now().strftime('%Y%m%d')
                prior = [d for d in days if d <= today]
                exp = prior[-1] if prior else today
            if days and latest_d8 in days and exp in days:
                return days.index(exp) - days.index(latest_d8)
            if days:
                prior = [d for d in days if d <= latest_d8]
                if prior and exp in days:
                    return days.index(exp) - days.index(prior[-1])
        # 退化：只数工作日（周一~周五）
        b = datetime.strptime(exp or datetime.now().strftime('%Y%m%d'), '%Y%m%d')
        a = datetime.strptime(latest_d8, '%Y%m%d')
        n, cur = 0, a + timedelta(days=1)
        while cur <= b:
            if cur.weekday() < 5:
                n += 1
            cur += timedelta(days=1)
        return n
    except Exception:
        return None


# ───────────────────────── 发信 ─────────────────────────
def _send(subject, body):
    ok = False
    try:
        if os.path.exists(SEND_EMAIL):
            tmp = '/tmp/job_health_%s.md' % datetime.now().strftime('%Y%m%d_%H%M%S')
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(body)
            p = subprocess.run([sys.executable, SEND_EMAIL, '--subject', subject,
                                '--body-file', tmp],
                               capture_output=True, text=True, timeout=150)
            ok = p.returncode == 0
            print('[job-health mail] rc=%d %s' % (p.returncode,
                                                 (p.stdout or '').strip()[:150]))
    except Exception as e:
        print('[job-health mail] 异常: %s' % str(e)[:120], file=sys.stderr)

    # 飞书兜底（顺手，失败不影响邮件结果）
    try:
        import urllib.request
        txt = '%s\n\n%s\n\n📎 来自 OpenClaw.Deepseek' % (subject, body[:1200])
        req = urllib.request.Request(
            FEISHU_HOOK,
            data=json.dumps({'msg_type': 'text',
                             'content': {'text': txt}}).encode(),
            headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print('[job-health feishu] %s' % str(e)[:100], file=sys.stderr)
    return ok


# ───────────────────────── 主入口 ─────────────────────────
def report(key, ok, detail='', threshold=None, extra=None):
    """登记一次运行结果。返回本次是否发了告警。

    Args:
        key       : 任务 key（见 NAMES）
        ok        : 本次是否健康（数据新且完整、程序没崩）
        detail    : 不健康的具体原因（会写进告警正文）
        threshold : 连续失败多少天报警（默认 FAIL_ALERT_DAYS）
        extra     : dict，附加到状态里便于排查（如最新数据日、覆盖只数）
    """
    today = datetime.now().strftime('%Y%m%d')
    now_s = datetime.now().strftime('%Y-%m-%d %H:%M')
    need = int(threshold or FAIL_ALERT_DAYS)
    name = NAMES.get(key, key)

    st = _load()
    rec = st.get(key) or {}
    rec.setdefault('fail_days', 0)
    rec.setdefault('since', None)
    rec.setdefault('alerts', [])

    # 同一自然日只计一次（重跑/多入口调用不重复计数）
    if rec.get('last_date') != today:
        if ok:
            if rec.get('fail_days', 0) >= need and rec.get('alerted'):
                _send('✅ 已恢复 · %s' % name,
                      '**任务**：%s（`%s`）\n\n**恢复时间**：%s\n\n'
                      '**此前连续失败**：%d 天\n\n**上次原因**：%s\n\n'
                      '现在数据/程序已恢复正常。\n\n---\n\n📎 来自 OpenClaw.Deepseek'
                      % (name, key, now_s, rec['fail_days'],
                         rec.get('last_detail') or '—'))
            rec['fail_days'] = 0
            rec['since'] = None
            rec['alerted'] = False
        else:
            rec['fail_days'] = int(rec.get('fail_days') or 0) + 1
            rec['since'] = rec.get('since') or today
            rec['last_detail'] = detail
        rec['last_date'] = today
        rec['last_ok'] = bool(ok)
        rec['last_run'] = now_s
        if detail:
            rec['last_detail'] = detail
        if extra:
            rec['extra'] = extra

    # 达到阈值且本窗口还没报过 → 发告警
    fired = False
    if (not ok) and rec['fail_days'] >= need and not rec.get('alerted'):
        rec['alerted'] = True
        rec['alerts'] = (rec.get('alerts') or [])[-9:] + [
            {'at': now_s, 'fail_days': rec['fail_days'], 'detail': detail}]
        body = (
            '**🚨 任务连续失败告警**\n\n'
            '**任务**：%s（`%s`）\n\n'
            '**已连续失败**：%d 天（自 %s 起）\n\n'
            '**最近原因**：%s\n\n'
            '**影响**：这个监控现在是**瞎的**——它的预警不会响，'
            '看到的数字可能是旧的。\n\n'
            '**处理**：看 `.cache/` 下对应日志；ETF 净申赎还要查 '
            '`~/.cache/data_hub/etf_net_flow/day/` 有没有残档（`*.bad`）。\n\n'
            '---\n\n📎 来自 OpenClaw.Deepseek'
            % (name, key, rec['fail_days'], rec.get('since') or '—', detail or '—'))
        _send('🚨 %s · 连续 %d 天失败' % (name, rec['fail_days']), body)
        fired = True

    st[key] = rec
    _save(st)
    return fired


def status():
    """返回状态 dict（调试/自查用）。"""
    return _load()


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', action='store_true', help='打印各任务健康状态')
    ap.add_argument('--report', nargs=2, metavar=('KEY', 'OK'),
                    help='手工登记：--report wide_etf_flow 0（1=正常 0=失败）')
    ap.add_argument('--detail', default='')
    a = ap.parse_args()
    if a.report:
        k, v = a.report
        r = report(k, str(v).lower() in ('1', 'true', 'ok', 'yes'), detail=a.detail)
        print('fired=%s' % r)
    else:
        st = status()
        if not st:
            print('（无状态记录）')
        for k, v in sorted(st.items()):
            print('%-22s 连续失败=%d 最后运行=%s 健康=%s  %s'
                  % (k, v.get('fail_days') or 0, v.get('last_run') or '—',
                     '✅' if v.get('last_ok') else '❌', (v.get('last_detail') or '')[:70]))
