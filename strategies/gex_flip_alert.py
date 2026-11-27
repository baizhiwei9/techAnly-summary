#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gex_flip_alert.py —— 科创50ETF GEX / Gamma Flip 每日报警 CLI
================================================================================

一句话：读期权链缓存 → 算当日 30/90 加权净 GEX + Gamma Flip + 分位 → 满足条件就发邮件。

典型用法
--------
    # 只看不算账（不发邮件，打印完整邮件正文）
    python3 strategies/gex_flip_alert.py --date 20261023 --dry-run

    # 正式跑（发邮件）
    python3 strategies/gex_flip_alert.py

    # 回补序列（从某天算到 --date），用于首次建历史分位
    python3 strategies/gex_flip_alert.py --start 20251023 --date 20261023

    # 调阈值 / 换收件人 / 强制发送
    python3 strategies/gex_flip_alert.py --percentile 10 --window 120 --email-to a@b.com
    python3 strategies/gex_flip_alert.py --force-send        # 即使不报警也发（日报模式）

报警逻辑（详见 docs/GEX_FLIP_ALERT_DESIGN.md）
-----------------------------------------------
    A: gex_weighted < 0                    净 GEX 为负 → 做市商追涨杀跌
    B: pctile(dist) <= --percentile(5)       Flip 距离处于历史低位 → 贴近翻转点
    红 = A and B ；黄 = --also-alert-flip-breach 且 dist<0（已跌破 Flip）
    去重：同级别且距上次报警 < --repeat-hours(20) 小时不重复发

退出码
------
    0 正常（无论是否报警）   1 未预期异常（已发「数据异常」邮件）
    2 数据缺失/不可用（已发「数据异常」邮件）
"""
from __future__ import annotations


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional

import pandas as pd

# --- 让 `python3 strategies/gex_flip_alert.py` / `python3 -m bin.gex_flip_alert` 都能 import
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import gex_core as gc  # noqa: E402

WS = os.path.dirname(_HERE)
OUT = os.path.join(WS, 'output')
MEM = os.path.join(WS, 'memory')
CACHE = os.path.join(WS, '.cache')

HIST = os.path.join(OUT, 'opt588000_hist_cache.csv')
ETF_CSV = os.path.join(OUT, 'kc50_etf_close.csv')
SERIES_CSV = os.path.join(OUT, 'gex_flip_series.csv')   # 本脚本自有序列（不覆盖 opt_gex.py 的 kc50_gex_w.csv）
STATE_JSON = os.path.join(MEM, 'gex_alert_state.json')
LOG = os.path.join(CACHE, 'gex_alert.log')
SEND_EMAIL = os.path.join(WS, 'scripts', 'send_email.py')

SERIES_COLS = ['trade_date', 'S', 'gex30', 'gex90', 'gex_weighted', 'gex_mix_simple',
               'n_legs', 'n_maturities', 'dte_list', 'interp_mode', 'quality',
               'flip_1', 'flip_2', 'flip_count', 'dist']


# ============================================================ 小工具
def log(msg: str) -> None:
    """同时打到 stdout 和 .cache/gex_alert.log（失败不致命）。"""
    line = f'[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}'
    print(line)
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def load_state() -> Dict:
    if os.path.exists(STATE_JSON):
        try:
            return json.load(open(STATE_JSON, encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(st: Dict) -> None:
    os.makedirs(MEM, exist_ok=True)
    tmp = STATE_JSON + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_JSON)


def load_inputs() -> tuple:
    """返回 (chain, etf_map, trade_dates)。数据缺失抛 FileNotFoundError。"""
    if not os.path.exists(HIST):
        raise FileNotFoundError(f'缺期权链缓存: {HIST}')
    if not os.path.exists(ETF_CSV):
        raise FileNotFoundError(f'缺 ETF 收盘: {ETF_CSV}')
    chain = gc.prepare_chain(pd.read_csv(HIST))
    etf = pd.read_csv(ETF_CSV, dtype={'trade_date': int})
    etf['trade_date'] = pd.to_numeric(etf['trade_date'], errors='coerce')
    etf = etf.dropna(subset=['trade_date', 'close'])
    etf_map = {int(k): float(v) for k, v in zip(etf.trade_date, etf.close)}
    # 交易日 = 期权链里有快照、且 ETF 有收盘
    dates = sorted({int(d) for d in chain.trade_date.unique() if int(d) in etf_map})
    return chain, etf_map, dates


def read_series_cache() -> Dict[int, Dict]:
    """读已算好的序列（增量复用，避免每次重算 243 天）。"""
    if not os.path.exists(SERIES_CSV):
        return {}
    try:
        df = pd.read_csv(SERIES_CSV, dtype={'trade_date': int})
    except (OSError, pd.errors.ParserError):
        return {}
    return {int(r['trade_date']): r.to_dict() for _, r in df.iterrows()}


def write_series_cache(rows: Dict[int, Dict]) -> None:
    if not rows:
        return
    df = pd.DataFrame([rows[k] for k in sorted(rows)])
    for c in SERIES_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[SERIES_COLS]
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(SERIES_CSV, index=False, encoding='utf-8-sig')


# ============================================================ 序列 / 分位
def ensure_series(chain: pd.DataFrame, etf_map: Dict[int, float],
                  dates: List[int], date: int, window: int, args) -> Dict[int, Dict]:
    """确保 [date-window 前的一段, date] 的 dist 序列都已算好（带缓存增量）。

    说明：分位只用到最近 window 个交易日的 dist，但缓存保留全部历史，方便回看。
    """
    cache = read_series_cache()
    idx = dates.index(date)
    lo = max(0, idx - int(window * 1.6) - 5)      # 多留一点余量，防止节假日/缺日
    need = dates[lo:idx + 1]
    # 只要缓存里有这一天就不再重算——即便 dist 是 NaN（当日无有效腿，属正常数据缺口）
    missing = [d for d in need if d not in cache]
    if args.start:
        back = [d for d in dates if int(args.start) <= d <= date]
        missing = sorted(set(missing) | {d for d in back if d not in cache})
    if missing:
        log(f'序列增量计算 {len(missing)} 天（缓存命中 {len(need) - len(missing)} / {len(need)}）…')
    for d in missing:
        snap = gc.daily_snapshot(chain, d, etf_map[d], r=args.r, q=args.q,
                                 dte_lo=args.dte_lo, dte_hi=args.dte_hi)
        cache[d] = {k: snap.get(k) for k in SERIES_COLS}
        cache[d]['trade_date'] = d
    if missing:
        write_series_cache(cache)
    return cache


def percentile_for(series: Dict[int, Dict], dates: List[int], date: int,
                   window: int, min_periods: int) -> tuple:
    """当前 dist 在过去 window 个交易日（不含当日）中的百分位排名。

    Returns (pct, n_used, short)  short=True 表示样本不足 min_periods。
    """
    idx = dates.index(date)
    past = dates[max(0, idx - window):idx]
    vals = [series[d]['dist'] for d in past
            if d in series and series[d].get('dist') is not None
            and pd.notna(series[d]['dist'])]
    pct, n = gc.percentile_rank(vals, float(series[date]['dist']))
    return pct, n, n < min_periods


# ============================================================ 判断
def decide(snap: Dict, pct: float, n_pct: int, short: bool, args) -> Dict:
    """返回 {'level': 'red'|'yellow'|'none', 'reasons': [...], 'warnings': [...]}"""
    gw = snap['gex_weighted']
    dist = snap['dist']
    quality = snap['quality']
    reasons: List[str] = []
    warnings: List[str] = []

    cond_a = bool(pd.notna(gw) and gw < 0)
    cond_b = bool(pd.notna(pct) and pct <= args.percentile and pd.notna(dist))
    breach = bool(pd.notna(dist) and dist < 0)

    if cond_a:
        reasons.append(f'净GEX<0（{gw:+.4f} 亿）')
    if cond_b:
        reasons.append(f'Flip分位 {pct:.1f}% ≤ {args.percentile}%')
    if breach:
        reasons.append(f'现价已跌破 Flip（dist {dist:+.2f}%）')

    if quality == 'low':
        warnings.append(f'数据质量 low：当日仅 {snap["n_maturities"]} 个到期月在 30~180 天区间，'
                        f'30/90 插值退化为单点，Flip 结论不可靠')
    if short:
        warnings.append(f'历史样本不足（{n_pct} 天 < {args.min_periods} 天），分位参考性弱')

    level = 'none'
    if cond_a and cond_b:
        level = 'red'
        if quality == 'low':
            level = 'yellow'
            warnings.append('红→黄降级：因 low quality，仅保留 GEX 方向判断')
    elif args.also_alert_flip_breach and breach:
        level = 'yellow'
    return dict(level=level, reasons=reasons, warnings=warnings,
                cond_a=cond_a, cond_b=cond_b, breach=breach)


def should_send(level: str, date: int, args, state: Dict) -> tuple:
    """去重：同级别、间隔 < repeat_hours 且非强制 → 不发。返回 (send, why)。"""
    if level == 'none' and not args.force_send:
        return False, '未触发报警（如需日报加 --force-send）'
    if args.force_send:
        return True, '--force-send 强制发送'
    last = state.get('last', {})
    if last.get('level') == level:
        try:
            prev = _dt.datetime.fromisoformat(last['ts'])
            gap_h = (_dt.datetime.now() - prev).total_seconds() / 3600.0
        except (KeyError, ValueError):
            gap_h = 1e9
        if gap_h < args.repeat_hours:
            return False, f'同级别 {level} 已于 {last.get("date")} 报警，间隔 {gap_h:.1f}h < {args.repeat_hours}h，跳过'
    return True, '首次触发或已过冷却期'


# ============================================================ 邮件正文
def fmt_amt(x: float) -> str:
    return '—' if pd.isna(x) else f'{x:+.4f}'


def fmt_pct(x: float) -> str:
    return '—' if pd.isna(x) else f'{x:+.2f}%'


def build_email(snap: Dict, pct: float, n_pct: int, dec: Dict, args,
               date: int, prev: Optional[Dict] = None) -> tuple:
    """组装 (标题, Markdown 正文)。"""
    level = dec['level']
    icon = {'red': '🔴', 'yellow': '🟡', 'none': '⚪'}[level]
    word = {'red': '红色报警', 'yellow': '黄色预警', 'none': '无报警'}[level]
    gw = snap['gex_weighted']
    flip = snap['flip_1']
    dist = snap['dist']
    flip_txt = '无零点' if flip is None else f'{flip:.4f}'
    d_iso = f'{date // 10000:04d}-{date // 100 % 100:02d}-{date % 100:02d}'
    pct_txt = f'{pct:.1f}%' if pd.notna(pct) else '—'
    kind = {'red': '报警', 'yellow': '预警', 'none': '日报'}[level]
    subj = (f'{icon} 科创50 GEX {kind} {d_iso} ｜ 净GEX {fmt_amt(gw)}亿 ｜ '
            f'Flip {flip_txt}（分位 {pct_txt}）')

    d = str(date)
    dl = f'{d[:4]}-{d[4:6]}-{d[6:]}'
    chg = ''
    if prev is not None and pd.notna(prev.get('gex_weighted')):
        chg = f'- 前一交易日净 GEX：{prev["gex_weighted"]:+.4f} 亿'

    L: List[str] = []
    L.append(f'## {icon} {dl} 科创50ETF 期权 GEX / Gamma Flip　**{word}**')
    L.append('')
    L.append('### 一、结论')
    L.append('')
    if level == 'red':
        L.append('> <font color="red">**红色报警：净 GEX 转负 + Flip 距离处于历史极低分位，'
                 '做市商 gamma 已切换到「追涨杀跌」状态，且现价紧贴/已跌破翻转点。**</font>')
        L.append('> ')
        L.append('> 含义：标的下跌时做市商需卖出对冲 → 放大跌幅；上涨时买入 → 放大涨幅。'
                 '此区间波动率易放大，注意仓位与止损。')
    elif level == 'yellow':
        L.append('> <font color="orange">**黄色预警：现价已跌破 Gamma Flip，'
                 '正 gamma 缓冲垫消失，波动放大风险上升。**</font>')
    else:
        if dec['cond_a'] is False:
            L.append('> 净 GEX 为正，做市商在涨跌两端都逆向对冲（压波动、钉住价格），'
                     '当前无系统性放大风险。')
        else:
            L.append('> 净 GEX 虽为负，但现价距离 Flip 仍有安全距离，未达报警阈值。')
    L.append('')
    L.append('触发原因：' + ('；'.join(dec['reasons']) if dec['reasons'] else '无（条件未满足）'))
    L.append('')

    L.append('### 二、关键指标')
    L.append('')
    L.append('| 指标 | 数值 | 说明 |')
    L.append('| --- | --- | --- |')
    L.append(f'| 标的收盘 S | {snap["S"]:.4f} | 588000 收盘价 |')
    L.append(f'| gex30 | {fmt_amt(snap["gex30"])} 亿 | 恒定期限 30 天插值 |')
    L.append(f'| gex90 | {fmt_amt(snap["gex90"])} 亿 | 恒定期限 90 天插值 |')
    L.append(f'| **gex_weighted** | **{fmt_amt(gw)} 亿** | 0.5·gex30 + 0.5·gex90（报警用） |')
    L.append(f'| gex_mix_simple | {fmt_amt(snap["gex_mix_simple"])} 亿 | 30~180 天直接加总（对照） |')
    L.append(f'| Gamma Flip | {flip_txt} | 净 GEX 曲线过零点（最近现价者） |')
    L.append(f'| 零点个数 | {snap["flip_count"]} | 全曲线扫描 0.90S~1.10S |')
    L.append(f'| Flip 距离 dist | {fmt_pct(dist)} | (S−Flip)/S，正=Flip 在下方（安全垫） |')
    L.append(f'| dist 分位 | {pct:.1f}% | 过去 {n_pct} 个交易日排名，越低越危险 |')
    L.append(f'| 有效腿 / 到期月 | {snap["n_legs"]} / {snap["n_maturities"]} | DTE {snap["dte_list"]} |')
    L.append(f'| 插值方式 | {snap["interp_mode"]} | 30 / 90 各自取值方式 |')
    L.append(f'| 数据质量 | {snap["quality"]} | {"≥2 个到期月，插值有效" if snap["quality"] == "ok" else "单月，插值退化"} |')
    L.append('')

    L.append('### 三、报警条件判定')
    L.append('')
    L.append('| 条件 | 阈值 | 实际 | 结果 |')
    L.append('| --- | --- | --- | --- |')
    L.append(f'| A 净 GEX | < 0 | {fmt_amt(gw)} 亿 | {"✅ 满足" if dec["cond_a"] else "❌ 不满足"} |')
    L.append(f'| B Flip 分位 | ≤ {args.percentile}% | {pct:.1f}% | {"✅ 满足" if dec["cond_b"] else "❌ 不满足"} |')
    L.append(f'| 补充 跌破 Flip | dist < 0 | {fmt_pct(dist)} | {"⚠️ 是" if dec["breach"] else "否"} |')
    L.append('')
    L.append(f'A + B 同时满足 → 红色；仅跌破 Flip（需开 --also-alert-flip-breach）→ 黄色。'
             f'本次判定：**{word}**。')
    L.append('')

    n_sec = 5 if dec['warnings'] else 4
    if dec['warnings']:
        L.append('### 四、数据提示')
        L.append('')
        for w in dec['warnings']:
            L.append(f'- <font color="orange">{w}</font>')
        L.append('')

    L.append(f'### {"一二三四五"[n_sec - 1]}、口径备注')
    L.append('')
    L.append(f'- 期限：剔除 DTE<{args.dte_lo} 近月与 DTE>{args.dte_hi} 远月，30/90 等权插值')
    L.append(f'- 定价：BSM 由中价反解 IV（close>0 用 close，否则 settle），r={args.r}，q={args.q}，乘数 10000')
    L.append('- 单位：亿元 / 每 1% 标的变动；Call 记正、Put 记负（dealer 视角）')
    L.append(f'- 分位窗口：{args.window} 个交易日（min_periods={args.min_periods}）')
    if chg:
        L.append(chg)
    L.append('')
    L.append('---')
    L.append('')
    L.append('<font color="grey">本邮件由 bin/gex_flip_alert.py 自动生成 ｜ 设计文档 docs/GEX_FLIP_ALERT_DESIGN.md'
             ' ｜ 不构成投资建议</font>')
    return subj, '\n'.join(L)


def send_mail(subject: str, body: str, args) -> bool:
    """调用现成 scripts/send_email.py（不自己写 SMTP）。

    正文走临时文件（--body-file）而不是 --body：文章体量大时命令行参数会太长，
    也避免正文里的引号/中文标点被 shell 解析。
    """
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.md', prefix='gex_alert_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(body)
        cmd = [sys.executable, SEND_EMAIL, '--subject', subject,
               '--body-file', tmp, '--html']
        if args.email_to:
            cmd += ['--to', args.email_to]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        log(f'❌ 发信异常: {e}')
        return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if p.returncode != 0:
        log(f'❌ 发信失败 rc={p.returncode}: {(p.stderr or p.stdout)[-500:]}')
        return False
    log(f'✅ 邮件已发送 → {args.email_to or "默认收件人"}')
    return True


def send_data_error(err: str, args, date: Optional[int] = None) -> None:
    """数据异常也要发信，避免静默失败。"""
    if args.no_email or args.dry_run:
        log(f'[dry-run] 数据异常邮件跳过：{err}')
        return
    subj = f'⚠️ 科创50 GEX 报警任务失败 {date or ""}'.strip()
    body = ('## ⚠️ GEX 报警任务数据异常\n\n'
            f'- 时间：{_dt.datetime.now():%Y-%m-%d %H:%M:%S}\n'
            f'- 日期参数：{date}\n'
            f'- 错误：`{err}`\n\n'
            '请检查 `output/opt588000_hist_cache.csv` 与 `output/kc50_etf_close.csv` 是否已更新。\n')
    send_mail(subj, body, args)


# ============================================================ 主流程
def run(args) -> int:
    chain, etf_map, dates = load_inputs()
    date = int(args.date) if args.date else dates[-1]
    if date not in etf_map:
        raise ValueError(f'ETF 收盘无 {date}')
    if date not in dates:
        # 允许期权链当日缺快照时明确报错（不静默用前一日）
        raise ValueError(f'期权链快照无 {date}（最新 {dates[-1]}）')

    series = ensure_series(chain, etf_map, dates, date, args.window, args)
    snap = gc.daily_snapshot(chain, date, etf_map[date], r=args.r, q=args.q,
                             dte_lo=args.dte_lo, dte_hi=args.dte_hi)
    if snap['quality'] == 'empty' or snap['n_legs'] == 0:
        raise ValueError(f'{date} 无有效腿（IV 全部反解失败？）')

    # 当日 dist 回写缓存（daily_snapshot 与序列同参，值应一致）
    series[date] = {k: snap.get(k) for k in SERIES_COLS}
    series[date]['trade_date'] = date

    pct, n_pct, short = percentile_for(series, dates, date, args.window, args.min_periods)
    idx = dates.index(date)
    prev = series.get(dates[idx - 1]) if idx > 0 else None

    dec = decide(snap, pct, n_pct, short, args)
    subj, body = build_email(snap, pct, n_pct, dec, args, date, prev)

    log('=' * 78)
    log(f'{date}  S={snap["S"]:.4f}  gex30={snap["gex30"]:+.4f}  gex90={snap["gex90"]:+.4f}  '
        f'gex_weighted={snap["gex_weighted"]:+.4f}  mix={snap["gex_mix_simple"]:+.4f}')
    log(f'      flip={snap["flip_1"]}  dist={snap["dist"]:+.2f}%  pctile={pct:.1f}% (n={n_pct})  '
        f'quality={snap["quality"]}  legs={snap["n_legs"]}@{snap["dte_list"]}')
    log(f'判定: {dec["level"].upper()}  |  A(净GEX<0)={dec["cond_a"]}  B(分位≤{args.percentile}%)={dec["cond_b"]}  '
        f'breach={dec["breach"]}')
    log(f'标题: {subj}')

    # 快照落盘（含判定与分位，便于事后复现）
    out = {k: v for k, v in snap.items() if k not in ('legs', 'meta', 'curve')}
    out.update(trade_date=date, pctile=pct, pctile_n=n_pct, level=dec['level'],
               reasons=dec['reasons'], warnings=dec['warnings'],
               r=args.r, q=args.q, dte_lo=args.dte_lo, dte_hi=args.dte_hi,
               window=args.window, threshold_pct=args.percentile)
    os.makedirs(OUT, exist_ok=True)
    snap_path = os.path.join(OUT, f'gex_alert_snapshot_{date}.json')
    with open(snap_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    log(f'快照 → {snap_path}')
    write_series_cache(series)

    do_send, why = should_send(dec['level'], date, args, load_state())
    log(f'发送判定: {"发" if do_send else "不发"} — {why}')

    if args.dry_run:
        print('\n' + '─' * 78)
        print(f'【邮件标题】{subj}')
        print('─' * 78)
        print(body)
        print('─' * 78)
        print(f'[dry-run] 未发送邮件（正文 {len(body)} 字）')
        return 0

    if not do_send or args.no_email:
        return 0
    if not send_mail(subj, body, args):
        return 1
    st = load_state()
    st['last'] = dict(level=dec['level'], date=date,
                      ts=_dt.datetime.now().isoformat(timespec='seconds'),
                      gex_weighted=snap['gex_weighted'],
                      flip_1=snap['flip_1'], dist=snap['dist'], pctile=pct)
    save_state(st)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description='科创50ETF GEX / Gamma Flip 日报与报警',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('--date', type=int, default=None, help='交易日 YYYYMMDD，默认最新')
    ap.add_argument('--start', type=int, default=None, help='回补序列起始日 YYYYMMDD')
    ap.add_argument('--dry-run', action='store_true', help='不发邮件，打印完整正文')
    ap.add_argument('--force-send', action='store_true', help='即使不报警也发（日报模式/绕过冷却）')
    ap.add_argument('--no-email', action='store_true', help='完全禁止发信（含异常邮件）')
    ap.add_argument('--email-to', default=None, help='收件人（默认用 scripts/email_config.json）')
    ap.add_argument('--percentile', type=float, default=5.0, help='Flip 分位阈值（%）')
    ap.add_argument('--window', type=int, default=252, help='分位窗口（交易日）')
    ap.add_argument('--min-periods', type=int, default=60, help='分位最少样本数')
    ap.add_argument('--repeat-hours', type=float, default=20.0, help='同级别报警冷却小时')
    ap.add_argument('--also-alert-flip-breach', action='store_true',
                    help='GEX≥0 但跌破 Flip 也发黄色预警')
    ap.add_argument('--r', type=float, default=gc.DEF_R,
                    help='无风险利率（默认 0.015，与 kc50_gex_w.csv 对齐；0.02 与 opt_gex_flip.py 对齐）')
    ap.add_argument('--q', type=float, default=gc.DEF_Q, help='股息率')
    ap.add_argument('--dte-lo', type=int, default=gc.DEF_DTE_LO, dest='dte_lo')
    ap.add_argument('--dte-hi', type=int, default=gc.DEF_DTE_HI, dest='dte_hi')
    return ap


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except (FileNotFoundError, ValueError) as e:
        log(f'❌ 数据异常: {e}')
        try:
            send_data_error(str(e), args, args.date)
        except Exception as e2:                      # 邮件失败也不能崩掉 cron
            log(f'❌ 异常通知也失败: {e2}')
        return 2
    except Exception as e:                            # noqa: BLE001
        import traceback
        log('❌ 未预期异常:\n' + traceback.format_exc())
        try:
            send_data_error(f'{type(e).__name__}: {e}', args, args.date)
        except Exception:
            pass
        return 1


if __name__ == '__main__':
    sys.exit(main())
