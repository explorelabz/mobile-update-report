#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p logs
LOG_FILE="logs/run-$(date +%Y-%m-%d).log"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') START ====="
  ./.venv/bin/python main.py
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') END ====="
} >> "$LOG_FILE" 2>&1
