#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
相变异常检测系统 · 第 2 层：计算层 (Compute Layer)
================================================
职责：只负责算五个指标 + 投票。不取数、不发消息、不写 JSON 状态。
输入：pws_data 提供的 DataFrame
输出：pws_compute.Result（DataFrame + meta）

五个指标（严格按用户 2026-11-03 提示词）
----------------------------------------
M1 AR(1) 恢复力衰减        告警: AR(1) > 0.6 且连续 5 日上升
M2 方差放大 Δσ             告警: Δσ > 1 × σ_std
M3 PSI 结构脆弱度          告警: PSI > PSI_90（过去 120 日 90 分位）
M4 α 幂律指数              告警: α < 1.8
M5 截面最大特征值 λmax     告警: 周环比 > 20%
综合: 5 个中至少 3 个同时告警 → 系统级告警

三个可切换口径（用户要求 A/B 对比）
----------------------------------
M1: ar_mode   'ret'(对日收益率) | 'resid'(对 log价格−MA20 残差)
M3: lam_mode  'drop'(丢弃负 ρ_k) | 'abs'(取 |ρ_k|)
M4: k_mode   'expand'(扩张窗口) | 'roll'(500日滚动)；k_min 可调(默认50)
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os
import sys
import math
import json
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------- 参数 ----------
AR_THR = 0.6
AR_RISE_DAYS = 3
AR_PERSIST = True      # 默认持续型（AR 贴近 1 后必然震荡，单调上升不可能）
AR_WIN = 20
# 2026-11-03 全历史检验后调整：2σ 的 lift 仅 0.9~1.1x（等于没信息），3σ 提到 1.19~1.28x
DSIG_MULT = 3.0
SIG_WIN = 20
SIG_SD_WIN = 20
LAM_WIN = 60
LAM_KMAX = 5
PSI_REF_WIN = 120
PSI_PCT = 90
PSI_MIN_OBS = 60
# M4 α 全历史永久失效（见 docs/pws_alpha_dead.md），阈值保留仅作记录
ALPHA_THR = 1.8
ALPHA_KMIN = 50
ALPHA_SIG_MULT = 2.5
ALPHA_SIG_HORIZON = 5
XSEC_WIN = 20
XSEC_STEP = 5
XSEC_WOW = 0.20
VOTE_SYS = 3

# ---- 2026-11-03 大佬定稿：M4 下线，告警口径改为「≥3 个指标同时亮」 ----
ENABLE_M4 = False          # α 永久失效（二十年零触发），不再计入票数
ALERT_VOTES = 3            # 可用指标里有 ≥3 个同时亮 → 触发提醒
# 计入票数的指标（M4 不在内）
VOTE_KEYS = ('m1_alert', 'm2_alert', 'm3_alert', 'm4_alert', 'm5_alert')


@dataclass
class Result:
    """计算层输出"""
    code: str
    name: str
    df: pd.DataFrame
    params: dict = field(default_factory=dict)

    @property
    def last(self):
        return self.df.iloc[-1].to_dict() if len(self.df) else {}

    def meta(self):
        d = self.last
        return {
            'code': self.code, 'name': self.name,
            'date': d.get('date'),
            'params': self.params,
        }


# ================= 指标 1: AR(1) =================
def m1_ar1(ret, close, mode='ret', win=AR_WIN):
    n = len(ret)
    out = np.full(n, np.nan)
    if mode == 'resid':
        lp = np.log(close)
        ma = pd.Series(lp).rolling(win).mean().to_numpy()
        src = lp - ma
        lo = win + 5
    else:
        src = ret
        lo = win
    for t in range(lo, n):
        x = src[t - win:t]
        y = src[t - win + 1:t + 1]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 10:
            continue
        xx, yy = x[ok], y[ok]
        vx = xx.var()
        if vx > 0:
            out[t] = np.cov(xx, yy, bias=True)[0, 1] / vx
    return out


def m1_alert(ar, thr=AR_THR, days=AR_RISE_DAYS, mode='rise', pct=None, ref=250):
    """M1 告警
    mode='rise'  原口径：AR>thr 且连续 days 日单调上升
    mode='persist' 持续口径：连续 days 日 AR>thr（AR 贴近 1 后必然震荡，单调上升不可能）
    均可选 pct：额外要求 AR 处于过去 ref 日的 pct 分位以上（避免太松）
    """
    n = len(ar)
    a = np.zeros(n, bool)
    for t in range(days - 1, n):
        if not (np.isfinite(ar[t]) and ar[t] > thr):
            continue
        seg = ar[t - days + 1:t + 1]
        if not np.all(np.isfinite(seg)):
            continue
        ok = np.all(seg > thr) if mode == 'persist' else np.all(np.diff(seg) > 0)
        if not ok:
            continue
        if pct is not None and t >= ref:
            w = ar[t - ref:t]
            w = w[np.isfinite(w)]
            if len(w) < ref * 0.5 or ar[t] < np.percentile(w, pct):
                continue
        a[t] = True
    return a


# ================= 指标 2: 方差放大 =================
def m2_dsig(ret, win=SIG_WIN, sd_win=SIG_SD_WIN):
    sig = pd.Series(ret).rolling(win).std(ddof=0).to_numpy()
    sd = pd.Series(sig).rolling(sd_win).std(ddof=0).to_numpy()
    n = len(ret)
    dsig = np.full(n, np.nan)
    for t in range(win, n):
        if np.isfinite(sig[t]) and np.isfinite(sig[t - win]):
            dsig[t] = sig[t] - sig[t - win]
    return sig, sd, dsig


def m2_alert(dsig, sd, mult=DSIG_MULT):
    n = len(dsig)
    a = np.zeros(n, bool)
    for t in range(n):
        if np.isfinite(dsig[t]) and np.isfinite(sd[t]) and sd[t] > 0:
            if dsig[t] > mult * sd[t]:
                a[t] = True
    return a


# ================= 指标 3: PSI =================
def m3_lambda(ret, win=LAM_WIN, kmax=LAM_KMAX, mode='drop'):
    n = len(ret)
    out = np.full(n, np.nan)
    for t in range(win, n):
        w = ret[t - win + 1:t + 1]
        w = w[np.isfinite(w)]
        if len(w) < win * 0.8:
            continue
        mu, v = w.mean(), w.var()
        if v <= 0:
            continue
        ks, logs = [], []
        for k in range(1, kmax + 1):
            a, b = w[:-k], w[k:]
            rho = ((a - mu) * (b - mu)).mean() / v
            if mode == 'abs':
                rho = abs(rho)
            if rho > 1e-6:
                ks.append(k)
                logs.append(math.log(rho))
        if len(ks) < 3:
            continue
        lam = -np.polyfit(ks, logs, 1)[0]
        out[t] = lam if lam > 1e-6 else np.nan
    return out


def m3_psi(sig, lam):
    with np.errstate(invalid='ignore', divide='ignore'):
        psi = np.where(np.isfinite(lam) & (lam > 0),
                       sig / np.square(np.where(lam > 0, lam, np.nan)), np.nan)
    return np.asarray(psi, float)


def m3_alert(psi, ref_win=PSI_REF_WIN, pct=PSI_PCT, min_obs=PSI_MIN_OBS):
    n = len(psi)
    thr = np.full(n, np.nan)
    a = np.zeros(n, bool)
    for t in range(ref_win, n):
        w = psi[t - ref_win:t]
        w = w[np.isfinite(w)]
        if len(w) >= min_obs:
            thr[t] = np.percentile(w, pct)
            if np.isfinite(psi[t]) and psi[t] > thr[t]:
                a[t] = True
    return thr, a


# ================= （已下线）α Hill 指数 —— 2026-11-03 大佬要求彻底去掉 M4 α =================
def _drawdowns(close):
    """局部峰谷配对，返回每段回撤幅度（正）"""
    n = len(close)
    if n < 5:
        return []
    ext = []
    for i in range(1, n - 1):
        if close[i] >= close[i - 1] and close[i] > close[i + 1]:
            ext.append((i, 'P'))
        elif close[i] <= close[i - 1] and close[i] < close[i + 1]:
            ext.append((i, 'T'))
    res, last_p = [], None
    for i, typ in ext:
        if typ == 'P':
            last_p = i
        elif typ == 'T' and last_p is not None:
            pk, tr = close[last_p], close[i]
            if pk > 0 and tr < pk:
                s = (pk - tr) / pk
                if s > 0:
                    res.append(s)
            last_p = None
    return res


def m4_alpha(close, ret, k_mode='expand', k_min=ALPHA_KMIN, rwin=500,
             sig_mult=ALPHA_SIG_MULT, sig_h=ALPHA_SIG_HORIZON):
    n = len(close)
    out = np.full(n, np.nan)
    kcnt = np.zeros(n, int)
    uu = np.full(n, np.nan)
    for t in range(60, n):
        lo = 0 if k_mode == 'expand' else max(0, t + 1 - rwin)
        c = close[lo:t + 1]
        dd = _drawdowns(c)
        if not dd:
            continue
        s = np.array(dd, float)
        r = ret[lo + 1:t + 1]
        r = r[np.isfinite(r)]
        if len(r) < 20:
            continue
        sig = r.std(ddof=0)
        u = max(np.percentile(s, 95), sig_mult * sig * math.sqrt(sig_h))
        tail = s[s > u]
        k = len(tail)
        kcnt[t] = k
        uu[t] = u
        if k < k_min or u <= 0:
            continue
        ml = np.mean(np.log(tail / u))
        if ml > 1e-9:
            out[t] = 1.0 / ml
    return out, kcnt, uu


def m4_alert(alpha, thr=ALPHA_THR):
    return np.isfinite(alpha) & (alpha < thr)


# ================= 指标 5: 截面 λmax =================
def xsec_lmax_calc(panel, win=XSEC_WIN, step=XSEC_STEP):
    """panel: index=date, columns=code, values=pct_chg"""
    out = {}
    idx = list(panel.index)
    for i in range(win, len(idx), step):
        sub = panel.iloc[i - win + 1:i + 1]
        sub = sub.dropna(axis=1, thresh=int(win * 0.95)).dropna()
        if sub.shape[1] < 10:
            continue
        C = np.corrcoef(sub.to_numpy(float).T)
        C = np.nan_to_num(C, nan=0.0)
        out[idx[i]] = float(np.linalg.eigvalsh(C)[-1])
    return pd.Series(out).sort_index()


def xsec_alert(lmax, wow_thr=XSEC_WOW):
    wow = lmax.pct_change()
    a = (wow > wow_thr).fillna(False)
    return wow, a.astype(int)


def xsec_align(df_dates, lmax, alert):
    """把稀疏的 λmax 观测点对齐到每个交易日（取最近一个已观测点）"""
    if lmax is None or len(lmax) == 0:
        return np.zeros(len(df_dates), int), np.full(len(df_dates), np.nan)
    obs = list(lmax.index)
    av = alert.to_dict()
    lv = lmax.to_dict()
    A, L = [], []
    j = -1
    for d in df_dates:
        while j + 1 < len(obs) and obs[j + 1] <= d:
            j += 1
        if j >= 0:
            A.append(int(av.get(obs[j], 0)))
            L.append(lv.get(obs[j]))
        else:
            A.append(0); L.append(np.nan)
    return np.array(A), np.array(L, float)


# ================= 主计算 =================
def compute(code, name, px, panel=None, xsec_lmax=None,
            ar_mode='ret', lam_mode='drop', k_mode='expand',
            k_min=ALPHA_KMIN, params_override=None,
            ar_days=AR_RISE_DAYS, ar_persist=True, ar_pct=None, dsig_mult=None,
            abs_ratio=None):
    """算指标 → 返回 Result

    abs_ratio: 吸收比率 AR_dm 序列（pws_absratio.load_ar），给了就算 M6 并启用双通道
    """
    if px is None or len(px) < 100:
        return None
    close = px.close.to_numpy(float)
    ret = px.ret.to_numpy(float)
    dates = px.trade_date.tolist()
    n = len(close)

    ar = m1_ar1(ret, close, ar_mode)
    ar_a = m1_alert(ar, AR_THR, ar_days, 'persist' if ar_persist else 'rise', ar_pct)
    sig, sd, dsig = m2_dsig(ret)
    var_a = m2_alert(dsig, sd, dsig_mult if dsig_mult is not None else DSIG_MULT)
    lam = m3_lambda(ret, mode=lam_mode)
    psi = m3_psi(sig, lam)
    psi_thr, psi_a = m3_alert(psi)
    # 注：α 已下线不再计算（M4 α 二十年零触发，大佬 2026-11-03 要求彻底去掉）
    alpha = ak = au = None
    a_a = np.zeros(n, int)

    # ---- 指标编号（2026-11-03 大佬定：去掉 α，剩 5 个重新编为 M1~M5）----
    # M1 = AR(1) 恢复力衰减      M2 = Δσ 波动放大     M3 = PSI 结构脆弱度
    # M4 = λmax 截面突变          M5 = 吸收比率 λ1/N
    # 指标5（λmax）
    if xsec_lmax is not None:
        wow, xa = xsec_alert(xsec_lmax)
        x4_a, lmax_arr = xsec_align(dates, xsec_lmax, xa)
    else:
        x4_a, lmax_arr, wow = np.zeros(n, int), np.full(n, np.nan), None

    # 指标5：吸收比率 λ1/N（去共同因子，本机 struct_tag 口径）
    if abs_ratio is not None:
        import pws_absratio as AB
        # close 传入 → 启用方向约束（暴跌途中不误报，2026-11-03 实测必需）
        m5_a, m5_v, m5_thr = AB.alert(abs_ratio, dates, close=close)
        ar5 = m5_a.astype(int)
    else:
        ar5, m5_v, m5_thr = np.zeros(n, int), np.full(n, np.nan), np.full(n, np.nan)

    # 注：m1_ar1 / m2_dsig / m3_psi 内部键名不再带 M4（α）；α 完全不参与
    votes = ar_a.astype(int) + var_a.astype(int) + psi_a.astype(int) + x4_a + ar5
    sys_a = votes >= ALERT_VOTES
    streak = np.zeros(n, int)
    for t in range(n):
        streak[t] = (streak[t - 1] + 1) if (t > 0 and sys_a[t]) else (1 if sys_a[t] else 0)

    # ---- 双通道（2026-11-03 定稿）----
    # 通道A 时序/波动（抓 7月型广谱顶）：M1 恢复力衰减 AND M2 波动放大
    # 通道B 截面/抱团（抓 10月型抱团顶）：M5 吸收比率破 120 日 P90
    chA = (ar_a & var_a).astype(int)
    chB = ar5
    dual = chA | chB
    dual_lv = np.where(chA & chB, 2, np.where(dual, 1, 0)).astype(int)   # 2=双道共振 1=单道

    df = pd.DataFrame({
        'date': dates, 'close': close, 'ret': ret,
        'm1_ar': ar, 'm1_alert': ar_a.astype(int),
        'm2_sig': sig, 'm2_sd': sd, 'm2_dsig': dsig, 'm2_alert': var_a.astype(int),
        'm3_lam': lam, 'm3_psi': psi, 'm3_psi_thr': psi_thr, 'm3_alert': psi_a.astype(int),
        'm4_lmax': lmax_arr, 'm4_alert': x4_a,
        'm5_ar': m5_v, 'm5_thr': m5_thr, 'm5_alert': ar5,
        'votes': votes, 'sys_alert': sys_a.astype(int), 'streak': streak,
        'chA': chA, 'chB': chB, 'dual': dual, 'dual_lv': dual_lv,
    })
    p = dict(ar_mode=ar_mode, lam_mode=lam_mode, k_mode=k_mode, k_min=k_min,
             vote_sys=ALERT_VOTES, ar_thr=AR_THR, alpha_thr=ALPHA_THR,
             enable_m4=ENABLE_M4, vote_keys=list(VOTE_KEYS),
             xsec_wow=XSEC_WOW, psi_pct=PSI_PCT,
             ar_days=ar_days, ar_persist=ar_persist, ar_pct=ar_pct,
             dsig_mult=(dsig_mult if dsig_mult is not None else DSIG_MULT),
             abs_ratio=abs_ratio is not None)
    if params_override:
        p.update(params_override)
    return Result(code=code, name=name, df=df, params=p)


# ================= 报告辅助 =================
def snapshot(r: Result, idx=None):
    """某日的五指标快照"""
    row = r.df.iloc[idx] if idx is not None else r.df.iloc[-1]
    def f(x, d=3):
        return None if (x is None or (isinstance(x, float) and not np.isfinite(x))) else round(float(x), d)
    return {
        'date': row['date'], 'close': f(row['close'], 2),
        'm1_ar': f(row['m1_ar']), 'm1': int(row['m1_alert']),
        'm2_dsig': f(row['m2_dsig'], 5), 'm2': int(row['m2_alert']),
        'm3_psi': f(row['m3_psi']), 'm3_thr': f(row['m3_psi_thr']), 'm3': int(row['m3_alert']),
        'm4_lmax': f(row['m4_lmax']), 'm4': int(row['m4_alert']),
        'm5_ar': f(row.get('m5_ar'), 4), 'm5_thr': f(row.get('m5_thr'), 4),
        'm5': int(row.get('m5_alert', 0)),
        'chA': int(row.get('chA', 0)), 'chB': int(row.get('chB', 0)),
        'dual': int(row.get('dual', 0)), 'dual_lv': int(row.get('dual_lv', 0)),
        'votes': int(row['votes']), 'sys': int(row['sys_alert']),
    }


if __name__ == '__main__':
    import argparse
    import pws_data as D
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--ar', default='ret', choices=['ret', 'resid'])
    ap.add_argument('--lam', default='drop', choices=['drop', 'abs'])
    ap.add_argument('--alpha', default='expand', choices=['expand', 'roll'])
    ap.add_argument('--kmin', type=int, default=ALPHA_KMIN)
    a = ap.parse_args()
    import pws_xsec
    end = a.date or pd.Timestamp.now().strftime('%Y%m%d')
    for code, name in D.IDX_NAME.items():
        px = D.index_px(code, '20250401', end)
        lm = pws_xsec.load_lmax(code, end)
        r = compute(code, name, px, xsec_lmax=lm,
                    ar_mode=a.ar, lam_mode=a.lam, k_mode=a.alpha, k_min=a.kmin)
        if r:
            print(f"{name:<8} {json.dumps(snapshot(r), ensure_ascii=False)}")
