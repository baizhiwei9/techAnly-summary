#!/usr/bin/env python3
"""
TDA-SPD 黎曼流形日报 — 60 因子细分概念版 v2（SPD 测地漂移选因子）
============================================================
新增（SPD 黎曼流形改造）：
  1. 因子选择：用 SPD 流形测地漂移替代原 TDA 投影+动量综合分。
     对每因子与等权市场的相关系数做 Fisher z 变换（=SPD(2) 测地坐标），
     漂移得分 delta_i=|z(t)-z(t_ref)|，权重 w_i=delta_i x (s_i+1)^2 x discount，Top5 归一化。
  2. 整体结构漂移 D=||logm(R_t)-logm(R_ref)||_F（Log-Euclidean 测地距离）作为监控指标。
  3. 股票：每因子从同花顺成分股(ths_member)内，按近20交易日动量选前3只强股
  4. 债券：akshare 中债收益率曲线（10Y/2Y/5Y + 利差）
  5. 模拟交易：独立状态文件，年初重置，T日信号 T+1 生效，每日跟踪累计收益
顶层 TDA 市场状态（熵口径脆弱性 → 总仓位）保持不变。
推送：飞书 interactive 卡片，标题在前（· 来自 OpenClaw.Deepseek）、署名（📎 来自钉钉OpenClaw）在末尾
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import sys, warnings, math, json, time, urllib.request, os
warnings.filterwarnings('ignore')
sys.path.insert(0, 'bin')
import socket
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET]
import numpy as np
import pandas as pd
from data_hub import ak as ak
from datetime import datetime
from pathlib import Path
from scipy.linalg import logm
from tushare_init import get_pro, get_bar, DIRECT_TOKEN

WINDOW = 60
REF_OFFSET = 60          # SPD 参考窗口偏移（60 个交易日前）
MAX_FACTORS = 60
CORR_THRESH = 0.95
WIND_IDX = 20
TOP_N = 5
STOCKS_PER_FACTOR = 3
MOMENTUM_DISCOUNT = 0.5  # SPD 正夏普因子动量折扣
CAP = 1000000  # 模拟资金 100 万
REPORT_DIR = Path('memory/tda_reports')
CACHE_DIR = Path('.cache/tda_concept_ak')
STATE_FILE = Path('memory/tda_60sim_state.json')
CACHE_DIR.mkdir(parents=True, exist_ok=True)
FEISHU_WEBHOOK = __import__("ocsecrets").feishu_hook()

# tushare：代理优先 → 直连兜底（2026-09-23 用户指定）
PRO = get_pro()
PRO_DIRECT = get_pro(token=DIRECT_TOKEN, http_url='')

def safe_pro_call(method_name, **kwargs):
    """tushare 调用：代理 token 优先，失败/无权限时直连兜底"""
    err = None
    try:
        return getattr(PRO, method_name)(**kwargs)
    except Exception as e:
        err = str(e)
    try:
        return getattr(PRO_DIRECT, method_name)(**kwargs)
    except Exception as e:
        raise Exception(f'{method_name} 代理失败({str(err)[:40]}) 且直连失败({str(e)[:40]})')
TODAY = datetime.now().strftime('%Y-%m-%d')
TODAY8 = datetime.now().strftime('%Y%m%d')
# 非交易日（周末/节假日）→ 回退到最近交易日，避免拿到空数据
IS_OPEN = True
REQ8 = TODAY8
try:
    import trade_day
    _r, IS_OPEN, _n = trade_day.resolve(TODAY8)
    if not IS_OPEN:
        TODAY8 = _r
        TODAY = f'{_r[:4]}-{_r[4:6]}-{_r[6:]}'
        print(f'[trade_day] {_n}', flush=True)
except Exception:
    pass

NOISE_KW = ['概念','板块','精选','指数','申万','昨日','今日','次新','ST','退市','融资','质押',
            '举牌','增持','回购','股东','重组','借壳','转债','期权','标的','期货','外汇','汇率',
            '宏观','政策','规划','会议','改革','示范区','自贸区','一体化','长三角','京津冀',
            '大湾区','雄安','海南','上海','深圳','北京','广州','浙江','江苏','山东','福建',
            '广东','四川','重庆','湖南','湖北','陕西','集团','央企','国企改','注册制','有实体']
# 通道/宽基类因子（成分股=全市场或太大，不做因子内选股，仅提示）
CHANNEL_FACTORS = {'沪股通','深股通','融资融券','标普道琼斯','MSCI','富时罗素','AH股','沪深300'}
CORE = ['人工智能','第三代半导体','光模块','算力','数据中心','人形机器人','创新药','光伏','储能',
        '固态电池','白酒','黄金','煤炭','稀土永磁','军工','商业航天','卫星导航','数字经济',
        '数据要素','信创','数字货币','碳中和','高股息','中特估','一带一路','减速器','工业母机',
        '光刻机','机器人','半导体','铜','证券','银行']


def _ak_call(fn, timeout=15, **kwargs):
    """akshare 带超时调用的封装：akshare 偶发挂起/断连，超时即异常以便兑底到 tushare
    注意：用 ThreadPoolExecutor 超时后线程无法强杀，故用 shutdown(wait=False) 避免阻塞。"""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn, **kwargs)
    try:
        return fut.result(timeout=timeout)
    except FutTimeout:
        ex.shutdown(wait=False)  # 不等待挂起线程，避免卡死
        raise TimeoutError(f'akshare 调用超时(>{timeout}s)')
    except Exception:
        ex.shutdown(wait=False)
        raise


def _tushare_thscode_cache():
    """概念名→ths_index ts_code 映射（tushare 兑底用，带缓存）"""
    fp = Path('.cache/ths_index_map.json')
    if fp.exists():
        try:
            return json.load(open(fp, encoding='utf-8'))
        except Exception:
            pass
    m = {}
    try:
        idx = safe_pro_call('ths_index', exchange='A', type='N')
        m = {str(row['name']): row['ts_code'] for _, row in idx.iterrows()}
        fp.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    except Exception as e:
        print(f'[tushare ths_index 失败] {str(e)[:50]}', flush=True)
    return m


def get_concept_list():
    # 多级获取：先 akshare（同花顺概念名，带超时），失败/超时回退 tushare ths_index
    names = []
    try:
        df = _ak_call(ak.stock_board_concept_name_ths, timeout=15)
        names = df['name'].tolist()
    except Exception as e:
        print(f'[akshare 概念列表失败/超时，兑底 tushare] {str(e)[:50]}', flush=True)
        try:
            names = list(_tushare_thscode_cache().keys())
        except Exception:
            names = []
    ranked = [c for c in CORE if c in names]
    for n in names:
        if n in ranked: continue
        if len(str(n)) > 10: continue
        if any(k in str(n) for k in NOISE_KW): continue
        ranked.append(n)
        if len(ranked) >= 130: break
    return ranked


def get_concept_idx(name):
    cf = CACHE_DIR / f'{name}.csv'
    if cf.exists():
        df = pd.read_csv(cf, parse_dates=['date'])
        latest = df['date'].max().strftime('%Y%m%d')
        if latest >= TODAY8:
            return df.set_index('date')['close']
    # 多级获取：先 akshare（带超时），失败/超时回退 tushare ths_daily
    df = None
    try:
        df = _ak_call(ak.stock_board_concept_index_ths, timeout=15,
                      symbol=name, start_date='20240101', end_date='20261231')
        df = df.rename(columns={'日期':'date','收盘价':'close'})
        df['date'] = pd.to_datetime(df['date'])
    except Exception as e:
        print(f'[akshare 概念指数失败/超时，兑底 tushare] {name}: {str(e)[:40]}', flush=True)
        try:
            m = _tushare_thscode_cache()
            code = m.get(name)
            if code:
                td = safe_pro_call('ths_daily', ts_code=code, start_date='20240101', end_date='20261231')
                td = td.sort_values('trade_date')
                df = pd.DataFrame({'date': pd.to_datetime(td['trade_date']), 'close': td['close'].astype(float)})
        except Exception as e2:
            print(f'[tushare 概念指数也失败] {name}: {str(e2)[:40]}', flush=True)
            return None
    if df is None or len(df) == 0:
        return None
    df[['date','close']].to_csv(cf, index=False)
    return df.set_index('date')['close'].tail(WINDOW + REF_OFFSET + 5)  # SPD 参考窗口需回溯 REF_OFFSET 交易日


# ── tushare 选股（直连 token 稳定）─────────────────
def _retry(fn, *a, tries=4, wait=8, **k):
    for i in range(tries):
        try:
            return fn(*a, **k)
        except Exception as e:
            if 'ip超限' in str(e): time.sleep(wait); continue
            time.sleep(1); continue
    return None


def load_constituents():
    """Top5因子成分股缓存；若缺失用代理token现拉（单次）"""
    fp = Path('.cache/ths_top5_constituents.json')
    if fp.exists():
        with open(fp) as f:
            return json.load(f)
    return {}


def pick_stocks(top_names):
    """每因子从成分股内按近20日动量选 top3 强势股"""
    cons = load_constituents()
    panel_fp = Path('.cache/ths_momentum_panel.csv')
    if not panel_fp.exists():
        return {n: [] for n in top_names}
    mkt = pd.read_csv(panel_fp, index_col=0)
    result = {}
    for f in top_names:
        info = cons.get(f)
        stocks = info.get('stocks', []) if info else []
        if f in CHANNEL_FACTORS or len(stocks) == 0:
            result[f] = {'channel': True, 'picks': []}
            continue
        rows = []
        for s in stocks:
            code, nm = s['con_code'], s['con_name']
            if 'ST' in str(nm).upper() or '退' in str(nm):
                continue  # 过滤ST/退市
            if code in mkt.index:
                rows.append((nm, code, float(mkt.loc[code, 'mom20_pct']), float(mkt.loc[code, 'last_pct'])))
        rows.sort(key=lambda x: x[2], reverse=True)
        picks = [{'name': nm, 'code': c, 'mom': m, 'last': l} for nm, c, m, l in rows[:STOCKS_PER_FACTOR]]
        result[f] = {'channel': False, 'picks': picks}
    return result


def get_bond():
    """中债国债收益率曲线 —— 优先读 data/market.db（cn.cb.govt.*），回源 akshare"""
    try:
        import sys as _s, os as _o
        _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__))))
        import marketdb as _M
        g = {}
        for t in ('2Y', '5Y', '10Y', '30Y'):
            r = _M.latest(f'cn.cb.govt.{t}')
            if not r:
                g = {}
                break
            g[t] = r[1]
        if g:
            return {'y2': g['2Y'], 'y5': g['5Y'], 'y10': g['10Y'], 'y30': g['30Y'],
                    'spread': round(g['10Y'] - g['2Y'], 4)}
    except Exception as e:
        print(f'[bond] 库读取失败，回源 akshare: {str(e)[:60]}')
    try:
        df = ak.bond_zh_us_rate()
        row = df.iloc[-1]
        return {
            'y2': float(row['中国国债收益率2年']),
            'y5': float(row['中国国债收益率5年']),
            'y10': float(row['中国国债收益率10年']),
            'y30': float(row['中国国债收益率30年']),
            'spread': float(row['中国国债收益率10年-2年']),
        }
    except Exception as e:
        print(f'债券获取失败: {str(e)[:60]}')
        return None


# ── 模拟交易收益跟踪 ──────────────────────────────
def load_sim_state():
    if STATE_FILE.exists():
        return json.load(open(STATE_FILE))
    return {'start_date': TODAY8, 'capital': CAP, 'cum_pnl': 0, 'records': [],
            'last_picks_snapshot': None}


def save_sim_state(s):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(s, open(STATE_FILE, 'w'), ensure_ascii=False, indent=1)


def sim_return(picks):
    """更新模拟账户：用上次持仓做今日收益，再刷新为新持仓（T+1生效）"""
    state = load_sim_state()
    # 代理优先，直连兑底（旧直连 token 分低）
    # 1. 用上次持仓的标的算今日涨跌 → 累加收益（仅当上次持仓是昨日或更早，即真正隔夜持有过）
    last = state.get('last_picks_snapshot')
    if last and last.get('date', '') < TODAY8:
        codes = last.get('all_codes', [])
        # 拉这些股票今日涨幅（双 token daily）
        try:
            tday = _retry(safe_pro_call, 'daily', trade_date=TODAY8)
            if tday is not None:
                d = dict(zip(tday['ts_code'], tday['pct_chg']))
                rets = [d.get(c, 0.0) for c in codes]
                avg_ret = np.mean(rets) / 100.0 if rets else 0.0
                state['cum_pnl'] += state['capital'] * avg_ret
                state['records'].append({
                    'date': TODAY8, 'lookback_ret_pct': round(avg_ret*100, 2),
                    'cum_pnl': round(state['cum_pnl'], 0)
                })
        except Exception as e:
            print(f'模拟收益更新失败: {str(e)[:60]}')
    # 2. 快照今日持仓（信号T日，T+1生效）
    all_codes = []
    for f, v in picks.items():
        if not v.get('channel'):
            all_codes += [p['code'] for p in v['picks']]
    state['last_picks_snapshot'] = {
        'date': TODAY8, 'snapshot_date': TODAY8,
        'all_codes': list(dict.fromkeys(all_codes))
    }
    save_sim_state(state)
    return state


def main():
    if not IS_OPEN:
        print(f'[trade_day] 今日非交易日，跳过 TDA 日报（不重复发送 {TODAY8} 的报告）', flush=True)
        return '', time.time()
    t0 = time.time()
    ranked = get_concept_list()
    print(f"概念池: {len(ranked)}", flush=True)
    series = {}
    for i, n in enumerate(ranked[:120]):
        try:
            s = get_concept_idx(n)
            if s is not None and len(s) > 40:
                series[n] = s
        except Exception:
            pass
    if len(series) < 30:
        print("⚠️ 概念不足"); return None, t0
    df = pd.DataFrame(series).dropna(how='any').tail(WINDOW)
    ret = np.log(df / df.shift(1)).dropna()
    corr = ret.corr()
    cols = list(corr.columns); keep = []
    for c in cols:
        if len(keep) >= MAX_FACTORS: break
        if not any(abs(corr.loc[c, k]) > CORR_THRESH for k in keep):
            keep.append(c)
    R = ret[keep]; C = R.corr().values
    n = C.shape[0]

    # ═══ SPD 黎曼流形中层选因子（替代原 TDA 投影+动量综合强度分）═══
    # 数学基础：协方差矩阵 Sigma 生活在 SPD 流形（仿射不变度量）。
    # 对每个因子 i，它与等权市场组合的相关系数 rho_i 做 Fisher z 变换
    #    z_i = atanh(rho_i)，即 SPD(2) 流形（2x2 相关矩阵流形）上的测地坐标
    # 漂移得分 delta_i = |z_i(t) - z_i(t_ref)|，t_ref = 60 交易日前。
    # delta_i 高 ⇒ 因子正在脱离群体的共动模式 ⇒ 轮动候选。
    # 权重公式：w_i = delta_i x (s_i+1)^2 x discount，Top5 归一化。
    # 另算全矩阵 Log-Euclidean 测地距离 D（整体结构漂移监控指标）。

    # 等权市场组合：用概念池全体收益的等权平均作为“群体中心”
    mkt_ret = ret[keep].mean(axis=1)

    # 当前窗口相关结构（t）与参考窗口相关结构（t_ref，REF_OFFSET 交易日前）
    # 参考窗口的收益率要从更早的价格算，故回溯拉取到 WINDOW+REF_OFFSET
    df_hist = pd.DataFrame(series).dropna(how='any')
    ret_hist = np.log(df_hist / df_hist.shift(1)).dropna()
    ret_hist = ret_hist[keep].dropna()
    if len(ret_hist) >= WINDOW + REF_OFFSET:
        ret_cur = ret_hist.tail(WINDOW)                      # 当前窗口
        ret_ref = ret_hist.iloc[-(WINDOW + REF_OFFSET):-REF_OFFSET]  # 60 交易日前窗口
        mkt_cur = ret_cur.mean(axis=1)
        mkt_ref = ret_ref.mean(axis=1)
    else:
        # 历史不足时退化：用当前窗口与较早期对比，delta 置 0 避免误判
        ret_cur = ret_hist.tail(WINDOW)
        ret_ref = ret_cur
        mkt_cur = ret_cur.mean(axis=1)
        mkt_ref = mkt_cur

    def _fisher_z(r):
        r = float(r)
        return float(np.arctanh(np.clip(r, -0.999999, 0.999999)))

    sharpe_ann = (ret_cur.mean() / ret_cur.std() * np.sqrt(252)).fillna(0.0)
    delta = {}
    rho_t, rho_ref = {}, {}
    for col in keep:
        r_t = ret_cur[col].corr(mkt_cur) if ret_cur[col].std() > 0 else 0.0
        r_ref = ret_ref[col].corr(mkt_ref) if ret_ref[col].std() > 0 else 0.0
        rho_t[col] = float(r_t); rho_ref[col] = float(r_ref)
        delta[col] = abs(_fisher_z(r_t) - _fisher_z(r_ref))

    # 全矩阵 Log-Euclidean 测地距离：D = ||logm(R_t) - logm(R_ref)||_F
    # 为让分位对照有意义，D 用基准文件的【固定因子集 factor_set】计算，
    # 与历史 D 序列同口径；再查近一年分位 Pxx 判断大小。
    total_drift = float('nan')
    drift_pctile = None   # 今天的 D 落在近一年分布的多少分位（0-100）
    drift_level = '—'      # 档位：安静/中等/高/极高
    try:
        base_fp = Path('.cache/spd_drift_base.json')
        if base_fp.exists():
            base = json.loads(base_fp.read_text(encoding='utf-8'))
            fset = [f for f in base['factor_set'] if f in df_hist.columns]
            if len(fset) >= 30:
                bh = df_hist[fset].dropna(how='any')
                r_hist = np.log(bh / bh.shift(1)).dropna()
                if len(r_hist) >= WINDOW + REF_OFFSET:
                    r_cur = r_hist.tail(WINDOW)
                    r_ref = r_hist.iloc[-(WINDOW + REF_OFFSET):-REF_OFFSET]
                    Rt = r_cur.corr().values
                    Rr = r_ref.corr().values
                    total_drift = float(np.linalg.norm(logm(Rt + 1e-8*np.eye(len(fset))) - logm(Rr + 1e-8*np.eye(len(fset))), 'fro'))
                    # 近一年分位
                    dl = base['daily']
                    ds = pd.Series(dl['D'], index=pd.DatetimeIndex([pd.Timestamp(x) for x in dl['date']]))
                    ds = ds[ds.index <= pd.Timestamp(TODAY)]
                    recent = ds.tail(base.get('stale_after', 250))
                    if len(recent) >= 50:
                        drift_pctile = float((recent <= total_drift).mean() * 100)
                        p75 = base['percentiles']['p75']; p90 = base['percentiles']['p90']
                        p25 = base['percentiles']['p25']
                        if total_drift >= p90: drift_level = '极高（变盘预警）'
                        elif total_drift >= p75: drift_level = '高'
                        elif total_drift <= p25: drift_level = '低（结构安静）'
                        else: drift_level = '中等'
        print(f'整体结构漂移 D(Log-Euclidean) = {total_drift:.4f}  近一年P{drift_pctile:.0f} | {drift_level}', flush=True)
    except Exception as e:
        print(f'[D 分位计算失败] {str(e)[:60]}', flush=True)

    # 原始权重：delta x (s_i+1)^2 x discount（仅 s>0 时才有动量折扣，与 TDA 相同）
    deltas = pd.Series(delta, index=keep)
    sharps = pd.Series(sharpe_ann, index=keep)
    raw_w = deltas * ((sharps + 1) ** 2) * MOMENTUM_DISCOUNT
    raw_w = raw_w.clip(lower=0).fillna(0.0)
    # 若所有漂移得分都为 0（历史不足等边角情形），回退到动量：提高稳健性
    if raw_w.sum() <= 0:
        raw_w = (sharps + 1).clip(lower=0.1) ** 2
    top_spd = raw_w.sort_values(ascending=False)
    top_names = top_spd.head(TOP_N).index.tolist()

    # Top5 内部归一化（权重和 = 1）
    top_raw = raw_w[top_names]
    top_raw = top_raw.clip(lower=top_raw.max() * 1e-6)
    w_rel = top_raw / top_raw.sum()

    w = pd.Series(0.0, index=keep)
    w[top_names] = w_rel
    w = w / w.sum()
    top = w[w > 0].sort_values(ascending=False)

    # 拓扑指标（TDA 顶层市场状态，仓位控制不变）
    eigvals = np.linalg.eigvalsh(C)[::-1]
    ev = eigvals[::-1]; ev = ev[ev > 1e-10]
    b0 = int(np.sum(ev > 1.0))
    p = ev/ev.sum(); H = -np.sum(p*np.log(p+1e-12)); Hmax = np.log(n)
    curv = 1 - H/Hmax; effD = math.exp(H)
    sigma = np.std(R.values.flatten())*np.sqrt(252)
    V_vol = min(65, max(20, 15+sigma*150))
    V_tda = min(25, H*4) if H > 1.5 else min(10, H*2)
    V_curv = -8 if curv > 0.7 else (+8 if curv < 0.3 else 0)
    vul = min(90, max(10, V_vol+V_tda+V_curv))
    state_s = ('强势上涨' if vul < 35 and curv > 0.5 else
               '震荡偏强' if vul < 50 else '震荡偏弱' if vul < 70 else '弱势下跌')
    pos = max(15, 100-int(vul))

    print(f"\n=== SPD-Top{TOP_N} 因子: {top_names}", flush=True)
    print(f"整体结构漂移 D(Log-Euclidean) = {total_drift:.4f}", flush=True)
    for nm in top_names:
        print(f"  {nm}: delta_测地漂移={deltas[nm]:.4f} 夏普={sharps[nm]:+.2f} 权重={w[nm]*100:.1f}%", flush=True)

    # 1. 选股
    picks = pick_stocks(top_names)
    # 2. 债券
    bond = get_bond()
    # 3. 模拟交易
    sim = sim_return(picks)
    cum_ret = sim['cum_pnl'] / sim['capital'] * 100

    # ── 报告 ──
    L = []
    L.append(f"**📊 TDA-SPD 黎曼流形日报（60因子版）** · 来自 OpenClaw.Deepseek")
    L.append(f"📅 {TODAY} ｜ 因子覆盖 {n} 个")
    L.append("")
    L.append(f"**【市场状态 · 总权重框架】**")
    L.append(f"状态：{state_s}　脆弱性：{vul:.1f}%　**总仓位：{pos}%**（剩余 {100-pos}% 进现金/债券）")
    L.append(f"▸ 总仓位由脆弱性决定：脆弱性{vul:.0f}%（80%=极脆弱→低仓，30%=稳健→高仓），故当前只投{pos}%进股票")
    L.append("")
    L.append(f"**【拓扑指标 · TDA 顶层】**")
    L.append(f"β₀={b0}（连通成分，越多=市场越分裂）　β₁(熵)={H:.4f}（因子分散度，越大越分散）　有效维={effD:.2f}（有效独立方向，越大越多元）　曲率={curv:.4f}（1-归一化熵，越大越集中/同涨同跌）")
    L.append("▸ 拓扑指标用于【判断市场状态】→ 间接影响仓位；目前状态偏弱，故总体偏防御")
    L.append("")
    L.append(f"**【SPD 黎曼流形 · 测地漂移选因子（只买 Top{TOP_N}）】**")
    L.append(f"整体结构漂移 D(Log-Euclidean) = {total_drift:.2f}")
    if drift_pctile is not None:
        L.append(f"　→ 近一年 P{drift_pctile:.0f}（{drift_level}）：超过近一年 {drift_pctile:.0f}% 的日子，相关结构变动当前处于{'较剧烈' if drift_pctile>=75 else '较平稳' if drift_pctile<=25 else '中等'}水平")
    L.append(f"▸ D 是【监控指标】（当前不参与仓位控制）：量级越大预示市场相关结构变化越剧烈、越可能变盘/风格切换")
    L.append("▸ 测地漂移Δ=因子与市场相关结构的脱钩程度：**Δ越大=越脱离主流=轮动候选，权重越高**")
    L.append("")
    for name, wgt in top.items():
        actual = wgt * pos / 100   # 实际投入仓 = 总仓位 × 因子相对权重
        L.append(f"▪️ {name}：相对权重{wgt*100:.1f}%（实际投入{actual*100:.1f}%）　｜测地漂移Δ={deltas[name]:.3f}　夏普{sharps[name]:+.2f}")
    L.append("")
    L.append(f"**【成分股选股 · 每因子前{STOCKS_PER_FACTOR}强势股】**")
    for name in top_names:
        info = picks.get(name, {})
        if info.get('channel'):
            L.append(f"▪️ {name}：通道/宽基因子，成分股近全市场，不做因子内选股")
        else:
            pl = info.get('picks', [])
            if pl:
                detail = '  '.join([f"{p['name']}({p['code'][:6]}):{p['mom']:.0f}%" for p in pl])
                L.append(f"▪️ {name}：{detail}")
            else:
                L.append(f"▪️ {name}：暂无成分股数据")
    L.append("")
    if bond:
        L.append(f"**【债券 · 中债收益率曲线】**")
        L.append(f"2Y={bond['y2']:.3f}%　5Y={bond['y5']:.3f}%　10Y={bond['y10']:.3f}%　30Y={bond['y30']:.3f}%")
        L.append(f"期限利差(10Y-2Y)：{bond['spread']:.3f}%　→　{'陡峭化' if bond['spread']>0.4 else '平坦'}")
    L.append("")
    L.append(f"**【模拟交易收益（100万 · 年初重置 · T+1生效）】**")
    L.append(f"建仓日：{sim['start_date']}　累计盈亏：**{sim['cum_pnl']:+,.0f} 元**（{cum_ret:+.2f}%）")
    if sim['records']:
        r = sim['records'][-1]
        L.append(f"最近更新：{r['date']}　当日组合收益 {r['lookback_ret_pct']:+.2f}%")
    report = "\n".join(L)
    # 2026-09-28 起：标题里已带「· 来自 OpenClaw.Deepseek」，正文不再重复署名
    print("\n" + report)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    f = REPORT_DIR / f'tda_60_daily_{TODAY8}.md'
    f.write_text(report, encoding='utf-8')
    print(f"[已存] {f}", flush=True)
    return report, t0


def send_feishu(text):
    """interactive card；2026-09-28 起：署名统一放正文第一行（紧跟标题）"""
    data = json.dumps({
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "📊 TDA-SPD 黎曼流形日报（60因子版） · 来自 OpenClaw.Deepseek"},
                "template": "blue"
            },
            "elements": [
                {"tag": "markdown", "content": "📎 来自钉钉OpenClaw"},
                {"tag": "markdown", "content": text}
            ]
        }
    }).encode('utf-8')
    req = urllib.request.Request(FEISHU_WEBHOOK, data=data, method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode()


if __name__ == '__main__':
    report, _ = main()
    if report:
        try:
            r = send_feishu(report)
            print(f"飞书已发送: {r[:60]}")
        except Exception as e:
            print(f"飞书发送失败: {str(e)[:60]}")
