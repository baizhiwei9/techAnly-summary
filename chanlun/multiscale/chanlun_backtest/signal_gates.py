"""
缠论分型版 · 级别交易闸门（第一批，2026-11-11）
口径对齐远程 techAnly-summary（main 5d72f91 + 2026-11-08/11-10/11-11 大佬定稿）：

  15分钟：只做结构参考，**不发交易信号**
  1小时 ：只认三买；一买须 MA10>MA20（金叉）；不认二买
          卖点对称：只认三卖；一卖须 MA10<MA20
  日线  ：一/二/三买 + 一/二/三卖，按「年龄 / 距买点距离 / 均线方向 / 结构未破」过滤
  周线  ：只认一买/二买（卖点对称），周线闸门放宽

日线闸门（对齐 bin/tech_buypoint.py）：
  · 一买：MA20 向上（硬门槛）+ age ≤ 40 + gap ∈ [-3%, +10%]
  · 二买：age ≤ 60 + gap ∈ [-3%, +10%]（MA20 向上仅加分）
  · 三买：age ≤ 90 + gap ∈ [-3%, +10%] + 现价 > ZG + 回踩后未破位
  · 一卖 MA20 向下（硬门槛）；二/三卖对称
  gap = (现价 / 买点价 - 1) * 100
"""
import pandas as pd

# (level, kind) -> 最大年龄（K线根数）；不在 allow 集合里的类型直接不认
LEVEL_RULES = {
    "15m": {
        "buy": set(), "sell": set(),
        "max_age": {},
        "near_up": 0.0, "near_dn": 0.0,
        "b1_ma": None,          # 不发信号
        "check_break": False,
        "note": "15分钟仅结构参考",
    },
    "1h": {
        "buy": {"B1", "B3"}, "sell": {"S1", "S3"},
        "max_age": {"B1": 30, "B2": 0, "B3": 30,
                    "S1": 30, "S2": 0, "S3": 30},
        "near_up": 5.0, "near_dn": 3.0,
        "b1_ma": "golden",      # 一买须 MA10>MA20；一卖须 MA10<MA20
        "check_break": True,
        "note": "1小时只认三买（一买须金叉）",
    },
    "daily": {
        "buy": {"B1", "B2", "B3"}, "sell": {"S1", "S2", "S3"},
        "max_age": {"B1": 40, "B2": 60, "B3": 90,
                    "S1": 40, "S2": 60, "S3": 90},
        "near_up": 10.0, "near_dn": 3.0,
        "b1_ma": "slope",       # 一买须 MA20 向上；一卖须 MA20 向下
        "check_break": True,
        "note": "日线一二三买",
    },
    "weekly": {
        "buy": {"B1", "B2"}, "sell": {"S1", "S2"},
        "max_age": {"B1": 12, "B2": 12, "S1": 12, "S2": 12},
        "near_up": 15.0, "near_dn": 5.0,
        "b1_ma": None,          # 周线放宽：不强制均线方向
        "check_break": False,
        "note": "周线只认一买/二买",
    },
}

MA20_SLOPE_BARS = 5   # 对齐远程 TECH_MA20_UP_BARS / DN_BARS


def _ma(close, period):
    if len(close) < period:
        return None
    return pd.Series(close).rolling(period).mean()


def _slope(ma, bars=MA20_SLOPE_BARS):
    """均线斜率（%），回看 bars 根；数据不足返回 0"""
    if ma is None or len(ma) <= bars:
        return 0.0
    cur, prev = ma.iloc[-1], ma.iloc[-1 - bars]
    if pd.isna(cur) or pd.isna(prev) or prev == 0:
        return 0.0
    return float((cur / prev - 1) * 100)


def _gate(df, tf_id, kind, level, idx):
    """单条信号过闸门。返回 (accept, why)"""
    rule = LEVEL_RULES.get(tf_id)
    if rule is None:
        return False, "未知级别"

    is_buy = kind.startswith("B")
    allowed = rule["buy"] if is_buy else rule["sell"]
    if kind not in allowed:
        return False, "该级别不认%s" % kind

    try:
        lvl = float(level)
        idx = int(idx)
    except (TypeError, ValueError):
        return False, "信号参数异常"
    if lvl <= 0:
        return False, "买/卖点价格异常(0)"

    n = len(df)
    if idx < 0 or idx >= n:
        return False, "信号位置越界"

    close = float(df["close"].iloc[-1])
    age = n - 1 - idx
    max_age = rule["max_age"].get(kind, 0)
    gap = (close / lvl - 1) * 100

    # ① 年龄
    if max_age <= 0 or age > max_age:
        return False, "信号太老（%d根前 > %d）" % (age, max_age)

    if is_buy:
        # ② 距离：现价须在买点附近（跑远 > near_up，跌破 > near_dn）
        if gap > rule["near_up"]:
            return False, "已跑远（现价高于买点 %.1f%%）" % gap
        if gap < -rule["near_dn"]:
            return False, "已跌破买点 %.1f%%（结构破坏）" % abs(gap)
        # ③ 三买：现价必须仍在 ZG 上方，且回踩后未跌破
        if kind == "B3":
            if close <= lvl:
                return False, "已跌回中枢内（现价 ≤ ZG）"
            if rule["check_break"]:
                lows = df["low"].values[idx:]
                if len(lows) and float(min(lows)) < lvl * 0.995:
                    return False, "回踩低点已被跌破（结构破坏）"
        # ④ 一买均线硬门槛
        if kind == "B1":
            mode = rule["b1_ma"]
            if mode == "slope":
                slope = _slope(_ma(df["close"].values, 20))
                if slope <= 0:
                    return False, "MA20 未向上（斜率 %+.2f%%）" % slope
            elif mode == "golden":
                ma10 = _ma(df["close"].values, 10)
                ma20 = _ma(df["close"].values, 20)
                if ma10 is None or ma20 is None or pd.isna(ma10.iloc[-1]) or pd.isna(ma20.iloc[-1]):
                    return False, "均线数据不足"
                if ma10.iloc[-1] <= ma20.iloc[-1]:
                    return False, "MA10 未上穿 MA20（未金叉）"
    else:
        # 对称：现价须在卖点附近（涨破 > near_dn，跌太远 > near_up）
        if gap > rule["near_dn"]:
            return False, "已涨破卖点 %.1f%%（卖点失效）" % gap
        if gap < -rule["near_up"]:
            return False, "已跌破卖点 %.1f%%（离卖点太远）" % abs(gap)
        if kind == "S3":
            if close >= lvl:
                return False, "已回到中枢内（现价 ≥ ZD）"
            if rule["check_break"]:
                highs = df["high"].values[idx:]
                if len(highs) and float(max(highs)) > lvl * 1.005:
                    return False, "反抽高点已被涨破（卖点失效）"
        if kind == "S1":
            mode = rule["b1_ma"]
            if mode == "slope":
                slope = _slope(_ma(df["close"].values, 20))
                if slope >= 0:
                    return False, "MA20 未向下（斜率 %+.2f%%）" % slope
            elif mode == "golden":
                ma10 = _ma(df["close"].values, 10)
                ma20 = _ma(df["close"].values, 20)
                if ma10 is None or ma20 is None or pd.isna(ma10.iloc[-1]) or pd.isna(ma20.iloc[-1]):
                    return False, "均线数据不足"
                if ma10.iloc[-1] >= ma20.iloc[-1]:
                    return False, "MA10 未下穿 MA20（未死叉）"

    return True, "通过"


def apply_level_gates(df, signals, tf_id):
    """
    对 detect_structure_signals() 的结果按级别过滤。
    signals: list of (idx, kind, stop_loss, structure_level)
    返回: 通过闸门的同结构列表
    """
    if not signals:
        return []
    accepted = []
    for idx, kind, stop_loss, struct_level in signals:
        ok, _why = _gate(df, tf_id, kind, stop_loss, idx)
        if ok:
            accepted.append((idx, kind, stop_loss, struct_level))
    return accepted
