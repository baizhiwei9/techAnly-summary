#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TDA 流形旋转多资产策略 — 每日运行脚本
=====================================
- 股票端：8 ETF → UMAP 降维 → 持久同调 → 市场状态 → (1-s)² 防御权重 → 下钻个股
- 债券端：国债期货 → 宏观状态 + TDA 震荡 → 多空方向
- 组合跟踪：从 2026-08-18 起模拟，年初重置
- 报告推送：飞书 Webhook + QQ 消息

用法:
    python3 tda_rotation.py              # 正常每日运行
    python3 tda_rotation.py --dry-run    # 只生成报告不推送
    python3 tda_rotation.py --backtest   # 回测模式
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import os, sys, json, math, warnings, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 路径 ─────────────────────────────────────────────
WORKSPACE = Path(os.path.expanduser("~/.openclaw/workspace"))
sys.path.insert(0, str(WORKSPACE / 'bin'))

# ── 数据 ─────────────────────────────────────────────
import numpy as np
import pandas as pd
from scipy import stats
from tushare_init import get_pro, get_bar, TOKEN, DIRECT_TOKEN

# ── TDA 依赖（带 fallback）────────────────────────────
HAS_UMAP = False
HAS_RIPSER = False
HAS_SKLEARN = False
try:
    from umap import UMAP
    HAS_UMAP = True
except ImportError:
    pass
try:
    from ripser import ripser
    HAS_RIPSER = True
except ImportError:
    pass
try:
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    pass

warnings.filterwarnings('ignore')

# ── 配置 ─────────────────────────────────────────────
# 代理优先 → 直连兜底（2026-09-23 用户指定）
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
TZ = timezone(timedelta(hours=8))
TODAY_STR = datetime.now(TZ).strftime('%Y%m%d')
STATE_FILE = WORKSPACE / 'memory' / 'tda_rotation_state.json'
REPORT_DIR = WORKSPACE / 'memory' / 'tda_reports'

# ETF 因子（industry 映射到 TuShare stock_basic 的交易所行业分类）
FACTORS = [
    {'name': '科技成长', 'ts_code': '159807.SZ', 'swIndustry': ['半导体', '元器件', '软件服务', 'IT设备', '通信设备', '互联网']},
    {'name': '高端制造', 'ts_code': '159638.SZ', 'swIndustry': ['专用机械', '机械基件', '电器仪表', '电气设备', '运输设备', '航天国防']},
    {'name': '新能源',   'ts_code': '516090.SH', 'swIndustry': ['光伏设备', '锂电池', '新能源车', '风电', '储能', '充电桩', '电池']},
    {'name': '医药健康', 'ts_code': '512010.SH', 'swIndustry': ['医疗保健', '化学制药', '生物制药', '医疗器械', '中成药', '医药流通', '医疗服务']},
    {'name': '大消费',   'ts_code': '159928.SZ', 'swIndustry': ['食品', '家用电器', '酒类', '饮料', '日用化工', '纺织服装', '百货', '零售']},
    {'name': '价值周期', 'ts_code': '512700.SH', 'swIndustry': ['银行', '保险', '证券', '多元金融', '房地产', '建筑装饰']},
    {'name': '红利低波', 'ts_code': '512890.SH', 'swIndustry': ['电力', '港口', '高速公路', '煤炭', '石油开采', '天然气', '水务', '铁路']},
    {'name': '动量',     'ts_code': '159967.SZ', 'swIndustry': ['半导体', '消费电子', '新能源车', '医疗器械', '软件服务', '化学原料']},
]

ETF_CODES = [f['ts_code'] for f in FACTORS]
INITIAL_CAPITAL = 1_000_000
STOCK_RATIO = 0.5
BOND_RATIO = 0.5
WINDOW = 60
TOP_N_STOCKS = 3
MAX_FACTOR_WEIGHT = 0.30  # 单因子上限 30%
MOMENTUM_DISCOUNT = 0.5   # s>0 时折扣
BOND_TX_CODE = 'T.CFE'    # 10年国债期货主力
FEISHU_WEBHOOK = __import__("ocsecrets").feishu_hook()
MSG_HEADER = '来自 openclaw.glm'  # 报告消息头署名

# ── 全局数据获取状态追踪（失败要写进报告）────────────
DATA_STATUS = {
    'etf_prices': {'ok': False, 'err': ''},
    'umap': {'ok': False, 'err': ''},
    'persistence': {'ok': False, 'err': ''},
    'stock_picks': {'ok': False, 'err': ''},
    'bond_yield': {'ok': False, 'err': ''},
    'shibor': {'ok': False, 'err': ''},
}

# ── 数据源超时（秒，全部放宽到 5 分钟以上）────────────
HTTP_TIMEOUT = 300  # 单个数据源 HTTP 超时
FETCH_TIMEOUT = 300  # 数据获取整体超时

# ── 工具 ─────────────────────────────────────────────
def trade_date_list(start, end):
    """获取交易日列表（升序）"""
    df = safe_pro_call('trade_cal', start_date=start, end_date=end)
    days = df[df['is_open'] == 1]['cal_date'].tolist()
    return sorted(days)

def get_etf_prices(codes, days=WINDOW):
    """获取ETF近N日收盘价 (用 fund_daily)"""
    end = TODAY_STR
    start = (datetime.now(TZ) - timedelta(days=days*2+30)).strftime('%Y%m%d')
    tdays = trade_date_list(start, end)
    if not tdays:
        DATA_STATUS['etf_prices']['err'] = '交易日列表为空'
        return None
    use_days = tdays[-days:]
    data = {}
    for code in codes:
        try:
            df = safe_pro_call('fund_daily', ts_code=code, start_date=use_days[0], end_date=use_days[-1])
            if df is not None and not df.empty:
                df = df.sort_values('trade_date')
                data[code] = df.set_index('trade_date')['close'].reindex(use_days)
        except Exception as e:
            DATA_STATUS['etf_prices']['err'] = f'{code}: {str(e)[:60]}'
    df = pd.DataFrame(data).dropna(how='all')
    if df.empty:
        DATA_STATUS['etf_prices']['err'] = '所有 ETF 数据均为空'
    else:
        DATA_STATUS['etf_prices']['ok'] = True
    return df

def get_stock_daily(ts_codes, start, end):
    """获取多只股票日线 (用 pro.daily)"""
    frames = []
    for code in ts_codes:
        try:
            df = safe_pro_call('daily', ts_code=code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                frames.append(df)
        except:
            pass
    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame()

def get_top_stocks(sw_industries, top_n=TOP_N_STOCKS, lookback=10):
    """从指定行业中选近lookback日涨幅top_n（批量优化版）

    对「end=当天但当天数据未出」做了健壮处理：
    从最近的交易日往前逐个尝试拉 daily，直到取到有数据的日期。
    避免在凌晨/盘前/节假日跑时因当天无数据而 KeyError 导致整个选股失败。
    """
    end = TODAY_STR
    start = (datetime.now(TZ) - timedelta(days=lookback*2+10)).strftime('%Y%m%d')
    tdays = trade_date_list(start, end)
    if len(tdays) < lookback:
        return []
    use_start = tdays[-lookback]

    # 获取股票基础信息
    try:
        stock_basic = safe_pro_call('stock_basic', exchange='', list_status='L', fields='ts_code,name,industry')
        mask = stock_basic['industry'].apply(lambda x: any(ind in str(x) for ind in sw_industries))
        candidates = stock_basic[mask][['ts_code', 'name']].values.tolist()
    except Exception as e:
        DATA_STATUS['stock_picks']['err'] = f'stock_basic: {str(e)[:60]}'
        return []

    if not candidates:
        return []

    code_set = set(c[0] for c in candidates)
    name_map = {c[0]: c[1] for c in candidates}

    # 找到最近一个有实际 daily 数据的交易日（作为 end day）
    # 从 tdays[-1]（今天）向前找，拉当日全市场数据，跳过数据未出的日期
    end_ok = None
    df_end = None
    for d in reversed(tdays):
        if d > end:
            continue
        try:
            df_t = safe_pro_call('daily', trade_date=d)
            if df_t is not None and not df_t.empty and 'ts_code' in df_t.columns:
                end_ok = d
                df_end = df_t
                break
        except Exception:
            continue
    if end_ok is None:
        DATA_STATUS['stock_picks']['err'] = 'daily: 未找到有数据的交易日'
        return []

    # 起始日：比 end_ok 早 lookback 个交易日
    try:
        idx_end = tdays.index(end_ok)
    except ValueError:
        idx_end = len(tdays) - 1
    idx_start = max(0, idx_end - lookback)
    use_start_d = tdays[idx_start]

    results = []
    try:
        df_start = safe_pro_call('daily', trade_date=use_start_d)
        if df_start is not None and not df_start.empty and 'ts_code' in df_start.columns:
            start_prices = df_start[df_start['ts_code'].isin(code_set)].set_index('ts_code')
            end_prices = df_end[df_end['ts_code'].isin(code_set)].set_index('ts_code')

            common = start_prices.index.intersection(end_prices.index)
            for code in common:
                try:
                    p0 = float(start_prices.loc[code, 'close'])
                    p1 = float(end_prices.loc[code, 'close'])
                    pct = (p1 - p0) / p0 * 100
                    results.append({
                        'ts_code': code,
                        'name': name_map.get(code, code),
                        'pct_2w': round(pct, 2),
                        'close': p1,
                    })
                except Exception:
                    pass
    except Exception as e:
        DATA_STATUS['stock_picks']['err'] = f'daily: {str(e)[:60]}'

    if results:
        DATA_STATUS['stock_picks']['ok'] = True
    results.sort(key=lambda x: x['pct_2w'], reverse=True)
    return results[:top_n]

def _force_ipv4():
    """强制所有 socket 请求走 IPv4（容器 IPv6 不通，会导致 akshare 超时）"""
    import socket
    if getattr(socket, '_ipv4_patched', False):
        return
    _orig = socket.getaddrinfo
    def _only_ipv4(*args, **kwargs):
        return [r for r in _orig(*args, **kwargs) if r[0] == socket.AF_INET]
    socket.getaddrinfo = _only_ipv4
    socket._ipv4_patched = True


def get_bond_yield():
    """获取国债收益率 10Y 和 1Y（2Y 作为短端代理）

    Fallback 链:
    0. data/market.db 的 cn.cb.govt.{2Y,5Y,10Y}（统一数据层，2026-10-18 接入）
    1. akshare bond_zh_us_rate（中债收益率曲线，含精确 10Y/2Y，强制 IPv4）
    2. TuShare SHIBOR 1Y + 国债 ETF 期限利差估算
    """
    import signal
    
    def _timeout_handler(signum, frame):
        raise TimeoutError('timeout')
    
    # ── 方案0: data/market.db（cn.cb.govt.*，库优先，2026-10-18 接入）──
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import marketdb as _M
        r10 = _M.latest('cn.cb.govt.10Y')
        r2 = _M.latest('cn.cb.govt.2Y')
        r5 = _M.latest('cn.cb.govt.5Y')
        if r10 and r2:
            DATA_STATUS['bond_yield']['ok'] = True
            DATA_STATUS['bond_yield']['err'] = ''
            print(f'  [market.db] 中债 2Y={r2[1]}%, 5Y={(r5 or [None, None])[1]}%, 10Y={r10[1]}% (as_of {r10[0]})')
            return round(r10[1], 4), round(r2[1], 4)
        DATA_STATUS['bond_yield']['err'] = 'market.db 无数据'
    except Exception as e:
        DATA_STATUS['bond_yield']['err'] = f'market.db失败: {str(e)[:60]}'

    # ── 方案1: akshare bond_zh_us_rate（精确中债收益率）──
    try:
        _force_ipv4()
        from data_hub import ak as ak
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(HTTP_TIMEOUT)
        df = ak.bond_zh_us_rate(start_date=(datetime.now(TZ)-timedelta(days=30)).strftime('%Y%m%d'))
        signal.alarm(0)
        if df is not None and not df.empty:
            row = df.iloc[-1]
            y10 = float(row['中国国债收益率10年'])
            y2 = float(row['中国国债收益率2年'])
            y5 = float(row['中国国债收益率5年'])
            DATA_STATUS['bond_yield']['ok'] = True
            DATA_STATUS['bond_yield']['err'] = ''
            print(f'  [akshare] 中债 2Y={y2}%, 5Y={y5}%, 10Y={y10}%')
            # 返回 10Y 和 2Y（2Y 作为短端代理，用于算利差）
            return round(y10, 4), round(y2, 4)
        DATA_STATUS['bond_yield']['err'] = 'akshare 返回空'
    except Exception as e:
        signal.alarm(0)
        DATA_STATUS['bond_yield']['err'] = f'akshare失败: {str(e)[:80]}'
    
    # ── 方案2: TuShare SHIBOR 1Y + 国债 ETF 期限利差估算 ──
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(HTTP_TIMEOUT)
        shibor_df = safe_pro_call('shibor', start_date=TODAY_STR, end_date=TODAY_STR)
        signal.alarm(0)
        if shibor_df is not None and not shibor_df.empty:
            DATA_STATUS['shibor']['ok'] = True
            y1_shibor = float(shibor_df.iloc[0]['1y'])
            try:
                bond_df = safe_pro_call('fund_daily', ts_code='511260.SH', start_date=TODAY_STR, end_date=TODAY_STR)
                if bond_df is not None and not bond_df.empty:
                    bond_pct = float(bond_df.iloc[0].get('pct_chg', 0))
                    spread_est = 0.46 - bond_pct * 0.5
                    y10_est = y1_shibor + max(0.1, spread_est)
                else:
                    y10_est = y1_shibor + 0.46
            except:
                y10_est = y1_shibor + 0.46
            print(f'  [fallback] SHIBOR 1Y={y1_shibor}%, 估算 10Y={y10_est:.2f}%')
            DATA_STATUS['bond_yield']['ok'] = True
            DATA_STATUS['bond_yield']['err'] = '用 SHIBOR 1Y + 期限利差估算（非精确）'
            return round(y10_est, 4), round(y1_shibor, 4)
        else:
            DATA_STATUS['shibor']['err'] = 'SHIBOR 返回空'
    except Exception as e:
        signal.alarm(0)
        DATA_STATUS['shibor']['err'] = str(e)[:80]
    
    # ── 全部失败 ──
    return None, None

# ── TDA 核心算法 ─────────────────────────────────────
def compute_persistence_features_2d(points):
    """
    在 2D 点云上计算持久同调特征（对齐设计文档 tda_core.py）
    返回 beta_0, beta_1, curvature, max_pers_1, avg_pers_1
      beta_0        = 0维持久图中 death=∞ 的无限长条数（最终存活连通分量）
      beta_1        = 1维持久图 ALL 条数（含噪声短命条）
      curvature     = min(1, max_pers_1 × 10)
      max_pers_1    = 1维持久性中最大 death-birth
      avg_pers_1    = 1维持久性平均值
    """
    if HAS_RIPSER:
        try:
            result = ripser(points, maxdim=1)
            dgms = result['dgms']
            # beta_0: 0维持久图中 death=∞ 的无限长条数（最终存活连通分量）
            dgm0 = dgms[0]
            beta_0 = int(np.sum(np.isinf(dgm0[:, 1]))) if len(dgm0) > 0 else 1
            # beta_1: 1维持久图 ALL 条数（含噪声条）—— 对齐文档 beta_1 = len(dgm_1)
            dgm1 = dgms[1] if len(dgms) > 1 else np.array([]).reshape(0, 2)
            beta_1 = int(len(dgm1))
            # 1维持久性统计
            if len(dgm1) > 0:
                finite = dgm1[~np.isinf(dgm1[:, 1])]
                if len(finite) > 0:
                    persistences = finite[:, 1] - finite[:, 0]
                    max_pers_1 = float(np.max(persistences))
                    avg_pers_1 = float(np.mean(persistences))
                else:
                    max_pers_1 = 0.0
                    avg_pers_1 = 0.0
            else:
                max_pers_1 = 0.0
                avg_pers_1 = 0.0
            # 曲率 = min(1, max_pers_1 × 10) —— 对齐文档公式
            curvature = min(1.0, max_pers_1 * 10) if (beta_1 > 0 and max_pers_1 > 0) else 0.0
            return beta_0, beta_1, round(curvature, 4), round(max_pers_1, 6), round(avg_pers_1, 6)
        except:
            pass
    
    # fallback: 用 scipy 的距离矩阵 + 简化持久同调
    from scipy.spatial.distance import pdist, squareform
    D = squareform(pdist(points))
    if len(D) < 2:
        return 1, 0, 0.0, 0.0, 0.0
    
    # 简化版: 用距离矩阵的统计特征代理
    np.fill_diagonal(D, 0)
    off_diag = D[D > 0]
    if len(off_diag) == 0:
        return 1, 0, 0.0, 0.0, 0.0
    
    # beta_0 代理: 连通分量数（用阈值）
    threshold = np.percentile(off_diag, 25)
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse import csr_matrix
    adj = (D <= threshold).astype(int)
    n_comp, _ = connected_components(csr_matrix(adj), directed=False)
    
    # beta_1 代理: 距离矩阵的方差（结构复杂度）
    beta_1_proxy = int(np.var(off_diag) * 100)
    # 持久性代理（对齐文档统计量）
    max_pers_1 = float(np.percentile(off_diag, 90)) if len(off_diag) else 0.0
    avg_pers_1 = float(np.mean(off_diag)) if len(off_diag) else 0.0
    
    # 曲率代理: 局部密度的变化
    curvature = float(np.std(off_diag) / (np.mean(off_diag) + 1e-8))
    
    return max(1, n_comp), max(0, beta_1_proxy), round(curvature, 4), round(max_pers_1, 6), round(avg_pers_1, 6)

def compute_annual_volatility(price_df):
    """从 ETF 价格矩阵计算年化波动率 σ_annual = 20日日均收益率 std × √252"""
    try:
        # 用收盘价计算日均收益率
        closes = price_df.dropna(how='all')
        if closes.shape[0] < 2:
            return 0.10
        # 取最近 20 个交易日
        recent = closes.tail(20)
        ret = np.log(recent / recent.shift(1)).dropna()
        if ret.empty:
            return 0.10
        # 所有因子的日均收益率序列合并求总体波动
        vals = ret.values.flatten()
        vals = vals[~np.isnan(vals)]
        if len(vals) < 2:
            return 0.10
        daily_std = float(np.std(vals))
        return float(daily_std * np.sqrt(252))
    except Exception:
        return 0.10

def umap_reduce(data_2d, n_components=2):
    """UMAP 降维，带 fallback"""
    # 标准化
    if HAS_SKLEARN:
        scaler = StandardScaler()
        data_std = scaler.fit_transform(data_2d)
    else:
        mean = np.mean(data_2d, axis=0)
        std = np.std(data_2d, axis=0) + 1e-8
        data_std = (data_2d - mean) / std
    
    if HAS_UMAP:
        try:
            reducer = UMAP(n_components=n_components, n_neighbors=15, min_dist=0.1, random_state=42)
            embedding = reducer.fit_transform(data_std)
            return embedding
        except:
            pass
    
    # fallback: PCA (用 numpy SVD)
    cov = np.cov(data_std.T)
    U, S, Vt = np.linalg.svd(cov)
    embedding = data_std @ U[:, :n_components]
    return embedding

def compute_sharpe(prices_series, periods=WINDOW):
    """计算夏普比"""
    if len(prices_series) < 2:
        return 0.0
    returns = np.diff(np.log(prices_series.values))
    if len(returns) < 2 or np.std(returns) == 0:
        return 0.0
    sharpe = np.mean(returns) / np.std(returns) * math.sqrt(252)
    return round(sharpe, 3)

def compute_defensive_weights(etf_prices):
    """
    动量-防御平衡权重分配
    =====================
    原则：既保留防御取向（弱势/负夏普因子被降权），又不让强势因子被系统性压死。
    - 用夏普排名映射到 [MIN_W, MAX_W] 的平滑权重区间
    - 强势因子（如医药健康夏普 +2）获得中性偏高权重，而非被 (1-s)² 打到地板
    - 弱势因子（负夏普）获得低权重，体现防御
    - 单因子上限 MAX_FACTOR_WEIGHT，归一化
    """
    # 权重区间的可配置边界（防止过于集中/过于分散）
    MIN_W = 0.05   # 单个因子最低权重（保底分散）
    MAX_W = 0.24   # 单个因子最高原始权重（低于 MAX_FACTOR_WEIGHT=0.30 给上限留空间）

    sharpes = {}
    for factor in FACTORS:
        code = factor['ts_code']
        if code in etf_prices.columns:
            s = compute_sharpe(etf_prices[code].dropna())
        else:
            s = 0.0
        sharpes[factor['name']] = s

    names = list(sharpes.keys())
    s_vals = np.array([sharpes[n] for n in names], dtype=float)

    # 用 rank 而不是原始夏普值，避免极端值对权重的非线性扭曲
    # 夏普越高 → rank 越高 → 权重越高
    order = np.argsort(np.argsort(s_vals))          # 0..n-1 排名（并列保序）
    n = len(names)
    if n <= 1:
        return {names[0]: 1.0}, sharpes

    # 线性映射 rank → [MIN_W, MAX_W]，并做 min-max 归一化
    # 保证最好因子 = MAX_W，最差因子 = MIN_W
    if s_vals.max() == s_vals.min():
        frac = np.full(n, 0.5)
    else:
        frac = (order / (n - 1)).astype(float)       # 0..1
    raw = MIN_W + (MAX_W - MIN_W) * frac
    raw_weights = dict(zip(names, raw.tolist()))

    # 归一化
    total = sum(raw_weights.values())
    weights = {k: v / total for k, v in raw_weights.items()}

    # 单因子上限控制（上限取 min(MAX_FACTOR_WEIGHT, 归一化后可能超的上限)）
    cap = MAX_FACTOR_WEIGHT
    excess = 0.0
    for k in weights:
        if weights[k] > cap:
            excess += weights[k] - cap
            weights[k] = cap
    # 把超出部分分配给未超限的因子
    if excess > 0:
        below = {k: v for k, v in weights.items() if v < cap}
        below_total = sum(below.values())
        if below_total > 0:
            for k in below:
                weights[k] += excess * (weights[k] / below_total)

    # 再次归一化
    total = sum(weights.values())
    weights = {k: round(v / total, 4) for k, v in weights.items()}
    return weights, sharpes

def judge_market_state(beta_0, beta_1, curvature, max_pers_1, avg_pers_1, sigma_annual):
    """判定市场状态和脆弱性（对齐设计文档 tda_core.py）
    脆弱性 = clip(V_vol + V_tda + V_curv, 10, 90)
    """
    # 分量1: 波动率锚定 (主分量, 20-65%)
    V_vol = min(65, max(20, 15 + sigma_annual * 150))

    # 分量2: TDA 结构洞修正 (0-25%)
    if avg_pers_1 > 0.1:      # 显著结构洞
        V_tda = min(25, beta_1 * 3)
    else:                      # 噪声主导
        V_tda = min(10, beta_1 * 1.5)

    # 分量3: 曲率微调 (-8 ~ +8)
    if curvature > 0.7:
        V_curv = -8       # 清晰方向 → 降脆弱性
    elif curvature < 0.3:
        V_curv = +8       # 方向不明 → 升脆弱性
    else:
        V_curv = 0

    vul = min(90, max(10, V_vol + V_tda + V_curv))

    # 市场状态判定 (对齐文档阈值 35/50/70)
    if vul < 35 and curvature > 0.5:
        state = '强势上涨'
    elif vul < 50:
        state = '震荡偏强'
    elif vul < 70:
        state = '震荡偏弱'
    else:
        state = '弱势下跌'

    pos_ratio = max(0.15, 1.0 - vul / 100)
    return state, round(vul, 1), round(pos_ratio, 2)

def judge_bond_direction(y10, y1, bond_prices):
    """债券端方向判定（简化版）"""
    if y10 is None or y1 is None:
        return '观望', 0, '数据不足'
    
    spread = y10 - y1
    # 国债收益率上行走空，下行走多
    # 简化：spread > 0.5 偏空，spread < 0.2 偏多
    if spread > 0.8:
        direction = '做空'
        reason = f'利差{spread:.2f}%偏阔，利率曲线陡峭'
    elif spread < 0.3:
        direction = '做多'
        reason = f'利差{spread:.2f}%偏窄，利率曲线平坦'
    else:
        direction = '观望'
        reason = f'利差{spread:.2f}%中性'
    
    # 止损
    if bond_prices is not None and len(bond_prices) >= 2:
        current = bond_prices.iloc[-1]
        atr = abs(np.diff(bond_prices.values)).mean() if len(bond_prices) > 1 else 0.5
        stop_loss = current - max(0.3, min(1.5, atr * 0.5))
    else:
        stop_loss = None
    
    return direction, round(spread, 2), reason, stop_loss

# ── 状态管理 ─────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except:
            pass
    return {
        'start_date': TODAY_STR,
        'start_capital': INITIAL_CAPITAL,
        'cumulative_pnl': 0,
        'daily_records': [],
        'last_signal': None,
        'year': str(datetime.now(TZ).year),
    }

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

def check_year_reset(state):
    """年初重置"""
    current_year = str(datetime.now(TZ).year)
    if state.get('year') != current_year:
        state = {
            'start_date': TODAY_STR,
            'start_capital': INITIAL_CAPITAL,
            'cumulative_pnl': 0,
            'daily_records': [],
            'last_signal': None,
            'year': current_year,
        }
    return state

# ── 报告生成 ─────────────────────────────────────────
def generate_report(state, market_state, vulnerability, pos_ratio,
                    weights, sharpes, stock_picks, bond_info, today_pnl):
    """生成 Markdown 报告"""
    factor_table = '\n'.join([
        f"  {f} | 夏普 {sharpes.get(f, 0):.2f} | 权重 {w*100:.1f}%"
        for f, w in sorted(weights.items(), key=lambda x: -x[1])
    ])
    
    stock_lines = []
    for factor in FACTORS:
        fname = factor['name']
        w = weights.get(fname, 0)
        picks = stock_picks.get(fname, [])
        if picks:
            stk = '、'.join([f"{p['name']}({p['ts_code']}) {p['pct_2w']:+.1f}%" for p in picks])
        else:
            stk = '—'
        stock_lines.append(f"  {fname}({w*100:.1f}%): {stk}")
    
    # 收益跟踪
    cap = state['start_capital']
    cum_pnl = state['cumulative_pnl']
    cum_ret = cum_pnl / cap * 100
    total_days = len(state['daily_records'])
    
    daily_lines = []
    for rec in state['daily_records'][-10:]:
        daily_lines.append(
            f"  {rec['date']} | 股票 {rec['stock_ret']:+.2f}% | 债券 {rec['bond_ret']:+.2f}% | 组合 {rec['combined_ret']:+.2f}% | 累计 {rec['cum_pnl']:+.0f}"
        )
    
    today_stock_ret = today_pnl.get('stock_ret', 0)
    today_bond_ret = today_pnl.get('bond_ret', 0)
    today_combined = today_pnl.get('combined_ret', 0)
    
    direction, spread, bond_reason, stop_loss = bond_info
    
    report = f"""日期: {TODAY_STR}
策略版本: v3.3 (个股口径) | 防御取向

━━━ 市场状态 ━━━

拓扑特征: β₀={market_state[0]}, β₁={market_state[1]}, 曲率={market_state[2]}
市场状态: {market_state[3]}
脆弱性: {vulnerability:.1f}% → 建议仓位: {pos_ratio*100:.0f}%

━━━ 因子权重 & 持仓 ━━━

{factor_table}

持仓明细:
""" + '\n'.join(stock_lines) + f"""

━━━ 债券端 ━━━

10Y-2Y 利差: {spread:.2f}%
方向: {direction} | {bond_reason}
""" + (f"止损位: {stop_loss:.2f}" if stop_loss else '') + f"""
""" + (f"⚠️ 数据来源: {DATA_STATUS['bond_yield'].get('err')}" if DATA_STATUS['bond_yield'].get('err') and DATA_STATUS['bond_yield'].get('ok') else '') + f"""

━━━ 收益跟踪 ━━━

起始日期: {state['start_date']}
初始资金: {cap:,.0f} 元
累计盈亏: {cum_pnl:+,.0f} 元 ({cum_ret:+.2f}%)
跟踪天数: {total_days} 个交易日

今日收益:
  股票端: {today_stock_ret:+.2f}%
  债券端: {today_bond_ret:+.2f}%
  组合:   {today_combined:+.2f}%

近期明细:
""" + '\n'.join(daily_lines[-10:]) + f"""

━━━ 备注 ━━━
• 信号 T 日收盘生成, T+1 日开盘生效
• {datetime.now(TZ).year} 年底重置
• 未计交易成本
"""
    # 数据获取失败标注
    failed_parts = []
    status_labels = {
        'etf_prices': 'ETF 价格',
        'umap': 'UMAP 降维',
        'persistence': '持久同调',
        'stock_picks': '选股',
        'bond_yield': '国债收益率',
        'shibor': 'SHIBOR',
    }
    for key, label in status_labels.items():
        st = DATA_STATUS.get(key, {})
        # shibor 只是 fallback，当国债收益率已成功时跳过
        if key == 'shibor' and DATA_STATUS.get('bond_yield', {}).get('ok'):
            continue
        if not st.get('ok') and st.get('err'):
            err = st.get('err', '未知错误')
            failed_parts.append(f'{label}: {err}')
    
    if failed_parts:
        report += "\n━━━ ⚠️ 数据获取告警 ━━━\n"
        for fp in failed_parts:
            report += f"• ❌ {fp}\n"
    
    return report

# ── 推送 ─────────────────────────────────────────────
def send_feishu(text):
    """飞书 Webhook 推送（interactive card 格式）"""
    import urllib.request
    # interactive card 格式，标题和来源同一行（标题在前、来源在后）
    payload = json.dumps({
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 TDA 流形旋转多资产策略 · {MSG_HEADER}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "markdown", "content": text}
            ]
        }
    }).encode('utf-8')
    req = urllib.request.Request(FEISHU_WEBHOOK, data=payload, method='POST',
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {'error': str(e)}

# ── 主流程 ───────────────────────────────────────────
def _generate_error_report(state, reason):
    """数据获取失败时生成错误报告"""
    report = f"""日期: {TODAY_STR}

━━━ ❌ 数据获取失败 ━━━

原因: {reason}

数据状态:
"""
    labels = {
        'etf_prices': 'ETF 价格',
        'umap': 'UMAP 降维',
        'persistence': '持久同调',
        'stock_picks': '选股',
        'bond_yield': '国债收益率',
        'shibor': 'SHIBOR',
    }
    for k, label in labels.items():
        st = DATA_STATUS.get(k, {})
        status = '✅ 正常' if st.get('ok') else f'❌ 失败: {st.get("err", "未知")}'
        report += f"• {label}: {status}\n"
    return report


def run_daily():
    """每日运行"""
    print(f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}] TDA 流形旋转策略启动...")
    state = load_state()
    state = check_year_reset(state)
    
    # 1. 获取 ETF 价格
    print("① 获取 ETF 价格...")
    etf_prices = get_etf_prices(ETF_CODES, WINDOW)
    if etf_prices is None or etf_prices.empty:
        print("❌ 无法获取ETF数据")
        # 即使 ETF 失败也尝试生成报告，标注失败
        return _generate_error_report(state, 'ETF 价格获取失败')
    
    # 2. UMAP 降维
    print("② UMAP 降维...")
    try:
        price_matrix = etf_prices.dropna(axis=1, how='all').ffill().values
        embedding = umap_reduce(price_matrix, n_components=2)
        DATA_STATUS['umap']['ok'] = True
    except Exception as e:
        DATA_STATUS['umap']['err'] = str(e)[:80]
        embedding = None
    
    # 3. 持久同调
    print("③ 持久同调...")
    if embedding is not None:
        beta_0, beta_1, curvature, max_pers_1, avg_pers_1 = compute_persistence_features_2d(embedding)
        DATA_STATUS['persistence']['ok'] = True
    else:
        beta_0, beta_1, curvature, max_pers_1, avg_pers_1 = 1, 0, 0.0, 0.0, 0.0
        DATA_STATUS['persistence']['err'] = 'UMAP 失败，使用默认值'
    
    # 4. 市场状态（含年化波动率，对齐文档脆弱性公式）
    print("④ 市场状态判定...")
    sigma_annual = compute_annual_volatility(etf_prices)
    mkt_state, vul, pos_ratio = judge_market_state(beta_0, beta_1, curvature, max_pers_1, avg_pers_1, sigma_annual)
    print(f"  β₀={beta_0}, β₁={beta_1}, 曲率={curvature}, max_pers₁={max_pers_1}, avg_pers₁={avg_pers_1}, σ年化={sigma_annual:.3f}")
    print(f"  状态={mkt_state}, 脆弱性={vul:.1f}%, 建议仓位={pos_ratio*100:.0f}%")
    
    # 5. 防御权重
    print("⑤ 防御权重分配...")
    weights, sharpes = compute_defensive_weights(etf_prices)
    for f, w in sorted(weights.items(), key=lambda x: -x[1]):
        print(f"  {f}: 夏普={sharpes[f]:.2f}, 权重={w*100:.1f}%")
    
    # 6. 下钻个股
    print("⑥ 选股...")
    stock_picks = {}
    for factor in FACTORS:
        picks = get_top_stocks(factor['swIndustry'])
        stock_picks[factor['name']] = picks
        if picks:
            names = [f"{p['name']}({p['pct_2w']:+.1f}%)" for p in picks]
            print(f"  {factor['name']}: {', '.join(names)}")
    
    # 7. 债券端
    print("⑦ 债券端...")
    y10, y1 = get_bond_yield()
    # 国债ETF价格（511260.SH 上证5年国债ETF）
    try:
        bond_df = safe_pro_call('fund_daily', ts_code='511260.SH', start_date=(datetime.now(TZ)-timedelta(days=120)).strftime('%Y%m%d'), end_date=TODAY_STR)
        bond_prices = bond_df.sort_values('trade_date')['close'].tail(WINDOW) if bond_df is not None and not bond_df.empty else None
    except:
        bond_prices = None
    bond_info = judge_bond_direction(y10, y1, bond_prices)
    print(f"  利差={bond_info[1]:.2f}%, 方向={bond_info[0]}")
    
    # 8. 计算今日收益（用上次信号）
    today_pnl = {'stock_ret': 0, 'bond_ret': 0, 'combined_ret': 0}
    if state.get('last_signal'):
        prev = state['last_signal']
        prev_date = prev.get('date', '')
        # 同日重跑不重复记录
        already_recorded = any(r.get('date') == TODAY_STR for r in state.get('daily_records', []))
        
        if not already_recorded and prev_date != TODAY_STR:
            # 股票端收益: today_ret 已经是百分比单位
            stock_rets = []
            for factor in FACTORS:
                fname = factor['name']
                w = prev['weights'].get(fname, 0)
                picks = prev['stock_picks'].get(fname, [])
                if picks:
                    avg_ret = np.mean([p.get('today_ret', 0) for p in picks])
                    stock_rets.append(w * avg_ret)
            today_pnl['stock_ret'] = round(sum(stock_rets), 2) if stock_rets else 0
            
            # 债券端收益
            if bond_prices is not None and len(bond_prices) >= 2:
                bond_ret = (bond_prices.iloc[-1] - bond_prices.iloc[-2]) / bond_prices.iloc[-2] * 100
                direction = prev.get('bond_direction', '观望')
                if direction == '做多':
                    today_pnl['bond_ret'] = round(bond_ret, 2)
                elif direction == '做空':
                    today_pnl['bond_ret'] = round(-bond_ret, 2)
            
            # 组合
            today_pnl['combined_ret'] = round(
                STOCK_RATIO * today_pnl['stock_ret'] + BOND_RATIO * today_pnl['bond_ret'], 2
            )
            
            # 更新累计
            daily_pnl = INITIAL_CAPITAL * today_pnl['combined_ret'] / 100
            state['cumulative_pnl'] += daily_pnl
            state['daily_records'].append({
                'date': TODAY_STR,
                'stock_ret': today_pnl['stock_ret'],
                'bond_ret': today_pnl['bond_ret'],
                'combined_ret': today_pnl['combined_ret'],
                'daily_pnl': round(daily_pnl, 2),
                'cum_pnl': round(state['cumulative_pnl'], 2),
            })
        elif already_recorded:
            # 同日重跑，显示已有记录但不重复追加
            rec = [r for r in state['daily_records'] if r.get('date') == TODAY_STR]
            if rec:
                today_pnl = {
                    'stock_ret': rec[-1]['stock_ret'],
                    'bond_ret': rec[-1]['bond_ret'],
                    'combined_ret': rec[-1]['combined_ret'],
                }
    
    # 9. 保存今日信号（供明日计算收益）
    #    注意：stock_picks 里的 today_ret 字段明天获取实际涨跌幅后回填
    state['last_signal'] = {
        'date': TODAY_STR,
        'weights': weights,
        'stock_picks': {k: [{'ts_code': p['ts_code'], 'name': p['name'], 'pct_2w': p['pct_2w']} for p in v] for k, v in stock_picks.items()},
        'bond_direction': bond_info[0],
        'market_state': mkt_state,
        'vulnerability': vul,
        'pos_ratio': pos_ratio,
    }
    
    # 10. 回填昨日选股的今日涨跌幅
    #     （下次运行时，last_signal 里的 stock_picks 已经有 today_ret 了）
    
    save_state(state)
    
    # 11. 生成报告
    print("⑪ 生成报告...")
    report = generate_report(
        state,
        (beta_0, beta_1, curvature, mkt_state),
        vul, pos_ratio,
        weights, sharpes, stock_picks, bond_info, today_pnl
    )
    
    # 保存报告
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORT_DIR / f'tda_report_{TODAY_STR}.md'
    report_file.write_text(report, encoding='utf-8')
    print(f"  报告已保存: {report_file}")
    
    return report


def backfill_today_returns():
    """回填昨日信号中选股的今日涨跌幅（同日信号不回填）"""
    state = load_state()
    if not state.get('last_signal'):
        return
    prev = state['last_signal']
    prev_date = prev.get('date', '')
    # 不回填同日信号（同日信号没有今日收益可言）
    if prev_date == TODAY_STR:
        return
    
    for fname, picks in prev.get('stock_picks', {}).items():
        for p in picks:
            code = p.get('ts_code')
            if not code:
                continue
            try:
                df = safe_pro_call('daily', ts_code=code, start_date=TODAY_STR, end_date=TODAY_STR)
                if df is not None and not df.empty:
                    p['today_ret'] = float(df.iloc[0]['pct_chg'])
                else:
                    p['today_ret'] = 0
            except:
                p['today_ret'] = 0
    save_state(state)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='TDA 流形旋转策略')
    parser.add_argument('--dry-run', action='store_true', help='不推送')
    parser.add_argument('--backtest', action='store_true', help='回测模式')
    args = parser.parse_args()
    
    # 回填昨日选股的今日涨跌幅
    backfill_today_returns()
    
    # 运行策略
    report = run_daily()
    if report is None:
        print('❌ 策略运行失败')
        sys.exit(1)
    
    print('\n' + '='*60)
    print(report)
    print('='*60)
    
    if not args.dry_run:
        result = send_feishu(report)
        print(f"\n飞书推送: {result}")
    else:
        print('\n[--dry-run] 跳过推送')


if __name__ == '__main__':
    main()
