# -*- coding: utf-8 -*-
"""pick_pool —— 各算法「自选股 / 选股结果」的统一落盘契约（宿主机副本）


# --- 路径引导（2026-11-27 迁入 techAnly-summary）---
try:
    from _paths import ensure as _ensure_paths
    _ensure_paths()
except Exception:
    pass

⚠️ 本文件是 stock-dashboard `case/_shared/pick_pool.py` 的**逐字副本**，
   只为宿主机 bin/ 下的脚本能直接 import。改契约请改框架仓库那份，改完同步过来，
   两边不一致时以 stock-dashboard 为准。

pick_pool —— 各算法「自选股 / 选股结果」的统一落盘契约

为什么需要它
============
7 个选股算法（general_growth / mad_weekly / five_system_concept / sector_rotation
/ tda_spd / twosigma_rotation / xi_d_rrg / stock_pool …）各自写各的 json：
字段名不同、结构不同、有的只有 markdown 或图。显示层要么逐个适配（永远追不上），
要么只能拿「有没有指标」凑合展示，结果就是**自选股那块一堆空壳**。

这里定一份最小契约：每个算法只要在 `out/picks.json` 落一个数组，
显示层就统一按「代码 · 名称 · 行业 · 理由 · 分数 · 标签」渲染；
算法之间怎么算的互不影响，也不用改自己的主流程。

字段（全部可缺省，只 `code` 或 `name` 二选一必填）
------------------------------------------------
  code       "600519" / "600519.SH"   ← 会归一成 6 位
  name       "贵州茅台"                 ← code 缺时用它兜底
  industry   "白酒"                     ← 申万三级优先，没有就填二级/一级
  sector_l1/l2/l3  申万三级各层（有就填，显示层做下钻）
  reason     一句话：为什么选它（策略自己的话，别写空话）
  score      数值分（越大越好；同算法内可比即可）
  rank       名次（不给就按数组顺序）
  tags       ["三买", "VWAP上", "共振3层"] 等标签
  direction  "long" / "short" / "watch"（默认 long）
  price      参考价；target / stop 可选

顶层结构（写成 dict，不要写裸数组，方便以后加元信息）
----------------------------------------------------
  {
    "algorithm": "mad_weekly",      # 必须是目录 key
    "title":     "MAD 均线选股",
    "as_of":     "2026-11-26",      # 数据日期，不是运行时间
    "generated_at": "2026-11-26 07:50:12",
    "cadence":   "weekly",          # daily / weekly / monthly
    "note":      "本周只在白酒与饲料里出票，行业偏窄",   # 可选，人话备注
    "picks":     [ {...}, {...} ]
  }

用法（算法侧，两行）
--------------------
  from pick_pool import write_picks, pick_row
  write_picks("mad_weekly", "MAD 均线选股", "2026-11-26", [
      pick_row("600519", "贵州茅台", industry="白酒", reason="MA20 上穿 MA60",
               score=8.2, tags=["均线多头"]),
  ], cadence="weekly")

用法（显示层）
--------------
  from pick_pool import load_picks
  data = load_picks(task_root)        # -> dict 或 None（None=该算法今天没出票）

设计原则
--------
· **fail-safe**：写盘失败只打印警告，绝不打断算法的推送主流程。
· **不编数据**：没算出来的字段就不写，显示层留空，不许补 0 / 猜行业。
· **一份文件**：算法只需要维护 `out/picks.json`，显示层不再扫各种杂 json。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

PICKS_FILE = "picks.json"
SCHEMA_VERSION = 1

# 只做展示口径的归一，不改变算法自己的数值
_DIRECTIONS = {"long", "short", "watch", "中性", "看多", "看空"}


def _norm_code(code) -> str:
    """'600519.SH' / 'sh600519' / 600519 → '600519'（6 位裸码）。"""
    if code is None:
        return ""
    s = str(code).strip().upper()
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s


def _norm_date(v) -> str:
    """日期归一成 YYYY-MM-DD。20261125 / 2026-11-25 / 2026/11/25 都收，
    识别不了就原样返回（显示层照原样写，不猜）。"""
    if not v:
        return ""
    s = str(v).strip().replace("/", "-")
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})$", s)
    return "%s-%s-%s" % (m.group(1), m.group(2), m.group(3)) if m else s


def _as_float(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # 过滤 NaN


def pick_row(code="", name="", *, industry="", sector_l1="", sector_l2="",
             sector_l3="", reason="", score=None, rank=None, tags=None,
             direction="long", price=None, target=None, stop=None, **extra):
    """构造一条标准 pick。多余字段原样带走，显示层不用但不丢。"""
    row = {
        "code": _norm_code(code),
        "name": (name or "").strip(),
        "industry": (industry or sector_l3 or sector_l2 or sector_l1 or "").strip(),
        "sector_l1": (sector_l1 or "").strip(),
        "sector_l2": (sector_l2 or "").strip(),
        "sector_l3": (sector_l3 or "").strip(),
        "reason": (reason or "").strip(),
        "direction": direction if direction in _DIRECTIONS else "long",
        "tags": [str(t) for t in (tags or []) if str(t).strip()],
    }
    for k, v in (("score", _as_float(score)), ("rank", _as_float(rank)),
                 ("price", _as_float(price)), ("target", _as_float(target)),
                 ("stop", _as_float(stop))):
        if v is not None:
            row[k] = v
    row.update(extra)
    # code 与 name 至少有一个，否则这条没意义
    if not row["code"] and not row["name"]:
        return None
    return row


def normalize(payload, algorithm="", title="", as_of="", cadence="", note="",
              allow_empty=False):
    """把各种历史结构尽量归一成契约格式（兼容老算法不用改就能上页面）。

    allow_empty=True：允许 picks 为空数组，但仍然产出结构（带 note）。
    选股算法「今天没出票」是有意义的结果，不能和「没接契约」混为一谈 ——
    显示层要能区分这两种情况。
    """
    if payload is None:
        return None

    picks, meta = None, {}
    if isinstance(payload, list):
        picks = payload
    elif isinstance(payload, dict):
        for k in ("picks", "stocks", "rows", "items", "codes", "list"):
            if isinstance(payload.get(k), list):
                picks = payload[k]
                break
        meta = {k: payload.get(k) for k in ("algorithm", "title", "as_of",
                                            "generated_at", "cadence", "note")
                if payload.get(k)}

    if picks is None:
        return None

    rows = []
    for i, it in enumerate(picks):
        if isinstance(it, str):                     # ["600519", ...]
            rows.append(pick_row(it, rank=i + 1))
        elif isinstance(it, dict):
            r = pick_row(it.get("code") or it.get("ts_code") or it.get("symbol"),
                         it.get("name") or it.get("cn_name") or it.get("stock_name"),
                         industry=it.get("industry") or it.get("sw_l3") or it.get("ind"),
                         sector_l1=it.get("sector_l1") or it.get("sw_l1") or "",
                         sector_l2=it.get("sector_l2") or it.get("sw_l2") or "",
                         sector_l3=it.get("sector_l3") or it.get("sw_l3") or "",
                         reason=it.get("reason") or it.get("why") or it.get("note"),
                         score=it.get("score") or it.get("value"),
                         rank=it.get("rank") or (i + 1),
                         tags=it.get("tags") or it.get("labels"),
                         direction=it.get("direction") or "long",
                         price=it.get("price") or it.get("close"))
            if r:
                rows.append(r)
    rows = [r for r in rows if r]
    if not rows and not allow_empty:
        return None

    out = {
        "schema": SCHEMA_VERSION,
        "algorithm": meta.get("algorithm") or algorithm,
        "title": meta.get("title") or title or algorithm,
        "as_of": _norm_date(meta.get("as_of") or as_of),
        "generated_at": meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cadence": meta.get("cadence") or cadence or "",
        "note": meta.get("note") or note or "",
        "picks": rows,
    }
    return out


def write_picks(algorithm, title, as_of, picks, *, cadence="", note="",
                out_dir=None, extra=None, allow_empty=False):
    """写 `out/picks.json`。失败只警告，绝不抛错打断算法主流程。

    out_dir 缺省：环境变量 CASE_OUT_DIR > 脚本同目录下的 out/。
    allow_empty=True：picks 可以为空（今天没出票），文件照样写，
    并把 note 带出去，显示层据此区分「没出票」与「没接契约」。
    """
    d = out_dir or os.environ.get("CASE_OUT_DIR") or \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    payload = normalize(
        {"picks": picks, "algorithm": algorithm, "title": title,
         "as_of": as_of, "cadence": cadence, "note": note},
        algorithm=algorithm, title=title, as_of=as_of, cadence=cadence,
        note=note, allow_empty=allow_empty)
    if payload is None:
        print("[pick_pool] 没有有效 pick，不写 %s" % PICKS_FILE)
        return None
    if extra:
        payload.update({k: v for k, v in extra.items() if k not in payload})
    try:
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, PICKS_FILE)
        tmp = fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, fp)
        print("[pick_pool] wrote %s（%d 只）" % (fp, len(payload["picks"])))
        return fp
    except Exception as e:                    # noqa: BLE001  fail-safe
        print("[pick_pool] 写 %s 失败：%s" % (PICKS_FILE, str(e)[:160]))
        return None


def load_picks(task_root):
    """显示层读 `out/picks.json`。没有/坏了 → None（表示该算法今天没出票）。"""
    if not task_root:
        return None
    fp = os.path.join(task_root, PICKS_FILE)
    if not os.path.isfile(fp):
        fp = os.path.join(task_root, "out", PICKS_FILE)
    if not os.path.isfile(fp):
        return None
    try:
        with open(fp, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    return normalize(payload)


def fallback_dir():
    """宿主机统一落盘目录：<WS>/.cache/picks。

    有些算法还没迁进 case（脚本还在 bin/ 下跑），没有 out/ 可写。
    这些脚本把 picks.json 写到这个目录（文件名 = 算法 key），
    显示层同样能认，迁 case 之前也能先上页面。
    """
    env = os.environ.get("PICK_POOL_DIR", "").strip()
    if env:
        return env
    ws = os.environ.get("STOCK_POOL_WS", "").strip() or \
        os.path.expanduser("~/.openclaw/workspace")
    return os.path.join(ws, ".cache", "picks")


def write_picks_fallback(algorithm, title, as_of, picks, *, cadence="", note="",
                         allow_empty=False, extra=None):
    """写 <WS>/.cache/picks/<algorithm>.json（宿主机脚本用，不依赖 case 目录）。

    多个算法共用同一个目录，所以文件名必须带算法名，不能都叫 picks.json。
    """
    d = fallback_dir()
    payload = normalize(
        {"picks": picks, "algorithm": algorithm, "title": title,
         "as_of": as_of, "cadence": cadence, "note": note},
        algorithm=algorithm, title=title, as_of=as_of, cadence=cadence,
        note=note, allow_empty=allow_empty)
    if payload is None:
        print("[pick_pool] 没有有效 pick，不写 %s.json" % algorithm)
        return None
    if extra:
        payload.update({k: v for k, v in extra.items() if k not in payload})
    try:
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, "%s.json" % algorithm)
        tmp = fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, fp)
        print("[pick_pool] wrote %s（%d 只）" % (fp, len(payload["picks"])))
        return fp
    except Exception as e:                    # noqa: BLE001  fail-safe
        print("[pick_pool] 写 %s.json 失败：%s" % (algorithm, str(e)[:160]))
        return None


def load_picks_fallback(algorithm):
    """显示层兜底：读 <WS>/.cache/picks/<algorithm>.json。"""
    if not algorithm:
        return None
    fp = os.path.join(fallback_dir(), "%s.json" % algorithm)
    if not os.path.isfile(fp):
        return None
    try:
        with open(fp, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    return normalize(payload)
