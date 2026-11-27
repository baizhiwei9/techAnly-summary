#!/usr/bin/env bash
# 盘中吸筹买点分析 v2 · 每日包装脚本（纯脚本 0 token）
#   ① 数据层：今日快照 + 历史日K（威科夫原始口径，同 wyckoff_today_data.py）
#   ② 分析：威科夫吸筹/派发 → 吸筹项过技术分析汇总四层
#   ③ 行业 Top10（超过 10 取前 10，不足用全部）
#   ④ 个股：每行业 ≤2 只、全市场 Top20，硬门槛 = 站上 VWAP −1σ
#   ⑤ 出图：每只股票一个子图（缠论分型版 + VWAP 六轨）
#   ⑥ 推送：飞书卡片（含图）+ QQ 文字
# 用法：bash bin/wyckoff_buypoint_daily.sh [--no-push]
set -u
WS="${TECH_WS:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$WS" || exit 1
LOG="$WS/.cache/wyckoff_buypoint.log"
ts(){ date '+%F %T'; }
echo "[$(ts)] ===== 盘中吸筹买点分析 v2 start =====" >> "$LOG"

# ⓿ 实时数据硬校验（不是今天的实时行情 → 中止，不推送）
echo "[$(ts)] ⓿ 实时数据校验" >> "$LOG"
LIVE=$(timeout 120 python3 bin/live_check.py 2>>"$LOG")
lrc=$?
echo "[$(ts)] live: $LIVE" >> "$LOG"
if [ $lrc -ne 0 ]; then
  echo "[$(ts)] ❌ 数据不是实时 → 中止，不推送" >> "$LOG"
  exit 9
fi

echo "[$(ts)] ① 抓数据（今日快照 + 历史日K）" >> "$LOG"
timeout 1800 python3 bin/wyckoff_today_data.py --kinds 行业,概念 --workers 8 >> "$LOG" 2>&1
rc1=$?

echo "[$(ts)] ② 威科夫 + 缠论分析（rc=$rc1）" >> "$LOG"
timeout 1800 python3 bin/wyckoff_today_analyze.py >> "$LOG" 2>&1
rc2=$?

if [ "${1:-}" = "--no-push" ]; then
  echo "[$(ts)] ③ 四层过滤 + 个股 + 出图（不推送）" >> "$LOG"
  timeout 1800 python3 bin/wyckoff_buypoint_v2.py --chart --dry >> "$LOG" 2>&1
  echo "[$(ts)] 完成 rc=$? data=$rc1 analyze=$rc2" >> "$LOG"
  exit 0
fi

echo "[$(ts)] ③ 四层过滤 + 个股 + 出图 + 推送（飞书含图 / QQ 文字）" >> "$LOG"
timeout 2400 python3 bin/wyckoff_buypoint_v2.py \
  --chart --push --qq --json "$WS/.cache/wyckoff_buypoint/latest.json" >> "$LOG" 2>&1
rc3=$?
echo "[$(ts)] 完成 rc: data=$rc1 analyze=$rc2 report=$rc3" >> "$LOG"
exit $rc3
