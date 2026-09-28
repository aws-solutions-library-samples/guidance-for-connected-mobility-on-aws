#!/bin/bash
"""
Daily cleanup automation script for CMS data retention.
Run this script daily via cron to maintain data retention policies.
"""

# Set script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/../logs/cleanup_$(date +%Y%m%d).log"

# Create logs directory if it doesn't exist
mkdir -p "$(dirname "$LOG_FILE")"

# Function to log with timestamp
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log "Starting daily cleanup process"

# Run the cleanup script
cd "$SCRIPT_DIR/.."
python3 scripts/cleanup_old_data.py >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    log "Cleanup completed successfully"
else
    log "Cleanup failed with error code $?"
fi

# Keep only last 30 days of logs
find "$(dirname "$LOG_FILE")" -name "cleanup_*.log" -mtime +30 -delete

log "Daily cleanup process finished"
