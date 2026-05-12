#!/usr/bin/env python3
"""
CompMurAnomMon.py  -  Monthly MUR41 SST Anomaly Composite (Cloud Run / GCS-native)

Usage:
    python CompMurAnomMon.py <YEAR> <MONTH>
Example:
    python CompMurAnomMon.py 2026 01
"""

import sys
import shutil
import subprocess
import tempfile
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date as date_cls
from pathlib import Path

import numpy as np
import numpy.ma as ma
from netCDF4 import Dataset, date2num
from google.cloud import storage

sys.path.insert(0, "/app/src")
from roylib import CFG
from roylib import send_to_servers

TEMPLATE_FILE = Path("/app/templates/murSSTmdayAnom.nc")
LAT_BLOCK = 1023   # conservative for anomaly files
DL_WORKERS = 8    # parallel download threads


def _discover_daily_anom_blobs(bucket, prefix: str, yyyymm: str) -> list:
    """Return sorted blobs matching YYYYMM*.nc under prefix/<YYYY>/"""
    year = yyyymm[:4]
    search_prefix = f"{prefix}/{year}/"
    blobs = [
        b for b in bucket.list_blobs(prefix=search_prefix)
        if Path(b.name).name.startswith(yyyymm) and b.name.endswith(".nc")
    ]
    blobs.sort(key=lambda b: b.name)
    return blobs


def _download_blob(args: tuple) -> Path:
    blob, dest = args
    blob.download_to_filename(str(dest))
    return dest


def _parallel_download(blobs: list, dl_dir: Path,
                       workers: int = DL_WORKERS) -> list[Path]:
    print(f"[INFO] Downloading {len(blobs)} files (parallel, {workers} workers)...")
    args = [(blob, dl_dir / Path(blob.name).name) for blob in blobs]

    local_files = [None] * len(args)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {executor.submit(_download_blob, arg): i
                         for i, arg in enumerate(args)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            path = future.result()
            local_files[idx] = path
            print(f"[INFO]   Downloaded: {path.name}")

    return local_files


def main(comp_year: str, comp_month: str) -> None:
    prod_cfg = CFG["PUBLISH_TARGETS"]["prod"]
    prod_bucket_name = prod_cfg["bucket"]
    prod_root = prod_cfg["root"].strip("/")
    interval_fmt = prod_cfg.get("interval_fmt", "{interval}day")

    # Source: daily anomaly files
    if "MUR41_ANOM" not in CFG or "prod_dst_dir" not in CFG["MUR41_ANOM"]:
        raise KeyError("config.yml missing MUR41_ANOM.prod_dst_dir")
    src_dst_dir = CFG["MUR41_ANOM"]["prod_dst_dir"].strip("/")
    src_interval = str(CFG["MUR41_ANOM"].get("interval", "1"))
    src_interval_dir = interval_fmt.format(interval=src_interval)  # "1day"
    src_gcs_prefix = f"{prod_root}/{src_dst_dir}/{src_interval_dir}"

    # Destination: monthly anomaly
    dst_cfg = CFG.get("MUR41_ANOM_MDAY", {"prod_dst_dir": "MUR41/anom", "interval": "m"})
    dst_dst_dir = dst_cfg["prod_dst_dir"].strip("/")
    dst_interval = str(dst_cfg.get("interval", "m"))
    dst_interval_dir = interval_fmt.format(interval=dst_interval)  # "mday"

    if not TEMPLATE_FILE.exists():
        raise FileNotFoundError(f"Template not found: {TEMPLATE_FILE}")

    yyyymm = f"{comp_year}{comp_month}"
    print(f"[INFO] Monthly anomaly composite: {comp_year}-{comp_month}")
    print(f"[INFO] Source: gs://{prod_bucket_name}/{src_gcs_prefix}/<YYYY>/")

    storage_client = storage.Client()
    bucket = storage_client.bucket(prod_bucket_name)

    blobs = _discover_daily_anom_blobs(bucket, src_gcs_prefix, yyyymm)
    if not blobs:
        print(f"[WARN] No daily anomaly files found for {yyyymm}. Exiting.")
        return

    print(f"[INFO] Found {len(blobs)} daily anomaly blobs")

    month_end = monthrange(int(comp_year), int(comp_month))[1]
    out_name = (
        f"{comp_year}{comp_month}01"
        f"{comp_year}{comp_month}{str(month_end).zfill(2)}"
        f"-GHRSST-SSTfndAnom-MUR-GLOB-v02.0-fv04.1.nc"
    )

    with tempfile.TemporaryDirectory(dir="/tmp", prefix=f"mur_anom_mday_{yyyymm}_") as tmp_root:
        tmp_path = Path(tmp_root)
        dl_dir = tmp_path / "daily_inputs"
        dl_dir.mkdir()
        out_path = tmp_path / out_name

        # ---- Parallel download ----
        local_files = _parallel_download(blobs, dl_dir)
        print("[INFO] All downloads complete.")

        # ---- Copy template ----
        shutil.copyfile(str(TEMPLATE_FILE), str(out_path))

        # ---- Grid shape from first file ----
        with Dataset(str(local_files[0]), "r") as ds0:
            nlat = ds0.variables["sstAnom"].shape[-2]
            nlon = ds0.variables["sstAnom"].shape[-1]
        print(f"[INFO] Grid: nlat={nlat}, nlon={nlon}")

        # ---- Write time + accumulate stripes ----
        out_nc = Dataset(str(out_path), "a")
        sst_out = out_nc.variables["sstAnom"]
        nobs_out = out_nc.variables["nobs"]
        time_out = out_nc.variables["time"]

        mydate = datetime(int(comp_year), int(comp_month), 16)
        time_out[0] = date2num(mydate, units=time_out.units)

        try:
            for i0 in range(0, nlat, LAT_BLOCK):
                i1 = min(i0 + LAT_BLOCK, nlat)
                h = i1 - i0
                print(f"[INFO]   Stripe lat[{i0}:{i1}]")

                sum_blk = np.zeros((h, nlon), dtype=np.float32)
                cnt_blk = np.zeros((h, nlon), dtype=np.int32)

                for fpath in local_files:
                    with Dataset(str(fpath), "r") as nc:
                        obs = nc.variables["sstAnom"][0, i0:i1, :]

                    if not isinstance(obs, ma.MaskedArray):
                        obs = ma.array(obs)

                    valid = ~ma.getmaskarray(obs)
                    cnt_blk += valid.astype(np.int32)
                    sum_blk += obs.filled(0.0).astype(np.float32)

                mean_blk = np.zeros((h, nlon), dtype=np.float32)
                has_obs = cnt_blk > 0
                mean_blk[has_obs] = sum_blk[has_obs] / cnt_blk[has_obs].astype(np.float32)

                sst_out[0, i0:i1, :] = mean_blk
                nobs_out[0, i0:i1, :] = cnt_blk

                del sum_blk, cnt_blk, mean_blk

        finally:
            out_nc.creation_date = str(date_cls.today())
            out_nc.close()

        print("[INFO] Stripe accumulation complete.")
        # ---- Upload ----
        
        send_to_servers(str(out_path), f"/{dst_dst_dir}", "m")
        print(f"[INFO] Published: {out_name}")

    print(f"[INFO] --- Complete: {out_name} ---")



if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python CompMurAnomMon.py <YEAR> <MONTH>")
        sys.exit(2)
    main(str(sys.argv[1]), str(sys.argv[2]).zfill(2))