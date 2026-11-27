#!/usr/bin/env bash
# 兼容入口（2026-11-12 目录整理）：真实脚本已移至 report/intraday_buypoint_daily.sh
# 保留旧路径，cron / 外部 `bash "$SELF_DIR"/intraday_buypoint_daily.sh` 照旧可用。
set -u

# 脚本所在目录（2026-11-27 迁入 techAnly-summary，替代原 bin/ 路径）
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${TECH_WS:-$(cd "$(dirname "$0")/.." && pwd)}"
REAL="$WS/misc/engine/intraday_buypoint_daily.sh"
[ -f "$REAL" ] || { echo "[compat] 找不到真实脚本: $REAL" >&2; exit 127; }
exec bash "$REAL" "$@"
