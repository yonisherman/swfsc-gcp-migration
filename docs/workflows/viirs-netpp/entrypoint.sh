#!/bin/bash
# =============================================================================
# entrypoint.sh - VIIRS primary productivity Cloud Run entrypoint
#
# JOB_MODE controls what this container instance does:
#
#   nrt_daily   - All enabled sensors, NRT, yesterday − 3 days → yesterday
#   sq_sweep    - All enabled sensors, SQ, sensor start → yesterday
#                 (idempotent: make script skips already-present GCS outputs)
#
# Both modes call control_viirs_netpp.py, which in turn calls make_viirs_netpp.py
# for each sensor. The make script is skips-aware (gcs_exists check), so running
# sq_sweep daily is safe and only does real work when new SQ files have appeared.
#
# Expected Secret Manager mounts (set via Cloud Run job --set-secrets):
#   /secrets/netrc/file       → NASA URS .netrc
#   /secrets/cookies/file     → NASA URS cookies (may be empty on first run)
# =============================================================================
set -euo pipefail

cd /app

echo "Preparing writable home in /tmp..."
export HOME=/tmp
mkdir -p /tmp/npp_scratch /tmp/logs

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
export NETRC="${NETRC:-/tmp/.netrc}"
export URS_COOKIES="${URS_COOKIES:-/tmp/.urs_cookies}"

if [[ -f /secrets/netrc/file ]]; then
    cp /secrets/netrc/file "$NETRC"
    chmod 600 "$NETRC"
else
    echo "ERROR: /secrets/netrc/file not found - netrc-secret mount missing?" >&2
    exit 1
fi

if [[ -f /secrets/cookies/file ]]; then
    cp /secrets/cookies/file "$URS_COOKIES"
else
    # Create an empty file; wget will populate it on first authenticated request
    : > "$URS_COOKIES"
fi
chmod 600 "$URS_COOKIES"

echo "Credentials configured."
echo "  NETRC=$NETRC"
echo "  URS_COOKIES=$URS_COOKIES"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
export NPP_LOCAL_SCRATCH="/tmp/npp_scratch"
CONFIG="/app/config/config.yml"
CONTROL="/app/scripts/control_viirs_netpp.py"

TODAY=$(date -u +%Y%m%d)
# Yesterday in UTC
YESTERDAY=$(date -u -d "yesterday" +%Y%m%d 2>/dev/null \
    || python3 -c "from datetime import datetime,timedelta; print((datetime.utcnow()-timedelta(days=1)).strftime('%Y%m%d'))")
# 3 days ago - NRT buffer to catch stragglers
THREE_DAYS_AGO=$(date -u -d "3 days ago" +%Y%m%d 2>/dev/null \
    || python3 -c "from datetime import datetime,timedelta; print((datetime.utcnow()-timedelta(days=3)).strftime('%Y%m%d'))")

MODE="${JOB_MODE:-nrt_daily}"

echo "-----------------------------------------------------"
echo "JOB_MODE  : ${MODE}"
echo "TODAY UTC : ${TODAY}"
echo "-----------------------------------------------------"

case "$MODE" in

  # -----------------------------------------------------------------------
  # NRT daily - run yesterday-3d through yesterday for all enabled sensors
  # -----------------------------------------------------------------------
  nrt_daily)
    echo "Running NRT daily sweep: ${THREE_DAYS_AGO} → ${YESTERDAY}"
    python3 "$CONTROL" \
        --start "$THREE_DAYS_AGO" \
        --end   "$YESTERDAY" \
        --sensor all \
        --dtype nrt \
        --config "$CONFIG" \
        --keep-going
    ;;

  # -----------------------------------------------------------------------
  # SQ sweep - scan from each sensor's operational start to yesterday.
  # The make script's gcs_exists() check makes this idempotent; it only
  # downloads and processes dates that are not yet in GCS. Running daily
  # means new SQ releases are picked up automatically regardless of when
  # NASA publishes them.
  # -----------------------------------------------------------------------
  sq_sweep)
    echo "Running SQ year sweep: sensor start → ${YESTERDAY}"
    python3 "$CONTROL" \
        --start 2026-01-01 \
        --end   "$YESTERDAY" \
        --sensor all \
        --dtype sq \
        --config "$CONFIG" \
        --keep-going
    ;;

  # -----------------------------------------------------------------------
  # Monthly composite - previous full calendar month by default, or a user-
  # supplied TARGET_YEAR / TARGET_MONTH override for backfills.
  #
  # This Cloud Run job is intended to be sensor-specific:
  #   TARGET_SENSOR=snpp    or    TARGET_SENSOR=noaa20
  #
  # Dtype is kept aligned with the on-prem workflow by running:
  #   nrt first, then sq
  # via control_viirs_netpp_monthly.py --dtype both
  # -----------------------------------------------------------------------
  monthly_composite)
    if [[ -n "${TARGET_YEAR:-}" && -n "${TARGET_MONTH:-}" ]]; then
      RUN_YEAR="$TARGET_YEAR"
      RUN_MONTH="$TARGET_MONTH"
    else
      RUN_YEAR=$(python3 -c "from datetime import date; from dateutil.relativedelta import relativedelta; d=date.today().replace(day=1)-relativedelta(months=1); print(d.year)")
      RUN_MONTH=$(python3 -c "from datetime import date; from dateutil.relativedelta import relativedelta; d=date.today().replace(day=1)-relativedelta(months=1); print(d.month)")
    fi

    TARGET_SENSOR="${TARGET_SENSOR:-}"
    if [[ -z "${TARGET_SENSOR}" ]]; then
      echo "ERROR: TARGET_SENSOR must be set for monthly_composite (e.g. snpp or noaa20)." >&2
      exit 2
    fi

    case "${TARGET_SENSOR}" in
      snpp|noaa20)
        ;;
      *)
        echo "ERROR: TARGET_SENSOR must be 'snpp' or 'noaa20'; got '${TARGET_SENSOR}'." >&2
        exit 2
        ;;
    esac

    echo "Running monthly composite for ${RUN_YEAR}-${RUN_MONTH} (sensor=${TARGET_SENSOR}, nrt then sq)"
    python3 /app/scripts/control_viirs_netpp_monthly.py \
        --year  "$RUN_YEAR" \
        --month "$RUN_MONTH" \
        --sensor "$TARGET_SENSOR" \
        --dtype both \
        --config "$CONFIG" \
        --keep-going
    ;;

  *)
    echo "ERROR: Unknown JOB_MODE='${MODE}'. Use nrt_daily, sq_sweep, or monthly_composite." >&2
    exit 2
    ;;
esac

echo "-----------------------------------------------------"
echo "Workflow '${MODE}' completed successfully."
echo "-----------------------------------------------------"