# charm_helper_functions.py

from __future__ import annotations
from numpy.typing import NDArray
import argparse
import logging
import os
import subprocess
from datetime import datetime, timedelta, date, time
from pathlib import Path
import numpy as np
import numpy.ma as ma
from dateutil.parser import parse
import shutil
from netCDF4 import Dataset
from scipy.interpolate import griddata, RegularGridInterpolator
import yaml
import types
from typing import List, Union, Optional, Iterable, Sequence, Tuple, Pattern, Any
import re
from dataclasses import dataclass


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def results_exist_in_gcs(date_str: str, prod_bucket: str, root: str) -> bool:
    """Check if 0day forecast already published to prod bucket for this date."""
    if not prod_bucket:
        return False
    gcs_path = f"gs://{prod_bucket}/{root}/wvcharmV4_0day/{date_str[:4]}/charm_v3_forecast_0day_{date_str}.nc"
    result = subprocess.run(
        ["gsutil", "-q", "stat", gcs_path],
        capture_output=True
    )
    exists = result.returncode == 0
    if exists:
        logger.info("Found existing results in bucket: %s", gcs_path)
    return exists

def delete_files_in_directory(
    directory_path: Union[str, Path],
    dry_run: bool = False,
    pattern: Union[str, Pattern[str], None] = None,
    recursive: bool = False
) -> None:
    """Delete files in a directory whose names match a regex pattern."""
    directory = Path(directory_path)

    if not directory.is_dir():
        logger.error("'%s' is not a valid directory.", directory)
        raise NotADirectoryError(f"'{directory}' is not a valid directory.")

    regex = re.compile(pattern) if isinstance(pattern, str) else pattern
    iterator = directory.rglob("*") if recursive else directory.iterdir()

    logger.info(
        "Starting cleanup in %s (recursive=%s, dry_run=%s, pattern=%s)",
        directory, recursive, dry_run, pattern or "None"
    )

    deleted_count = 0
    error_count = 0

    for item in iterator:
        if not item.is_file():
            continue
        if regex and not regex.search(item.name):
            continue

        try:
            if dry_run:
                logger.info("[DRY-RUN] Would delete: %s", item)
            else:
                item.unlink()
                deleted_count += 1
                logger.info("Deleted: %s", item)
        except PermissionError as e:
            error_count += 1
            logger.error("Permission denied deleting %s: %s", item, e)
        except OSError as e:
            error_count += 1
            logger.error("Error deleting %s: %s", item, e)

    if dry_run:
        logger.info("Dry-run complete. No files were deleted.")
    elif deleted_count == 0 and error_count == 0:
        logger.info("No matching files found to delete in %s.", directory)
    elif error_count > 0:
        logger.warning("Deleted %d files, but %d errors occurred.", deleted_count, error_count)
    else:
        logger.info("Successfully deleted %d files in %s", deleted_count, directory)


@dataclass
class Config:
    sat_vars: types.SimpleNamespace
    gridding: types.SimpleNamespace
    wcofs_st_config: dict
    eof_nc: types.SimpleNamespace

    eof_dir: Path
    cwutil_dir: Path
    erddap_dir: Path
    nasa_base_url: str

    base_dir: Path
    data_dir: Path
    res_dir: Path
    work_dirs: Path
    config_dirs: Path

    # Cloud/publish extras (optional)
    stage_root: Path = Path("/tmp/charm")
    history_days_required: int = 180
    erdwork_bucket: str = ""
    erdprod_bucket: str = ""
    publish_targets: dict = None
    c_harm: dict = None
    work_l2_prefix: str = "edge/c-harm/processed_nasa_data"

def load_config(filepath: str) -> Config:
    """Load the multi-document CHARM YAML config into a typed runtime object."""
    with open(filepath, "r") as f:
        yaml_docs_list = list(yaml.safe_load_all(f))

    base_dirs = None
    sat_vars = None
    io_paths = None
    gridding = None
    eof_nc = None
    wcofs = None
    cloud_doc: dict[str, Any] | None = None

    for doc in yaml_docs_list:
        if not isinstance(doc, dict):
            continue
        if "eof" in doc:
            base_dirs = types.SimpleNamespace(**doc)
        elif "gappy" in doc:
            sat_vars = types.SimpleNamespace(**doc)
        elif "erddap" in doc:
            io_paths = types.SimpleNamespace(**doc)
        elif "chl_pattern" in doc:
            gridding = types.SimpleNamespace(**doc)
        elif "first" in doc:
            eof_nc = types.SimpleNamespace(**doc)
        elif "st_0" in doc:
            wcofs = doc
        elif "ERDWORK_BUCKET" in doc:
            cloud_doc = doc

    if base_dirs is None or sat_vars is None or io_paths is None or gridding is None or eof_nc is None or wcofs is None:
        raise ValueError(f"Config file missing one or more required YAML docs: {filepath}")

    eof_dir = Path(base_dirs.eof)
    cwutil_dir = Path(base_dirs.cwutil)
    erddap_dir = Path(io_paths.erddap)
    nasa_base_url = io_paths.nasa_base

    base_dir = Path(base_dirs.home)
    data_dir = base_dir / base_dirs.dirs_endpts["data_dir"]
    config_dirs = base_dir / base_dirs.dirs_endpts["config_dirs"]
    res_dir = base_dir / base_dirs.dirs_endpts["resources_dir"]
    work_dirs = data_dir / base_dirs.dirs_endpts["work_dirs"]

    stage_root = Path("/tmp/charm")
    history_days_required = 180
    erdwork_bucket = ""
    erdprod_bucket = ""
    publish_targets = None
    work_l2_prefix = "edge/c-harm/processed_nasa_data"
    c_harm = None

    if cloud_doc:
        stage_root = Path(cloud_doc.get("CHARM_LOCAL_STAGE_ROOT", "/tmp/charm"))
        history_days_required = int(cloud_doc.get("CHARM_HISTORY_DAYS_REQUIRED", 180))
        erdwork_bucket = str(cloud_doc.get("ERDWORK_BUCKET", ""))
        erdprod_bucket = str(cloud_doc.get("ERDPROD_BUCKET", ""))
        publish_targets = cloud_doc.get("PUBLISH_TARGETS", None)
        work_l2_prefix = cloud_doc.get("WORK_L2_PREFIX", "edge/c-harm/processed_nasa_data")
        # C_HARM lives in the same YAML doc as ERDWORK_BUCKET
        c_harm = cloud_doc.get("C_HARM", None)

    # Cloud Run safety: allow runtime override of stage_root
    env_stage_root = os.environ.get("CHARM_LOCAL_STAGE_ROOT") or os.environ.get("STAGE_ROOT")
    if env_stage_root:
        stage_root = Path(env_stage_root)

    # Verify stage_root is writable
    stage_root.mkdir(parents=True, exist_ok=True)
    try:
        probe = stage_root / f".write_probe_{os.getpid()}"
        probe.write_text("ok")
        probe.unlink()
    except Exception as e:
        raise PermissionError(
            f"stage_root is not writable: {stage_root} (uid={os.getuid()} gid={os.getgid()})"
        ) from e

    return Config(
        sat_vars=sat_vars,
        gridding=gridding,
        wcofs_st_config=wcofs,
        eof_nc=eof_nc,
        eof_dir=eof_dir,
        cwutil_dir=cwutil_dir,
        erddap_dir=erddap_dir,
        nasa_base_url=nasa_base_url,
        base_dir=base_dir,
        data_dir=data_dir,
        res_dir=res_dir,
        work_dirs=work_dirs,
        config_dirs=config_dirs,
        stage_root=stage_root,
        history_days_required=history_days_required,
        erdwork_bucket=erdwork_bucket,
        erdprod_bucket=erdprod_bucket,
        publish_targets=publish_targets,
        c_harm=c_harm,
        work_l2_prefix=work_l2_prefix,
    )


def parse_args(argv: List[str] | None) -> Tuple[datetime, bool, bool]:
    """Parse date, backfill, and overwrite flags for one CHARM run."""
    parser = argparse.ArgumentParser(description="C-HARM nowcast/backfill control utility.")

    parser.add_argument(
        "-d", "--date",
        required=False,
        metavar="DATE",
        help="Start date (required only when --backfill is used). Ignored when running nowcast."
    )

    parser.add_argument(
        "-b", "--backfill",
        action="store_true",
        help="Enable backfill mode. If present, --date becomes required."
    )

    parser.add_argument(
        "-o", "--overwrite",
        action="store_true",
        help="Overwrite existing results if they already exist."
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.backfill:
        if args.date is None:
            parser.error("--date is required when --backfill is used")
        try:
            start_date_obj = parse(args.date)
        except Exception as e:
            parser.error(f"Invalid date format for --date: {e}")
    else:
        # Cloud Run-safe: use UTC in nowcast mode
        start_date_obj = datetime.utcnow()

    return start_date_obj, args.backfill, args.overwrite


def ensure_dirs(*dirs: Path) -> None:
    """Create one or more directories if they do not already exist."""
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)


def run_cmd(cmd: Union[List[str], str], msg: Optional[str] = None) -> None:
    """Run a command and raise RuntimeError when it exits unsuccessfully."""
    printable = " ".join(cmd) if isinstance(cmd, list) else cmd
    log_message = f"[run_cmd] {msg}: {printable}" if msg else f"[run_cmd] {printable}"
    logger.info(log_message)

    subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        check=True,
        capture_output=False,
        text=True,
    )


def sync_processed_l3_from_work_bucket(processed_viirs_dir: Path, config) -> None:
    """
    Pull the rolling L3 history cache from the WORK bucket into local processed_viirs_dir.

    Requires:
      - google-cloud-cli in image (gsutil)
      - Cloud Run service account has storage.objects.list/get on ERDWORK_BUCKET

    Controlled by env var:
      SYNC_L3_FROM_WORK_BUCKET=1
    """
    if os.getenv("SYNC_L3_FROM_WORK_BUCKET", "1") != "1":
        logger.info("SYNC_L3_FROM_WORK_BUCKET disabled; skipping L3 cache sync")
        return

    bucket = getattr(config, "erdwork_bucket", "") or ""
    if not bucket:
        logger.warning("No erdwork_bucket in config; skipping L3 cache sync")
        return

    # Prefer config key if present; otherwise fall back to the common prefix you used.
    # (Adjust the default if your YAML uses a different name.)
    prefix = ""
    if hasattr(config, "publish_targets") and config.publish_targets:
        # optional: if you store work prefix here
        pass

    # These are common in your YAML: WORK_L2_PREFIX = "edge/c-harm/processed_nasa_data"
    # If your Config object exposes it differently, map it in load_config().
    work_l2_prefix = getattr(config, "work_l2_prefix", "edge/c-harm/processed_nasa_data")

    gcs_src = f"gs://{bucket}/{work_l2_prefix}"
    processed_viirs_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Syncing L3 cache: %s -> %s", gcs_src, processed_viirs_dir)
    cmd = ["gsutil", "-m", "rsync", "-r", gcs_src, str(processed_viirs_dir)]
    run_cmd(cmd, msg="gsutil rsync L3 cache")


def publish_results_to_gcs(results_year_dir: Path, config: Config, interval_days: int = 1) -> None:
    """
    Publish archived netCDFs to GCS.
    Structure: gs://<bucket>/wvcharmV4_<Nday>/<year>/<file>.nc
    """
    if not config.publish_targets or "prod" not in config.publish_targets:
        logger.warning("publish_results_to_gcs: no publish_targets.prod in config; skipping")
        return

    prod = config.publish_targets["prod"]
    bucket = prod.get("bucket", config.erdprod_bucket)

    if not config.c_harm:
        logger.warning("publish_results_to_gcs: missing C_HARM in config; skipping")
        return

    year = results_year_dir.name
    nc_files = sorted(results_year_dir.glob("charm_v3_forecast_*.nc"))
    if not nc_files:
        logger.warning("publish_results_to_gcs: no forecast files found in %s", results_year_dir)
        return

    for nc_file in nc_files:
        # Parse day offset from filename e.g. charm_v3_forecast_0day_20260302.nc -> "0day"
        parts = nc_file.stem.split("_")
        try:
            day_str = next(p for p in parts if p.endswith("day"))
        except StopIteration:
            logger.warning("Could not parse day offset from %s, skipping", nc_file.name)
            continue

        # gs://<bucket>/edge/charm_v4/wvcharmV4_<Nday>/<year>/<file>.nc
        root = prod.get("root", "edge")
        gcs_dst = f"gs://{bucket}/{root}/wvcharmV4_{day_str}/{year}/{nc_file.name}"

        logger.info("Publishing: %s -> %s", nc_file.name, gcs_dst)

        cmd = ["gsutil", "cp", str(nc_file), gcs_dst]
        run_cmd(cmd, msg=f"gsutil publish wvcharmV4_{day_str}")


def charms_nc3(
    pn: np.ndarray,
    pd: np.ndarray,
    pc: np.ndarray,
    model_time: Sequence[float],
    schla: np.ndarray,
    sr488: np.ndarray,
    sr555: np.ndarray,
    model_salt: np.ndarray,
    model_temp: np.ndarray,
    data_dir: str | Path,
    ref_dir: str | Path,
    saltmins: Sequence[float],
    tempmins: Sequence[float],
) -> list[str]:
    """
    Generate and finalize daily **C-HARM v3** forecast NetCDF files.

    This function creates four NetCDF files (nowcast + 3 forecast days) using a CDL
    template and populates them with modeled probability and oceanographic data.
    It performs masking, metadata population, and compression to produce CF-compliant
    outputs suitable for publication or ERDDAP ingestion.

    The procedure:
        1. Generate four NetCDF templates via `ncgen`.
        2. Append daily probability, reflectance, salinity, and temperature fields.
        3. Set valid time metadata and global attributes.
        4. Apply salinity and temperature masking thresholds.
        5. Compress outputs with `nccopy`.

    Args:
        pn (np.ndarray):
            Pseudo-nitzschia probability array with shape ``(time, lat, lon)``.
        pd (np.ndarray):
            Particulate domoic acid probability array with shape ``(time, lat, lon)``.
        pc (np.ndarray):
            Cellular domoic acid probability array with shape ``(time, lat, lon)``.
        model_time (Sequence[float]):
            Sequence of ordinal date values (e.g., from `datetime.date.toordinal`)
            representing each forecast day.
        schla (np.ndarray):
            Chlorophyll concentration array [mg m⁻³], shape ``(time, lat, lon)``.
        sr488 (np.ndarray):
            489 nm reflectance (Rrs_488) array, shape ``(time, lat, lon)``.
        sr555 (np.ndarray):
            556 nm reflectance (Rrs_555) array, shape ``(time, lat, lon)``.
        model_salt (np.ndarray):
            WCOFS salinity array [psu], shape ``(time, lat, lon)``.
        model_temp (np.ndarray):
            WCOFS water temperature array [°C], shape ``(time, lat, lon)``.
        data_dir (str | Path):
            Output directory to store generated and compressed NetCDF files.
        ref_dir (str | Path):
            Directory containing the CDL template file (typically `charm2022_out_tmpl.cdf`).
        saltmins (Sequence[float]):
            Minimum salinity threshold values (per forecast day) for masking invalid data.
        tempmins (Sequence[float]):
            Minimum temperature threshold values (per forecast day) for masking invalid data.

    Returns:
        list[str]:
            A list of filenames (relative to `data_dir`) for the generated NetCDF files.
            The list always contains four entries, one for each forecast day.

            Example::
                [
                    'charm_v3_forecast_0day_20250101.nc',
                    'charm_v3_forecast_1day_20250101.nc',
                    'charm_v3_forecast_2day_20250101.nc',
                    'charm_v3_forecast_3day_20250101.nc'
                ]

    Raises:
        subprocess.CalledProcessError:
            If any of the external commands (`ncgen`, `nccopy`) fail.
        OSError:
            If input or output directories are not writable.
        FileNotFoundError:
            If the CDL template file does not exist in `ref_dir`.

    Notes:
        * Requires the command-line utilities `ncgen` and `nccopy` (NetCDF toolkit).
        * Each output file follows CF-compliant conventions, including proper
          `time_coverage_start` and `time_coverage_end` attributes.
        * Masking is applied to `salinity` and `water_temparture` variables using
          thresholds defined in `saltmins` and `tempmins`.
        * The global `history` attribute documents the DINEOF and forecast workflow.
    """
    data_dir = Path(data_dir)
    ref_dir = Path(ref_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.utcnow()
    mod_time = now.strftime("%Y%m%dT%H%M%SZ")
    history_time = now.strftime("%Y-%m-%d")

    # Convert model_time ordinals → datetime.date objects
    tobj = [date.fromordinal(int(ln)) for ln in model_time]

    # Define templates and CDL file
    template_files = [f"output_day{i}.nc" for i in range(4)]
    cdl_file = ref_dir / "charm2022_out_tmpl.cdf"

    logger.info("Creating NetCDF templates from CDL at %s", cdl_file)
    for tmpl in template_files:
        cmd = [
            "ncgen", "-k", "nc4",
            "-o", str(Path(data_dir) / tmpl),
            str(cdl_file)
        ]
        logger.debug("Running command: %s", " ".join(cmd))
        subprocess.run(
            cmd,
            shell=isinstance(cmd, str),
            check=True,
            capture_output=False,
            text=True,
        )

    titles = ["Nowcast", "1-Day Forecast", "2-Day Forecast", "3-Day Forecast"]
    out_file_names: list[str] = []

    for idx, tmpl in enumerate(template_files):
        tmpl_path = data_dir / tmpl
        logger.info("Appending data → %s", tmpl_path)

        with Dataset(tmpl_path, "a") as nc:
            nc.variables["pseudo_nitzschia"][0, :, :] = pn[idx]
            nc.variables["particulate_domoic"][0, :, :] = pd[idx]
            nc.variables["cellular_domoic"][0, :, :] = pc[idx]
            nc.variables["chla_filled"][0, :, :] = schla[idx]
            nc.variables["r489_filled"][0, :, :] = sr488[idx]
            nc.variables["r556_filled"][0, :, :] = sr555[idx]
            nc.variables["salinity"][0, :, :] = model_salt[idx]
            nc.variables["water_temparture"][0, :, :] = model_temp[idx]

            start_time = datetime.combine(tobj[idx], time(0, 0))
            center_time = datetime.combine(tobj[idx], time(12, 0))
            end_time = start_time + timedelta(days=1)

            start_iso = start_time.strftime("%Y%m%dT%H%M%SZ")
            end_iso = end_time.strftime("%Y%m%dT%H%M%SZ")
            start_epoch = (start_time - datetime(1970, 1, 1)).total_seconds()
            center_epoch = (center_time - datetime(1970, 1, 1)).total_seconds()
            end_epoch = start_epoch + 24 * 3600

            time_var = nc.variables["time"]
            time_var[0] = center_epoch
            time_var.actual_range = [start_epoch, end_epoch]

            nc.date_created = mod_time
            nc.date_issued = mod_time
            nc.date_metadata_modified = mod_time
            nc.date_modified = mod_time
            nc.history = (
                f"{history_time}: DINEOF gap filling was applied to daily NOAA-20 VIIRS "
                "chlorophyll, 556 nm reflectance, and 489 nm reflectance fields "
                "extending back 180 days from today’s date.\n"
                "The gap-filled data plus salinity and temperature data from the WCOFS "
                "model were used as inputs to the C-HARM model to obtain nowcast.\n"
                "ROMS current forecasts were used to advect the NOAA VIIRS data 1, 2, "
                "and 3 days into the future. Data gaps resulting from the advection "
                "were filled with a second DINEOF.\n"
                "The advected gap-filled data plus salinity and temperature WCOFS "
                "forecast data were used as inputs to the C-HARM model to obtain "
                "forecasts for 1, 2, and 3 days into the future."
            )
            nc.time_coverage_start = start_iso
            nc.time_coverage_end = end_iso
            nc.id = f"charmForecast{idx}dayV3"

            time_var.comment = (
                "The day represented by the forecasts"
                if idx != 0
                else "The day represented by the nowcasts"
            )
            time_var.long_name = (
                "Centered Forecast Time" if idx != 0 else "Centered Nowcast Time"
            )

            nc.title = (
                "C-HARM v3.1 {}, Pseudo-nitzschia, cellular domoic acid, "
                "and particulate domoic acid probability, California and "
                "Southern Oregon coast, 2022-present".format(titles[idx])
            )

        logger.debug("Applying salinity and temperature masks for day %d", idx)
        with Dataset(tmpl_path, "a") as nc:
            salt = ma.masked_invalid(nc.variables["salinity"][:, :, :])
            salt = ma.filled(salt, fill_value=saltmins[idx])
            nc.variables["salinity"][:, :, :] = ma.masked_where(
                salt <= saltmins[idx], salt
            )

            temp = ma.masked_invalid(nc.variables["water_temparture"][:, :, :])
            temp = ma.filled(temp, fill_value=tempmins[idx])
            nc.variables["water_temparture"][:, :, :] = ma.masked_where(
                temp <= tempmins[idx], temp
            )

        start_str = f"{tobj[idx]:%Y%m%d}"
        out_name = f"charm_v3_forecast_{idx}day_{start_str}.nc"
        cmd = [
            "nccopy", "-d6",
            str(Path(data_dir) / tmpl),
            str(Path(data_dir) / out_name)
        ]
        logger.info("Compressing %s → %s", tmpl, out_name)
        logger.debug("Running command: %s", " ".join(cmd))
        subprocess.run(
            cmd,
            shell=isinstance(cmd, str),
            check=True,
            capture_output=False,
            text=True,
        )
        out_file_names.append(out_name)

    logger.info("C-HARM NetCDF generation completed: %d files created", len(out_file_names))
    return out_file_names


def send_to_erddap(path_file: Path, remotedir: str) -> None:
    """Transfer a local file to multiple remote ERDDAP servers via SCP.

    This function automates uploading a local file to a predefined set of
    remote ERDDAP servers under `/u00/satellite/<remotedir>`.

    Args:
        path_file (Path): Local file to upload. Must exist.
        remotedir (str): Subdirectory name under `/u00/satellite/`
            on the remote servers where the file should be copied.

    Returns:
        None: Performs the file transfers and logs results.

    Example:
        >>> from pathlib import Path
        >>> send_to_erddap(Path("/tmp/my_file.nc"), "daily_products")
        # Uploads to:
        #   cwatch@192.168.31.15:/u00/satellite/daily_products
        #   cwatch@161.55.17.28:/u00/satellite/daily_products
        #   cwatch@192.168.31.27:/u00/satellite/daily_products
    """
    # making sure this is a Path obj
    path_file = Path(path_file)

    if not path_file.exists():
        logger.error("File not found: %s", path_file)
        raise FileNotFoundError(f"Local file not found: {path_file}")

    base_dir = Path("/u00/satellite") / remotedir
    servers = [
        ("192.168.31.15", "cw"),
        ("161.55.17.28", "tds2"),
        ("192.168.31.27", "tds1"),
    ]

    for ip, label in servers:
        dest = f"cwatch@{ip}:{base_dir}"
        cmd = ["scp", str(path_file), dest]

        logger.info("Transferring %s to %s (%s)", path_file.name, dest, label)

        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info("Transfer to %s succeeded. Output: %s", label, result.stdout.strip())
        except subprocess.CalledProcessError as e:
            logger.warning("Transfer to %s failed: %s", label, e.stderr.strip() or e)

# -----------------------------------------------------------------------------
# Particle advection
# -----------------------------------------------------------------------------


def mymove1(
    time: Sequence[float],
    x_pos: NDArray[np.float_],
    y_pos: NDArray[np.float_],
    xarr: NDArray[np.float_],
    yarr: NDArray[np.float_],
    modelu: NDArray[np.float_],
    modelv: NDArray[np.float_],
    t0: float,
    themask: NDArray[np.float_],
) -> Tuple[NDArray[np.float_], NDArray[np.float_], NDArray[np.float_]]:
    """Compute particle trajectories on a lon/lat grid using model u/v.

    Advance particle positions (initialized at the model grid nodes) forward
    in time using the provided model velocity fields and a simple forward-
    Euler integration.

    Args:
        time: 1D sequence of times (same units as returned - typically days as
            ordinal+fraction, e.g. `date.toordinal() + hour/24`). Length `nt`.
        x_pos: 1D array of longitudes (size `nx`) OR 2D grid of longitudes
            (shape `ny,nx`) — used to derive the lon axis.
        y_pos: 1D array of latitudes (size `ny`) OR 2D grid of latitudes
            (shape `ny,nx`) — used to derive the lat axis.
        xarr: 2D longitude grid (`ny, nx`) (usually `np.meshgrid(mylonm, mylatm)[1]`).
        yarr: 2D latitude grid (`ny, nx`)  (usually `np.meshgrid(mylonm, mylatm)[0]`).
        modelu: 3D u-velocity array with shape `(nt, ny, nx)` (degrees/day).
        modelv: 3D v-velocity array with shape `(nt, ny, nx)` (degrees/day).
        t0: initial reference time (kept for signature compatibility; not used
            internally here — `time` controls the integration).
        themask: 2D mask array (`ny, nx`) where `1` means ocean/valid and `0`
            means land/invalid.

    Returns:
        Tuple containing:
        - advected_x: 3D array shape `(nt, ny, nx)` of particle longitudes
        - advected_y: 3D array shape `(nt, ny, nx)` of particle latitudes
        - rtime: 1D numpy array of times (same as the input `time` converted)

    Notes:
        - This function expects `modelu`/`modelv` to be in the same coordinate
          order as `xarr`/`yarr` (lat dimension first, lon second).
        - The interpolation uses `RegularGridInterpolator` with linear method
          and `fill_value=0.0` for velocities outside the grid.
    """
    # convert inputs to numpy arrays
    time = np.asarray(time, dtype=float)
    rtime = time.copy()
    modelu = np.asarray(modelu, dtype=float)
    modelv = np.asarray(modelv, dtype=float)
    themask = np.asarray(themask, dtype=bool)

    # check shapes
    if modelu.ndim != 3 or modelv.ndim != 3:
        raise ValueError("modelu and modelv must be 3D arrays with shape (nt, ny, nx)")
    nt, ny, nx = modelu.shape
    if modelv.shape != modelu.shape:
        raise ValueError("modelu and modelv must have the same shape")

    # derive lon/lat axes (1D) from inputs
    # allow x_pos/y_pos to be either 1D axis arrays or 2D meshgrids
    if x_pos.ndim == 1 and y_pos.ndim == 1:
        lon_axis = np.asarray(x_pos, dtype=float)
        lat_axis = np.asarray(y_pos, dtype=float)
    else:
        # use xarr/yarr to derive axes
        lon_axis = np.asarray(xarr)[0, :].astype(float)
        lat_axis = np.asarray(yarr)[:, 0].astype(float)

    # Ensure axes are strictly increasing; if not, flip arrays to make them so
    # (RegularGridInterpolator requires ascending coordinates)
    lon_asc = lon_axis[0] < lon_axis[-1]
    lat_asc = lat_axis[0] < lat_axis[-1]

    if not lon_asc:
        lon_axis = lon_axis[::-1].copy()
        modelu = modelu[:, :, ::-1]
        modelv = modelv[:, :, ::-1]
        themask = themask[:, ::-1]
        xarr = xarr[:, ::-1]
        logger.debug("Reversed lon axis to ascending order")

    if not lat_asc:
        lat_axis = lat_axis[::-1].copy()
        modelu = modelu[:, ::-1, :]
        modelv = modelv[:, ::-1, :]
        themask = themask[::-1, :]
        yarr = yarr[::-1, :]
        logger.debug("Reversed lat axis to ascending order")

    # initial positions: start particles at model grid nodes (lon/lat)
    # If xarr,yarr are 2D grids, use them; otherwise build meshgrid from axes
    if np.asarray(xarr).ndim == 2 and np.asarray(yarr).ndim == 2:
        pos_lon = np.zeros((nt, ny, nx), dtype=float)
        pos_lat = np.zeros((nt, ny, nx), dtype=float)
        pos_lon[0, :, :] = np.asarray(xarr, dtype=float)
        pos_lat[0, :, :] = np.asarray(yarr, dtype=float)
    else:
        lon_grid, lat_grid = np.meshgrid(lon_axis, lat_axis)
        pos_lon = np.zeros((nt, ny, nx), dtype=float)
        pos_lat = np.zeros((nt, ny, nx), dtype=float)
        pos_lon[0, :, :] = lon_grid
        pos_lat[0, :, :] = lat_grid

    # integration loop: forward Euler using model velocities sampled at particle positions
    # Prepare points layout dims for interpolation
    for k in range(1, nt):
        dt = float(rtime[k] - rtime[k - 1])  # time step in same units as rtime (days)
        # build interpolators for the velocity fields at previous timestep
        # note argument order for RegularGridInterpolator is (lat_axis, lon_axis)
        u_interp = RegularGridInterpolator(
            (lat_axis, lon_axis),
            modelu[k - 1, :, :],
            bounds_error=False,
            fill_value=0.0,
        )
        v_interp = RegularGridInterpolator(
            (lat_axis, lon_axis),
            modelv[k - 1, :, :],
            bounds_error=False,
            fill_value=0.0,
        )

        # sample velocities at current particle positions
        pts = np.column_stack(
            (pos_lat[k - 1].ravel(), pos_lon[k - 1].ravel())
        )  # shape (ny*nx, 2) in (lat, lon) order
        u_vals = u_interp(pts).reshape(ny, nx)
        v_vals = v_interp(pts).reshape(ny, nx)

        # update positions: forward Euler (vel in degrees/day * dt in days)
        new_lon = pos_lon[k - 1] + u_vals * dt
        new_lat = pos_lat[k - 1] + v_vals * dt

        # clip to grid bounds to avoid runaway positions
        lon_min, lon_max = lon_axis[0], lon_axis[-1]
        lat_min, lat_max = lat_axis[0], lat_axis[-1]
        new_lon = np.clip(new_lon, lon_min, lon_max)
        new_lat = np.clip(new_lat, lat_min, lat_max)

        # mask out land/invalid cells (propagate mask)
        new_lon = np.where(themask, new_lon, np.nan)
        new_lat = np.where(themask, new_lat, np.nan)

        pos_lon[k, :, :] = new_lon
        pos_lat[k, :, :] = new_lat

    logger.info(
        "Particle movement complete: produced %d time steps (%d x %d grid)", nt, ny, nx
    )

    logger.info("Subset for 24, 48 and 72 hour forecast")
    subset_idx = [23, 47, -1]
    pos_lon_subset = pos_lon[subset_idx]
    pos_lat_subset = pos_lat[subset_idx]
    rtime_subset = rtime[subset_idx]

    return pos_lon_subset, pos_lat_subset, rtime_subset


# -------- Utility functions --------
def parse_args_OLD(argv: List[str]) -> Tuple[datetime, bool, bool]:
    """Parse command-line arguments for the C-HARM nowcast control script.

    This function processes command-line arguments for controlling
    a C-HARM nowcast or backfill run. It validates and converts
    the provided date string into a ``datetime`` object and
    converts backfill/overwrite options into booleans.

    Args:
        argv (list[str]): Command-line arguments, typically ``sys.argv[1:]``.

    Returns:
        tuple[datetime, bool, bool]:
            - ``start_date_obj``: Parsed start date as a ``datetime`` object.
            - ``backfill``: Whether backfill mode is enabled (``True``/``False``).
            - ``overwrite``: Whether to overwrite existing outputs (``True``/``False``).

    Raises:
        SystemExit: If argument parsing fails or an invalid date is provided.

    Example:
        >>> import sys
        >>> sys.argv = ["script.py", "-d", "2024-08-15", "-b", "yes", "-o"]
        >>> date_obj, backfill, overwrite = parse_args(sys.argv[1:])
        >>> date_obj.year, backfill, overwrite
        (2024, True, True)
    """
    parser = argparse.ArgumentParser(
        description="C-HARM nowcast control utility for managing forecast/backfill runs."
    )

    parser.add_argument(
        "-d", "--date",
        required=True,
        metavar="DATE",
        help="Start date (any parseable format, e.g. '2024-08-15' or 'Aug 15 2024')."
    )

    parser.add_argument(
        "-b", "--backfill",
        required=True,
        choices=["yes", "no"],
        help="Run in backfill mode ('yes') or nowcast mode ('no')."
    )

    parser.add_argument(
        "-o", "--overwrite",
        action="store_true",
        help="Overwrite existing results if they already exist."
    )

    args = parser.parse_args(argv)
    

    # Validate and parse date
    try:
        start_date_obj = parse(args.date)
    except Exception as e:
        parser.error(f"Invalid date format for --date: {e}")

    backfill = args.backfill.lower() == "yes"

    return start_date_obj, backfill, args.overwrite
    

def parse_args(argv: List[str]) -> Tuple[datetime, bool, bool]:
    """Parse command-line arguments for the C-HARM nowcast control script.

    This version makes ``--date`` required only when ``--backfill`` is supplied.
    When running in nowcast mode (no ``--backfill`` flag), the date is ignored
    even if provided, and ``datetime.now()`` is used automatically.

    Args:
        argv (list[str]): Command-line arguments, typically ``sys.argv[1:]``.

    Returns:
        tuple[datetime, bool, bool]:
            - start_date_obj: ``datetime`` object representing the selected date
              (parsed from --date when backfilling, or ``datetime.now()`` otherwise).
            - backfill: ``True`` if ``--backfill`` flag is present.
            - overwrite: ``True`` if ``--overwrite`` was supplied.

    Raises:
        SystemExit: On invalid arguments or date format.
    """
    parser = argparse.ArgumentParser(
        description="C-HARM nowcast/backfill control utility."
    )

    parser.add_argument(
        "-d", "--date",
        required=False,
        metavar="DATE",
        help="Start date (required only when --backfill is used). "
             "Ignored when running in nowcast mode."
    )

    parser.add_argument(
        "-b", "--backfill",
        action="store_true",
        help="Enable backfill mode. If present, --date becomes required."
    )

    parser.add_argument(
        "-o", "--overwrite",
        action="store_true",
        help="Overwrite existing results if they already exist."
    )

    args = parser.parse_args(argv)

    # ------------------------------------------------------------------
    # Handle date logic
    # ------------------------------------------------------------------

    if args.backfill:
        # DATE REQUIRED
        if args.date is None:
            parser.error("--date is required when --backfill is used")

        # Parse provided date
        try:
            start_date_obj = parse(args.date)
        except Exception as e:
            parser.error(f"Invalid date format for --date: {e}")

    else:
        # NOWCAST MODE
        start_date_obj = datetime.now()

    return start_date_obj, args.backfill, args.overwrite


def ensure_dirs(*dirs: Path) -> None:
    """
    Ensure that one or more directories exist, creating them if necessary.

    This function takes any number of directory paths and ensures each exists.
    If a directory or any of its parent directories do not exist, they are created.
    If the directory already exists, nothing is changed.

    Args:
        *dirs (Path): One or more `pathlib.Path` objects representing directories
            that should be created if missing.

    Returns:
        None

    Raises:
        OSError: If a directory cannot be created due to permissions or filesystem issues.

    Example:
        >>> from pathlib import Path
        >>> ensure_dirs(Path("/tmp/data/output"), Path("/tmp/logs"))
        >>> Path("/tmp/data/output").exists()
        True
        >>> Path("/tmp/logs").exists()
        True

    Notes:
        - This function uses `mkdir(parents=True, exist_ok=True)`, so it will not
          raise an error if the directories already exist.
        - It’s safe to call repeatedly in parallel scripts.
    """
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        logger.debug("Ensured directory exists: %s", d)


def run_cmd(
    cmd: Union[List[str], str],
    msg: Optional[str] = None
) -> None:
    """Execute a shell command with structured logging and error handling.

    This utility function runs a shell command either as a list (recommended)
    or as a raw string, logs the command being executed, and raises an error if
    the command fails. It is designed to integrate cleanly into automated
    workflows and processing pipelines by providing consistent, human-readable
    logs.

    Args:
        cmd (list[str] | str):
            The command to execute.
            - If provided as a list, each argument should be a separate list element
              (e.g., `["ls", "-l", "/tmp"]`).
            - If provided as a string, the command is executed in a shell
              (e.g., `"ls -l /tmp"`).
        msg (str, optional):
            A short descriptive message to include in the log entry, such as
            `"Running CHARM post-processing"`. Defaults to None.

    Returns:
        None: This function performs side effects only (logs and executes the command).

    Raises:
        subprocess.CalledProcessError:
            If the command returns a non-zero exit status, indicating failure.

    Logs:
        INFO: Logs the command being executed and any provided message.
        ERROR: Logs any failed command execution with exception details.

    Example:
        >>> run_cmd(["ls", "-l", "/data"])
        INFO:root:[run_cmd] ls -l /data
        # (lists files)

        >>> run_cmd("echo 'Processing complete'", msg="Pipeline finished")
        INFO:root:[run_cmd] Pipeline finished: echo 'Processing complete'
        Processing complete
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    #printable = " ".join(cmd) if isinstance(cmd, list) else cmd
    printable = cmd[0] if isinstance(cmd, list) else cmd
    log_message = f"[run_cmd] {msg}: {printable}" if msg else f"[run_cmd] {printable}"
    logging.info(log_message)

    try:
        subprocess.run(
            cmd,
            shell=isinstance(cmd, str),
            check=True,
            capture_output=False,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with exit code {e.returncode}: {printable}")
        raise


def run_cmd_parallel(
    cmd: list[str] | str,
    msg: str | None = None,
    check: bool = True,
    capture_output: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run a shell command with logging and error handling.

    Args:
        cmd: Command to run, either as a list of arguments or a string.
        msg: Optional descriptive message to log before running the command.
        check: If True, raise a CalledProcessError on nonzero exit.
        capture_output: If True, capture stdout/stderr instead of streaming.
        cwd: Working directory to run the command in.

    Returns:
        subprocess.CompletedProcess: Completed process object.

    Raises:
        subprocess.CalledProcessError: If the command fails and check=True.
    """
    printable = " ".join(cmd) if isinstance(cmd, list) else cmd
    if msg:
        logger.info("[run_cmd_parallel] %s: %s", msg, printable)
    else:
        logger.info("[run_cmd_parallel] %s", printable)

    return subprocess.run(
        cmd,
        shell=isinstance(cmd, str),  # shell only if string provided
        check=check,
        capture_output=capture_output,
        text=True,
        cwd=cwd,
    )


# -------- Core processing functions --------
def list_local_l3_files(l3_dir: Path) -> List[str]:
    """Return filenames (not paths) of local L3 files with 'l2toL3.nc4' in name."""
    logger.debug("Listing local L3 files in %s", l3_dir)
    files = []
    if not l3_dir.exists():
        return files
    for root, _, filenames in os.walk(l3_dir):
        for fn in filenames:
            if "l2toL3.nc4" in fn:
                files.append(fn)
    logger.info("Found %d local L3 files", len(files))
    return files


def needed_l3_filenames_for_period(reference_date: datetime, days: int = 180) -> List[str]:
    """Return list of expected L3 filenames for `days` prior to reference (inclusive)."""
    names = []
    for d in range(0, days + 1):
        dt = reference_date - timedelta(days=d)
        names.append(f"vcharm_{dt:%Y%m%d}_l2toL3.nc4")
    return names


def create_mod_nc_template(
    work_l3_dir: Path,
    res_dir: Path,
    template_name: str = "viirs_L3.cdl"
) -> Path:
    """
    Create a `mod_nc.nc4` file from a CDL template using the `ncgen` command.

    This function prepares the working and resource directories, ensures they
    exist, and runs `ncgen` to compile a NetCDF file (`mod_nc.nc4`) from a
    specified CDL template.

    Args:
        work_l3_dir (Path): Directory where the generated `mod_nc.nc4` will be placed.
        res_dir (Path): Directory containing the CDL template file.
        template_name (str, optional): Name of the CDL template file within `res_dir`.
            Defaults to `"viirs_L3.cdl"`.

    Returns:
        Path: Full path to the newly created `mod_nc.nc4` file.

    Raises:
        FileNotFoundError: If the template file does not exist.
        RuntimeError: If the `ncgen` command fails.

    Example:
        >>> from pathlib import Path
        >>> mod_nc = create_mod_nc_template(Path("/tmp/work/L3"), Path("/resources"))
        >>> mod_nc
        PosixPath('/tmp/work/L3/mod_nc.nc4')

    Notes:
        - This function depends on an external `ncgen` executable (NetCDF toolkit).
        - The helper `run_cmd()` should handle logging and error propagation.
        - The `ensure_dirs()` helper ensures both directories exist before running `ncgen`.
    """
    mod_nc = work_l3_dir / "mod_nc.nc4"
    template = res_dir / template_name

    if not template.exists():
        logger.error("Template file not found: %s", template)
        raise FileNotFoundError(f"Template file not found: {template}")

    ensure_dirs(work_l3_dir, res_dir)
    run_cmd(["ncgen", "-o", str(mod_nc), str(template)], msg="Create mod_nc template")

    logger.info("Created mod_nc template file: %s", mod_nc)
    return mod_nc


def archive_charm_outputs(
  charms_out_files: Iterable[Union[str, Path]],
  work_dir: Union[str, Path],
  results_dir: Union[str, Path],
  now_satellite: datetime
) -> None:
    """Archive CHARM model output files into a year-based results directory.

    Moves one or more CHARM-generated output files from a working directory into
    a structured archive organized by year. The subdirectory name is derived
    from the `now_satellite` datetime year (e.g., "2025"). If the destination
    directory does not exist, it is created automatically.

    The function uses Python's ``logging`` module for detailed progress and
    error reporting. Missing files are skipped gracefully, and existing
    destination files are replaced safely.

    Args:
        charms_out_files (Iterable[str | Path]):
            List of CHARM output filenames or paths to move.
        work_dir (str | Path):
            Directory containing the CHARM output files to be archived.
        results_dir (str | Path):
            Base results directory where archived files are stored.
            A year-specific subdirectory (e.g., "2025") will be created here.
        now_satellite (datetime):
            Datetime object used to extract the satellite year for the archive
            subdirectory name.

    Returns:
        None: This function does not return a value; it performs file operations
        and logs results.

    Logs:
        INFO: Successful file moves and overall progress.
        WARNING: Missing files that were skipped.
        ERROR: Failed move operations due to exceptions.

    Example:
        >>> from datetime import datetime
        >>> archive_charm_outputs(
        ...     charms_out_files=["now_bf_chlor_a_v4.nc", "now_bf_Rrs_489_v4.nc"],
        ...     work_dir="/home/cwatch/work",
        ...     results_dir="/home/cwatch/results",
        ...     now_satellite=datetime(2025, 10, 7)
        ... )
        INFO:root:Archiving 2 CHARM output files to /home/cwatch/results/2025
        INFO:root:(1/2) Moving /home/cwatch/work/now_bf_chlor_a_v4.nc -> /home/cwatch/results/2025/now_bf_chlor_a_v4.nc
        INFO:root:(2/2) Moving /home/cwatch/work/now_bf_Rrs_489_v4.nc -> /home/cwatch/results/2025/now_bf_Rrs_489_v4.nc
        INFO:root:✅ Archiving complete.
    """
    # Ensure logging is configured
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    work_dir = Path(work_dir)
    results_dir = Path(results_dir)
    year_dir = results_dir / str(now_satellite.year)
    year_dir.mkdir(parents=True, exist_ok=True)

    total_files = len(charms_out_files)
    logging.info(f"Archiving {total_files} CHARM output files to {year_dir}")

    for n, fl in enumerate(charms_out_files, start=1):
        src = work_dir / fl
        dst = year_dir / Path(fl).name

        if not src.exists():
            logging.warning(f"Skipping missing file ({n}/{total_files}): {src}")
            continue

        logging.info(f"({n}/{total_files}) Moving {src} -> {dst}")

        try:
            if dst.exists():
                dst.unlink()  # safely remove existing destination file
            shutil.move(str(src), str(dst))
        except Exception as e:
            logging.error(f"Failed to move {src} -> {dst}: {e}")
            continue

    logging.info("✅ Archiving complete.")
