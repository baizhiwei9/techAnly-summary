#!/usr/bin/env bash
# 盘中买点分析（行业 → 个股）· 每日包装脚本（纯脚本 0 token）
#   ① 实时数据硬校验：不是今天的实时行情 → 报错退出，不推送
#   ② 行业买点排序（缠论分型版 > 均线版 > SMC > VWAP），选前 N 个（默认 3）
#   ③ 从选出的行业下钻个股：**必须站在 VWAP −1σ 以上**，再按买点优先级取前 M 只（默认 3）
#   ④ 推送：飞书私聊卡片 + QQ 私聊文字
# 用法：bash misc/engine/intraday_buypoint_daily.sh [--no-push]
set -u
# 2026-11-28：workspace 的 bin/ 已拆除；本仓库脚本在 strategies/，运维脚本在 optstack/jobs
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WS="${TECH_WS:-${OPENCLAW_WORKSPACE:-/root/.openclaw/workspace}}"
cd "$WS" || exit 1
export PYTHONPATH="$REPO/strategies:$REPO:${PYTHONPATH:-}"
LOG="$WS/.cache/intraday_buypoint.log"
ts(){ date '+%F %T'; }
TOP="${IBP_TOP:-3}"
PER="${IBP_STOCK_PER:-3}"
echo "[$(ts)] ===== 盘中买点分析（行业→个股）start top=$TOP per=$PER =====" >> $LOG

# ⓿ 实时数据硬校验（大佬 2026-10-30 要求：不是实时就报错不执行）
echo "[$(ts)] ⓿ 实时数据校验" >> $LOG
LIVE=$(timeout 120 python3 "$REPO"/strategies/live_check.py 2>>$LOG)
lrc=$?
echo "[$(ts)] live: $LIVE" >> $LOG
if [ $lrc -ne 0 ]; then
  echo "[$(ts)] ❌ 数据不是实时 → 中止，不推送" >> $LOG
  exit 9
fi

if [ "${1:-}" = "--no-push" ]; then
  echo "[$(ts)] ① 跑分析（不推送）" >> $LOG
  timeout 1800 python3 "$REPO"/strategies/tech_intraday_buypoint.py --top "$TOP" --stock-per "$PER" --dry >> $LOG 2>&1
  echo "[$(ts)] 完成 rc=$?" >> $LOG
  exit 0
fi

echo "[$(ts)] ① 分析 + 推送（飞书卡片 + QQ 文字）" >> $LOG
timeout 1800 python3 "$REPO"/strategies/tech_intraday_buypoint.py \
  --top "$TOP" --stock-per "$PER" --push --qq \
  --json .cache/intraday_buypoint/latest.json >> $LOG 2>&1
rc=$?
echo "[$(ts)] 完成 rc=$rc" >> $LOG
exit $rc
