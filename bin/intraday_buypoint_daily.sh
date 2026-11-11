#!/usr/bin/env bash
# 兼容入口（2026-11-12 目录整理）：真实脚本已移至 report/intraday_buypoint_daily.sh
# 保留旧路径，cron / 外部 `bash bin/intraday_buypoint_daily.sh` 照旧可用。
set -u
WS="${TECH_WS:-$(cd "$(dirname "$0")/.." && pwd)}"
REAL="$WS/report/intraday_buypoint_daily.sh"
[ -f "$REAL" ] || { echo "[compat] 找不到真实脚本: $REAL" >&2; exit 127; }
exec bash "$REAL" "$@"
