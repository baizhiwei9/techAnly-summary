#!/bin/bash
# 小波多尺度策略信号监控 - crontab wrapper
# 部署时把飞书群 ID 与 lark-cli 配置目录放到环境变量里，不要把真实 ID 写进仓库。
#
#   export WAVELET_FEISHU_CHAT_ID=oc_xxxxxxxx
#   export LARKSUITE_CLI_CONFIG_DIR=/path/to/.feishu_cli
#
# crontab 示例（工作日 10:15 / 11:35 / 14:15 / 15:10 / 22:15）：
#   15 10 * * 1-5 /path/to/strategies/wavelet/run_monitor.sh >> /var/log/wavelet_signal_monitor.log 2>&1

export LARKSUITE_CLI_CONFIG_DIR="${LARKSUITE_CLI_CONFIG_DIR:-$HOME/.feishu_cli}"
export LARKSUITE_CLI_DATA_DIR="${LARKSUITE_CLI_DATA_DIR:-$LARKSUITE_CLI_CONFIG_DIR}"
export LARKSUITE_CLI_LOG_DIR="${LARKSUITE_CLI_LOG_DIR:-/tmp}"
export PYTHONUNBUFFERED=1

cd "$(dirname "$0")" || exit 1

# 交易日检查是部署机可选模块；没有就跳过
python3 -c "from trading_day_check import check_trading_day; check_trading_day()" 2>/dev/null || true

exec python3 signal_monitor.py
