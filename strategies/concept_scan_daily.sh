#!/usr/bin/env bash
# 概念拓扑缠论选股 · 每日早间私聊推送
# ============================================================================
# 2026-10-28 16:46 大佬定稿：
#   「如果选出结果大于 10，那你就选 Top 10 概念。就是，按照 20 日的动量排选 Top 10，
#     然后这个概念，根据这个概念然后选出，每个概念选出涨幅最好的两个股票，然后再过滤，
#     就用这个先用动量，再用结构拓扑，最后用这个缠论过滤。也就是说最多选出 20 个股票来。
#     然后你建立这个任务，就是叫做概念拓扑缠论选股。我想的是你每天这个任务，
#     每天早上发到私聊。」
#
# 流水线（四层漏斗）：
#   ① 动量：概念指数 20 日超额动量 > 0（跑赢沪深300） 且 当日 > 0
#   ② 拓扑：κR > 0 且 κI > 0（资金聚集、结构没撕裂）
#   ③ 缠论（概念层）：三买区 / 二买区·底背驰 / 突破中枢
#      → 结果 > 10 个则按 20 日动量取 Top 10
#   ④ 个股：每个概念的成分股按 20 日涨幅取前 2 只 → 缠论过滤 → 去重
#      → 最多 20 只（10 概念 × 2 只）
#
# 纯脚本 0 token，不发 PDF/图，飞书卡片 + QQ 私聊双通道。
# 日志：.cache/concept_scan_daily.log
# ============================================================================
set -uo pipefail

# 脚本所在目录（2026-11-27 迁入 techAnly-summary，替代原 bin/ 路径）
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}" || exit 1

export TZ=Asia/Shanghai
LANG=C.UTF-8
export PYTHONUNBUFFERED=1
export CS_MOM_MODE="${CS_MOM_MODE:-rel}"

LOG=".cache/concept_scan_daily.log"
mkdir -p .cache memory/concept_reports

echo "" >> "$LOG"
echo "════════════════════════════════════════════════════════════════" >> "$LOG"
echo "[$(date '+%F %T %Z')] 概念拓扑缠论选股 · 开始（工作日 07:30）" >> "$LOG"

# 超时 66 分钟（2026-11-12 实测：概念扩到 353 个 → 拓扑 180 节点 617s、个股拓扑 269 节点更久，
#   整条链路 2625s 已超原 2200s 上限 → rc=124 失败。内层 4000s + 外层 cron 4200s 双保险）
timeout 4000 python3 "$SELF_DIR"/concept_scan.py \
    --layer 4 \
    --per-ind 50 \
    --max-ind 10 \
    --max-out 20 \
    >> "$LOG" 2>&1
rc=$?

if [ $rc -eq 0 ]; then
    echo "[$(date '+%F %T')] 完成 rc=0" >> "$LOG"
else
    echo "[$(date '+%F %T')] FAILED rc=$rc" >> "$LOG"
    # 失败告警：直接发一条纯文本到 QQ，避免静默失败
    python3 - <<'PY' 2>/dev/null || true
import subprocess, os
msg = "⚠️ 概念拓扑缠论选股 执行失败，请查看 .cache/concept_scan_daily.log"
subprocess.run(["openclaw","message","send","--channel","qqbot",
                "--target", os.environ.get("CS_QQ_TARGET",""),
                "--message", msg], capture_output=True, timeout=120)
PY
fi

# 只保留最近 2000 行日志
tail -2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"

exit $rc
