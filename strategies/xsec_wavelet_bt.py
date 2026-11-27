# -*- coding: utf-8 -*-
"""三栏信号胜率/收益回测（等名义额 topk 版）"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import xsec_wavelet_sim as S

np.seterr(all='ignore')

def bt(W, R, lev, cost_bp, name):
    """W/ R 对齐；T日信号→T+1持仓收益。返回统计"""
    W = W.reindex(R.index).fillna(0.0)
    w = W.shift(1).fillna(0.0)
    turn = (W.shift(1) - W.shift(2)).abs().sum(axis=1).fillna(0.0)
    gross = (w * R.fillna(0.0)).sum(axis=1)
    cost = turn * cost_bp / 10000.0
    net = (gross - cost) * lev
    net = net.dropna()
    net = net[net.index > pd.Timestamp('2019-01-01')]
    if len(net) < 50:
        print(f'{name}: 样本不足'); return None
    ann = net.mean() * 252
    vol = net.std() * np.sqrt(252) + 1e-12
    sharpe = ann / vol
    eq = (1 + net).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    win = (net > 0).mean()
    t = net.mean() / (net.std() / np.sqrt(len(net)) + 1e-12)
    print(f'{name:12s} 年化{ann*100:+6.2f}%  夏普{sharpe:5.2f}  回撤{dd*100:6.1f}%  日胜率{win*100:4.1f}%  t={t:5.2f}  n={len(net)}')
    return dict(ann=ann, sharpe=sharpe, dd=dd, win=win, t=t)

def main():
    tg = S.build_targets('2026-10-14', no_fetch=True)
    print('--- 各栏信号回测（2019起，含成本，杠杆后）---')
    if 'basis' in tg:
        d = tg['basis']; bt(d['W'], d['R'], S.LEV['basis'], S.COST['basis'], '期货栏')
    if 'wavelet' in tg:
        d = tg['wavelet']; bt(d['W'], d['R'], S.LEV['wavelet'], S.COST['wavelet'], '小波栏')
    if 'commodity' in tg:
        d = tg['commodity']; bt(d['W'], d['R'], S.LEV['commodity'], S.COST['commodity'], '估值栏')

    # 小波栏：逐信号（价/量/OI）分解测试
    from xsec_wavelet_sim import load_commodity_panel, _topk_W
    C, O, V, meta = load_commodity_panel(no_fetch=True)
    R = np.log(C).diff()
    print('\n--- 小波栏：分项信号（多空各K=6）---')
    for tag in ['sigP', 'sigV', 'sigO', 'comb']:
        if tag == 'comb':
            Sdf, _, _, _ = None, None, None, None
        pass
    # 用已算的 S（组合方向）
    Sc = pd.read_csv(os.path.join(S.CACHE, 'wavelet_scores.csv'), index_col=0, parse_dates=True).reindex(C.index)
    z = Sc.sub(Sc.mean(axis=1), axis=0).div(Sc.std(axis=1) + 1e-12, axis=0)
    for K in (4, 6, 8, 10, 12):
        W = _topk_W(z, K); bt(W, R, S.LEV['wavelet'], S.COST['wavelet'], f'小波组合K={K}')
    # 估值栏不同K
    print('\n--- 估值栏 K 敏感性 ---')
    mom = np.log(C).diff(60); sig = -mom
    for K in (4, 6, 8, 10, 12):
        W = _topk_W(sig, K); bt(W, R, S.LEV['commodity'], S.COST['commodity'], f'估值K={K}')

if __name__ == '__main__':
    main()
