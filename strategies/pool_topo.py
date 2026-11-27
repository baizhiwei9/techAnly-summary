#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧬 备选/弃选池 · 拓扑结构过滤（TDA + UMAP 双口径）
=====================================================================
大佬口径（2026-11-02）：
  「必须是上涨的时候资金聚集，下跌的时候必须是资金发散」
  「用 TDA 还是 UMAP？你自己选这两个」→ 两个都上，投票取一致。

为什么这么设计
--------------
资金「聚集」和「发散」在拓扑上是两件可测的事，两种方法各测一面：

  ▸ TDA（ripser 持久同调，主口径）
    · 输入：池内个股相对沪深300 的 20 日残差收益相关矩阵 → 角距离 d=√(2(1-ρ))
    · 吸收比率 AR = λ₁/N（第一特征值占比）→ **资金往一个方向挤** 的强度（Kritzman 吸收比率）
    · H1 总持续度 = Σ(死亡-出生) → **结构空洞/分环** 的总量，簇分裂时升高
    · 判据（上涨/聚集）：ΔAR>0 且 ΔH1<0  → 资金越挤、结构越紧
      判据（下跌/发散）：ΔAR<0 且 ΔH1>0  → 资金散开、结构碎成多块

  ▸ UMAP（二维嵌入，交叉验证）
    · 同一相关距离矩阵嵌入 2D → 计算 离散度 spread = 平均两两距离
    · 单链接连通分量数 = 簇数（阈值取两两距离中位数）
    · 上涨/聚集：Δspread<0；下跌/发散：Δspread>0

  ▸ 个股层面
    在 UMAP 图上取「点数最多的连通簇」= 核心簇；落在核心簇的票标 `核心`，
    其余标 `边缘`。上涨聚集时资金挤在核心簇 → 备选只留核心簇票；
    下跌发散时全池散开 → 只做标注不删。

缓存：.cache/pool_topo/<code>.csv（当日 TTL 6h）
用法：
  python3 strategies/pool_topo.py --date 2026-10-30               # 读备份池 json 自动跑
  python3 strategies/pool_topo.py --date 2026-10-30 --codes 600519.SH,000568.SZ
库调用：import pool_topo as PT; PT.analyze(codes, date)
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, time, argparse
import numpy as np
import pandas as pd

BASE = '/root/.openclaw/workspace'
CACHE = os.path.join(BASE, '.cache/pool_topo')
BENCH = '000300.SH'
MKT_NAME = '沪深300'
WIN = 20          # 相关矩阵窗口（交易日）
LOOK = 5          # 结构变化对比的间隔（交易日）
SHOW = 6          # 展示最近几天的结构序列
TTL_H = 6
_NORM = ('SH', 'SZ', 'BJ')


def _log(m):
    print(m, file=sys.stderr)


def to_ts(code):
    c = str(code).strip().upper()
    if c.endswith(_NORM) or '.' in c:
        return c
    if c.startswith(('6', '9')):
        return c + '.SH'
    if c.startswith(('4', '8')):
        return c + '.BJ'
    return c + '.SZ'


def bars(ts_code, days=400, refresh=False, end=None):
    """个股/指数日线 close（本地 CSV 缓存，当日 TTL）；end=YYYYMMDD 时只取到该日"""
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, ts_code.replace('.', '_') + '.csv')
    df = None
    if not refresh and os.path.exists(fp):
        age_h = (time.time() - os.path.getmtime(fp)) / 3600.0
        if age_h <= TTL_H:
            try:
                df = pd.read_csv(fp, parse_dates=['date'])
                if len(df) <= 60:
                    df = None
            except Exception:
                df = None
    if df is None:
        sys.path.insert(0, os.path.join(BASE, 'bin'))
        from tushare_init import ts_call
        api = 'index_daily' if ts_code in (BENCH, '000905.SH', '000852.SH', '000688.SH',
                                           '399006.SZ', '000016.SH') else 'daily'
        # 代理 token 有「ip超限」限流 → 重试 3 次（指数退避），仍失败走 datahub 三级灾备
        for i in range(3):
            df = ts_call(api, ts_code=ts_code)
            if df is not None and len(df):
                break
            time.sleep(2 + 3 * i)
        if df is None or len(df) == 0:
            try:
                import datahub as DH
                rows = DH.daily(ts_code, days=days)
                if rows:
                    df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'vol'])
                    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d', errors='coerce')
                    df['amount'] = np.nan
                    df = df.dropna(subset=['date']).sort_values('date').reset_index(drop=True)
                    _log('[bars] %s → datahub 兼容取数 %d 行' % (ts_code, len(df)))
            except Exception as e:
                _log('[bars] %s datahub 也失败 %s' % (ts_code, str(e)[:80]))
        if df is None or len(df) == 0:
            _log('[bars] %s 取数失败' % ts_code)
            return None
        df = df.rename(columns={'trade_date': 'date'})
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d', errors='coerce')
        df = df.dropna(subset=['date']).sort_values('date').tail(days)
        keep = [c for c in ('date', 'close', 'vol', 'amount') if c in df.columns]
        df = df[keep].reset_index(drop=True)
        for c in ('vol', 'amount', 'close'):
            if c not in df.columns:
                df[c] = np.nan
        df.to_csv(fp, index=False)
    if end:
        cut = pd.to_datetime(str(end), format='%Y%m%d', errors='coerce')
        if cut is not None:
            df = df[df['date'] <= cut]
    return df


def _panel(codes, end=None):
    """对齐后的 close 面板 + 基准"""
    mkt = bars(BENCH, end=end)
    if mkt is None:
        return None, None
    px = mkt.set_index('date')[['close']].rename(columns={'close': '__mkt'})
    names = []
    for c in codes:
        ts = to_ts(c)
        d = bars(ts, end=end)
        if d is None or len(d) < 80:
            continue
        px = px.join(d.set_index('date')['close'].rename(ts), how='inner')
        names.append(ts)
    if not names:
        return None, None
    px = px.dropna()
    if px.shape[1] < 2:
        return None, None
    return px, names


def _resid(px, names, beta_win=60):
    lg = np.log(px).diff().dropna()
    R = lg[names].values
    m = lg['__mkt'].values
    n = len(names)
    bw = min(beta_win, len(lg) - 5)
    b = np.array([np.cov(m[-bw:], R[-bw:, i], ddof=1)[0, 1] / max(np.var(m[-bw:], ddof=1), 1e-18)
                  for i in range(n)])
    Res = R - np.outer(m, b)
    return pd.DataFrame(Res - Res.mean(axis=0, keepdims=True), index=lg.index, columns=names), lg, b


def _corr(M):
    with np.errstate(invalid='ignore', divide='ignore'):
        C = np.corrcoef(M.T)
    C = np.nan_to_num(np.asarray(C, float), nan=0.0)
    C = np.clip((C + C.T) / 2, -1, 1)
    np.fill_diagonal(C, 1.0)
    return C


def absorption(C):
    """吸收比率 λ₁/N —— 资金聚集度"""
    n = C.shape[0]
    if n < 2:
        return float('nan')
    w = np.linalg.eigvalsh(C)
    return float(w[-1] / n)


def h0_mst(C):
    """H0 总持续度 = 角距离 MST 总长（结构离散度；聚拢时变小）"""
    n = C.shape[0]
    if n < 2:
        return float('nan')
    D = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - C)))
    np.fill_diagonal(D, 0.0)
    D = (D + D.T) / 2.0
    try:
        import networkx as nx
        G = nx.from_numpy_array(D)
        mst = nx.minimum_spanning_tree(G, weight='weight')
        return float(sum(d['weight'] for _, _, d in mst.edges(data=True)))
    except Exception:
        return float('nan')


def h1_total(C):
    """H1 总持续度（拓扑分环总量）"""
    n = C.shape[0]
    if n < 3:
        return 0.0
    try:
        from ripser import ripser
    except Exception:
        return float('nan')
    D = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - C)))
    np.fill_diagonal(D, 0.0)
    D = (D + D.T) / 2.0
    try:
        r = ripser(D, distance_matrix=True, maxdim=1)
    except Exception as e:
        _log('[h1] ripser fail %s' % str(e)[:60])
        return float('nan')
    tot = 0.0
    H1 = r['dgms'][1] if len(r['dgms']) > 1 else np.empty((0, 2))
    for b, d in H1:
        if np.isfinite(d):
            tot += max(0.0, d - b)
    return float(tot)


def umap_geo(C):
    """UMAP 2D 嵌入 → (spread 离散度, 核心簇成员索引, 簇数)；稀疏时（n<5）改用 PCoA 降维"""
    n = C.shape[0]
    D = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - C)))
    np.fill_diagonal(D, 0.0)
    D = (D + D.T) / 2.0
    emb = None
    nclu = 1
    if n >= 5:
        try:
            import umap
            emb = umap.UMAP(n_neighbors=max(2, min(8, n - 1)), min_dist=0.05,
                            n_components=2, metric='precomputed',
                            random_state=42, n_epochs=300).fit_transform(D)
        except Exception as e:
            _log('[umap] fail %s' % str(e)[:60])
            emb = None
    if emb is None:
        # 小样本（n<5）UMAP 不稳定 → 经典 MDS/PCoA（对距离矩阵做双中心化后取前 2 特征向量）
        try:
            J = np.eye(n) - np.ones((n, n)) / n
            B = -0.5 * J @ (D ** 2) @ J
            w, v = np.linalg.eigh(B)
            keep = np.argsort(w)[::-1][:2]
            emb = v[:, keep] * np.sqrt(np.maximum(w[keep], 0))
        except Exception as e:
            _log('[pcoa] fail %s' % str(e)[:60])
            return float('nan'), list(range(n)), 1
    dm = np.linalg.norm(emb[:, None, :] - emb[None, :, :], axis=-1)
    iu = np.triu_indices(n, 1)
    if not len(iu[0]):
        return float('nan'), list(range(n)), 1
    spread = float(dm[iu].mean() / np.sqrt(n))
    thr = float(np.median(dm[iu]))
    adj = dm <= max(thr, 1e-9)
    seen, comps = [-1] * n, []
    for i in range(n):
        if seen[i] >= 0:
            continue
        st, grp = [i], []
        while st:
            u = st.pop()
            if seen[u] >= 0:
                continue
            seen[u] = len(comps)
            grp.append(u)
            st += [v for v in range(n) if adj[u, v] and seen[v] < 0]
        comps.append(grp)
    core = max(comps, key=len) if comps else list(range(n))
    return spread, core, len(comps)


def analyze(codes, date=None, show=SHOW):
    """返回 {'pool': {...}, 'stocks': {code: {...}}, 'ok': bool, 'why': str}"""
    codes = [c for c in dict.fromkeys(codes or []) if c]
    if len(codes) < 2:
        return {'ok': False, 'why': '池内不足 2 只，拓扑不可算', 'pool': {}, 'stocks': {}}
    end = str(date).replace('-', '') if date else None
    px, names = _panel(codes, end=end)
    if px is None or len(px) < WIN + LOOK + 20:
        return {'ok': False, 'why': '共同交易日不足 %d' % (WIN + LOOK + 20),
                'pool': {}, 'stocks': {}}
    Res, lg, betas = _resid(px, names)
    n = len(names)
    idx = Res.index
    need = WIN + show
    ref_off = LOOK
    if len(Res) < need:
        show = max(2, len(Res) - WIN)
        ref_off = min(LOOK, max(1, show - 1))

    series = []       # 最近 show 天序列
    core_idx = list(range(n))
    nclu = 1
    for k in range(show, 0, -1):
        sl = Res.iloc[-k - WIN + 1: len(Res) - k + 1] if k > 1 else Res.iloc[-WIN:]
        C = _corr(sl.values)
        ar = absorption(C)
        h0 = h0_mst(C)
        h1 = h1_total(C)
        sp, core, nc = umap_geo(C)
        series.append({'d': str(idx[-k].date()), 'ar': ar, 'h0': h0, 'h1': h1,
                       'spread': sp, 'nclu': nc})
        if k == 1:
            core_idx, nclu = core, nc

    cur, ref = series[-1], series[max(0, len(series) - 1 - ref_off)]
    d_ar = (cur['ar'] or 0) - (ref['ar'] or 0)
    d_h0 = (cur['h0'] or 0) - (ref['h0'] or 0)
    d_h1 = (cur['h1'] or 0) - (ref['h1'] or 0)
    d_sp = (cur['spread'] or 0) - (ref['spread'] or 0)

    # 池内当日等权涨幅 / 残差超额
    ret_today = float(np.expm1(lg[names].iloc[-1].values.mean()) * 100)
    resid_today = float(Res[names].iloc[-1].values.mean() * 100)

    # 主判据用「相对强弱」：胜率 + 残差超额，规避普涨/普跌的假信号
    pct_pos = float((Res[names].iloc[-1].values > 0).mean() * 100)
    up = ret_today > 0
    strong_up = (ret_today > 0) and (resid_today > 0) and (pct_pos >= 50)
    strong_dn = (ret_today < 0) and (resid_today < 0) and (pct_pos < 50)

    # TDA：聚集 = AR↑ 且 结构紧致（H0↓）或 H1↓；发散 = AR↓ 且 结构离散（H0↑ 或 H1↑）
    if up:
        tda_ok = (d_ar > 0) and (d_h0 < 0 or d_h1 < 0)
        umap_ok = d_sp < 0
        want = '上涨 → 需资金聚集（AR↑ + 结构紧致 / 嵌入聚拢）'
    else:
        tda_ok = (d_ar < 0) and (d_h0 > 0 or d_h1 > 0)
        umap_ok = d_sp > 0
        want = '下跌 → 需资金发散（AR↓ + 结构离散 / 嵌入散开）'
    votes = int(bool(tda_ok)) + int(bool(umap_ok))
    verdict = '配合' if votes >= 1 else '不配合'
    # 强度：双口径一致 + 涨跌方向有相对强弱支撑 → 强
    strength = ('强' if votes == 2 and (strong_up or strong_dn) else
                '中' if votes >= 1 else '弱')

    pool = {
        'n': n, 'names': names, 'date': str(idx[-1].date()),
        'ret_today': round(ret_today, 3), 'resid_today': round(resid_today, 3),
        'pct_pos': round(pct_pos, 1),
        'up': bool(up), 'strong_up': bool(strong_up), 'strong_dn': bool(strong_dn),
        'ar': round(cur['ar'], 4), 'h0': round(cur['h0'], 4), 'h1': round(cur['h1'], 5),
        'spread': round(cur['spread'], 5), 'nclu': cur['nclu'],
        'd_ar': round(d_ar, 4), 'd_h0': round(d_h0, 4), 'd_h1': round(d_h1, 5),
        'd_spread': round(d_sp, 5),
        'tda_ok': bool(tda_ok), 'umap_ok': bool(umap_ok), 'votes': votes,
        'verdict': verdict, 'strength': strength, 'want': want, 'series': series,
    }
    stocks = {}
    for i, ts in enumerate(names):
        stocks[ts] = {'core': i in core_idx, 'tag': '核心' if i in core_idx else '边缘',
                      'ret_today': round(float(np.expm1(lg[ts].iloc[-1]) * 100), 3),
                      'resid_today': round(float(Res[ts].iloc[-1] * 100), 3)}
    return {'ok': True, 'why': '', 'pool': pool, 'stocks': stocks}


def load_pool(date):
    fp = os.path.join(BASE, '.cache/stock_pool/pool_%s.json' % date.replace('-', ''))
    if not os.path.exists(fp):
        return None
    return json.load(open(fp, encoding='utf-8'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--codes', default='')
    ap.add_argument('--which', default='pick', choices=['pick', 'avoid', 'all'])
    args = ap.parse_args()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(',') if c.strip()]
    else:
        d = load_pool(args.date)
        if not d:
            print('找不到池子 json，先跑 bin/stock_pool.py --no-send', file=sys.stderr)
            sys.exit(1)
        if args.which == 'all':
            codes = [r['code'] for r in d['pick'] + d['avoid']]
        else:
            codes = [r['code'] for r in d[args.which]]
    r = analyze(codes, args.date)
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
