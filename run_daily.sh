#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p logs
DATE="$(date +%Y-%m-%d)"
LOG_FILE="logs/run-${DATE}.log"
SUCCESS_FLAG="logs/run-success-${DATE}.flag"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

if [[ -f "$SUCCESS_FLAG" ]]; then
  log "===== $(date '+%Y-%m-%d %H:%M:%S %Z') SKIP（今日已成功跑过，不再重试）====="
  exit 0
fi

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') START ====="
  if ./.venv/bin/python main.py; then
    touch "$SUCCESS_FLAG"
    echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') END ok（已标记今日成功）====="
  else
    ec=$?
    echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') END fail exit=${ec}（未写成功标记，下一时段可重试）====="
    exit "$ec"
  fi
} >>"$LOG_FILE" 2>&1
