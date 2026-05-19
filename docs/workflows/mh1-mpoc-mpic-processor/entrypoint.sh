#!/bin/bash
# Cloud Run entrypoint for the MH1 / MPOC / MPIC processor.
#
# The job prepares writable runtime directories, copies Earthdata credentials
# from Secret Manager mounts into /tmp, then runs all near-real-time retrieval
# suites followed by all science-quality retrieval suites.

set -e # Exit immediately if a command fails

# ---------------------------------------------------------
# 0. Environment & Directory Setup
# ---------------------------------------------------------
echo "Preparing writable environment in /tmp..."

# Create essential folders for downloads, NASA list resources, and logs
mkdir -p /tmp/data /tmp/resources /tmp/logs

# ---------------------------------------------------------
# 0.5. Secret Manager Setup
# ---------------------------------------------------------
echo "Setting up credentials from Secret Manager..."

# Copy secrets from unique sub-folder mounts to writable /tmp
# Secret Manager mounts are read-only; /tmp is our only writable space.
cp /secrets/netrc/file /tmp/.netrc
cp /secrets/cookies/file /tmp/.urs_cookies

# Set strict permissions (required by NASA/wget)
chmod 600 /tmp/.netrc /tmp/.urs_cookies

# Export variables so scripts/wget find the creds in /tmp
export HOME=/tmp
export NETRC=/tmp/.netrc

echo "Credentials and directories configured successfully."

# ---------------------------------------------------------
# 1. Start NRT Processing Suites
# ---------------------------------------------------------
echo "-----------------------------------------------------"
echo "Starting Near-Real-Time (NRT) Processing Workflow..."
echo "-----------------------------------------------------"

# MH1 NRT (Chla, nFLH, PAR, Kd490)
python scripts/MH1/getMH1OceanColor_NRT.py

# MH1 SST NRT (sst, sstMasked — 1day, 8day, mday)
python scripts/MH1/getMH1SST_NRT.py

# PIC NRT
python scripts/MPIC/getMPIC_NRT.py

# POC NRT
python scripts/MPOC/getMPOC_NRT.py

# ---------------------------------------------------------
# 2. Start Science Quality (Delayed) Suites
# ---------------------------------------------------------
echo "-----------------------------------------------------"
echo "Starting Science Quality (Delayed) Processing Workflow..."
echo "-----------------------------------------------------"

# MH1 Science (Chla, nFLH, PAR, Kd490)
python scripts/MH1/getMH1OceanColor_Sci.py

# MH1 SST Science (sst, sstMasked — 1day, 8day, mday)
python scripts/MH1/getMH1SST_Sci.py

# PIC Science
python scripts/MPIC/getMPIC_Sci.py

# POC Science
python scripts/MPOC/getMPOC_Sci.py

echo "-----------------------------------------------------"
echo "ALL MH1 / MPOC / MPIC Master Suites Completed Successfully."
echo "-----------------------------------------------------"