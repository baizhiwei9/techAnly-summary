#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""registry.py · 画图登记表（唯一一份）
=========================================
本库所有「会出图」的脚本都登记在这里，`misc/charts/run.py` 只认这张表。
新增一张图 = 在这里加一条 dict，别的地方不用动。

每条的字段：
    name        统一入口用的名字（命令行第一个参数）
    desc        一句话说明（`--list` 直接打印）
    module      脚本相对**仓库根**的路径
    default_out 未指定 --out 时的默认输出位置（相对仓库根）
    out_kind    "file" = --out 是 png 文件路径 / "dir" = --out 是目录 / None = 该脚本没有 --out
    kind        "script" = 有可执行入口；"api" = 只有可调用的函数（没有独立 CLI）
    entry       kind="api" 时的可调用入口签名（给 --list 和直接执行时提示用）
"""

CHARTS = [
    dict(
        name="phase-transition",
        keywords=["相变监控", "相变图", "临界慢化", "序参量", "吸收比率图", "画相变"],
        desc="相变监控面板（五栏）：①价格 ②序参量 M + 关联长度 ξ ③AR(1) of M "
             "④M1 AR(1) 恢复力 + M2 Δσ（通道A）⑤吸收比率 λ1/N（通道B）。"
             "算法唯一来源 charts/phase_signals.py（2026-11-20 已并入本机 pws_compute 全部算子，"
             "含 compute()/snapshot()/M5 λmax/α 接口，口径逐列对齐）。"
             "⚠️ 2026-11-19 实测：相变族对 2026-07 大跌无预警能力，只能当结构状态描述，不可当交易信号",
        module="charts/phase_transition.py",
        default_out="reports/charts/phase_transition",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="phase-mfdfa",
        keywords=["相变MFDFA", "相变多分形", "MF-DFA相变", "相变图加MFDFA", "相变图加多分形",
                  "多分形谱宽相变", "6栏相变", "六栏相变", "相变监控MFDFA"],
        desc="相变监控面板（六栏）：前 5 栏与 phase-transition 完全同口径，"
             "第 6 栏追加 MF-DFA 滚动 Δα（多分形谱宽，N=250，scales=[20,30,50,80,125]，q=[-4..4]），"
             "突破 90 分位处画红色竖虚线。Δα 只是结构状态描述，不是交易信号",
        module="charts/phase_mfdfa.py",
        default_out="reports/charts/phase_mfdfa",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="chan-fractal",
        keywords=["缠论分型版", "分型版K线", "画缠论图", "缠论图",
                  "技术分析汇总", "五大宽基日线", "宽基堆叠", "MF-DFA双轴"],
        desc="缠论·分型版出图：单标的（包含→分型→笔→中枢→背驰，K线+MACD）"
             "；`--stack 沪深300,中证500,...` 出「五大宽基日线」堆叠图——副图右轴画 "
             "MF-DFA Δα（原 R_cascade 位已替换），`--alert-email` 可对 Δα 高位触发预警（带图，通道由调用方注册）",
        module="chanlun/fractal/chan_chart.py",
        default_out="reports/charts/chan-fractal",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="chan-ma",
        keywords=["缠论均线版", "均线版K线", "画均线版"],
        desc="缠论·均线版单标的出图（MA5/10 双穿 MA20 → 笔/线段/走势类型）",
        module="chanlun/ma/chan_ma_chart.py",
        default_out="reports/charts/chan-ma",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="buypoint",
        keywords=["买点图", "买点三联图", "成分股网格"],
        desc="买点个股三联图（日线 + 小时线 + 缠论均线版）。"
             "⚠️ 没有独立 CLI/main，只能当库调用："
             "draw_stock_grid(picks, out_path, n_show=120, ...) / "
             "draw_buypoint_chart(disp, day, h1=None, out=None, ...)，"
             "日常由 misc/engine/tech_buypoint.py 调起",
        module="misc/engine/buypoint_chart.py",
        default_out="reports/charts/buypoint",
        out_kind=None,
        kind="api",
        entry="draw_stock_grid(picks, out_path, ...) / draw_buypoint_chart(disp, day, h1=None, out=None, ...)",
    ),
    dict(
        name="simple",
        keywords=["单图速览", "一张图看结构"],
        desc="单图速览（缠论分型版笔/中枢 + VWAP 六轨，K线一屏）。"
             "⚠️ 无 CLI 参数：__main__ 是写死的样板（极米科技）；"
             "要换标的请 import：draw_stock(code, name, d, n_show=120, out=None)",
        module="misc/engine/simple_chart.py",
        default_out="reports/charts/simple",   # 该脚本自己写死 .cache/charts，不改它的行为
        out_kind=None,
        kind="script",
        entry="draw_stock(code, name, d, n_show=120, out=None)",
    ),
    dict(
        name="topology-spd",
        keywords=["流形图", "画流形", "SPD流形", "相位图"],
        desc="拓扑·SPD 流形四合一图 + 多标的二维相位图（H1 环寿命 × 平均相关性）",
        module="topology/run.py",
        default_out="reports/charts/topology-spd",
        out_kind="dir",
        kind="script",
        entry=None,
    ),
    dict(
        name="topology-terrain",
        keywords=["地形图", "黎曼地形图", "黎曼流形地形图"],
        desc="拓扑·黎曼流形地形图（左：地形 + 持续同调条码 / 右：相关矩阵曲面）",
        module="topology/terrain.py",
        default_out="reports/charts/topology-terrain",
        out_kind="dir",
        kind="script",
        entry=None,
    ),
    dict(
        name="five-index",
        keywords=["五大宽基", "五大指数", "五个指数", "宽基总览", "五指数对比"],
        desc="五大宽基（沪深300/中证500/中证1000/创业板指/科创50）缠论买卖点 + 拓扑结构 合并总览图"
             "（每行一个指数：左=缠论分型版，右=成分股 UMAP 结构 + 结构分 + 趋势层）",
        module="charts/five_index.py",
        default_out="reports/charts/five_index",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="five-topo",
        keywords=["拓扑结构图", "拓扑结构", "拓扑图", "画拓扑", "拓扑结构总览",
                  "五大宽基拓扑", "五个指数拓扑", "宽基拓扑结构", "五大宽基里奇曲率"],
        desc="拓扑结构总览图（独立程序，--targets 传任意 指数/行业/概念/板块，一个标的一行，多标的凑一张）。"
             "每行：左=UMAP 结构位置，右=里奇曲率联动网络（含里奇流小图），结论条含多空 + 撕裂/聚集五态判定。"
             "**不掺缠论**；默认五大宽基，晚任务只需 "
             "`--targets 沪深300,中证500,中证1000,创业板指,科创50`",
        module="charts/five_topo.py",
        default_out="reports/charts/five_topo",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="rmt-rc",
        keywords=["RMT矩阵图", "RMT 矩阵图", "RMT图", "RMT监控图", "RMT+RC", "RMT",
                  "outlier_energy", "特征值吸收率", "双Y轴RMT", "λ1/λ2",
                  "MF-DFA告警", "Δα告警", "多分形谱宽"],
        desc="RMT + MFDFA Δα 联合监控图（60 日滚动窗口）。单图/多图都行："
             "--targets 科创50 出一张；--targets 沪深300,科创50 出并排两张；"
             "左内轴 outlier_energy、右外轴 λ1/λ2、右轴 AR5/λ1Σλ/Δα（多分形谱宽）；"
             "页脚多行图例（大字号）+ 历史分位一行；--alert 分位>90% 可触发通知（Δα 亦入列）",
        module="charts/rmt_rc.py",
        default_out="reports/charts/rmt",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="topology-manifold",
        keywords=["流形拓扑图", "拓扑流形图", "流形结构图", "流形拓扑", "里奇曲率", "UMAP结构"],
        desc="流形拓扑图（左：UMAP 结构位置 / 右：里奇曲率网络）。"
             "标题分两块：结构研判（不含方向）+ 趋势层（含方向）",
        module="topology/manifold_topo.py",
        default_out="reports/charts/topology-manifold",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="rcascade",
        keywords=["R_cascade", "rcascade", "级联比", "波动率级联比", "波动率级联",
                  "波动率吉连比", "吉连比", "R cascade"],
        desc="【已并入 MF-DFA Δα，R_cascade 仅作取数库】原 R_cascade 波动率级联比"
             "（平滑版 std20/std60）：科创50ETF / 创业板指 / 中证1000 三面板（K线+MA20 / R曲线）。"
             "现在日线副图右轴改画 MF-DFA Δα（见 chan-fractal --stack、rmt-rc），"
             "预警走 `charts/rmt_rc.py --alert --email` 或 `chan_fractal.py --stack ... --alert-email`"
             "（通道由调用方注册，或设 TECHANLY_NOTIFIERS）",
        module="misc/engine/rcascade.py",
        default_out="reports/charts/rcascade",
        out_kind="file",
        kind="script",
        entry=None,
    ),
    dict(
        name="margin",
        keywords=["两融", "两融图", "A股两融", "融资融券", "融资余额", "融券余额",
                  "两融余额", "两融账户", "两融余额图", "画两融", "两融全景",
                  "维持担保比例", "担保比例", "融资买入额",
                  "个人投资者", "投资者数量", "有融资融券负债", "两融负债"],
        desc="A股两融全景图（6 面板，共享时间轴）：①融资余额+MA5/MA20+MACD(10,20,5) "
             "②融券余额 ③融资买入/偿还+单日净买入 "
             "④两融账户存量（个人投资者万户 + 有融资融券负债万人，同轴可比）"
             "⑤参与交易（万人）+机构投资者（万家）⑥平均维持担保比例+担保物总价值。"
             "数据只读 DataHub EDB cn_market（CN.EQ.MARGIN.*，保留 500 自然日≈340 交易日）。"
             "--days 250 默认最近 250 交易日；--json 只回数值；--refresh 先补库",
        module="charts/margin.py",
        default_out="reports/charts/margin",
        out_kind="file",
        kind="script",
        entry=None,
    ),
]


def resolve(text):
    """按自然语言关键词找图名（最长关键词优先）。找不到返回 None。

    例：「画拓扑结构图」→ five-topo ；「画流形拓扑图」→ topology-manifold ；「画流形图」→ topology-spd
    """
    t = str(text or "").strip()
    if not t:
        return None
    best, best_len = None, -1
    for c in CHARTS:
        if t == c["name"]:
            return c["name"]
        for kw in c.get("keywords") or []:
            if kw in t and len(kw) > best_len:
                best, best_len = c["name"], len(kw)
    return best


def names():
    return [c["name"] for c in CHARTS]


def get(name):
    for c in CHARTS:
        if c["name"] == name:
            return c
    return None
