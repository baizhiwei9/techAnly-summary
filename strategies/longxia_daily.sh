#!/usr/bin/env bash
# 🦞 龙虾缠论 · 龙虎榜选股 · 每日早盘前定时任务（纯脚本，0 token）
# 每天只发一次：飞书私聊 1 条卡片 + QQ 私聊 1 条文字，发过就不再发。
#
# crontab: 40 7 * * 1-5  →  每交易日早盘前 07:40
set -uo pipefail

# 脚本所在目录（2026-11-27 迁入 techAnly-summary，替代原 bin/ 路径）
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}" || exit 1

LOG=.cache/longxia_cron.log
mkdir -p .cache
TODAY=$(date +%Y%m%d)

# 0) 防重：进程锁防并发；「该交易日已发送」的判断在 longxia_pick.py 内部（按交易日记账）
exec 9>.cache/longxia_run.lock
if ! flock -n 9; then
  echo "[skip] $(date '+%F %T') 已有实例在跑，退出" >> "$LOG"
  exit 0
fi

echo "" >> "$LOG"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 开始 =====" >> "$LOG"

# 1) 交易日判断（今天不开市就不发）
IS_OPEN=$(python3 - "$TODAY" <<'PY'
import sys
sys.path.insert(0, 'bin')
from tushare_init import get_pro
d = sys.argv[1]
try:
    df = get_pro().trade_cal(exchange='SSE', start_date=d, end_date=d, is_open='1')
    print('1' if (df is not None and len(df) > 0) else '0')
except Exception:
    print('0')   # 接口异常时宁可不发，避免推旧数据
PY
)
if [ "$IS_OPEN" != "1" ]; then
  echo "[skip] $TODAY 非交易日，退出" >> "$LOG"
  exit 0
fi

# 2) 数据面板（增量，正常 2~5 秒）
python3 "$SELF_DIR"/longxia_panel.py --days 260 >> "$LOG" 2>&1
echo "[panel] rc=$?" >> "$LOG"

# 2.5) 股东户数否决名单（大佬 2026-11-04：没披露的直接不选）
#      产出 contracts/筹码.json + contracts/户数否决名单.json
#      失败不阻塞选股（脚本内部有开关 LX_CHIPS_VETO=1，读不到名单就跳过）
timeout 600 python3 "$SELF_DIR"/fetch_chips.py --ttl 24 >> "$LOG" 2>&1
echo "[chips] rc=$?" >> "$LOG"

# 3) 选股 + 推送（脚本内部还有一层 sent 去重）
#    LX_FORMAL=1：告诉脚本「本次是正式定时任务」，才允许写 sent 记账；
#    手动跑（测试/补发）不写记账，避免正式任务误判「今日已发送」而漏发。
LX_FORMAL=1 timeout 900 python3 "$SELF_DIR"/longxia_pick.py --qq >> "$LOG" 2>&1
echo "[pick] rc=$?" >> "$LOG"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 结束 =====" >> "$LOG"
exit 0
