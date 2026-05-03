#!/bin/bash
# Clean up old trading logs — keep last 7 days
# Runs via cron daily

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
RETENTION_DAYS=7

find "$LOG_DIR" -name "*.log" -type f -mtime "+$RETENTION_DAYS" -delete 2>/dev/null
# Truncate nohup.out if it exists
for f in "$SCRIPT_DIR"/nohup.out; do
    [ -f "$f" ] && truncate --size 0 "$f"
done
exit 0
