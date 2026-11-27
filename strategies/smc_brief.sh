#!/bin/bash
# SMC 开盘持仓播报（日盘/夜盘）—— 纯脚本，0 token
#   用法: bash "$SELF_DIR"/smc_brief.sh day     # 日盘开盘（09:00）
#         bash "$SELF_DIR"/smc_brief.sh night   # 夜盘开盘（21:00）
# 非交易日自动跳过（用户 2026-10-09 要求：所有日报只在交易日发）
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# 脚本所在目录（2026-11-27 迁入 techAnly-summary，替代原 bin/ 路径）
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}" || exit 1
tag="${1:-day}"
trade=$(python3 -c "import sys; sys.path.insert(0,'bin'); import trade_day; print('1' if trade_day.is_open()[0] else '0')" 2>/dev/null)
if [ "$trade" != "1" ]; then
  echo "[$(date '+%F %T')] 非交易日，跳过 SMC ${tag} 开盘播报"
  exit 0
fi
echo "[$(date '+%F %T')] SMC ${tag} 开盘播报"
timeout 600 python3 smc/smc_notify.py --brief "$tag"
