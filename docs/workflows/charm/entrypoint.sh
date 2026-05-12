#!/bin/bash
set -euo pipefail

# 1. Source NASA OCSSW environment
if [ -f "/root/ocssw/OCSSW_bash" ]; then
    source /root/ocssw/OCSSW_bash
    echo "[entrypoint] NASA OCSSW environment initialized."
fi

# 2. Re-assert Paths and HOME
export HOME=/tmp
export OCSSWROOT=/root/ocssw
export PATH="/app/src/DINEOF:/opt/cwutils/bin:${OCSSWROOT}/bin:${OCSSWROOT}/bin/scripts:${PATH}"
export PYTHONPATH="/app:/app/scripts"

# 3. ROOT FIX: Relocate assets to writable /tmp
# DINEOF and other tools need to write logs/status files in these folders
echo "[entrypoint] Relocating config and templates to writable /tmp..."
mkdir -p /tmp/config /tmp/templates /tmp/data/work_dirs

# Copy the static files from the container image to the writable RAM disk
cp -r /app/config/* /tmp/config/
cp -r /app/templates/* /tmp/templates/

# Ensure full read/write/execute permissions for the runtime
chmod -R 777 /tmp/config /tmp/templates /tmp/data

# Create the specific sub-structure needed for DINEOF processing
mkdir -p /tmp/data/dineof/chlor_a \
         /tmp/data/dineof/Rrs_489 \
         /tmp/data/dineof/Rrs_556 \
         /tmp/data/bf_dineof/chlor_a \
         /tmp/data/bf_dineof/Rrs_489 \
         /tmp/data/bf_dineof/Rrs_556 \
         /tmp/data/results \
         /tmp/data/nasa_source_data \
         /tmp/data/processed_nasa_data \
         /tmp/data/work_dirs/work \
         /tmp/data/work_dirs/bf_work \
         /tmp/data/work_dirs/L3_work \
         /tmp/data/work_dirs/bf_L3_work \
         /tmp/charm /tmp/charm_results /tmp/logs

# 4. Point the app to the NEW writable config
export CONFIG_PATH="/tmp/config/config.yaml"

# 5. Setup Earthdata Credentials
echo "[entrypoint] Setting up Earthdata credentials..."
# Ensure these are exactly where NASA/Curl expect them
export NETRC="${HOME}/.netrc"
export URS_COOKIES="${HOME}/.urs_cookies"

if [[ -f "/secrets/netrc/file" ]]; then
  cp "/secrets/netrc/file" "${NETRC}" && chmod 600 "${NETRC}"
else
  echo "ERROR: /secrets/netrc/file not found" && exit 1
fi

if [[ -f "/secrets/cookies/file" ]]; then
  cp "/secrets/cookies/file" "${URS_COOKIES}" && chmod 600 "${URS_COOKIES}"
fi

# 6. Execute
cd /app
echo "[entrypoint] Environment ready. Starting CHARM..."

# Start the controller. Arguments like --backfill_days will pass through.
# Using -m scripts.control_charm_cron_v1 ensures it picks up the PYTHONPATH
exec python3 -m scripts.control_charm_cron_v1 "$@"