#!/usr/bin/env python3
"""
updateMURanom1dayCron.py (Integrated Container Version)

Description:
- Processes a SINGLE MUR SST file passed as sys.argv[1].
- Computes anomaly: analysed_sst - daily climatology mean_sst.
- Overwrites any existing anomaly in GCS (Standard for NRT -> Final updates).
"""

import os
import sys
import shutil
import subprocess
import tempfile
from datetime import datetime
from netCDF4 import Dataset
import yaml

def _run(cmd: list[str]) -> None:
    """Uses the modern gcloud storage CLI."""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"GCP Command Failed: {' '.join(cmd)}\n{p.stderr}")

# 1. Load Config (Cloud Run-safe: prefer ROYLIB_CONFIG env var)
CONFIG_PATH = os.environ.get("ROYLIB_CONFIG", "/config/config.yml")

if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(
        f"Config not found: {CONFIG_PATH}. "
        f"Set ROYLIB_CONFIG (e.g., /secrets/config/config.yml) or mount /config/config.yml."
    )

with open(CONFIG_PATH, "r") as f:
    CFG = yaml.safe_load(f)

# Reconstruct environment from Master Config
HOME_DIR = CFG["HOME_DIR"].rstrip("/")
PROD_BUCKET = CFG["ERDPROD_BUCKET"]
PROD_ROOT = CFG["PUBLISH_TARGETS"]["prod"]["root"]
CLIM_GCS_PREFIX = CFG.get("MUR_CLIM_GCS_PREFIX", "").rstrip("/")

# Target anomaly settings
ANOM_CFG = CFG["MUR41_ANOM"]
ANOM_DST_DIR = ANOM_CFG["prod_dst_dir"].strip("/")
ANOM_INTERVAL = str(ANOM_CFG.get("interval", "1"))
INTERVAL_FMT = CFG["PUBLISH_TARGETS"]["prod"]["interval_fmt"].format(interval=ANOM_INTERVAL)

# Local Template Path
ANOM_TEMPLATE = "/app/templates/murAnomTemplate.nc"

def process_single_file(sst_path):
    """Computes anomaly and uploads directly to GCS."""
    if not os.path.exists(sst_path):
        print(f"Error: Input file {sst_path} not found.")
        sys.exit(1)

    fName = os.path.basename(sst_path)
    # Extract YYYYMMDD from the start of the MUR filename
    date_str = fName[:8] 
    working_date = datetime.strptime(date_str, "%Y%m%d")
    year_str = working_date.strftime("%Y")
    doy = working_date.strftime("%j").zfill(3)
    
    # Map leap year day 366 to climatology day 365
    clim_doy = "365" if doy == "366" else doy
    clim_name = f"mur_{clim_doy}.nc"

    print(f"=== Starting Anomaly Computation: {fName} ===")

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="anom_work_") as tmp_root:
        
        # A. Fetch Climatology from GCS
        clim_local = os.path.join(tmp_root, clim_name)
        _run(["gcloud", "storage", "cp", f"{CLIM_GCS_PREFIX}/{clim_name}", clim_local])

        # B. Prepare Output from Template
        anom_name = fName.replace("SSTfnd", "SSTfndAnom")
        out_local = os.path.join(tmp_root, anom_name)
        shutil.copy2(ANOM_TEMPLATE, out_local)

        # C. Compute (Chunked to 2000 for your 8GB RAM target)
        LAT_BLOCK = 2000
        with Dataset(sst_path, "r") as sstgrp, \
             Dataset(clim_local, "r") as climgrp, \
             Dataset(out_local, "a") as anomgrp:

            sst_var = sstgrp.variables["analysed_sst"]
            mean_var = climgrp.variables["mean_sst"]
            
            anomgrp.variables["lat"][:] = sstgrp.variables["lat"][:]
            anomgrp.variables["lon"][:] = sstgrp.variables["lon"][:]
            anomgrp.variables["time"][:] = sstgrp.variables["time"][:]

            nlat, nlon = mean_var.shape
            
            for i0 in range(0, nlat, LAT_BLOCK):
                i1 = min(i0 + LAT_BLOCK, nlat)
                anomgrp.variables["sstAnom"][0, i0:i1, :] = sst_var[0, i0:i1, :] - mean_var[i0:i1, :]
                anomgrp.variables["mask"][0, i0:i1, :] = sstgrp.variables["mask"][0, i0:i1, :]

            anomgrp.creation_date = str(datetime.now())
            anomgrp.source = f"{fName}, {clim_name}"

        # D. Upload Result (Overwrite is default for cp)
        prod_uri = f"gs://{PROD_BUCKET}/{PROD_ROOT}/{ANOM_DST_DIR}/{INTERVAL_FMT}/{year_str}/{anom_name}"
        print(f"Uploading Anomaly: {prod_uri}")
        _run(["gcloud", "storage", "cp", out_local, prod_uri])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 updateMURanom1dayCron.py <path_to_sst_file>")
        sys.exit(1)
    
    process_single_file(sys.argv[1])
    print("Anomaly processing complete.")