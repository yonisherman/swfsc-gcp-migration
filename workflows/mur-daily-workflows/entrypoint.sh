#!/bin/bash
set -euo pipefail

cd /app

echo "Preparing writable environment in /tmp..."
export HOME=/tmp

mkdir -p /tmp/data /tmp/resources /tmp/logs

echo "Setting up credentials from Secret Manager mounts..."

export NETRC="${NETRC:-/tmp/.netrc}"
export URS_COOKIES="${URS_COOKIES:-/tmp/.urs_cookies}"
export ROYLIB_CONFIG="/app/config/config.yml"

if [[ -f /secrets/netrc/file ]]; then
  cp /secrets/netrc/file "$NETRC"
  chmod 600 "$NETRC"
else
  echo "ERROR: /secrets/netrc/file not found (netrc-secret mount missing?)"
  exit 1
fi

if [[ -f /secrets/cookies/file ]]; then
  cp /secrets/cookies/file "$URS_COOKIES"
else
  : > "$URS_COOKIES"
fi
chmod 600 "$URS_COOKIES"

echo "Credentials and config configured successfully."
echo "NETRC=$NETRC"
echo "URS_COOKIES=$URS_COOKIES"
echo "ROYLIB_CONFIG=$ROYLIB_CONFIG"

# ---------------------------------------------------------
# 1. Run workflow (mur41 or mur42)
# ---------------------------------------------------------
MODE="${JOB_MODE:-mur41}"
echo "-----------------------------------------------------"
echo "Starting workflow: ${MODE}"
echo "-----------------------------------------------------"

case "$MODE" in
  mur41)         SCRIPT="/app/scripts/mur_v41_downloader_dailyproc.sh" ;;
  mur42)         SCRIPT="/app/scripts/mur_v42_downloader.sh" ;;
  mur41_monthly) SCRIPT="/app/scripts/MonthlyProc/MUR41_MonProc.sh" ;;
  *)             echo "ERROR: Unknown JOB_MODE=${MODE} (use mur41, mur42, mur41_monthly)"; exit 2 ;;
esac

if [[ ! -f "$SCRIPT" ]]; then
  echo "ERROR: Script not found in image: $SCRIPT"
  echo "       Check Dockerfile COPY and script path."
  exit 1
fi

chmod +x "$SCRIPT" || true
"$SCRIPT"

echo "-----------------------------------------------------"
echo "Workflow ${MODE} completed successfully."
echo "-----------------------------------------------------"