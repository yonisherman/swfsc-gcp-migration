#!/usr/bin/env python3
"""
CompMURmon.py  -  Monthly MUR41 SST Composite (Cloud Run / GCS-native)

Usage:
    python CompMURmon.py <YEAR> <MONTH>
Example:
    python CompMURmon.py 2026 01
"""

import sys
import logging
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from calendar import monthrange
from pathlib import Path

import numpy as np
import numpy.ma as ma
from netCDF4 import Dataset, date2num
from google.cloud import storage

sys.path.insert(0, "/app/src")
from roylib import CFG, send_to_servers

TEMPLATE_FILE = Path("/app/templates/murSSTmday.nc")
LAT_BLOCK = 1023   # matches MUR template chunk size
DL_WORKERS = 8     # parallel download threads


def _setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger()


def _discover_daily_blobs(bucket, prod_root: str, prod_dst_dir: str,
                          interval_dir: str, yyyymm: str) -> list:
    prefix = f"{prod_root}/{prod_dst_dir}/{interval_dir}/"
    blobs = [
        b for b in bucket.list_blobs(prefix=prefix)
        if Path(b.name).name.startswith(yyyymm) and b.name.endswith(".nc")
    ]
    blobs.sort(key=lambda b: b.name)
    return blobs


def _download_blob(args: tuple) -> Path:
    blob, dest = args
    blob.download_to_filename(str(dest))
    return dest


def _parallel_download(blobs: list, dl_dir: Path, log: logging.Logger,
                       workers: int = DL_WORKERS) -> list[Path]:
    log.info(f"Downloading {len(blobs)} files (parallel, {workers} workers)...")
    args = [(blob, dl_dir / Path(blob.name).name) for blob in blobs]

    local_files = [None] * len(args)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {executor.submit(_download_blob, arg): i
                         for i, arg in enumerate(args)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            path = future.result()
            local_files[idx] = path
            log.info(f"  Downloaded: {path.name}")

    # local_files preserves blob order (same order as blobs list)
    return local_files


def main(comp_year: str, comp_month: str) -> None:
    ds_key = "MUR41"
    ds_cfg = CFG[ds_key]

    prod_cfg = CFG["PUBLISH_TARGETS"]["prod"]
    prod_bucket_name = prod_cfg["bucket"]
    prod_root = prod_cfg["root"].strip("/")
    interval_fmt = prod_cfg.get("interval_fmt", "{interval}day")
    interval_1day = str(ds_cfg.get("interval", "1"))
    prod_dst_dir = ds_cfg["prod_dst_dir"].strip("/")
    interval_dir = interval_fmt.format(interval=interval_1day)  # "1day"

    log_dir = Path("/tmp/logs")
    log = _setup_logging(log_dir / f"mur41_mday_{comp_year}{comp_month}.log")
    log.info(f"--- MUR41 monthly SST composite: {comp_year}-{comp_month} ---")
    log.info(f"Template: {TEMPLATE_FILE}")

    if not TEMPLATE_FILE.exists():
        raise FileNotFoundError(f"Template not found: {TEMPLATE_FILE}")

    yyyymm = f"{comp_year}{comp_month}"
    storage_client = storage.Client()
    bucket = storage_client.bucket(prod_bucket_name)

    blobs = _discover_daily_blobs(bucket, prod_root, prod_dst_dir, interval_dir, yyyymm)
    if not blobs:
        log.warning(f"No daily SST files found for {yyyymm} in GCS. Exiting.")
        return

    log.info(f"Found {len(blobs)} daily blobs")
    log.info(f"First: {Path(blobs[0].name).name}")
    log.info(f"Last : {Path(blobs[-1].name).name}")

    month_end = monthrange(int(comp_year), int(comp_month))[1]
    out_name = (
        f"{comp_year}{comp_month}01"
        f"{comp_year}{comp_month}{str(month_end).zfill(2)}"
        f"-GHRSST-SSTfnd-MUR-GLOB-v02.0-fv04.1.nc"
    )

    with tempfile.TemporaryDirectory(dir="/tmp", prefix=f"mur_mday_{yyyymm}_") as tmp_root:
        tmp_path = Path(tmp_root)
        dl_dir = tmp_path / "daily"
        dl_dir.mkdir()
        out_path = tmp_path / out_name

        # ---- Parallel download ----
        local_files = _parallel_download(blobs, dl_dir, log)
        log.info("All downloads complete.")

        # ---- Copy template ----
        shutil.copyfile(str(TEMPLATE_FILE), str(out_path))

        # ---- Write time + creation_date ----
        mydate = datetime(int(comp_year), int(comp_month), 16)
        cenTime = date2num(mydate, units="seconds since 1970-01-01T00:00:00Z")
        with Dataset(str(out_path), "a") as out_nc:
            out_nc.variables["time"][0] = cenTime
            out_nc.creation_date = str(date.today())

        # ---- Stripe accumulation ----
        log.info(f"Accumulating stripes (LAT_BLOCK={LAT_BLOCK})...")
        out_nc = Dataset(str(out_path), "a")
        out_sst = out_nc.variables["sst"]
        out_nobs = out_nc.variables["nobs"]

        try:
            nlat = out_sst.shape[1]
            nlon = out_sst.shape[2]
            log.info(f"Grid: nlat={nlat}, nlon={nlon}")

            for lat0 in range(0, nlat, LAT_BLOCK):
                lat1 = min(lat0 + LAT_BLOCK, nlat)
                h = lat1 - lat0
                log.info(f"  Stripe lat[{lat0}:{lat1}]")

                sum_blk = np.zeros((h, nlon), dtype=np.float32)
                num_blk = np.zeros((h, nlon), dtype=np.int32)

                for fpath in local_files:
                    with Dataset(str(fpath), "r") as nc:
                        obs = nc.variables["analysed_sst"][0, lat0:lat1, :]

                    if not isinstance(obs, ma.MaskedArray):
                        obs = ma.array(obs)

                    num_add = np.ones((h, nlon), dtype=np.int32)
                    num_add[ma.getmaskarray(obs)] = 0
                    num_blk += num_add
                    sum_blk += obs.filled(0.0).astype(np.float32)

                mean_blk = ma.array(sum_blk, mask=(num_blk == 0), fill_value=-999.0)
                denom = ma.array(num_blk, mask=(num_blk == 0)).astype(np.float32)
                mean_blk = (mean_blk / denom) - 273.15  # K -> C

                out_sst[0, lat0:lat1, :] = mean_blk.filled(-999.0).astype(np.float32)
                out_nobs[0, lat0:lat1, :] = num_blk.astype(np.int32)

                del sum_blk, num_blk, mean_blk, denom

        finally:
            out_nc.close()

        log.info("Stripe accumulation complete.")

        # ---- Publish ----
        send_to_servers(str(out_path), f"/{prod_dst_dir}", "m")
        log.info(f"Published: {out_name}")

    log.info(f"--- Complete: {out_name} ---")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python CompMURmon.py <YEAR> <MONTH>")
        sys.exit(2)
    main(str(sys.argv[1]), str(sys.argv[2]).zfill(2))