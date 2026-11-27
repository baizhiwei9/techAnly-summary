#!/usr/bin/env python3
"""
60 因子 TDA 引擎 — TuShare 同花顺概念版（带缓存优化）
======================================================
数据：TuShare ths_index/ths_daily/ths_member（与百度/Wind 同源同花顺概念）
缓存：概念K线与成分股缓存到本地 parquet，避免每日重复拉取
"""

# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

import sys, warnings, math, json, os, time
warnings.filterwarnings('ignore')
sys.path.insert(0, 'bin')
import socket
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET]
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from tushare_init import get_pro, TOKEN, DIRECT_TOKEN

# 代理优先 → 直连兜底（2026-09-23 用户指定）
_PRO = get_pro()
_PRO_DIRECT = get_pro(token=DIRECT_TOKEN, http_url='')

def safe_pro_call(method_name, **kwargs):
    err = None
    try:
        return getattr(_PRO, method_name)(**kwargs)
    except Exception as e:
        err = str(e)
    try:
        return getattr(_PRO_DIRECT, method_name)(**kwargs)
    except Exception as e:
        raise Exception(f'{method_name} 代理失败({str(err)[:40]}) 且直连失败({str(e)[:40]})')

WINDOW = 60
MAX_FACTORS = 60
CORR_THRESH = 0.95
END = datetime.now().strftime('%Y%m%d')
START = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
CACHE_DIR = Path('.cache/tda_concepts')
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CORE_CONCEPTS = ['人工智能','第三代半导体','光模块','算力','数据中心','人形机器人','创新药',
                 '光伏','储能','固态电池','白酒','证券','银行','铜','黄金','煤炭','稀土永磁',
                 '军工','商业航天','卫星导航','数字经济','数据要素','信创','数字货币','碳中和',
                 '高股息','中特估','一带一路','减速器','工业母机','光刻机','机器人','半导体']
NOISE_KW = ['概念','板块','精选','指数','申万','昨日','今日','次新','ST','退市','融资','质押',
            '举牌','增持','回购','股东','重组','借壳','转债','期权','标的','期货','外汇','汇率',
            '宏观','政策','规划','会议','改革','示范区','自贸区','一体化','长三角','京津冀',
            '大湾区','雄安','海南','上海','深圳','北京','广州','浙江','江苏','山东','福建',
            '广东','四川','重庆','湖南','湖北','陕西','集团','央企','国企改','注册制']


def build_concept_list():
    pro = get_pro()
    idx = safe_pro_call('ths_index', exchange='A')
    idx = idx[idx['type'] == 'N']
    name2code = dict(zip(idx['name'], idx['ts_code']))
    all_names = idx['name'].tolist()
    ranked = [c for c in CORE_CONCEPTS if c in name2code]
    for n in all_names:
        if n in ranked: continue
        if len(str(n)) > 10: continue
        if any(k in str(n) for k in NOISE_KW): continue
        ranked.append(n)
        if len(ranked) >= 85: break
    return ranked[:85], name2code


def fetch_cached(pro, n, code):
    """带缓存拉概念K线"""
    cf = CACHE_DIR / f'{code}.csv'
    if cf.exists():
        df = pd.read_csv(cf, dtype={'trade_date': str})
        last = df['trade_date'].max()
        if last >= END:  # 缓存当天可用
            return df
    df = safe_pro_call('ths_daily', ts_code=code, start_date=START, end_date=END)
    if df is not None and len(df) > 0:
        df.to_csv(cf, index=False)
    return df


def main():
    t0 = time.time()
    print("构建概念列表...", flush=True)
    ranked, name2code = build_concept_list()
    print(f"待拉概念: {len(ranked)}", flush=True)
    pro = get_pro()

    series, member_map = {}, {}
    for i, n in enumerate(ranked):
        code = name2code[n]
        try:
            df = fetch_cached(pro, n, code)
            if df is not None and len(df) > 40:
                s = df.set_index('trade_date')['close'].astype(float).sort_index().tail(WINDOW+5)
                series[n] = s
                mf = CACHE_DIR / f'{code}_mem.csv'
                if mf.exists():
                    m = pd.read_csv(mf)
                else:
                    m = safe_pro_call('ths_member', ts_code=code)
                    if m is not None and len(m) > 0:
                        m.to_csv(mf, index=False)
                if m is not None and len(m) > 0:
                    member_map[n] = m['con_code'].tolist()
        except Exception:
            pass
    print(f"成功拉取: {len(series)}, 耗时{time.time()-t0:.0f}s", flush=True)

    if len(series) < 30:
        print("⚠️ 数据不足", len(series)); return

    df = pd.DataFrame(series).dropna(how='any').tail(WINDOW)
    ret = np.log(df / df.shift(1)).dropna()
    corr = ret.corr()
    cols = list(corr.columns); keep = []
    for c in cols:
        if len(keep) >= MAX_FACTORS: break
        if not any(abs(corr.loc[c, k]) > CORR_THRESH for k in keep):
            keep.append(c)
    print(f"去冗余后因子: {len(keep)}", flush=True)

    R = ret[keep]
    C = R.corr().values
    eigvals = np.linalg.eigvalsh(C)[::-1]; eigvals = eigvals[eigvals > 1e-10]
    n = C.shape[0]
    b0 = int(np.sum(eigvals > 1.0))
    p = eigvals/eigvals.sum(); H = -np.sum(p*np.log(p+1e-12))
    Hmax = np.log(n); curv = 1 - H/Hmax; effD = math.exp(H)
    sigma = np.std(R.values.flatten())*np.sqrt(252)
    V_vol = min(65,max(20,15+sigma*150))
    V_tda = min(25,H*4) if H>1.5 else min(10,H*2)
    V_curv = -8 if curv>0.7 else (+8 if curv<0.3 else 0)
    vul = min(90,max(10,V_vol+V_tda+V_curv))
    state = ('强势上涨' if vul<35 and curv>0.5 else '震荡偏强' if vul<50 else '震荡偏弱' if vul<70 else '弱势下跌')
    pos = max(15, 100-int(vul))

    print(f"\n=== {n} 因子 TDA（同花顺概念·TuShare）===")
    print(f"β₀={b0} β₁={H:.4f} 有效维={effD:.2f} 曲率={curv:.4f} σ={sigma:.3f}")
    print(f"脆弱性={vul:.1f}% (V_vol{V_vol:.0f}+V_tda{V_tda:.0f}+V_curv{V_curv:.0f})")
    print(f"状态={state} 仓位={pos}%")
    print(f"前8特征值:{np.round(eigvals[:8],3)}")
    print(f"\n因子列表({len(keep)}): {keep}")

    tda = {'n':n,'b0':b0,'b1':round(H,4),'effD':round(effD,2),'curv':round(curv,4),
           'sigma':round(sigma,3),'vul':round(vul,1),'state':state,'pos':pos,
           'V_vol':round(V_vol,1),'V_tda':round(V_tda,1),'V_curv':round(V_curv,1)}
    state_out = {'date':END,'factors':keep,'tda':tda,
                 'members':{c:member_map.get(c,[]) for c in keep}}
    Path('memory/tda_reports').mkdir(parents=True,exist_ok=True)
    Path('memory/tda_60factor_state.json').write_text(
        json.dumps(state_out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n[已存状态] memory/tda_60factor_state.json")
    print(f"[总耗时] {time.time()-t0:.0f}s")


if __name__ == '__main__':
    main()
