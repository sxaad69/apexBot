#!/bin/bash
# Clean apex bot log files older than 7 days.
# Keep only the last 7 days of apex hunter logs.
#
# SAFETY (we learned this the hard way — bot hung, logs vanished):
# - Only DELETE date-named apex_hunter_YYYYMMDD.log files; these are created
#   per-day and the day's file is never reused, so deleting old ones is safe.
# - NEVER delete the file the process currently has open, or the fd is
#   orphaned and the bot writes to a phantom inode you can never read.
#   Today's apex_hunter_<date>.log is excluded explicitly.
# - apex_error.log is a single permanent file (RotatingFileHandler) that the
#   process keeps open forever, so TRUNCATE it instead of deleting.

LOG_DIR=${LOG_DIR:-/home/ubuntu/apexBot/logs}
KEEP_DAYS=${KEEP_DAYS:-7}

# Current (active) daily log — never touch it.
TODAY_FILE="$LOG_DIR/apex_hunter_$(date +%Y%m%d).log"

echo "Cleaning $LOG_DIR (keeping last $KEEP_DAYS days)..."

# Delete date-named daily logs older than KEEP_DAYS, except the active day's file.
find "$LOG_DIR" -name 'apex_hunter_*.log' -type f \
    -mtime "+${KEEP_DAYS}" \
    ! -wholename "*$(basename "$TODAY_FILE")" \
    -exec rm -f {} +

# Truncate the permanent error log if it is older than KEEP_DAYS modified time
# (i.e. no new errors in a week). Truncating keeps the fd valid.
find "$LOG_DIR" -name 'apex_error.log*' -type f -mtime "+${KEEP_DAYS}" -exec truncate -s 0 {} +

echo "Log cleanup complete. Kept files from last $KEEP_DAYS days."
