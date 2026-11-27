#!/bin/bash
# 科创50 三态信号 · 每日（工作日 16:35）
# 1) 用干净口径（--conf 0）重训 5 种子 → 2) 判定机会/做空/平仓 → 3) 有信号才推飞书
set -u

# 脚本所在目录（2026-11-27 迁入 techAnly-summary，替代原 bin/ 路径）
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
PKG=".cache/quark_dl/科创50日频预测包_20261018"
LOG=".cache/kc50_tristate_cron.log"
TS=$(date '+%F %T')
echo "===== $TS 开始 =====" >> "$LOG"

# 重训（5 种子并行；--conf 0 关闭低置信冻结，避免 0 值污染分位窗口）
cd "$PKG" || { echo "包目录不存在" >> "$LOG"; exit 1; }
for S in 42 7 123 2026 999; do
  TAG="nf_t4dimraw_spread_000688_SH"
  [ "$S" != "42" ] && TAG="${TAG}_s${S}"
  timeout 1500 python3 train_variants.py --code 000688_SH --method raw --dim 0 \
    --seed "$S" --tag "$TAG" --variant raw \
    --panel daily_features_options_spread_000688_SH.csv --conf 0 \
    > "/tmp/kc50_train_${S}.log" 2>&1 &
done
wait
echo "重训完成" >> "$LOG"

cd "${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}" || exit 1
timeout 300 python3 "$SELF_DIR"/kc50_tristate.py --push >> "$LOG" 2>&1
RC=$?
echo "$(date '+%F %T') 结束 rc=$RC" >> "$LOG"
exit 0
