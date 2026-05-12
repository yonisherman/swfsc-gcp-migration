"""Create primary productivity satellite-based products - GCP edition.

Supports:
  Sensors : VIIRS SNPP (snpp), NOAA-20 (noaa20), NOAA-21 (noaa21)
  Types   : Near Real Time (nrt), Science Quality (sq)

Per-date workflow
-----------------
1.  Query NASA OceanColor API for chl / par / sst file URLs.
2.  Download each raw file from NASA to local scratch.
3.  Load arrays, compute PP using Behrenfeld & Falkowski 1997 (VGPM).
4.  Write compressed output NetCDF to local scratch.
5.  Upload final NetCDF to gs://YOUR_GCS_BUCKET/edge/{sensor}/netpp_{dtype}/{year}/
6.  Clean up all local scratch files (inputs are NOT archived to GCS).

Environment variables
---------------------
  NPP_LOCAL_SCRATCH   Local scratch dir on VM (default: /tmp/npp_scratch)
  NPP_NCO_DIR         Directory containing ncgen / nccopy binaries
  NPP_PYTHON          Path to python binary
"""

__author__ = "Dale Robinson, Madison Richardson"
__credits__ = ["Isaac Schroeder", "Jesse Espinoza"]
__license__ = "GPL"
__version__ = "3.0"
__maintainer__ = "Dale Robinson"
__status__ = "Production"

import argparse
import os
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import numpy as np
import numpy.ma as ma
import pandas as pd
import yaml
from dateutil.parser import parse
from netCDF4 import Dataset

warnings.filterwarnings("ignore")

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Earliest possible date any supported sensor was operational.
# Used as a hard floor when no sensor start_date is configured.
_ABSOLUTE_FLOOR = datetime(2012, 1, 2)


# ---------------------------------------------------------------------------
# Config / env helpers
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load and merge all YAML documents from config file into one dict."""
    with open(config_path, "r") as fh:
        docs = list(yaml.safe_load_all(fh))
    merged = {}
    for doc in docs:
        if doc:
            merged.update(doc)
    return merged


def resolve_env(var: str, default: str | None = None) -> str:
    """Return env var value, raising clearly if absent and no default given."""
    val = os.environ.get(var, default)
    if val is None:
        raise EnvironmentError(
            f"Required environment variable '{var}' is not set."
        )
    return val


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
        "repo_root": str(REPO_ROOT),
        "templates_dir": str(REPO_ROOT / "templates"),
    }


def sensor_start_date(cfg: dict, sensor: str) -> datetime:
    """Return the operational start date for a sensor as a naive datetime."""
    raw = cfg.get("sensors", {}).get(sensor, {}).get("start_date")
    if raw:
        return parse(str(raw)).replace(tzinfo=None)
    return _ABSOLUTE_FLOOR


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------

def gcs_upload(local_path: str, gcs_uri: str) -> None:
    """Upload a local file to GCS, raising on failure."""
    result = subprocess.run(
        ["gsutil", "-q", "cp", local_path, gcs_uri],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gsutil upload failed: {result.stderr.strip()}")
    print(f"  ✔ Uploaded to GCS: {os.path.basename(local_path)} → {gcs_uri}")


def gcs_exists(gcs_uri: str) -> bool:
    """Return True if a GCS object exists."""
    result = subprocess.run(
        ["gsutil", "-q", "stat", gcs_uri],
        capture_output=True, text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# NASA download
# ---------------------------------------------------------------------------

def _validate_nc(path: str) -> bool:
    """Return True if the file can be opened as a valid NetCDF/HDF5 file."""
    try:
        with Dataset(path, "r"):
            pass
        return True
    except Exception:
        return False


def download_nasa_file(url: str, dest_dir: str, max_attempts: int = 3) -> str:
    """
    Download a NASA OceanColor file to dest_dir using wget with URS cookies.

    After each download the file is opened with netCDF4 to confirm it is a
    valid HDF5/NetCDF file.  If the file is corrupt (truncated transfer, HTML
    error page served as .nc, etc.) it is deleted and the download is retried
    up to max_attempts times before raising RuntimeError.

    Returns the full local path of the downloaded file.
    Raises RuntimeError if all attempts fail or the file remains invalid.
    """
    cookie = os.path.expanduser("~/.urs_cookies")
    fname = url.split("/")[-1].split("?")[0]
    dest = os.path.join(dest_dir, fname)

    wget_cmd = [
        "wget", "-nv",
        "--load-cookies", cookie,
        "--save-cookies", cookie,
        "--auth-no-challenge=on",
        "--content-disposition",
        "-O", dest,
        url,
    ]

    for attempt in range(1, max_attempts + 1):
        print(f"  ↓ NASA download: {fname}  (attempt {attempt}/{max_attempts})")

        # Remove any leftover partial file before each attempt
        if os.path.exists(dest):
            os.remove(dest)

        result = subprocess.run(wget_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = result.stderr.strip()[:200]
            print(f"  ✗ wget exit {result.returncode}: {err}")
            if attempt == max_attempts:
                raise RuntimeError(f"wget failed for {fname} after {max_attempts} attempts: {err}")
            continue

        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            print(f"  ✗ Downloaded file missing or empty.")
            if attempt == max_attempts:
                raise RuntimeError(f"Download produced empty file for {fname}")
            continue

        if _validate_nc(dest):
            return dest

        # File exists but is not valid HDF5 - log size for diagnostics
        bad_size = os.path.getsize(dest)
        print(f"  ✗ HDF5 validation failed for {fname} ({bad_size:,} bytes) - likely corrupt download.")
        os.remove(dest)
        if attempt == max_attempts:
            raise RuntimeError(
                f"Downloaded file failed HDF5 validation after {max_attempts} attempts: {fname}"
            )

    raise RuntimeError(f"download_nasa_file: exhausted attempts for {fname}")


# ---------------------------------------------------------------------------
# NASA OceanColor file search
# ---------------------------------------------------------------------------

def check_satellite_data(
    st_date_obj: datetime,
    en_date_obj: datetime,
    myvar: str,
    cfg: dict,
    work_dir: str,
    sensor: str,
    dtype: str,
) -> list[str]:
    """
    Query NASA OceanColor API for available file URLs.

    Args:
        st_date_obj: Start of date range.
        en_date_obj: End of date range.
        myvar:       Variable name: 'chl', 'par', or 'sst'.
        cfg:         Loaded config dict.
        work_dir:    Local directory for the API response text file.
        sensor:      Sensor key: 'snpp', 'noaa20', or 'noaa21'.
        dtype:       Data type key: 'nrt' or 'sq'.

    Returns:
        List of .nc URLs matching the request, empty list on failure.
    """
    api_cfg = cfg["nasa_api"]

    # Resolve sensor_id and variable config for the requested dtype
    sensor_id = api_cfg["sensor_ids"][sensor]
    var_cfg = api_cfg["variables"][dtype][sensor][myvar]

    st_str = st_date_obj.strftime("%Y-%m-%d")
    ed_str = en_date_obj.strftime("%Y-%m-%d")

    post_data = (
        f'results_as_file=1&sensor_id={sensor_id}'
        f'&dtid={var_cfg["dtid"]}'
        f'&sdate={st_str} 00:00:00&edate={ed_str} 23:59:59'
        f'&subType=1&addurl=1&prod_id={var_cfg["prod_id"]}'
        f'&resolution_id={api_cfg["resolution_id"]}&period={api_cfg["period"]}'
    )

    os.makedirs(work_dir, exist_ok=True)
    txt_file = os.path.join(work_dir, f"check_{sensor}_{dtype}_{myvar}.txt")

    wget_cmd = (
        f'wget -q --post-data="{post_data}" '
        f'-O "{txt_file}" {api_cfg["url"]}'
    )

    print(f"  Querying NASA API [{sensor}/{dtype}] for {myvar} ({st_str} → {ed_str})")
    ret = subprocess.call(wget_cmd, shell=True)

    if ret != 0 or not os.path.exists(txt_file):
        print(f"  ✗ NASA API query failed for {myvar}.")
        return []

    try:
        urls = pd.read_csv(txt_file, names=["url"])["url"].dropna().tolist()
    except Exception as exc:
        print(f"  ✗ Error reading URL list: {exc}")
        return []

    nc_filter = var_cfg.get("nc_filter", ".nc")
    urls = [u for u in urls if nc_filter in u and u.endswith(".nc")]

    if not urls:
        print(f"  No files found for {myvar} ({st_str} → {ed_str}).")
    return urls


# ---------------------------------------------------------------------------
# Science functions
# ---------------------------------------------------------------------------

def daylength(doy: int, lat: np.ndarray) -> np.ndarray:
    """Daylength in decimal hours (Brock model, Forsythe et al. 1995)."""
    if doy == 366:
        doy = 365
    lat_rad = np.deg2rad(lat)
    decl = 23.45 * np.sin(np.deg2rad(360.0 * (283.0 + doy) / 365.0))
    cos_ha = np.clip(-np.tan(lat_rad) * np.tan(np.deg2rad(decl)), -1.0, 1.0)
    day_len = 2.0 * np.rad2deg(np.arccos(cos_ha)) / 15.0
    day_len = np.where(cos_ha <= -1.0, 24.0, day_len)
    day_len = np.where(cos_ha >= 1.0, 0.0, day_len)
    return day_len


def calculate_PbOpt(sst_data_mod: np.ndarray) -> np.ndarray:
    """Max chlorophyll fixation rate from SST (Behrenfeld & Falkowski 1997)."""
    sst = np.asarray(sst_data_mod, dtype=np.float32)
    coeffs = np.array([
        -3.27e-8, 3.4132e-6, -1.348e-4, 2.462e-3,
        -0.0205, 0.0617, 0.2749, 1.2956,
    ])
    PbOpt = np.polyval(coeffs, sst)
    return float(PbOpt) if np.isscalar(sst_data_mod) else PbOpt


def calculate_Z_eu(chl_input: np.ndarray) -> np.ndarray:
    """Euphotic depth (m) from chl-a (Morel & Berthon 1989)."""
    chl = ma.masked_invalid(chl_input)
    chl_eu = ma.where(chl > 1.0, 40.2 * chl ** 0.5070, 38.0 * chl ** 0.4250)
    Z_eu = ma.where(
        chl_eu > 10.0,
        568.2 * chl_eu ** -0.746,
        200.0 * chl_eu ** -0.293,
    )
    return Z_eu


def calculate_PPeu(
    chl: np.ndarray,
    Pbopt: np.ndarray,
    Z_eu: np.ndarray,
    par: np.ndarray,
    day_len_2d: np.ndarray,
) -> np.ndarray:
    """Daily depth-integrated primary production (VGPM)."""
    par_ratio = par / (par + 4.1 + 1e-6)
    return 0.66125 * Pbopt * par_ratio * Z_eu * chl * day_len_2d


# ---------------------------------------------------------------------------
# NetCDF metadata
# ---------------------------------------------------------------------------

def get_summary_and_history(sensor: str, dtype: str) -> tuple[str, str]:
    su = sensor.upper()
    dtype_label = "Near Real Time (NRT)" if dtype == "nrt" else "Science Quality (SQ)"
    dtype_short  = "NRT" if dtype == "nrt" else "Science Quality"

    summary = (
        f"The Visible and Infrared Imager/Radiometer Suite (VIIRS), {su} "
        f"Primary Productivity product provides {dtype_label} estimates "
        "of net carbon fixation by phytoplankton using the algorithm of "
        "Behrenfeld and Falkowski (1997). Incorporates chlorophyll a, PAR, "
        "and SST. Mapped to a NASA 4km Standard Mapped Image (SMI)."
    )
    history = (
        "Chlorophyll a, PAR, and SST satellite data were applied to the equation "
        "of Behrenfeld and Falkowski, 1997."
    )
    return summary, history


def write_nc_metadata(
    nc_file,
    sensor: str,
    dtype: str,
    current_date: datetime,
    noon_ts: float,
    now: datetime,
    cfg: dict,
) -> None:
    start_year = cfg["sensors"][sensor]["start_year"]
    summary, history = get_summary_and_history(sensor, dtype)
    su = sensor.upper()
    dtype_label = "NRT" if dtype == "nrt" else "Science Quality"

    nc_file.time_coverage_start = current_date.strftime("%Y-%m-%dT00:00:00Z")
    nc_file.time_coverage_end = current_date.strftime("%Y-%m-%dT23:59:59Z")
    nc_file.date_created = now.isoformat("T", "seconds")
    nc_file.platform = su
    nc_file.id = f"productivity_{sensor}_day_{dtype}"
    nc_file.product_name = f"VIIRS {su} Primary Productivity {dtype_label}"
    nc_file.source = f"satellite observations from VIIRS {su}"
    nc_file.acknowledgement = (
        "The project was supported by funding from the Portfolio Management "
        "Branch of NESDIS and NOAA CoastWatch."
    )
    nc_file.contributors = (
        "Dale Robinson, Isaac Shroeder, Ryan Vandermeulen, Jonathan Sherman, "
        "Jesse Espinoza, Madison Richardson"
    )
    nc_file.title = ", ".join([
        "Primary Productivity", f"VIIRS {su}", dtype_label, "Global", "4km",
        f"{start_year}-present (1 Day Composite)",
    ])
    nc_file.summary = summary
    nc_file.history = history


# ---------------------------------------------------------------------------
# Per-date processing
# ---------------------------------------------------------------------------

def process_date(
    current_date: datetime,
    sensor: str,
    dtype: str,
    overwrite: bool,
    cfg: dict,
    paths: dict,
    tmp_dir: str,
) -> bool:
    """
    Full pipeline for one date, sensor, and data type.

    1. Download chl/par/sst from NASA → local scratch
    2. Compute PP
    3. Compress and upload output NetCDF to erddap GCS bucket
    4. Remove all local scratch files (inputs are NOT uploaded to work bucket)

    Returns True on success, False to skip this date.
    """
    proc_cfg = cfg["processing"]
    gcs_cfg = cfg["gcs"]
    date_str = current_date.strftime("%Y%m%d")
    year = current_date.year

    tmpl = proc_cfg["output_filename_template"][dtype]
    ofile = tmpl.format(sensor=sensor, date=date_str)
    
    if dtype == "nrt":
        interval = "1day_nrt"
    else:
        interval = "1day"

    out_prefix = gcs_cfg["erddap_prefix"].format(
    sensor=sensor,
    interval=interval,
    year=year,)

    out_gcs = f"gs://{gcs_cfg['erddap_bucket']}/{out_prefix}/{ofile}"

    if not overwrite and gcs_exists(out_gcs):
        print(f"  ⏭  {ofile} already in GCS, skipping.")
        return True

    print(f"\n{'='*60}")
    print(f"  Processing {current_date.strftime('%Y-%m-%d')}  [{sensor} / {dtype}]")
    print(f"{'='*60}")

    # ------------------------------------------------------------------
    # Download inputs to scratch - these are temp files only, not archived
    # ------------------------------------------------------------------
    local_inputs = {}
    for var in ["chl", "par", "sst"]:
        urls = check_satellite_data(
            current_date, current_date, var, cfg, tmp_dir, sensor, dtype,
        )
        if not urls:
            print(f"  ✗ No {var} data for {date_str} [{sensor}/{dtype}], skipping date.")
            return False
        try:
            fname = urls[0].split("/")[-1].split("?")[0]
            local_path = os.path.join(tmp_dir, fname)
            if os.path.exists(local_path) and _validate_nc(local_path):
                print(f"  ✔ Already in scratch (valid): {fname}")
            else:
                if os.path.exists(local_path):
                    print(f"  ⚠  Cached file failed validation, re-downloading: {fname}")
                    os.remove(local_path)
                local_path = download_nasa_file(urls[0], tmp_dir)
            local_inputs[var] = local_path
        except RuntimeError as exc:
            print(f"  ✗ Download failed for {var}: {exc}")
            return False

    # ------------------------------------------------------------------
    # Build output NetCDF from CDL template first (before loading big arrays)
    # ------------------------------------------------------------------
    cdl_path = os.path.join(
        paths["templates_dir"],
        proc_cfg["cdl_file"].format(sensor=sensor)
    )
    temp_nc = os.path.join(
        tmp_dir,
        proc_cfg["temp_nc"].format(sensor=sensor, dtype=dtype)
    )
    ncgen_bin = os.path.join(paths["nco_dir"], "ncgen")

    if not os.path.exists(cdl_path):
        print(f"  ✗ CDL template not found: {cdl_path}")
        return False

    result = subprocess.run(
        [ncgen_bin, "-o", temp_nc, cdl_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ✗ ncgen failed: {result.stderr.strip()}")
        return False

    # ------------------------------------------------------------------
    # Compute and write PP in row chunks to limit peak RAM.
    # Each chunk loads only CHUNK_ROWS rows of chl/par/sst at a time.
    # Peak RAM ≈ CHUNK_ROWS * ncols * n_arrays * 4 bytes
    # (512 rows × 8640 cols × ~8 arrays × 4 B ≈ 142 MB per chunk)
    # ------------------------------------------------------------------
    CHUNK_ROWS = 512

    try:
        now = datetime.now()
        noon_ts = current_date.replace(hour=12, tzinfo=timezone.utc).timestamp()
        doy = int(current_date.strftime("%j"))

        with Dataset(local_inputs["chl"], "r") as ds_chl,              Dataset(local_inputs["par"], "r") as ds_par,              Dataset(local_inputs["sst"], "r") as ds_sst,              Dataset(temp_nc, "a", format="NETCDF4") as nc:

            lat = nc["latitude"][:]
            lon = nc["longitude"][:]
            nlat = len(lat)
            nlon = len(lon)
            day_len_1d = daylength(doy, lat)  # (nlat,) - compute once

            nc["time"][0] = noon_ts
            write_nc_metadata(nc, sensor, dtype, current_date, noon_ts, now, cfg)

            for row0 in range(0, nlat, CHUNK_ROWS):
                row1 = min(row0 + CHUNK_ROWS, nlat)

                # Load one strip from each input (2D: lat × lon)
                chl_c = ma.masked_invalid(
                    np.asarray(ds_chl["chlor_a"][row0:row1, :], dtype=np.float32)
                )
                par_c = ma.masked_invalid(
                    np.asarray(ds_par["par"][row0:row1, :], dtype=np.float32)
                )
                sst_c = ma.masked_invalid(
                    np.asarray(ds_sst["sst"][row0:row1, :], dtype=np.float32)
                )

                sst_c = ma.masked_where(sst_c < proc_cfg["sst_mask_below"], sst_c)
                sst_clipped = ma.clip(sst_c, proc_cfg["sst_clip_min"], proc_cfg["sst_clip_max"])

                PbOpt_c  = calculate_PbOpt(sst_clipped)
                Z_eu_c   = calculate_Z_eu(chl_c)

                day_len_c = np.outer(day_len_1d[row0:row1], np.ones(nlon, dtype=np.float32))
                PPeu_c    = calculate_PPeu(chl_c, PbOpt_c, Z_eu_c, par_c, day_len_c)
                PPeu_c    = ma.masked_where(PPeu_c <= 0, PPeu_c)

                nc["sea_surface_temperature"][0, row0:row1, :] = sst_c
                nc["chlor_a"][0, row0:row1, :]                 = chl_c
                nc["par"][0, row0:row1, :]                     = par_c
                nc["productivity"][0, row0:row1, :]            = PPeu_c

                # Explicitly release chunk arrays before next iteration
                del chl_c, par_c, sst_c, sst_clipped, PbOpt_c, Z_eu_c, day_len_c, PPeu_c

        print("  ✔ PP computed and written.")

    except Exception as exc:
        print(f"  ✗ Failed during chunked compute/write: {exc}")
        return False

    # Delete input files immediately - free disk and RAM pressure before nccopy
    for p in local_inputs.values():
        try:
            os.remove(p)
        except OSError:
            pass
    local_inputs.clear()

    # ------------------------------------------------------------------
    # Compress
    # ------------------------------------------------------------------
    compressed_nc = os.path.join(tmp_dir, ofile)
    nccopy_bin = os.path.join(paths["nco_dir"], "nccopy")
    result = subprocess.run(
        [nccopy_bin, f"-d{proc_cfg['nccopy_compression']}", temp_nc, compressed_nc],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ✗ nccopy failed: {result.stderr.strip()}")
        return False
    os.remove(temp_nc)

    # ------------------------------------------------------------------
    # Upload output to erddap bucket only (no input archiving)
    # ------------------------------------------------------------------
    try:
        gcs_upload(compressed_nc, out_gcs)
    except RuntimeError as exc:
        print(f"  ✗ Output GCS upload failed: {exc}")
        return False

    # ------------------------------------------------------------------
    # Clean up compressed output (inputs already deleted above)
    # ------------------------------------------------------------------
    try:
        os.remove(compressed_nc)
    except OSError:
        pass

    print(f"  ✔ {ofile} complete.")
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
        "-a", "--start", required=True,
        help="Start date YYYYMMDD or YYYY-MM-DD",
    )
    parser.add_argument(
        "-z", "--end", required=True,
        help="End date YYYYMMDD or YYYY-MM-DD",
    )
    parser.add_argument(
        "-s", "--sensor", required=True,
        choices=["snpp", "noaa20", "noaa21"],
        help="Sensor to process",
    )
    parser.add_argument(
        "-t", "--dtype", required=True,
        choices=["nrt", "sq"],
        help="Data type: nrt (Near Real Time) or sq (Science Quality)",
    )
    parser.add_argument(
        "-c", "--config",
        default=str(REPO_ROOT / "config" / "config.yml"),
        help="Path to config.yml",
    )
    parser.add_argument(
        "-o", "--overwrite", action="store_true",
        help="Overwrite existing GCS output files",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    cfg = load_config(args.config)
    paths = build_paths(cfg)
    start_date = parse(args.start).replace(tzinfo=None)
    end_date = parse(args.end).replace(tzinfo=None)

    if start_date > end_date:
        sys.exit("start date must be before end date")

    if args.sensor not in cfg.get("sensors", {}):
        sys.exit(f"Sensor '{args.sensor}' not found in config sensors block.")

    # Enforce sensor operational start date - silently clip the range
    op_start = sensor_start_date(cfg, args.sensor)
    if end_date < op_start:
        sys.exit(
            f"Requested end date {end_date.date()} is before {args.sensor} "
            f"operational start ({op_start.date()}). Nothing to process."
        )
    if start_date < op_start:
        print(
            f"  ⚠  Clipping start date from {start_date.date()} to "
            f"{op_start.date()} ({args.sensor} operational start)."
        )
        start_date = op_start

    scratch = paths["scratch"]
    os.makedirs(scratch, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=scratch, prefix="npp_") as tmp_dir:
        current_date = start_date
        while current_date <= end_date:
            process_date(
                current_date=current_date,
                sensor=args.sensor,
                dtype=args.dtype,
                overwrite=args.overwrite,
                cfg=cfg,
                paths=paths,
                tmp_dir=tmp_dir,
            )
            current_date += timedelta(days=1)

    print("\nAll dates processed.")


if __name__ == "__main__":
    main()