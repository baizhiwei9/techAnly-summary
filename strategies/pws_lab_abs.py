#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""吸收比率 AR = λ1/N —— 与本机 pool_topo/struct_tag 完全同源口径（Kritzman）
关键：它和现在相变系统的 M1「AR(1) 时间序列自相关」是两个完全不同的东西，只是都缩写 AR。
本模块把本机口径算成逐日序列，供对齐 / 回测用。

优化：池子过大时按固定种子抽样（默认 300 只），λ1 用 eigvalsh(float32)。
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json
import numpy as np
import pandas as pd

_BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
WS = os.environ.get("OPENCLAW_WORKSPACE") or __import__("ocsecrets").workspace()
LAB = os.path.join(WS, '.cache', 'pws_lab')
os.makedirs(LAB, exist_ok=True)


def ar_series(panel, codes, win=20, step=1, cap=300, seed=7, min_stock=30):
    cols = [c for c in codes if c in panel.columns]
    if len(cols) > cap:
        rng = np.random.default_rng(seed)
        cols = sorted(rng.choice(cols, cap, replace=False))
    if len(cols) < min_stock:
        return None
    sub = panel[cols]
    idx = list(sub.index)
    out = []
    for i in range(win - 1, len(idx), step):
        w = sub.iloc[i - win + 1:i + 1]
        w = w.dropna(axis=1, thresh=int(win * 0.95)).dropna()
        if w.shape[1] < min_stock:
            out.append((idx[i], np.nan, 0))
            continue
        C = np.corrcoef(w.to_numpy(np.float32).T)
        C = np.nan_to_num(C, nan=0.0)
        np.fill_diagonal(C, 1.0)
        lam1 = float(np.linalg.eigvalsh(C.astype(np.float32))[-1])
        out.append((idx[i], lam1 / w.shape[1], w.shape[1]))
    return pd.DataFrame(out, columns=['date', 'ar', 'N']).set_index('date')


def build(index_code, name, win=20, cap=300, force=False):
    fp = os.path.join(LAB, f'abs_{index_code.replace(".", "_")}_w{win}_n{cap}.json')
    if os.path.exists(fp) and not force:
        d = json.load(open(fp))
        s = pd.Series({k: v for k, v in d['ar'].items() if v is not None}, name=name)
        s.index = s.index.astype(str)
        return s.sort_index()
    import pws_lab_data as L
    df = ar_series(L.panel(), L.pool(index_code), win, 1, cap)
    if df is None or df.empty:
        return None
    s = df['ar'].dropna()
    s.index = s.index.astype(str)
    json.dump({'index': index_code, 'name': name, 'win': win, 'cap': cap,
               'N': int(df['N'].median()),
               'ar': {k: round(float(v), 6) for k, v in s.items()}},
              open(fp, 'w'), ensure_ascii=False)
    return s.rename(name)


def build_all(win=20, cap=300, force=False):
    import pws_lab_data as L
    out = {}
    for c, n in L.IDX.items():
        s = build(c, n, win, cap, force)
        if s is not None:
            out[n] = s
    return out


if __name__ == '__main__':
    import time
    t = time.time()
    for w in (20, 60):
        res = build_all(w, 300, force=True)
        for n, s in res.items():
            print(f'{n:<8} win={w:<3} {len(s)}点 {s.index[0]}~{s.index[-1]} '
                  f'最新 {s.iloc[-1]:.4f} 区间 {s.min():.4f}~{s.max():.4f}')
    print('用时', round(time.time() - t, 1), 's')
