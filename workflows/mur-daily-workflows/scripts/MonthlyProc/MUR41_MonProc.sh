#!/bin/bash
# ============================================================
# mur_v41_monthly_proc.sh  —  MUR41 Monthly Composite Runner
#
# Runs inside the same container as the daily jobs.
# Triggered by JOB_MODE=mur41_monthly in entrypoint.sh.
#
# Behavior:
#   - Determines which YYYY/MM to process from MONTH_OFFSET env var
#     (default: 1 = last completed month).
#   - Runs CompMURmon.py    -> monthly SST composite
#   - Runs CompMurAnomMon.py -> monthly anomaly composite
#
# Environment variables:
#   MONTH_OFFSET   How many months back to process (default: 1 = last month)
#                  Set to 2 to reprocess two months ago, etc.
#   ROYLIB_CONFIG  Path to config.yml (set by entrypoint.sh)
# ============================================================
set -euo pipefail

ts()    { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log()   { echo "[$(ts)] $*"; }
info()  { log "[INFO] $*"; }
err()   { log "[ERR ] $*"; }
phase() { log "== $* =="; }

# ----------------------------
# Determine target YYYY / MM
# ----------------------------
MONTH_OFFSET="${MONTH_OFFSET:-1}"

# Use Python to safely compute the target month (handles year rollover cleanly)
read YEAR MONTH < <(python3 -c "
from datetime import date
from dateutil.relativedelta import relativedelta
import sys
offset = int('${MONTH_OFFSET}')
target = date.today().replace(day=1) - relativedelta(months=offset)
print(target.strftime('%Y'), target.strftime('%m'))
")

phase "MUR41 Monthly Processing: ${YEAR}-${MONTH}"
info "ROYLIB_CONFIG=${ROYLIB_CONFIG}"

# ----------------------------
# 1) Monthly SST composite
# ----------------------------
phase "${YEAR}-${MONTH} SST COMPOSITE"
info "Running CompMURmon.py ${YEAR} ${MONTH}"
python3 /app/scripts/MonthlyProc/CompMURmon.py "${YEAR}" "${MONTH}"
info "SST composite complete."

# ----------------------------
# 2) Monthly anomaly composite
# ----------------------------
phase "${YEAR}-${MONTH} ANOMALY COMPOSITE"
info "Running CompMurAnomMon.py ${YEAR} ${MONTH}"
python3 /app/scripts/MonthlyProc/CompMurAnomMon.py "${YEAR}" "${MONTH}"
info "Anomaly composite complete."

phase "Done: ${YEAR}-${MONTH}"