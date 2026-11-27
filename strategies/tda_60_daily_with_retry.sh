#!/usr/bin/env bash
# TDA 60因子日报 — 带监控/失败重试的每日入口
# 用法：bin/tda_60_daily_with_retry.sh
# 流程：build_momentum(更新面板) → tda_60_daily_v2(生成+发飞书) → 校验发送成功 → 失败重试(<=3次)
# 关键：进程未正常完成 或 飞书未发送成功 → 自动重试，直到成功或耗尽重试次数
set -u

# 脚本所在目录（2026-11-27 迁入 techAnly-summary，替代原 bin/ 路径）
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
LOG=.cache/tda_60_daily_cron.log
: > "$LOG"   # 每次清空，便于校验

MAX_RETRY=3
RETRY_WAIT=120   # 重试间隔(秒)

ok_flag=0

# 0) 非交易日（周末/节假日）→ 直接跳过，不发旧报告也不告警
if ! /usr/bin/python3 "$SELF_DIR"/trade_day.py --fmt d8 >/dev/null 2>&1; then
  echo "[监控] trade_day 解析失败，按交易日继续" >> "$LOG"
fi
IS_OPEN=$(/usr/bin/python3 -c "import sys; sys.path.insert(0,'bin'); import trade_day; _,ok,_=trade_day.resolve(); print(1 if ok else 0)" 2>/dev/null || echo 1)
if [ "$IS_OPEN" != "1" ]; then
  echo "[监控] ✅ 今日非交易日，跳过 TDA 日报（不重复发送）@ $(date '+%F %T')" >> "$LOG"
  exit 0
fi

for attempt in $(seq 1 $MAX_RETRY); do
  echo "========== 尝试 #$attempt @ $(date '+%F %T') ==========" >> "$LOG"

  # 0) 前置：更新动量面板(数据准备)。失败不阻断，面板可能已有。
  if [ "$attempt" -eq 1 ]; then
    echo "[监控] 更新动量面板(build_momentum)..." >> "$LOG"
    /usr/bin/python3 "$SELF_DIR"/build_momentum.py >> "$LOG" 2>&1
    echo "[监控] 面板更新退出码=$?" >> "$LOG"
  fi

  # 1) 生成+发送日报
  echo "[监控] 运行 tda_60_daily_v2 生成日报..." >> "$LOG"
  /usr/bin/python3 -u "$SELF_DIR"/tda_60_daily_v2.py >> "$LOG" 2>&1
  rc=$?
  echo "[监控] v2 退出码=$rc" >> "$LOG"

  # 2) 校验：进程正常退出 且 日志含飞书成功
  if [ $rc -eq 0 ] && grep -q "飞书已发送.*success\|飞书已发送.*StatusCode.:0" "$LOG"; then
    ok_flag=1
    echo "[监控] ✅ 发送成功，任务完成。" >> "$LOG"
    break
  else
    echo "[监控] ⚠️ 第 $attempt 次未能确认发送成功(rc=$rc)。" >> "$LOG"
    if [ "$attempt" -lt "$MAX_RETRY" ]; then
      echo "[监控] 等待 ${RETRY_WAIT}s 后重试..." >> "$LOG"
      sleep "$RETRY_WAIT"
    fi
  fi
done

# 3) 全部失败 → 飞书告警
if [ "$ok_flag" -ne 1 ]; then
  echo "[监控] ❌ $MAX_RETRY 次尝试均未成功，发飞书告警。" >> "$LOG"
  /usr/bin/python3 - <<'PYEOF' >> "$LOG" 2>&1
import json, urllib.request
hook = __import__("ocsecrets").feishu_hook()
msg = ("⚠️ TDA 60因子日报运行失败\n"
       "连续 3 次尝试均未确认飞书发送成功。\n"
       "请检查 .cache/tda_60_daily_cron.log")
payload = json.dumps({"msg_type":"interactive","card":{"header":{"title":{"tag":"plain_text","content":"⚠️ TDA 60因子日报运行失败 · 来自 OpenClaw.Deepseek"},"template":"red"},"elements":[{"tag":"markdown","content":"📎 来自钉钉OpenClaw"},{"tag":"markdown","content": msg}]}}).encode()
req = urllib.request.Request(hook, data=payload, method='POST', headers={'Content-Type':'application/json'})
print(urllib.request.urlopen(req, timeout=15).read().decode())
PYEOF
fi

echo "[监控] 结束 @ $(date '+%F %T')" >> "$LOG"
