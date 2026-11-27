#!/usr/bin/env bash
# 任务① 行业资金诊断（吸筹/派发）—— 纯脚本 0 token
set -uo pipefail

# 脚本所在目录（2026-11-27 迁入 techAnly-summary，替代原 bin/ 路径）
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /root/.openclaw/workspace || exit 1
PUSH="${1:-both}"
D8=$(python3 -c "import sys;sys.path.insert(0,'bin');import trade_day;print(trade_day.resolve().replace('-',''))" 2>/dev/null)
[ -z "$D8" ] && D8=$(date +%Y%m%d)
LOG=.cache/fund_flow_cron.log
mkdir -p .cache
echo "=== $(date '+%F %T') start $D8 push=$PUSH ===" >> "$LOG"
timeout 2000 python3 "$SELF_DIR"/fund_flow_daily.py --push "$PUSH" --out ".cache/fund_flow_${D8}.txt" >> "$LOG" 2>&1
RC=$?
echo "=== $(date '+%F %T') done rc=$RC ===" >> "$LOG"
if [ $RC -ne 0 ]; then
  timeout 120 python3 - <<'PY' >> "$LOG" 2>&1
import json,urllib.request
hook=__import__("ocsecrets").feishu_hook()
t='⚠️ 行业资金诊断（吸筹/派发）执行失败，见 .cache/fund_flow_cron.log\n\n📎 来自 OpenClaw.Deepseek'
b={'msg_type':'text','content':{'text':t}}
try:
    urllib.request.urlopen(urllib.request.Request(hook,data=json.dumps(b).encode(),headers={'Content-Type':'application/json'}),timeout=20)
except Exception as e:
    print('alert fail',e)
PY
fi
exit $RC
