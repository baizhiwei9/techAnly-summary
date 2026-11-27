#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
相变异常检测系统 · 第 3 层：报警层 (Alert Layer)
==============================================
职责：只负责「拿到计算结果 → 决定发不发 → 发到哪里」。
不取数、不算指标、不碰原始数据。

告警口径（大佬 2026-11-03 22:30 定稿）
------------------------------------
**「每个指数，可用指标里有 ≥3 个同时亮 → 发提醒」**

可用指标 5 个（M4 α 已永久下线，二十年零触发，见 docs/pws_alpha_dead.md）：
  M1 AR(1) 恢复力衰减   AR(1) 残差 > 0.6 持续 3 日
  M2 Δσ 波动放大        Δσ > 3 × σ_std
  M3 PSI 结构脆弱度     PSI > 过去 120 日 P90
  M5 λmax 截面突变      周环比 > 20%
  M6 吸收比率 λ1/N      > 120 日 P90 且 收盘 > MA60

**三个指数合并成一条消息统一发**（大佬明确要求：不要每个指数发一遍）。

输出通道
--------
1. 本地状态：.cache/pws/state.json（每日快照 + 告警历史）
2. 文本/日志：stdout
3. 推送：邮件（主）+ 飞书 webhook + QQ（走 openclaw message）

用法
----
    import pws_alert as AL
    AL.run(results)                  # 全流程：判级 → 存档 → 推送
    AL.run(results, dry=True)        # 只判级 + 存档，不推送
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import json
import time
import subprocess
import socket

# 容器 IPv6 不通 → 所有对外请求强制 IPv4（大佬 2026-11-03 定规矩）
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
WS = os.environ.get("OPENCLAW_WORKSPACE") or __import__("ocsecrets").workspace()
STATE_DIR = os.path.join(WS, '.cache', 'pws')
os.makedirs(STATE_DIR, exist_ok=True)
STATE_FP = os.path.join(STATE_DIR, 'state.json')
HIST_FP = os.path.join(STATE_DIR, 'alert_history.jsonl')

FEISHU_HOOK = __import__("ocsecrets").feishu_hook()
QQ_TARGET = 'qqbot:c2c:D3056302911060130E3DEEC57C618084'
MAIL_TO = __import__('ocsecrets').get('MAIL_DEFAULT_TO', ('EMAIL_TO_ADDR',), '410119229@qq.com')
# ⚠️ 邮箱通道用 scripts/send_email.py（163 邮箱，实测可用）
#    qqmail-assistant 那个 QQ 邮箱授权码已失效（AUTHENTICATIONFAILED），别用
MAIL_SCRIPT = os.path.join(WS, 'scripts/send_email.py')

# 告警阈值：可用指标里 ≥3 个同时亮
ALERT_VOTES = 3

# ⚠️ 颜色口径（大佬 2026-11-03 定）：风险=红🔴，机会=绿🟢，不能反
LEVEL = {0: ('🟢', '正常'), 1: ('🟢', '平稳'), 2: ('🟡', '关注'),
         3: ('🔴', '预警'), 4: ('🔴', '高度预警'), 5: ('🔴', '系统级')}

# 指标清单：(键, 简称, 全名, 判定条件文案)
# 2026-11-03 大佬定：去掉 α（原 M4），剩下的重新编为 M1~M5
IND_META = [
    ('m1', 'M1', 'AR(1) 恢复力衰减', 'AR(1) > 0.60 且持续 3 日'),
    ('m2', 'M2', 'Δσ 波动放大', 'Δσ > 3 × σ_std'),
    ('m3', 'M3', 'PSI 结构脆弱度', 'PSI > 过去 120 日 P90'),
    ('m4', 'M4', 'λmax 截面突变', '截面最大特征值周环比 > 20%'),
    ('m5', 'M5', '吸收比率 λ1/N', '> 120 日 P90 且 收盘 > MA60'),
]
VOTE_KEYS = [k for k, _, _, _ in IND_META]     # 5 个 = M1~M5

# 指标释义 + 正常值（大佬 2026-11-03 要求：报告里直接告诉他 M1~M5 是啥、正常值多少、为什么会报）
LEGEND = \
"""📖 指标释义与正常值（亮灯=超阈值=风险🔴）
· M1 AR(1) 恢复力衰减：日收益 20 日一阶自相关系数，衡量「昨天涨跌对今天的延续性」。正常 −0.5~+0.3（A股日频轻微反转）；持续 3 日 > 0.60 = 趋势记忆异常强，短期涨跌被单向锁定、失去自我修复力，是顶部典型特征，风险。
· M2 Δσ 波动放大：20 日波动率 减去 20 日前的波动率（单位 日收益标准差）。正常在 ±0.002 内震荡；> 3×波动率自身的标准差（近 120 日均约 0.005）才亮 = 波动骤然放大，风险。
· M3 PSI 结构脆弱度：波动率 ÷ 自相关衰减指数 λ²，越低 λ 代表涨跌越像白噪声。正常 = 低于自己过去 120 日的 P90（滚动阈值、无固定数）；破 P90 说明结构支撑变薄，风险。
· M4 λmax 截面突变：全市场 20 日相关矩阵最大特征值，正常 30~60（与股票数有关）；周环比 > 20% = 全市场突然一起涨一起跌、分化瞬间消失，风险。
· M5 吸收比率 λ1/N：去共同因子后相关矩阵第一特征值占比，等于「被共同因子解释的比例」。正常 = 低于自己过去 120 日 P90（滚动阈值），且要求指数在 MA60 上方；亮灯 = 抱团到极限，风险。"""


# ---------------- 判级 ----------------
def classify(results):
    """results: list of pws_compute.Result → 汇总 dict（三指数合并）"""
    out = {'ts': int(time.time()), 'date': None, 'items': [], 'max_votes': 0,
           'level': 0, 'level_text': '正常', 'triggers': [], 'alert': False,
           'data_issues': []}
    for r in results:
        if r is None or not len(r.df):
            continue
        row = r.df.iloc[-1]
        flags = {
            'm1': int(row['m1_alert']), 'm2': int(row['m2_alert']),
            'm3': int(row['m3_alert']), 'm4': int(row['m4_alert']),
            'm5': int(row.get('m5_alert', 0)),
        }
        v = sum(flags[k] for k in VOTE_KEYS)          # 只数可用 5 个
        icon, txt = LEVEL.get(v, LEVEL[5])
        item = {
            'code': r.code, 'name': r.name,
            'date': row['date'], 'close': round(float(row['close']), 2),
            'votes': v, 'level': v, 'level_text': txt, 'icon': icon,
            'flags': flags,
            'streak': int(row['streak']),
            'raw': {
                'm1_ar': _f(row['m1_ar']), 'm1_thr': 0.60,
                'm2_dsig': _f(row['m2_dsig'], 5), 'm2_thr': _f(3 * row['m2_sd'], 5),
                'm3_psi': _f(row['m3_psi']), 'm3_thr': _f(row['m3_psi_thr']),
                'm4_lmax': _f(row['m4_lmax'], 1),
                'm5_ar': _f(row.get('m5_ar'), 4), 'm5_thr': _f(row.get('m5_thr'), 4),
            },
        }
        out['items'].append(item)
        out['date'] = max(out['date'] or '', row['date'])
        out['max_votes'] = max(out['max_votes'], v)
        if v >= ALERT_VOTES:
            names = [lab for k, lab, _, _ in IND_META if flags[k]]
            out['triggers'].append(f"{r.name}：{v} 个指标（{'＋'.join(names)}）")
    out['level'] = out['max_votes']
    out['level_text'] = LEVEL.get(out['max_votes'], LEVEL[5])[1]
    out['alert'] = out['max_votes'] >= ALERT_VOTES
    return out


def _f(x, d=3):
    try:
        v = float(x)
        return None if not np.isfinite(v) else round(v, d)
    except Exception:
        return None


# ---------------- 状态存档 ----------------
def save_state(summary):
    hist = []
    if os.path.exists(STATE_FP):
        try:
            hist = json.load(open(STATE_FP))
        except Exception:
            hist = []
    if isinstance(hist, dict):
        hist = hist.get('history', [])
    hist.append(summary)
    hist = hist[-90:]
    json.dump({'updated': int(time.time()), 'history': hist},
              open(STATE_FP, 'w'), ensure_ascii=False, indent=1)
    with open(HIST_FP, 'a') as f:
        for it in summary['items']:
            f.write(json.dumps({'ts': summary['ts'], 'date': it['date'],
                                'code': it['code'], 'name': it['name'],
                                'votes': it['votes'], 'flags': it['flags'],
                                'raw': it['raw'], 'streak': it['streak']},
                               ensure_ascii=False) + '\n')
    return STATE_FP


# ---------------- 文本 ----------------
def render(summary, mode='full'):
    """生成人类可读文本（三指数合并一条）"""
    if mode == 'brief':
        # 无告警 且 数据齐全 → 不发；数据缺失/回退一定要发出来说明
        if not summary.get('alert') and not summary.get('data_issues'):
            return None
    v = summary['max_votes']
    icon, txt = LEVEL.get(v, LEVEL[5])
    L = []
    head = '⚠️ 相变异常检测系统' if summary.get('alert') else '相变异常检测系统'
    # 全都没算出来 → 不能说「正常」，必须明说数据缺失
    if not summary['items']:
        icon, txt = '⚠️', '数据缺失，本期无法判断'
    L.append(f"{icon} {head} · {summary['date']} · 最高 {v}/5 个指标亮 · {txt}")
    L.append('')

    dq = summary.get('data_issues') or []
    if dq:
        L.append('⚠️ 数据口径提醒（结论需打折看）')
        for name, why in dq:
            L.append(f'· {name}：{why}')
        if not summary['items']:
            L.append('· 全部指数均未取到数据，本期不做任何判断')
        L.append('')

    hdr = f"{'指数':<10}{'收盘':>10}  {'M1':>3}{'M2':>3}{'M3':>3}{'M4':>3}{'M5':>3}  风险灯"
    L.append(hdr)
    L.append('-' * 58)
    for it in summary['items']:
        f = it['flags']
        # 亮灯 = 风险，标红；没亮 = 没风险，标绿
        mk = ''.join(' 🔴' if f[k] else ' 🟢' for k in VOTE_KEYS)
        L.append(f"{it['name']:<10}{it['close']:>10.2f}{mk}  {it['votes']}/5")
    L.append('')
    # 明细：只列亮的指标
    for it in summary['items']:
        on = [k for k in VOTE_KEYS if it['flags'][k]]
        if not on:
            continue
        r = it['raw']
        parts = []
        for k in on:
            if k == 'm1':
                parts.append(f"AR(1)={r['m1_ar']:.2f}(阈>0.60)")
            elif k == 'm2':
                parts.append(f"Δσ={r['m2_dsig']:.5f}(阈>{r['m2_thr']:.5f})")
            elif k == 'm3':
                parts.append(f"PSI={r['m3_psi']}(阈{r['m3_thr']})")
            elif k == 'm4':
                parts.append(f"λmax={r['m4_lmax']}")
            elif k == 'm5':
                parts.append(f"AR比={r['m5_ar']:.4f}(阈>{r['m5_thr']:.4f})")
        L.append(f"  {it['name']}：{' | '.join(parts)}")
    if summary['triggers']:
        L.append('')
        L.append('🔔 触发：' + '；'.join(summary['triggers']))
    L.append('')
    L.append('判定：同一个指数的 5 个指标（M1~M5）里有 ≥3 个同时亮 🔴 → 才预警（不是几个指数凑票数）')
    L.append('颜色：🔴 亮灯=风险｜🟢 未亮=没风险')
    L.append('')
    L.append(LEGEND)
    L.append('')
    L.append('📎 来自 OpenClaw.Deepseek')
    return '\n'.join(L)


# ---------------- 推送 ----------------
def push_feishu(text):
    if not text:
        return False
    payload = {'msg_type': 'text', 'content': {'text': text}}
    try:
        import urllib.request
        req = urllib.request.Request(
            FEISHU_HOOK,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        r = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        ok = r.get('code') == 0 or r.get('StatusCode') == 0
        if not ok:
            print(f'[alert] 飞书失败: {r}', file=sys.stderr)
        return ok
    except Exception as e:
        print(f'[alert] 飞书异常: {e}', file=sys.stderr)
        return False


def push_qq(text, target=QQ_TARGET):
    if not text:
        return False
    try:
        r = subprocess.run(
            ['openclaw', 'message', 'send', '--channel', 'qqbot',
             '--target', target, '--message', text],
            capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            print(f'[alert] QQ 失败: {r.stderr[:300]}', file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f'[alert] QQ 异常: {e}', file=sys.stderr)
        return False


def push_mail(text, subject=None, to=MAIL_TO):
    """邮件推送（走 scripts/send_email.py，一封发完三个指数，不分开几封）"""
    if not text:
        return False
    subj = subject or f"⚠️ 相变预警 · {time.strftime('%Y-%m-%d')}"
    try:
        # 正文写临时文件传，避免命令行长度 / 特殊字符问题
        import tempfile
        tmp = os.path.join(STATE_DIR, '_mail_body.txt')
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        r = subprocess.run(
            [sys.executable, MAIL_SCRIPT, '--subject', subj,
             '--body-file', tmp, '--to', to, '--plain'],
            capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            print(f'[alert] 邮件失败: {(r.stderr or r.stdout)[:400]}', file=sys.stderr)
            return False
        out = (r.stdout or '').strip()
        ok = 'sent' in out
        print(f'[alert] 邮件{"已发 " + to if ok else "异常: " + out[:200]}')
        return ok
    except Exception as e:
        print(f'[alert] 邮件异常: {e}', file=sys.stderr)
        return False


def run(results, dry=False, only_sys=True, push=True, channels=('mail', 'feishu', 'qq'),
        missing=None):
    """全流程（2026-11-03 23:10 大佬定：日报每天都发，只有邮件是 ≥3 指标才发）

    - 日报（飞书 + QQ）：**每个交易日都发**，不管几票亮 → 用 full 全文
    - 邮件：只有 ≥3 个指标同时亮（系统级告警）才发
    - 数据缺失/回退：一并写进报告
    - only_sys=False 时连邮件也不看票数（强制打扰模式，仅调试用）
    """
    s = classify(results)
    # ⚠️ 2026-11-10 修 bug：邮件闸门原来跟着 only_sys 参数走，而 crontab 没传 --only-sys
    #    → only_sys=False → 每天必发邮件。现在「单指数 ≥3 灯」这条闸门写死在这里，
    #    only_sys=False 只作为调试开关注入，不影响生产路径。
    # 数据缺失/回退 = 结论不可全信，必须推出去说明（否则用户以为"没报警=正常"）
    s['data_issues'] = list(missing or [])
    save_state(s)
    text = render(s, 'full')          # 日报一律完整版
    # 邮件只认「某个指数自己 ≥3 个指标同时亮」；only_sys=False 是调试强制模式
    mail_text = text if (s.get('alert') or only_sys is False) else None
    sent = {}
    if not dry and push:
        if 'mail' in channels and mail_text:
            sent['mail'] = push_mail(mail_text, subject=f"⚠️ 相变预警 · {s.get('date') or ''}")
        elif 'mail' in channels:
            sent['mail'] = 'skip(未达3票)'
        if 'feishu' in channels and text:
            sent['feishu'] = push_feishu(text)
        if 'qq' in channels and text:
            # QQ 通道发长文本很慢（实测 1000 字 60s 超时）→ QQ 只发精简版（去掉指标图例）
            qq_text = text.split('📖 指标释义')[0].rstrip()
            sent['qq'] = push_qq(qq_text)
    return s, text, sent


if __name__ == '__main__':
    import argparse
    import pws_data as D
    import pws_compute as C
    import pws_xsec as X
    import pws_absratio as AB
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--all', action='store_true', help='不管多少票都输出完整报告')
    ap.add_argument('--no-push', action='store_true')
    ap.add_argument('--channels', default='mail,feishu,qq')
    ap.add_argument('--ar', default='resid')
    ap.add_argument('--lam', default='abs')
    ap.add_argument('--alpha', default='expand')
    ap.add_argument('--kmin', type=int, default=50)
    a = ap.parse_args()

    end = a.date or time.strftime('%Y%m%d')
    res = []
    for code, name in D.IDX_NAME.items():
        px = D.index_px(code, '20250401', end)
        lm = X.load_lmax(code, end)
        r = C.compute(code, name, px, xsec_lmax=lm,
                      ar_mode=a.ar, lam_mode=a.lam, k_mode=a.alpha, k_min=a.kmin,
                      abs_ratio=AB.load_ar(code, end))
        if r:
            res.append(r)
    s, text, sent = run(res, dry=a.dry, only_sys=not a.all,
                        push=not a.no_push,
                        channels=tuple(a.channels.split(',')))
    print(text or '(无 ≥3 指标告警，静默)')
    if sent:
        print(f'\n[sent] {sent}')
