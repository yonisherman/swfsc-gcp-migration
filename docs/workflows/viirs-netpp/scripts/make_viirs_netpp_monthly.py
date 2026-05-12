#!/usr/bin/env python3
"""Monthly primary productivity composites from daily VIIRS netpp files.

Workflow
--------
For a given year-month, sensor, and dtype:

1.  List daily productivity .nc files for that month from GCS.
2.  Download one daily file at a time to local scratch.
3.  Read the productivity array in row chunks and accumulate a pixel-wise sum
    and valid-observation count.
4.  Delete each daily file immediately after it has been incorporated.
5.  Write the monthly composite to a new NetCDF using the sensor monthly CDL
    template.
6.  Compress with nccopy and upload to GCS.
7.  Clean up all local scratch files.

This implementation keeps memory and /tmp usage much lower than downloading the
entire month first. It preserves the same valid-pixel monthly mean logic as the
on-prem workflow.

GCS layout (mirrors daily):
  Daily inputs:
    gs://YOUR_GCS_BUCKET/edge/{sensor}_netpp/1day_nrt/{year}/
    gs://YOUR_GCS_BUCKET/edge/{sensor}_netpp/1day/{year}/
  Monthly outputs:
    gs://YOUR_GCS_BUCKET/edge/{sensor}_netpp/mday_nrt/{year}/
    gs://YOUR_GCS_BUCKET/edge/{sensor}_netpp/mday/{year}/

Output filename convention:
  NRT : productivity_viirs_{sensor}_monthly_nrt_{YYYYMM}.nc
  SQ  : productivity_viirs_{sensor}_monthly_sq_{YYYYMM}.nc

Environment variables
---------------------
  NPP_LOCAL_SCRATCH   Local scratch dir  (default: /tmp/npp_scratch)
  NPP_NCO_DIR         Directory containing ncgen / nccopy binaries
"""

from __future__ import annotations

import argparse
import calendar
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml
from netCDF4 import Dataset

warnings.filterwarnings("ignore")

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


# ---------------------------------------------------------------------------
# Config / env helpers
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    with open(config_path, "r") as fh:
        docs = list(yaml.safe_load_all(fh))
    merged = {}
    for doc in docs:
        if doc:
            merged.update(doc)
    return merged


def build_paths(cfg: dict) -> dict:
    ncgen = shutil.which("ncgen")
    nccopy = shutil.which("nccopy")
    nco_dir = os.environ.get("NPP_NCO_DIR")
    if not nco_dir:
        if ncgen and nccopy:
            nco_dir = os.path.dirname(ncgen)
        else:
            raise EnvironmentError(
                "Could not find ncgen/nccopy. Set NPP_NCO_DIR or add them to PATH."
            )
    return {
        "scratch": os.environ.get("NPP_LOCAL_SCRATCH", "/tmp/npp_scratch"),
        "nco_dir": nco_dir,
        "templates_dir": str(REPO_ROOT / "templates"),
    }


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------

def gcs_upload(local_path: str, gcs_uri: str) -> None:
    result = subprocess.run(
        ["gsutil", "-q", "cp", local_path, gcs_uri],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gsutil upload failed: {result.stderr.strip()}")
    print(f"  ✔ Uploaded: {os.path.basename(local_path)} → {gcs_uri}")


def gcs_exists(gcs_uri: str) -> bool:
    result = subprocess.run(
        ["gsutil", "-q", "stat", gcs_uri],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def gcs_list(gcs_prefix: str) -> list[str]:
    """List GCS objects under a prefix. Returns [] if none found."""
    result = subprocess.run(
        ["gsutil", "ls", gcs_prefix],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip().endswith(".nc")]


def gcs_download(gcs_uri: str, dest_path: str) -> None:
    """Download a single GCS object to dest_path."""
    result = subprocess.run(
        ["gsutil", "-q", "cp", gcs_uri, dest_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gsutil download failed for {gcs_uri}: {result.stderr.strip()}"
        )


def _validate_nc(path: str) -> bool:
    try:
        with Dataset(path, "r"):
            pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# GCS path helpers
# ---------------------------------------------------------------------------

def daily_gcs_prefix(cfg: dict, sensor: str, dtype: str, year: int) -> str:
    """Return the GCS prefix where daily files for this sensor/dtype/year live."""
    gcs_cfg = cfg["gcs"]
    interval = "1day_nrt" if dtype == "nrt" else "1day"
    prefix = gcs_cfg["erddap_prefix"].format(sensor=sensor, interval=interval, year=year)
    return f"gs://{gcs_cfg['erddap_bucket']}/{prefix}"


def monthly_gcs_uri(
    cfg: dict,
    sensor: str,
    dtype: str,
    year: int,
    month: int,
    ofile: str,
) -> str:
    """Return the full GCS URI for the monthly output file."""
    gcs_cfg = cfg["gcs"]
    interval = "mday_nrt" if dtype == "nrt" else "mday"
    prefix = gcs_cfg["erddap_prefix"].format(sensor=sensor, interval=interval, year=year)
    return f"gs://{gcs_cfg['erddap_bucket']}/{prefix}/{ofile}"


# ---------------------------------------------------------------------------
# Output filename
# ---------------------------------------------------------------------------

def monthly_filename(cfg: dict, sensor: str, dtype: str, year: int, month: int) -> str:
    tmpl = cfg["processing"]["output_filename_template_monthly"][dtype]
    return tmpl.format(sensor=sensor, yearmonth=f"{year:04d}{month:02d}")


# ---------------------------------------------------------------------------
# NetCDF metadata
# ---------------------------------------------------------------------------

def write_monthly_metadata(
    nc_file,
    sensor: str,
    dtype: str,
    year: int,
    month: int,
    n_days: int,
    now: datetime,
    cfg: dict,
) -> None:
    start_year = cfg["sensors"][sensor]["start_year"]
    su = sensor.upper()
    dtype_label = "NRT" if dtype == "nrt" else "Science Quality"

    first_day = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = datetime(
        year,
        month,
        calendar.monthrange(year, month)[1],
        23,
        59,
        59,
        tzinfo=timezone.utc,
    )
    center_ts = datetime(year, month, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    nc_file.time_coverage_start = first_day.strftime("%Y-%m-%dT00:00:00Z")
    nc_file.time_coverage_end = last_day.strftime("%Y-%m-%dT23:59:59Z")
    nc_file.time_coverage_duration = "P1M"
    nc_file.time_coverage_resolution = "P1M"
    nc_file.date_created = now.isoformat("T", "seconds")
    nc_file.platform = su
    nc_file.id = f"productivity_{sensor}_month_{dtype}"
    nc_file.product_name = f"VIIRS {su} Primary Productivity Monthly {dtype_label}"
    nc_file.source = f"satellite observations from VIIRS {su}"
    nc_file.comment = (
        f"Monthly composite computed from {n_days} daily {dtype_label} "
        "productivity files using a valid-pixel mean."
    )
    nc_file.acknowledgement = (
        "The project was supported by funding from the Portfolio Management "
        "Branch of NESDIS and NOAA CoastWatch."
    )
    nc_file.contributors = (
        "Dale Robinson, Isaac Shroeder, Ryan Vandermeulen, Jonathan Sherman, "
        "Jesse Espinoza, Madison Richardson"
    )
    nc_file.title = ", ".join(
        [
            "Primary Productivity",
            f"VIIRS {su}",
            dtype_label,
            "Global",
            "4km",
            f"{start_year}-present (Monthly Composite)",
        ]
    )
    nc_file.summary = (
        f"The Visible and Infrared Imager/Radiometer Suite (VIIRS), {su} "
        f"Monthly Primary Productivity composite ({dtype_label}). Pixel-wise mean "
        "of daily net carbon fixation estimates (Behrenfeld & Falkowski 1997 VGPM) "
        "for the calendar month. Mapped to a NASA 4km Standard Mapped Image (SMI)."
    )
    nc_file.history = (
        "Daily productivity files averaged to monthly composite. "
        "Chlorophyll a, PAR, and SST satellite data were applied to the equation "
        "of Behrenfeld and Falkowski, 1997."
    )

    try:
        nc_file["time"][0] = center_ts
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core compositing
# ---------------------------------------------------------------------------

def find_daily_files_for_month(
    cfg: dict,
    sensor: str,
    dtype: str,
    year: int,
    month: int,
) -> list[str]:
    """
    Return GCS URIs of all daily productivity files for the requested month.

    Only files whose filename contains the correct YYYYMM prefix are included,
    so files from adjacent months that might sit in the same year-prefix are
    filtered out.
    """
    prefix = daily_gcs_prefix(cfg, sensor, dtype, year)
    all_uris = gcs_list(f"{prefix}/")
    ym = f"{year:04d}{month:02d}"
    return sorted(u for u in all_uris if f"_{ym}" in os.path.basename(u))


def prepare_output_template(
    cfg: dict,
    paths: dict,
    tmp_dir: str,
    sensor: str,
    dtype: str,
    year: int,
    month: int,
    ofile: str,
) -> tuple[str, str]:
    """
    Build the uncompressed monthly NetCDF from the monthly CDL template.

    Returns
    -------
    temp_nc : str
        Path to the temporary uncompressed NetCDF.
    cdl_path : str
        Path to the CDL used.
    """
    cdl_sensor = "noaa20" if sensor in ("noaa20", "noaa21") else sensor

    cdl_template = cfg["processing"].get("cdl_file_monthly")
    if cdl_template:
        cdl_name = cdl_template.format(sensor=cdl_sensor)
    else:
        cdl_name = f"nasa_{cdl_sensor}_4km_month.cdl"

    cdl_path = os.path.join(paths["templates_dir"], cdl_name)
    if not os.path.exists(cdl_path):
        raise FileNotFoundError(f"Monthly CDL template not found: {cdl_path}")

    temp_nc = os.path.join(
        tmp_dir,
        f"temp_monthly_{sensor}_{dtype}_{year}{month:02d}.nc",
    )
    ncgen_bin = os.path.join(paths["nco_dir"], "ncgen")

    result = subprocess.run(
        [ncgen_bin, "-o", temp_nc, cdl_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ncgen failed: {result.stderr.strip()}")

    return temp_nc, cdl_path


def composite_month(
    sensor: str,
    dtype: str,
    year: int,
    month: int,
    overwrite: bool,
    cfg: dict,
    paths: dict,
    tmp_dir: str,
) -> bool:
    """
    Build and upload a monthly composite for one sensor / dtype / year-month.

    Returns True on success, False on skip or failure.
    """
    proc_cfg = cfg["processing"]
    chunk_rows = int(proc_cfg.get("monthly_chunk_rows", 256))

    ofile = monthly_filename(cfg, sensor, dtype, year, month)
    out_gcs = monthly_gcs_uri(cfg, sensor, dtype, year, month, ofile)

    if not overwrite and gcs_exists(out_gcs):
        print(f"  ⏭  {ofile} already in GCS, skipping.")
        return True

    print(f"\n{'='*60}")
    print(f"  Monthly composite  {year}-{month:02d}  [{sensor} / {dtype}]")
    print(f"{'='*60}")

    daily_uris = find_daily_files_for_month(cfg, sensor, dtype, year, month)
    if not daily_uris:
        print(f"  ⏭  No daily files found in GCS for {year}-{month:02d} [{sensor}/{dtype}] - skipping.")
        return True

    print(f"  Found {len(daily_uris)} daily files.")

    # ------------------------------------------------------------------
    # Download first valid file to discover shape and initialise accumulators
    # ------------------------------------------------------------------
    first_valid_path = None
    first_valid_uri = None

    for uri in daily_uris:
        fname = os.path.basename(uri)
        dest_path = os.path.join(tmp_dir, fname)
        print(f"  ↓ {fname}")
        try:
            gcs_download(uri, dest_path)
        except RuntimeError as exc:
            print(f"  ✗ Download failed: {exc} - skipping this file.")
            continue

        if not _validate_nc(dest_path):
            print(f"  ✗ Invalid NetCDF after download: {fname} - skipping.")
            try:
                os.remove(dest_path)
            except OSError:
                pass
            continue

        first_valid_path = dest_path
        first_valid_uri = uri
        break

    if first_valid_path is None:
        print(f"  ✗ No valid daily files downloaded for {year}-{month:02d}.")
        return False

    with Dataset(first_valid_path, "r") as ds0:
        prod_var = ds0["productivity"]
        ndim = prod_var.ndim
        shape = prod_var.shape
        if ndim == 4:
            # Daily files are (time, altitude, lat, lon)
            _, _, nlat, nlon = shape
        elif ndim == 3:
            # Fallback: (time, lat, lon)
            _, nlat, nlon = shape
        else:
            print(f"  ✗ Unexpected productivity shape {shape} - cannot composite.")
            try:
                os.remove(first_valid_path)
            except OSError:
                pass
            return False
    print(f"  Daily file shape: {shape}  → compositing ({nlat}, {nlon}) lat/lon grid.")

    # Keep the arithmetic conservative to match the on-prem approach.
    pp_sum = np.zeros((nlat, nlon), dtype=np.float64)
    pp_count = np.zeros((nlat, nlon), dtype=np.int32)

    valid_days = 0

    def accumulate_one_file(local_path: str) -> bool:
        nonlocal valid_days
        try:
            with Dataset(local_path, "r") as ds:
                prod = ds["productivity"]
                file_ndim = prod.ndim
                fill = getattr(prod, "_FillValue", None)

                for row0 in range(0, nlat, chunk_rows):
                    row1 = min(row0 + chunk_rows, nlat)

                    # Handle both (time, alt, lat, lon) and (time, lat, lon)
                    if file_ndim == 4:
                        raw = np.asarray(prod[0, 0, row0:row1, :], dtype=np.float64)
                    else:
                        raw = np.asarray(prod[0, row0:row1, :], dtype=np.float64)

                    # Mask fill values and non-finite values
                    if fill is not None:
                        raw[raw == float(fill)] = np.nan
                    valid = np.isfinite(raw) & (raw > 0)

                    if np.any(valid):
                        pp_sum[row0:row1, :][valid] += raw[valid]
                        pp_count[row0:row1, :] += valid.astype(np.int32)

        except Exception as exc:
            print(f"  ✗ Could not read productivity from {os.path.basename(local_path)}: {exc}")
            return False

        valid_days += 1
        return True

    # Accumulate first valid file
    if not accumulate_one_file(first_valid_path):
        try:
            os.remove(first_valid_path)
        except OSError:
            pass
        print("  ✗ Failed reading first valid file after download.")
        return False

    try:
        os.remove(first_valid_path)
    except OSError:
        pass

    # Accumulate remaining files one at a time
    for uri in daily_uris:
        if uri == first_valid_uri:
            continue

        fname = os.path.basename(uri)
        dest_path = os.path.join(tmp_dir, fname)
        print(f"  ↓ {fname}")

        try:
            gcs_download(uri, dest_path)
        except RuntimeError as exc:
            print(f"  ✗ Download failed: {exc} - skipping this file.")
            continue

        if not _validate_nc(dest_path):
            print(f"  ✗ Invalid NetCDF after download: {fname} - skipping.")
            try:
                os.remove(dest_path)
            except OSError:
                pass
            continue

        _ = accumulate_one_file(dest_path)

        try:
            os.remove(dest_path)
        except OSError:
            pass

    if valid_days == 0:
        print("  ✗ No valid productivity data could be accumulated.")
        return False

    print(f"  ✔ {valid_days} valid daily files accumulated.")

    # ------------------------------------------------------------------
    # Build output NetCDF from monthly CDL template
    # ------------------------------------------------------------------
    try:
        temp_nc, _ = prepare_output_template(
            cfg=cfg,
            paths=paths,
            tmp_dir=tmp_dir,
            sensor=sensor,
            dtype=dtype,
            year=year,
            month=month,
            ofile=ofile,
        )
    except Exception as exc:
        print(f"  ✗ {exc}")
        return False

    # Write monthly mean in row chunks
    now = datetime.now()
    try:
        with Dataset(temp_nc, "a", format="NETCDF4") as nc:
            prod_out = nc["productivity"]

            fill_val = float(getattr(prod_out, "_FillValue", -999.0))

            for row0 in range(0, nlat, chunk_rows):
                row1 = min(row0 + chunk_rows, nlat)
                sum_chunk = pp_sum[row0:row1, :]
                count_chunk = pp_count[row0:row1, :]

                out_chunk = np.full((row1 - row0, nlon), fill_val, dtype=np.float32)
                valid = count_chunk > 0
                if np.any(valid):
                    out_chunk[valid] = (sum_chunk[valid] / count_chunk[valid]).astype(np.float32)

                prod_out[0, row0:row1, :] = out_chunk

            write_monthly_metadata(nc, sensor, dtype, year, month, valid_days, now, cfg)
    except Exception as exc:
        print(f"  ✗ Failed writing monthly NetCDF: {exc}")
        return False

    # ------------------------------------------------------------------
    # Compress and upload
    # ------------------------------------------------------------------
    compressed_nc = os.path.join(tmp_dir, ofile)
    nccopy_bin = os.path.join(paths["nco_dir"], "nccopy")
    level = proc_cfg["nccopy_compression"]

    result = subprocess.run(
        [nccopy_bin, f"-d{level}", temp_nc, compressed_nc],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  ✗ nccopy failed: {result.stderr.strip()}")
        return False

    try:
        os.remove(temp_nc)
    except OSError:
        pass

    try:
        gcs_upload(compressed_nc, out_gcs)
    except RuntimeError as exc:
        print(f"  ✗ GCS upload failed: {exc}")
        return False

    try:
        os.remove(compressed_nc)
    except OSError:
        pass

    print(f"  ✔ {ofile} complete  ({valid_days} days averaged).")
    return True


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-y",
        "--year",
        type=int,
        required=True,
        help="Year of the month to composite (e.g. 2025)",
    )
    parser.add_argument(
        "-m",
        "--month",
        type=int,
        required=True,
        choices=range(1, 13),
        metavar="MONTH",
        help="Month to composite (1-12)",
    )
    parser.add_argument(
        "-s",
        "--sensor",
        required=True,
        choices=["snpp", "noaa20", "noaa21"],
        help="Sensor to process",
    )
    parser.add_argument(
        "-t",
        "--dtype",
        required=True,
        choices=["nrt", "sq"],
        help="Data type: nrt or sq",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=str(REPO_ROOT / "config" / "config.yml"),
        help="Path to config.yml",
    )
    parser.add_argument(
        "-o",
        "--overwrite",
        action="store_true",
        help="Overwrite existing GCS output",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    cfg = load_config(args.config)
    paths = build_paths(cfg)

    if args.sensor not in cfg.get("sensors", {}):
        sys.exit(f"Sensor '{args.sensor}' not found in config.")

    scratch = paths["scratch"]
    os.makedirs(scratch, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=scratch, prefix="npp_monthly_") as tmp_dir:
        ok = composite_month(
            sensor=args.sensor,
            dtype=args.dtype,
            year=args.year,
            month=args.month,
            overwrite=args.overwrite,
            cfg=cfg,
            paths=paths,
            tmp_dir=tmp_dir,
        )

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()