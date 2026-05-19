"""
Shared utility functions for ERD Cloud Run processing workflows.

This module centralizes configuration loading, path expansion, Google Cloud
Storage helpers, NASA OceanColor file-search utilities, NetCDF aggregation
helpers, and publishing logic used by the MH1, MPOC, and MPIC workflows.

Configuration is loaded from ROYLIB_CONFIG when provided, or from
/app/config/config.yml inside the Cloud Run container.
"""

from __future__ import print_function
from __future__ import division
from future import standard_library

standard_library.install_aliases()
from past.utils import old_div
from datetime import date, datetime, timedelta
from netCDF4 import Dataset, num2date, date2num
import numpy as np
import numpy.ma as ma
import time, subprocess, yaml, os, re
import urllib.request, urllib.parse, urllib.error
import urllib.request, urllib.error, urllib.parse
from pathlib import Path
from google.cloud import storage
from google.api_core.exceptions import PreconditionFailed
from typing import List, Sequence
import urllib.request
from urllib.parse import urlencode


# --- Placeholder pattern like ${KEY}
_VAR = re.compile(r"\$\{([^}]+)\}")


def _expand_once(value, cfg):
    """Expand ${KEY} placeholders using environment variables or config values.

    Environment variables take precedence over keys in cfg. Shell-style
    variables such as $HOME and user paths such as ~ are also expanded.
    """
    if not isinstance(value, str):
        return value

    # Replace ${KEY} with os.environ[KEY] or cfg[KEY] (in that order)
    def repl(m):
        key = m.group(1)
        if key in os.environ:
            return os.environ[key]
        if key in cfg:
            return str(cfg[key])
        # leave as-is if unknown; user may set later
        return m.group(0)

    out = _VAR.sub(repl, value)
    # Also support $HOME and similar shell-style vars
    out = os.path.expandvars(out)
    # Expand ~user or ~
    out = os.path.expanduser(out)
    return out


def _expand_tree(obj, cfg):
    """Recursively expand strings contained in dictionaries and lists."""
    if isinstance(obj, dict):
        return {k: _expand_tree(_expand_once(v, cfg), cfg) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_tree(_expand_once(v, cfg), cfg) for v in obj]
    return _expand_once(obj, cfg)


def _inject_dirs(cfg: dict) -> dict:
    """
    Resolve HOME_DIR + DIRS into absolute paths and inject legacy keys.

    This supports the concise config style:
        HOME_DIR: "/home/erd"
        DIRS:
          LOCAL_STAGE_DIR: "data/staging"
          CDL_DIR: "templates"
          ...

    After injection:
      - cfg["LOCAL_STAGE_DIR"], cfg["CDL_DIR"], etc. exist (absolute paths)
      - cfg["DIRS"][...] entries are normalized to absolute paths too

    If DIRS is absent, this is a no-op (backwards compatible).
    """
    dirs = cfg.get("DIRS")
    home = cfg.get("HOME_DIR")
    if not isinstance(dirs, dict) or not home:
        return cfg

    home_p = Path(str(home)).expanduser()

    for key, rel in dirs.items():
        if not isinstance(rel, str):
            continue

        rel_p = Path(rel).expanduser()
        abs_p = rel_p if rel_p.is_absolute() else (home_p / rel_p)

        # Provide legacy top-level key if missing
        cfg.setdefault(key, str(abs_p))

        # Normalize DIRS entry to absolute (helps bash/yq and debugging)
        cfg["DIRS"][key] = str(abs_p)

    return cfg


def load_cfg(path: str):
    """Load a YAML configuration file and resolve path-style variables.

    The loader supports ${KEY} references to other config values or environment
    variables, expands shell-style variables, and injects absolute paths for
    entries under HOME_DIR + DIRS.
    """
    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    # Seed cfg with raw values so keys are available for expansion
    cfg = dict(raw)

    # NEW: inject DIRS-derived legacy keys early (if DIRS present)
    cfg = _inject_dirs(cfg)

    # Iterate expansion until stable (handles chained refs)
    # e.g., A: "/x", B: "${A}/y", C: "${B}/z"
    for _ in range(5):  # usually resolves in 1–2 passes
        expanded = _expand_tree(cfg, cfg)
        if expanded == cfg:
            break
        cfg = expanded

        # NEW: re-inject in case HOME_DIR or DIRS entries were templated/expanded
        cfg = _inject_dirs(cfg)

    return cfg


# Use env var ROYLIB_CONFIG or default path you prefer:
# Use repo-local config by default, while still allowing ROYLIB_CONFIG to override it.
CFG_PATH = os.environ.get("ROYLIB_CONFIG", "/app/config/config.yml")

if not CFG_PATH:
    raise RuntimeError(
        "No configuration path set. Set ROYLIB_CONFIG or include /app/config/config.yml."
    )

CFG = load_cfg(CFG_PATH)


# Convenience getters
def P(key):  # returns a pathlib.Path
    return Path(CFG[key])


# UPDATED CODE (Uses defaults if keys are missing)
WGET_BIN = CFG.get("WGET_BIN", "/usr/bin/wget")
NCGEN_BIN = CFG.get("NCGEN_BIN", "/usr/bin/ncgen")


def list_bucket_content(bucket_name: str, dir_path: str) -> List[str]:
    """
    Returns a list containing only the base filenames (e.g., 'file.txt' instead of
    'folder/file.txt') within a GCS bucket directory.

    :param bucket_name: The name of the GCS bucket (e.g. CFG.get("ERDPROD_BUCKET") ).
    :param dir_path: The directory or prefix (e.g., "ERDprod/satellite/MH1_NRT/chla/1day") to search within.
    :return: A list of base filenames (strings).

    You must have the 'google-cloud-storage'

    Edited by
    ----------
    Jonathan Sherman — 10/10/2025
    """
    storage_client = storage.Client()

    # Ensure the path ends with a slash to act as a proper prefix filter
    prefix = dir_path
    if prefix and not prefix.endswith('/'):
        prefix += '/'

    # List all blobs under the specified prefix (recursively)
    blobs = storage_client.list_blobs(bucket_name, prefix=prefix)

    file_basenames = []
    for blob in blobs:
        # 1. Skip the directory key itself if it exists as a separate object
        if blob.name == prefix:
            continue

        # 2. Extract the base filename using os.path.basename
        basename = os.path.basename(blob.name)
        file_basenames.append(basename)

    return file_basenames



def get_nasa_l2_flist(day: datetime, param: str) -> list[str]:
    """
    refactor of roylib lecgcy url_lines1 function
    Query NASA OceanColor `file_search/` for MODIS-Aqua L2 filenames over a time window.

    The function takes a Python datetime object (`day`), automatically builds
    the start (`sdate`) and end (`edate`) strings needed for file search

    This version performs a single HTTP POST (form-encoded) to the
    `https://oceandata.sci.gsfc.nasa.gov/file_search/` endpoint, passing a time-bounded
    window (`sdate`, `edate`) and a product selector via `dtid` derived from `param`.

    Parameters
    ----------
    day : datetime.datetime
        Target date for the query. The function will search from 00:00:00 to 23:59:59 UTC.
    param : str, optional
        MODIS data parameter to query (e.g., "OC" or "SST"). mapped to dtid_map internally
        See https://oceandata.sci.gsfc.nasa.gov/file_search/file_search_help/ for dtid list.

    Returns
    -------
    list[str]
        Filenames returned by the service, one per list element (whitespace stripped).

    Raises
    ------
    ValueError
        If `param` is not supported by the internal `dtid_map`.
    urllib.error.URLError
        Network/transport errors (includes timeouts).
    urllib.error.HTTPError
        Non-2xx HTTP status from the server.

    Notes
    -----
    This code was rewritten based on the roylib `url_lines1` function and differs from the original:
      • Uses a POST with form data and a time-bounded window (`sdate`/`edate`)
        instead of building a URL with a wildcard `search=` query. This significantly speeds runtime.
      • Removes the 24-try retry loop and relies on a single request with a 30 s timeout.
        If you still want retries, wrap this function at the call site.
      • Adds a minimal log of the HTTP method, endpoint, and encoded payload.

    Edited by
    ----------
    Jonathan Sherman — 10/23/2025
    """

    # Map product family to dataset id (verify values against OceanColor docs as needed).
    dtid_map = {
        "OC": 1053,
        "SST": 1059,  # adjust if your SST dtid differs
    }

    # Normalize and validate the requested product family.
    param_upper = param.upper()
    if param_upper not in dtid_map:
        raise ValueError(f"Unknown parameter '{param}'. Must be one of: {list(dtid_map)}")
    dtid = dtid_map[param_upper]

    sdate = day.strftime("%Y-%m-%d 00:00:00")
    edate = day.strftime("%Y-%m-%d 23:59:59")

    # Endpoint and POST parameters (spaces in sdate/edate are safely form-encoded).
    base_url = CFG["NASA_FILE_SEARCH_URL"].rstrip("/") + "/"  # ensure trailing slash
    params = {
        "results_as_file": "1",
        "sensor_id": "7",   # 7 = MODIS-Aqua
        "dtid": str(dtid),
        "subType": "1",     # Subscription Type; 1 = Non-Extracted
        "sdate": sdate,
        "edate": edate,
    }

    # Encode POST body as application/x-www-form-urlencoded bytes for urllib.
    payload = urlencode(params)
    data = payload.encode("utf-8")

    # Logging: show what is being posted (useful for reproducibility/debugging).
    print(f"[INFO] POST {base_url}")
    print(f"[INFO] Payload: {payload}")

    # Issue the POST; urllib raises HTTPError/URLError on failure.
    req = urllib.request.Request(base_url, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        lines = response.read().decode("utf-8").splitlines()

    # Normalize to a clean list of non-empty filenames.
    return [ln.strip() for ln in lines if ln.strip()]


# def get_netcdf_file(fileName, dest_path=None):
#     """
#     Download a NetCDF file from the NASA OceanColor server using wget.

#     This function builds and executes a wget command that uses stored URS cookies
#     for authentication. The target file is fetched from the NASA OceanColor `getfile`
#     endpoint and saved to the given destination path.

#     Parameters
#     ----------
#     fileName : str
#         The name of the remote NetCDF file to download (e.g.,
#         'A2023123006000.L2_LAC_SST.nc').
#     dest_path : str or Path, optional
#         Directory or full file path where the file will be saved.
#         - If a directory is given, the file is saved as <dest_path>/<fileName>.
#         - If None, saves to the current working directory (legacy behavior).

#     Returns
#     -------
#     Path
#         Full local path to the downloaded file.

#     Raises
#     ------
#     RuntimeError
#         If the wget command returns a non-zero exit status.

#     Edited by
#     ----------
#     Jonathan Sherman
#         ??? - First refactor. use of subprocess.run and other edits
#          11/05/2025 - Added --no-use-server-timestamps to the wget cmd. This avoid utime errors on gcsfuse mounts.
#                       GCS doesn’t support setting file mtimes, and timestamps are already encoded in filenames.
#     """

#     base_url = CFG["NASA_GETFILE_URL"]
#     wget_bin = CFG["WGET_BIN"]

#     # Resolve destination
#     if dest_path is None:
#         out_path = Path.cwd() / fileName
#     else:
#         dest_path = Path(dest_path)
#         if dest_path.exists() and dest_path.is_dir():
#             out_path = dest_path / fileName
#         else:
#             out_path = dest_path
#         out_path.parent.mkdir(parents=True, exist_ok=True)

#     cmd = [
#         wget_bin,
#         "-4",
#         "--load-cookies", os.path.expanduser("~/.urs_cookies"),
#         "--save-cookies", os.path.expanduser("~/.urs_cookies"),
#         "--auth-no-challenge=on",
#         "--keep-session-cookies",
#         "--content-disposition",
#         "--no-check-certificate",
#         "-O", str(out_path),
#         f"{base_url}{fileName}",
#         "--no-use-server-timestamps"]

#     try:
#         subprocess.run(cmd, check=True)
#         return out_path
#     except subprocess.CalledProcessError as e:
#         raise RuntimeError(f"wget failed for {fileName}: {e}") from e

def stream_netcdf_to_gcs(fileName: str, bucket_name: str, blob_name: str) -> None:
    """
    Download a NetCDF file from the NASA OceanColor server using wget and upload it to GCS.

    This function builds and executes a wget command that uses stored URS cookies
    for authentication. The target file is fetched from the NASA OceanColor `getfile`
    endpoint, written to a local staging directory, uploaded to a Google Cloud Storage
    bucket, and then removed from local disk.

    Parameters
    ----------
    fileName : str
        The name of the remote NetCDF file to download (e.g.,
        'AQUA_MODIS.20251219T044001.L2.SST.NRT.nc').
    bucket_name : str
        Name of the destination GCS bucket (without the ``gs://`` prefix).
    blob_name : str
        Full object path within the destination bucket where the file will be uploaded
        (e.g., 'modisa/data/L2/2025/353/SST/<fileName>').

    Returns
    -------
    None
        The function performs the download, upload, and cleanup as side effects.

    Raises
    ------
    RuntimeError
        If the wget command returns a non-zero exit status or the upload fails.

    Notes
    -----
    - Authentication relies on an existing ``~/.netrc`` and ``~/.urs_cookies`` setup
      for NASA Earthdata / OceanColor access.
    - The wget option ``--no-use-server-timestamps`` is used to avoid utime errors on
      gcsfuse mounts; file timestamps are already encoded in the OceanColor filenames.

    Edited by
    ----------
    Jonathan Sherman
        12/22/2025 - Refactored from a local-only download helper to a
                     download → upload → cleanup workflow for GCS-backed processing.
    """

    base_url = CFG["NASA_GETFILE_URL"].rstrip("/") + "/"
    wget_bin = CFG["WGET_BIN"]

    staging_dir = Path("/home/erd/data/stage_nasa")
    staging_dir.mkdir(parents=True, exist_ok=True)

    out_path = staging_dir / fileName

    cmd = [
        wget_bin,
        "-4",
        "--netrc",
        "--load-cookies", os.path.expanduser("~/.urs_cookies"),
        "--save-cookies", os.path.expanduser("~/.urs_cookies"),
        "--auth-no-challenge=on",
        "--keep-session-cookies",
        "--content-disposition",
        "--no-check-certificate",
        "--no-use-server-timestamps",
        "-O", str(out_path),
        f"{base_url}{fileName}",
    ]

    print(f"[INFO] Downloading {fileName}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"wget failed for {fileName}: {e}") from e

    print(f"[INFO] Uploading {fileName} -> gs://{bucket_name}/{blob_name}")
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(out_path))

    try:
        out_path.unlink()
    except Exception as e:
        print(f"[WARNING] Uploaded but could not delete local file {out_path}: {e}")


def retrieve_new_files(l2_bucket: str, l2_prefix: str, param: str, day) -> bool:
    """
    Check for and upload new MODIS Level-2 files for a given parameter/date directly to GCS.

    This version does NOT write L2 files to local disk. Instead, for each NASA filename
    returned by get_nasa_l2_flist(), it checks for existence in:

        gs://<l2_bucket>/<l2_prefix>/<YYYY>/<DOY>/<param>/<filename>

    Missing files are streamed directly into the destination GCS blob via wget piping.

    Parameters
    ----------
    l2_bucket : str
        GCS bucket name where L2 is stored (e.g., CFG["ERDWORK_BUCKET"])
    l2_prefix : str
        Bucket-relative prefix for L2 root (e.g., CFG["WORK_L2_PREFIX"])
    param : str
        "OC" or "SST"
    day : datetime.date or datetime.datetime
        Target UTC date

    Returns
    -------
    bool
        True if any new file was uploaded; False if all files already existed.
    """
    if param not in {"OC", "SST"}:
        raise ValueError(f"Unsupported param: {param}")

    # Normalize date
    if isinstance(day, datetime):
        day = day.date()
    elif not isinstance(day, date):
        raise ValueError("`day` must be a datetime.date or datetime.datetime")

    yyyy = day.strftime("%Y")
    doy = day.strftime("%j")

    l2_bucket = str(l2_bucket).strip()
    l2_prefix = str(l2_prefix).strip().strip("/")

    storage_client = storage.Client()
    bucket = storage_client.bucket(l2_bucket)

    # Query NASA OceanColor for filenames for this UTC day
    file_list = get_nasa_l2_flist(day=day, param=param)

    uploaded_any = False

    for fname in file_list:
        blob_name = f"{l2_prefix}/{yyyy}/{doy}/{param}/{fname}"

        # Fast existence check in GCS
        blob = bucket.blob(blob_name)
        if blob.exists():
            continue

        try:
            stream_netcdf_to_gcs(fname, l2_bucket, blob_name)
            print(f"Uploaded {fname} -> gs://{l2_bucket}/{blob_name}")
            uploaded_any = True
            time.sleep(1)  # polite throttle; keep small since we're streaming
        except Exception as e:
            print(f"Failed to upload {fname}: {e}")

    return uploaded_any

# def retrieve_new_files(data_root, param: str, day) -> bool:
#     """
#     Check for and download new MODIS Level-2 files for a given parameter and date.

#     This function queries the NASA OceanColor `file_search` API based on the provided
#     parameter (e.g., `"OC"` or `"SST"`) and date. It builds a time-bounded search window
#     covering the full UTC day (00:00:00–23:59:59), retrieves the list of matching MODIS-Aqua
#     L2 filenames via `get_nasa_l2_flist`, and verifies that each file exists locally under
#     a date-partitioned directory structure:

#         <data_root>/<YYYY>/<DDD>/<param>/

#     where `<DDD>` is the Julian day-of-year (001–366).
#     For example:
#         /ERDwork/modisa/data/netcdf/2025/001/OC/ (for OC data from Jan 1st 2025)

#     Any missing files are downloaded using `get_netcdf_file`, with a brief pause between
#     requests to reduce load on the NASA server.

#     Parameters
#     ----------
#     data_root : str or Path
#         The base directory where daily subfolders will be created and files stored.
#     param : str
#         The MODIS data parameter to retrieve, typically `"OC"` (ocean color) or `"SST"`.
#     day : datetime.datetime or datetime.date
#         The calendar date to check for. All available L2 files for this date are queried.

#     Returns
#     -------
#     bool
#         True if any new file was downloaded; False if all files were already present.

#     Raises
#     ------
#     ValueError
#         If `param` is not a supported product (only `"OC"` or `"SST"` are valid).
#     urllib.error.URLError
#         If the network request inside `get_nasa_l2_flist` fails.
#     OSError
#         If local file operations (existence checks or writing) fail, or if
#         `get_netcdf_file` cannot write the downloaded file.

#     Edited by
#     ----------
#     Jonathan Sherman
#         • 10/23/2025 - First refactor.
#         • 11/05/2025 - Switched to nested year/month/day/param directory layout
#           (<data_root>/<YYYY>/<DDD>/<param>/) for better organization and faster
#           downstream access (original version saved L2 data under monthly directories (<data_root>/<YYYYMM>/)
#     """
#     if param not in {"OC", "SST"}:
#         raise ValueError(f"Unsupported param: {param}")

#     data_root = Path(data_root)

#     # Normalize date
#     if isinstance(day, datetime):
#         day = day.date()
#     elif not isinstance(day, date):
#         raise ValueError("`day` must be a datetime.date or datetime.datetime")

#     # Target dir: YYYY/DDD/<param>
#     yyyy = day.strftime("%Y")
#     doy  = day.strftime("%j")
#     target_dir = data_root / yyyy / doy / param
#     target_dir.mkdir(parents=True, exist_ok=True)
#     print(f"Target directory: {target_dir}")

#     # Query NASA OceanColor for filenames for this UTC day
#     file_list = get_nasa_l2_flist(day=day, param=param)

#     downloaded_any = False

#     # Iterate through file list and download missing files
#     for fname in file_list:
#         dest_path = target_dir / fname
#         if dest_path.exists():
#             continue
#         try:
#             get_netcdf_file(fname, dest_path)
#             print(f"Downloaded {fname} → {dest_path}")
#             downloaded_any = True
#             time.sleep(2)  # polite throttle
#         except Exception as e:
#             print(f"Failed to download {fname}: {e}")

#     return downloaded_any


def myReshape(dataArray):
    """
    Flatten an N-dimensional array into a 2D column vector of type float32.

    This helper is used before gridding routines that expect a single column of
    values (shape: `(n_pixels, 1)`). If `dataArray` is a `numpy.ma.MaskedArray`,
    masked entries are preserved in the output.

    Parameters
    ----------
    dataArray : numpy.ndarray or numpy.ma.MaskedArray
        Any N-dimensional array (e.g., `(n, m)`, `(n,)`, or higher rank).
        If a masked array is provided, masks are preserved in the result.

    Returns
    -------
    numpy.ma.MaskedArray or numpy.ndarray
        A 2D array of shape `(dataArray.size, 1)` with dtype `float32`.
        - If `dataArray` was a masked array, the result is a masked array.
        - Otherwise, the result is a regular `ndarray` of `float32`.
    """
    dataArray = dataArray.reshape(dataArray.size, 1)
    dataArray = np.asarray(dataArray, np.float32)
    return dataArray





'''
Deleted safe_remove
JS 10-23-2025
'''

def send_to_servers(nc_src_path: str | Path, dst_dir: str | Path, interval: str = "1") -> None:
    """
    Upload to PROD and optionally also upload a WORK 1day copy for MB/MW interval==1.
    """
    nc_src_path = Path(nc_src_path)
    if not nc_src_path.exists():
        raise OSError(f"Local file not found: {nc_src_path}")
    nc_file = nc_src_path.name

    # Normalize dst_dir to a clean relative Path like "MB/sstd"
    dst_dir = Path(str(dst_dir).strip("/"))

    client = storage.Client()

    pub = CFG["PUBLISH_TARGETS"]
    prod_cfg = pub["prod"]
    work_cfg = pub["work"]

    prod_bucket_name = prod_cfg["bucket"]
    work_bucket_name = work_cfg["bucket"]

    prod_bucket = client.bucket(prod_bucket_name)
    work_bucket = client.bucket(work_bucket_name)

    # ---- PROD path: <prod.root>/<dst_dir>/<interval>day/<filename>
    dst_root_dir = Path(str(prod_cfg["root"]).strip("/"))
    interval_dir = Path(prod_cfg.get("interval_fmt", "{interval}day").format(interval=interval))

    remote_dir = dst_root_dir / dst_dir / interval_dir
    blob_name_prod = remote_dir / nc_file

    try:
        prod_bucket.blob(str(blob_name_prod)).upload_from_filename(str(nc_src_path))
        print(f"Uploaded {nc_file} → gs://{prod_bucket_name}/{remote_dir}")
    except Exception as e:
        raise RuntimeError(f"Upload to PROD bucket failed: {e}") from e

    # ---- Optional WORK 1day mirror for MB/MW 1-day products
    parent = dst_dir.parts[0].upper() if len(dst_dir.parts) >= 2 else ""
    if str(interval) == "1" and parent in ("MB", "MW"):
        region_name = work_cfg["region_map"][parent]  # "modisgf" or "modiswc"
        addl_remote_dir = Path(str(work_cfg["root"]).strip("/")) / region_name / "1day"

        work_blob_name = addl_remote_dir / nc_file
        try:
            work_bucket.blob(str(work_blob_name)).upload_from_filename(str(nc_src_path))
            print(f"Also uploaded {nc_file} → gs://{work_bucket_name}/{addl_remote_dir}")
        except Exception as e:
            raise RuntimeError(f"Upload to WORK bucket failed: {e}") from e


def update_modis_1day(
    now: datetime,
    param: str,
    update_flags: Sequence[bool],
    ) -> None:
    """
    Execute daily MODIS 1-day processing scripts for SST or Chla based on update flags.

    This function checks the three most recent lags (-3, -2, -1 days relative to `now`).
    For each lag where `update_flags[lag + 3]` is True, it constructs and executes
    the appropriate MODIS-Aqua one-day processing scripts for both the West Coast (MW)
    and Pacific (MB) regions. The scripts are called via `subprocess.run`, with command
    arguments including the base L2 data directory, the configured work directory, the
    four-digit year, and day-of-year (DOY).

    Parameters
    ----------
    now : datetime.datetime
        Reference timestamp (typically the current date and time). Used to compute
        target processing dates for the last three days.
    base_data_dir : str or pathlib.Path
        Root directory where the daily L2 files are stored (e.g.,
        `/mnt/gcs/ERDwork/modisa/data/netcdf`).
    param : str
        MODIS parameter type, either `"SST"` or `"Chla"`. Determines which scripts to call.
    update_flags : Sequence[bool]
        Boolean list of length ≥ 4, where `update_flags[lag + 3]` indicates whether
        data for that lag index (-3 to -1) should trigger a reprocessing run.

    Returns
    -------
    None
        Executes external scripts as side effects; does not return a value.

    Raises
    ------
    subprocess.CalledProcessError
        If a subprocessed MODIS 1-day script exits with a non-zero status.

    Notes
    -----
    This refactored version replaces hard-coded `os.system` calls with explicit,
    logged `subprocess.run()` invocations, ensuring better error handling and
    traceability. It also adopts PEP 8–compliant variable names, type hints, and
    centralized configuration through the `CFG` dictionary.

    Edited by
    ----------
    Jonathan Sherman — 2025-10-24
    """


    python_bin = CFG["PYTHON_BIN"]
    script_dir = Path(CFG["HOME_DIR"].rstrip("/")) / "scripts" / "modisa"

    for lag in range(-3, 0):
        idx = lag + 3
        if not (0 <= idx < len(update_flags) and update_flags[idx]):
            continue

        d = now + timedelta(days=lag)
        year = f"{d.year}"
        doy = d.strftime("%j")

        param_u = param.upper()
        if param_u == "SST":
            scripts = ["makeSST1daynewMW.py", "makeSST1daynewMB.py"]
        elif param_u in ("CHLA", "OC"):
            scripts = ["makeChla1daynewMW.py", "makeChla1daynewMB.py"]
        else:
            raise ValueError("param must be one of: 'SST', 'Chla' (or 'OC')")

        for script_name in scripts:
            cmd = [python_bin, str(script_dir / script_name), year, doy]
            print(f"[INFO] Running: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, check=True, capture_output=False)
            except subprocess.CalledProcessError as cpe:
                print(f"[ERROR] {script_name} failed for {year} DOY {doy} (lag {lag}): {cpe}")



def update_modis_composite(now: datetime,
                           param: str,
                           update_flags: Sequence[bool],
                           composite: str,
                          ) -> None:
    """
    Execute MODIS composite-processing scripts (MB + MW) for SST or Chla based on update flags.

    This function checks the three most recent lags (-3, -2, -1 days relative to `now`).
    For each lag where `update_flags[lag + 3]` is True, it constructs and executes the
    appropriate MODIS-Aqua composite scripts for both the Pacific (MB) and West Coast (MW)
    regions. The function uses `subprocess.run` to call the MB and MW composite scripts
    (`CompMB*.py`, `CompMW*.py`) with command-line arguments that include each region’s
    configured daily input directory, work directory, four-digit year, day-of-year (DOY),
    and the composite interval.

    Parameters
    ----------
    now : datetime.datetime
        Reference timestamp (typically the current date and time). Used to compute
        target composite-processing dates for the last three days.
    param : str
        MODIS parameter type, either `"SST"` or `"Chla"`. Determines which MB/MW
        composite scripts are executed.
    update_flags : Sequence[bool]
        Boolean list of length ≥ 4, where `update_flags[lag + 3]` indicates whether
        a composite update should be triggered for that lag index (-3 to -1).
    composite : str
        Composite interval identifier (e.g., `"3"`, `"5"`, `"8"`, `"14"`). Passed to
        the external composite scripts to control the temporal window of averaging.

    Returns
    -------
    None
        Executes external composite-generation scripts as side effects; does not
        return a value.

    Raises
    ------
    subprocess.CalledProcessError
        If any composite-processing script exits with a non-zero return code.

    Notes
    -----
    This refactored version removes duplicated logic and dispatches scripts through
    structured lookups, improving readability and maintainability. It also replaces
    legacy `os.system` calls with explicit `subprocess.run()` execution for better
    error handling, observability, and robustness. Region-specific daily input and
    work directories are now derived internally within the composite scripts using
    configuration from `CFG`.

    Edited by
    ----------
    Jonathan Sherman — 2025-12-01
    """

    python_bin = CFG["PYTHON_BIN"]
    script_dir = Path(CFG["HOME_DIR"].rstrip("/")) / "scripts" / "modisa"

    # Script mapping by parameter and region
    scripts = {
        "SST":  {"MB": "CompMBSST.py",  "MW": "CompMWSST.py"},
        "Chla": {"MB": "CompMBChla.py", "MW": "CompMWChla.py"},
    }

    # Process lags -3, -2, -1
    for lag in range(-3, 0):
        idx = lag + 3
        if not (0 <= idx < len(update_flags) and update_flags[idx]):
            continue

        d = now + timedelta(days=lag)
        year = f"{d.year}"
        doy = d.strftime("%j")

        for region in ("MW", "MB"):
            cmd = [
                python_bin,
                str(script_dir / scripts[param][region]),
                year,
                doy,
                composite,
            ]

            print(f"[INFO] Running {region} composite (lag {lag}, DOY {doy}): {' '.join(cmd)}")
            subprocess.run(cmd, check=True)



from pathlib import Path

def list_daily_blob_names(
    bucket,
    rel_prefix: Path,
    region: str,
    year: str,
    start_doy: int,
    end_doy: int,
    dtype: str,
):
    """
    List GCS blob names for daily files for a given region (MW or MB),
    year, DOY range, and parameter (dtype).

    Parameters
    ----------
    bucket : google.cloud.storage.bucket.Bucket
        The GCS bucket object.
    rel_prefix : pathlib.Path
        Path under the bucket where the daily files live, relative to
        the mount root (e.g., DAILY_WC_DIR.relative_to(MOUNT_ROOT)).
    region : str
        Region code, e.g. "MW" or "MB".
    year : str
        Four-digit year, e.g. "2025".
    start_doy : int
        Starting day-of-year (1–366), inclusive.
    end_doy : int
        Ending day-of-year (1–366), inclusive.
    dtype : str
        Parameter name, e.g. "chla", "k490", "par0", "cflh".

    Returns
    -------
    list of str
        Blob names (full paths within the bucket) matching the pattern:
            <rel_prefix>/<REGION>YYYYDOY_YYYYDOY_<dtype>.nc

        For daily products, the start and end DOY in the filename are equal.

    Edited by
    ----------
    Jonathan Sherman — 2025-12-05: Added this helper function (originally embedded in the composite script) into roylib. Updates include generalizing the logic for cross
    region application (MW/MB) and improving clarity and reusability.
    """

    blob_names = []
    region = region.upper()
    suffix = f"_{dtype}.nc"

    for day in range(start_doy, end_doy + 1):
        doy_str = f"{day:03d}"

        # Prefix looks like: <rel_prefix>/<REGION>YYYYDOY_
        # (we rely on the "_<dtype>.nc" suffix to pick the right variable)
        day_prefix = str(rel_prefix / f"{region}{year}{doy_str}_")

        # GCS list_blobs prefix-match
        for blob in bucket.list_blobs(prefix=day_prefix):
            name = blob.name

            # Match only files ending in "_<dtype>.nc"
            if name.endswith(suffix):
                blob_names.append(name)

    return blob_names


'''
Deleted send_ncml_to_servers
JS 10-23-2025
'''

'''
The retrieve_new_files1 function was deprecated and removed because it duplicated retrieve_new_files, referenced obsolete L2_LAC file naming patterns, and is not used in any current processing scripts.
JS 10-23-2025
'''

'''
The custom isleap function is unnecessary. Scripts that need this check have been updated to use the calendar module's built-in isleap function
JS 12-02-2025
'''
import numpy as np
import numpy.ma as ma

def meanVar(mean, num, obs):
    """
    Update the running mean and count of observations with new data.

    Parameters
    ----------
    mean : numpy.ma.MaskedArray or numpy.ndarray
        The current running mean values for each element.
    num : numpy.ndarray
        The current count of valid observations for each element (integer array).
    obs : numpy.ma.MaskedArray
        The new observations with the same shape as `mean`. Masked entries are not used.

    Returns
    -------
    tuple
        A 2-tuple `(updated_mean, updated_count)` where:
        - `updated_mean` is a masked or regular array of new mean values (dtype float32).
        - `updated_count` is an integer array of updated counts (dtype int32).

    Raises
    ------
    None
        Shape mismatches or invalid operations will propagate NumPy errors.


    Edited by
    ----------
    Jonathan Sherman — 2025-12-02: changd function to use Dale's meanVar version (update_mean as Dale defines it)
    """
    # Valid (unmasked) entries
    mask = ~ma.getmaskarray(obs)

    # Increment sample count only for valid cells
    num[mask] += 1

    # Running mean update (use updated count)
    num_float = num[mask].astype(mean.dtype, copy=False)
    mean[mask] += (obs[mask] - mean[mask]) / num_float

    return mean, num

# def meanVar(mean, num, obs):
#     """
#     Update the running mean and count of observations with new data.

#     Parameters
#     ----------
#     mean : numpy.ma.MaskedArray or numpy.ndarray
#         The current running mean values for each element.
#     num : numpy.ndarray
#         The current count of valid observations for each element (integer array).
#     obs : numpy.ma.MaskedArray
#         The new observations with the same shape as `mean`. Masked entries are not used.

#     Returns
#     -------
#     tuple
#         A 2-tuple `(updated_mean, updated_count)` where:
#         - `updated_mean` is a masked or regular array of new mean values (dtype float32).
#         - `updated_count` is an integer array of updated counts (dtype int32).

#     Raises
#     ------
#     None
#         Shape mismatches or invalid operations will propagate NumPy errors.
#     """

#     import numpy as np
#     import numpy.ma as ma

#     numShape = num.shape
#     temp = np.subtract(obs, mean, dtype=np.single)
#     numAdd = np.ones(numShape, dtype=np.int32)
#     numAdd[obs.mask] = 0
#     num = np.add(num, numAdd, dtype=np.int32)
#     tempNum = ma.array(num, mask=(num == 0), dtype=np.int32)
#     temp = np.divide(temp, tempNum.astype("float"), dtype=np.single)
#     mean = np.add(mean, temp.filled(0.0), dtype=np.single)
#     return (mean, num)


def mean_sumsq(mean, ss, num, obs):
    """
    Cumulative calculation of mean and sum of squares for masked arrays.

    This function updates the running mean, sum of squares, and count of observations
    given a new set of observations. All input arrays (`mean`, `ss`, and `obs`) must be
    NumPy masked arrays of identical shape. If any input is not a masked array, the
    function exits with an error message.

    Parameters
    ----------
    mean : numpy.ma.MaskedArray
        The current running mean array. Masked entries indicate missing data and
        are not included in the update.
    ss : numpy.ma.MaskedArray
        The current running sum of squares array. Masked entries are not included
        in the update.
    num : numpy.ndarray
        The current count of valid observations for each element, as an integer array
        of the same shape as `mean`. This array is incremented for each unmasked entry
        in `obs`.
    obs : numpy.ma.MaskedArray
        The new observations to incorporate. Masked entries indicate missing data and
        are not used in updating `mean`, `ss`, or `num`.

    Returns
    -------
    tuple
        A 3-tuple `(updated_mean, updated_ss, updated_num)` where:
        - `updated_mean` (numpy.ma.MaskedArray): The updated running mean array.
        - `updated_ss` (numpy.ma.MaskedArray): The updated running sum of squares array.
        - `updated_num` (numpy.ndarray): The updated count of observations as an integer array.

    Raises
    ------
    SystemExit
        If any of `mean`, `ss`, or `obs` is not a `numpy.ma.MaskedArray`, the function
        prints an error message and exits.
    """
    import numpy as np
    import numpy.ma as ma
    import sys

    if (
        not isinstance(mean, np.ma.MaskedArray)
        or not isinstance(ss, np.ma.MaskedArray)
        or not isinstance(obs, np.ma.MaskedArray)
    ):

        print("Input arguments mean, ss, and obs are not numpy masked arrays")
        print("Try converting mean, ss, and obs to masked arrays")
        print("before using them in the function, e.g. ma.array(arg)")
        print(" ")
        sys.exit(mean_sumsq.__doc__)

    numShape = num.shape
    temp = np.subtract(obs, mean.filled(0.0), dtype=np.single)
    numAdd = np.ones(numShape, dtype=np.int32)
    numAdd[obs.mask] = 0
    num = np.add(num, numAdd, dtype=np.int32)
    tempNum = ma.array(num, mask=(num == 0), dtype=np.int32)
    print("tempNum", tempNum.min(), tempNum.max())
    tNfloat = tempNum.astype("float")

    temp = ma.divide(temp, tNfloat, dtype=np.single)
    mean = np.add(mean.filled(0.0), temp.filled(0.0), dtype=np.single)
    # mean = ma.masked_where(mean == 0., mean)

    num1 = np.copy(num)
    num2 = num1 - 1
    print("num2", num2.min(), num2.max())
    if np.any(num1 > 1):
        num1 = np.divide(num1, num2, where=(num1 > 1))

    print("num1", num1.min(), num1.max())
    print("num", num.min(), num.max())
    temp1 = ma.subtract(obs, mean, dtype=np.single)
    temp2 = np.multiply(temp1, temp1)
    print(
        "ss parts",
        ss.filled(0.0).max(),
        ma.multiply(num1, temp2).filled(0.0).max(),
    )
    ss = ss.filled(0.0) + ma.multiply(num1, temp2).filled(0.0)

    mean = ma.masked_where(num == 0, mean)
    ss = ma.masked_where(num == 0, ss)
    vr = np.divide(ss, num)
    sdev = np.sqrt(vr)
    print("stDev", sdev.min(), sdev.max(), sdev.mean())
    # print(stdev.min(), stdev.max(), stdev.mean())
    return (mean, ss, num)


def makeNetcdf(mean, nobs, interval, outFile, filesUsed, workDir):
    """
    Create a NetCDF file from multi-day aggregated data arrays and assign metadata.

    Parameters
    ----------
    mean : numpy.ma.MaskedArray or numpy.ndarray
        A 2D array of mean values (masked or regular). Masked entries are treated as missing.
    nobs : int
        Ignored. The number of observations is recomputed from `mean` (non-masked values).
    interval : int
        Time interval in days (e.g., 1, 3, 5, 8, 14). Determines which CDL template to use.
    outFile : str
        Output filename (e.g., 'MW20250012025008_chla.nc'). The function infers:
        - `dataset`: first two characters.
        - `param`: substring after the first underscore past index 10.
        - `time1` and `time2`: substrings indicating start and end DOY.
    filesUsed : list of str
        List of source filenames that contributed to `mean`. Stored in the NetCDF’s `files` attribute.
    workDir : str or path-like
        Directory in which to write the new NetCDF file and run `ncgen`.

    Returns
    -------
    str
        Full path of the generated NetCDF file.

    Raises
    ------
    OSError
        If `ncgen` cannot be executed.
    RuntimeError
        If the `ncgen` command returns a non-zero exit code.


    Edited by
    ----------
    Jonathan Sherman — 2025-12-02: Refactored to remove directory-changing side effects, use pathlib for file handling, fix the undefined return value, and streamline
    filename/CDL parsing while preserving original behavior
    """
    import os
    import subprocess
    from pathlib import Path
    from datetime import datetime, date, timedelta

    import numpy as np
    import numpy.ma as ma
    from netCDF4 import Dataset, date2num

    # Recompute nobs and percent coverage from the masked mean
    nobs = ma.count(mean)
    noMiss = ma.count_masked(mean)
    total = nobs + noMiss
    percentCoverage = float(nobs) / float(total) if total > 0 else 0.0

    # Ensure workDir is a Path
    workDir = Path(workDir)

    # Work with just the filename portion for parsing
    filename = Path(outFile).name  # in case outFile had a path
    # Example: MW20250012025008_chla.nc
    stem = filename[:-3]  # strip ".nc"

    dataset = filename[0:2]      # "MW"
    offset = stem.find("_", 10)  # find "_" after date ranges
    param = stem[(offset + 1):] if offset != -1 else ""  # e.g., "chla"
    time1 = filename[2:9]        # "YYYYDOY"
    time2 = filename[10:17]      # "YYYYDOY" (currently unused, but kept for clarity)

    interval1 = str(interval)

    # Build CDL file path.
    # Note: All composites (1, 3, 5, 8, 14 days) use the same "3Day" CDL template.
    # The only interval-specific metadata in the CDL is the long_name text
    # "(3 Day Composite)", which is overwritten below based on the actual interval.
    cdlFile = Path(CFG["CDL_DIR"]) / f"{dataset}{param}3Day.cdl"

    # Output NetCDF path
    nc_path = workDir / filename

    # ncgen command
    ncgen_bin = CFG["NCGEN_BIN"]
    cmd = [ncgen_bin, "-o", str(nc_path), str(cdlFile)]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ncgen failed: {' '.join(cmd)}") from e

    # Open the generated NetCDF for editing
    with Dataset(nc_path, "a") as nc:
        now = datetime.now()
        now1 = date(now.year, now.month, now.day)

        # Time variable
        mytime = nc.variables["time"]

        # Global attributes
        nc.files = filesUsed
        nc.date_created = str(now1)
        nc.date_issued = str(now1)

        # Data variable
        paramName = dataset + param  # e.g., "MWchla"
        myparam = nc.variables[paramName]

        tempName = myparam.long_name
        composite = f"({interval1} Day Composite)"
        # Preserve original behavior: replace "(3 Day Composite)" with new text
        myparam.long_name = tempName.replace("(3 Day Composite)", composite)

        myparam.numberOfObservations = nobs
        myparam.percentCoverage = percentCoverage

        # Assume mean is 2D; variable shape is (time, z, y, x)
        myparam[0, 0, :, :] = mean[:, :]
        myparam.actual_range = np.array([mean.min(), mean.max()])

        # Compute center date from time1 and interval
        startTimeYear = int(time1[0:4])
        startTimeDoy = int(time1[4:7])
        startDate = datetime(startTimeYear, 1, 1, 0) + timedelta(startTimeDoy - 1)

        if interval1 == "1":
            centerDate = startDate + timedelta(hours=12)
        elif interval1 == "3":
            centerDate = startDate + timedelta(hours=36)
        elif interval1 == "5":
            centerDate = startDate + timedelta(hours=60)
        elif interval1 == "8":
            centerDate = startDate + timedelta(days=4)
        elif interval1 == "14":
            centerDate = startDate + timedelta(days=7)
        else:
            # Fallback: mid-point of the interval
            centerDate = startDate + timedelta(days=interval / 2.0)

        udtime = date2num(centerDate, units="seconds since 1970-01-01")
        mytime[0] = udtime
        mytime.actual_range = np.array([udtime, udtime])

    # Return the full path as a string
    return str(nc_path)



def makeNetcdfmDay(mean, nobs, interval, outFile, filesUsed, workDir):
    """
    Create a NetCDF file for monthly composite data.

    Parameters
    ----------
    mean : numpy.ma.MaskedArray or numpy.ndarray
        A 2D array of mean values for the composite period. Masked entries are
        treated as missing.
    nobs : int
        Ignored. The number of observations is recomputed from `mean` (non-masked values).
    interval : int
        Length of the composite period in days. Used for metadata and center time.
    outFile : str
        Desired output filename (e.g., 'MW2025001_2025031_chla.nc'). The function
        infers:
        - `dataset`: first two characters (e.g., "MW").
        - `param`: substring after the first underscore past index 10.
        - `time1` and `time2`: substrings "YYYYDOY" for start/end.
    filesUsed : list of str or str
        Source filenames that contributed to `mean`. Stored in NetCDF `files` attribute.
    workDir : str or path-like
        Directory in which to write the new NetCDF file and run `ncgen`.

    Returns
    -------
    str
        Full path of the generated NetCDF file.

    Raises
    ------
    RuntimeError
        If the `ncgen` command returns a non-zero exit code.

    Edited by
    ----------
    Jonathan Sherman — 2025-12-05: Refactored to match makeNetcdf
    function (pathlib, cfg-based ncgen/cdl paths, no chdir, clearer parsing)
    while preserving original monthly center-time logic and mDay templates.
    """
    import os
    import subprocess
    from pathlib import Path
    from datetime import datetime, date, timedelta

    import numpy as np
    import numpy.ma as ma
    from netCDF4 import Dataset, date2num

    # Recompute nobs and percent coverage from the masked mean
    nobs = ma.count(mean)
    noMiss = ma.count_masked(mean)
    total = nobs + noMiss
    percentCoverage = float(nobs) / float(total) if total > 0 else 0.0

    # Ensure workDir is a Path
    workDir = Path(workDir)

    # Normalize filesUsed to a string for the attribute
    if isinstance(filesUsed, (list, tuple)):
        files_attr = ", ".join(str(f) for f in filesUsed)
    else:
        files_attr = str(filesUsed)

    # Work with just the filename portion for parsing
    filename = Path(outFile).name  # in case outFile had a path
    # Example: MW2025001_2025031_chla.nc
    stem = filename[:-3]  # strip ".nc"

    dataset = filename[0:2]       # "MW"
    offset = stem.find("_", 10)   # find "_" after date ranges
    param = stem[(offset + 1):] if offset != -1 else ""  # e.g., "chla"
    time1 = filename[2:9]         # "YYYYDOY"
    time2 = filename[10:17]       # "YYYYDOY"

    # Monthly CDL template: <dataset><param>mDay.cdl
    cdlFile = Path(CFG["CDL_DIR"]) / f"{dataset}{param}mDay.cdl"

    # Output NetCDF path
    nc_path = workDir / filename

    # ncgen command
    ncgen_bin = CFG["NCGEN_BIN"]
    cmd = [ncgen_bin, "-o", str(nc_path), str(cdlFile)]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ncgen failed: {' '.join(cmd)}") from e

    # Open the generated NetCDF for editing
    with Dataset(nc_path, "a") as nc:
        now = datetime.now()
        now1 = date(now.year, now.month, now.day)

        # Time variable
        mytime = nc.variables["time"]

        # Global attributes
        nc.files = files_attr
        nc.date_created = str(now1)
        nc.date_issued = str(now1)

        # Data variable
        paramName = dataset + param  # e.g., "MWchla"
        myparam = nc.variables[paramName]

        myparam.numberOfObservations = nobs
        myparam.percentCoverage = percentCoverage

        # Assume mean is 2D; variable shape is (time, z, y, x)
        myparam[0, 0, :, :] = mean[:, :]
        myparam.actual_range = np.array([mean.min(), mean.max()])

        # -----------------------------------------------------------------
        # Center time logic (preserve original monthly behavior)
        # -----------------------------------------------------------------
        startTimeYear = int(time1[0:4])
        startTimeDoy = int(time1[4:7])
        endTimeYear = int(time2[0:4])
        endTimeDoy = int(time2[4:7])

        # These should be same-year for your monthly MW/MB use case
        startDate = datetime(startTimeYear, 1, 1, 0) + timedelta(startTimeDoy - 1)
        endDate = datetime(endTimeYear, 1, 1, 0) + timedelta(endTimeDoy - 1)

        # Center DOY (float, like original centerDoy = (start + end) / 2.0)
        centerDoy = (startTimeDoy + endTimeDoy) / 2.0
        centerDate = datetime(startTimeYear, 1, 1, 0) + timedelta(centerDoy - 1)

        udtime = date2num(centerDate, units="seconds since 1970-01-01")

        # Preserve original "31-day month" adjustment:
        # if (endTimeDoy - startTimeDoy + 1) == 31: add 12 hours
        if (endTimeDoy - startTimeDoy + 1) == 31:
            udtime = udtime + 43200.0  # 12 hours in seconds

        mytime[0] = udtime
        mytime.actual_range = np.array([udtime, udtime])

    # Return the full path as a string
    return str(nc_path)


def grd2netcdf1(grdFile, fileOut, filesUsed, my_mask, fType):
    """
    Convert a GRD file to a NetCDF file, copying spatial data and metadata.

    This function reads a GRD file containing gridded data (with variables named
    either "lon"/"lat"/"z" or "x"/"y"/"z" depending on `fType`), computes coverage statistics,
    generates a NetCDF file via an `ncgen` command on a corresponding CDL template, and
    writes spatial coordinates, data values, and metadata (file list, creation date,
    observation count, coverage, and actual range) into the new NetCDF. The time variable
    is centered based on the date stamps in the original filename.

    Parameters
    ----------
    grdFile : str
        Path to the input GRD file (e.g., "/path/to/MB2023123001000.grd").
        The filename must encode the dataset, parameter, and date information:
        - The first two characters are the dataset code.
        - Characters 2-9 represent the start date (YYYYDDD).
        - Characters 10-17 represent the end date (YYYYDDD).
    filesUsed : list of str
        A list of source filenames that contributed to the GRD data. The function will join them into
        a comma-separated string and store them in the NetCDF `files` attribute.
    fType : str
        File type indicator:
        - `"MW"` uses variables `"lon"`, `"lat"`, and `"z"`.
        - Any other value uses variables `"x"`, `"y"`, and `"z"`.

    Returns
    -------
    str
        The path of the generated NetCDF file (same as `grdFile` with a “.nc” extension).

    Raises
    ------
    OSError
        If reading the GRD file or executing the `ncgen` command fails (e.g., file not found,
        permission denied).
    RuntimeError
        If the `ncgen` command returns a non-zero exit code, indicating failure to generate
        the NetCDF file.

    Edited by
    ----------
    Jonathan Sherman — 2025-10-24

    """
    now = datetime.now()
    now1 = date(now.year, now.month, now.day)

    # --- Extract coordinates and values ---
    if fType == "MW":
        x = grdFile.lon.values
        y = grdFile.lat.values
        z = grdFile.values
    else:
        x = grdFile.x.values
        y = grdFile.y.values
        z = grdFile.values

    # --- Apply land mask ---
    if my_mask is not None:
        if my_mask.shape != z.shape:
            raise ValueError("Mask shape does not match data array.")
        z[my_mask != 1] = np.nan

    # Mask NaNs for stats and writing
    z_ma = ma.array(z, mask=np.isnan(z), fill_value=-9999999.0)
    nobs = int(ma.count(z_ma))
    noMiss = int(ma.count_masked(z_ma))
    percentCoverage = float(nobs) / float(nobs + noMiss) if (nobs + noMiss) > 0 else 0.0

    '''
    REFACTOR!!!
    '''
    # --- Derive metadata from fileOut ---
    ncFile = fileOut[:-4]
    dataset = fileOut[0:2]
    offset = ncFile.find("_", 10)
    param = ncFile[(offset + 1):]
    time1 = fileOut[2:9]
    time2 = fileOut[10:17]
    interval = str(int(time2) - int(time1) + 1)
    ncFile = ncFile + ".nc"
    # print(f"{CFG['HOME_DIR']}/data/work_{fType.lower()}/{ncFile}")

    # --- Use GCP config ---
    cdlFile = f"{CFG['CDL_DIR']}/{dataset}{param}{interval}Day.cdl"
    # print(cdlFile)
    cmd = [CFG["NCGEN_BIN"], "-o", f"{CFG['HOME_DIR']}/data/work_{fType.lower()}/{ncFile}", cdlFile]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ncgen failed: {' '.join(cmd)}") from e

    # --- Write data to NetCDF ---
    ncPointer = Dataset(f"{CFG['HOME_DIR']}/data/work_{fType.lower()}/{ncFile}", "a")
    lat = ncPointer.variables["lat"]
    lon = ncPointer.variables["lon"]
    mytime = ncPointer.variables["time"]
    ncPointer.files = ", ".join(filesUsed)
    ncPointer.date_created = str(now1)
    ncPointer.date_issued = str(now1)

    paramName = dataset + param
    myparam = ncPointer.variables[paramName]
    lat[:] = y[:]
    lon[:] = x[:]
    myparam[0, 0, :, :] = z_ma[:, :]
    myparam.numberOfObservations = nobs
    myparam.percentCoverage = percentCoverage
    myparam.actual_range = np.array([z_ma.min(), z_ma.max()], dtype="float64")

    # --- Time metadata ---
    startTimeYear = int(time1[0:4])
    startTimeDoy = int(time1[4:7])
    startDate = datetime(startTimeYear, 1, 1, 0) + timedelta(days=startTimeDoy - 1)

    if interval == "1":
        centerDate = startDate + timedelta(hours=12)
    elif interval == "3":
        centerDate = startDate + timedelta(hours=36)
    elif interval == "5":
        centerDate = startDate + timedelta(hours=60)
    elif interval == "8":
        centerDate = startDate + timedelta(days=4)
    elif interval == "14":
        centerDate = startDate + timedelta(days=7)
    else:
        centerDate = startDate + timedelta(days=int(interval) / 2.0)

    udtime = date2num(centerDate, units="seconds since 1970-01-01")
    mytime[0] = udtime
    mytime.actual_range = np.array([udtime, udtime], dtype="float64")

    ncPointer.close()
    return f"{CFG['HOME_DIR']}/data/work_{fType.lower()}/{ncFile}"


'''
def grd2netcdf1(grdFile, filesUsed, fType):
    """
    Convert a GRD file to a NetCDF file, copying spatial data and metadata.

    This function reads a GRD file containing gridded data (with variables named
    either "lon"/"lat"/"z" or "x"/"y"/"z" depending on `fType`), computes coverage statistics,
    generates a NetCDF file via an `ncgen` command on a corresponding CDL template, and
    writes spatial coordinates, data values, and metadata (file list, creation date,
    observation count, coverage, and actual range) into the new NetCDF. The time variable
    is centered based on the date stamps in the original filename.

    Parameters
    ----------
    grdFile : str
        Path to the input GRD file (e.g., "/path/to/MB2023123001000.grd").
        The filename must encode the dataset, parameter, and date information:
        - The first two characters are the dataset code.
        - Characters 2-9 represent the start date (YYYYDDD).
        - Characters 10-17 represent the end date (YYYYDDD).
    filesUsed : list of str
        A list of source filenames that contributed to the GRD data. This list is stored
        in the NetCDF file's `files` attribute.
    fType : str
        File type indicator:
        - `"MW"` uses variables `"lon"`, `"lat"`, and `"z"`.
        - Any other value uses variables `"x"`, `"y"`, and `"z"`.

    Returns
    -------
    str
        The path of the generated NetCDF file (same as `grdFile` with a “.nc” extension).

    Raises
    ------
    OSError
        If reading the GRD file or executing the `ncgen` command fails (e.g., file not found,
        permission denied).
    RuntimeError
        If the `ncgen` command returns a non-zero exit code, indicating failure to generate
        the NetCDF file.
    """
    now = datetime.now()
    now1 = date(now.year, now.month, now.day)

    # --- Read GRD file ---
    grdPointer = Dataset(grdFile)
    if fType == "MW":
        x = grdPointer.variables["lon"][:]
        y = grdPointer.variables["lat"][:]
        z = grdPointer.variables["z"][:, :]
    else:
        x = grdPointer.variables["x"][:]
        y = grdPointer.variables["y"][:]
        z = grdPointer.variables["z"][:, :]
    grdPointer.close()

    # --- Mask-aware stats ---
    z = np.array(z)
    z_ma = ma.array(z, mask=np.isnan(z))
    nobs = ma.count(z_ma)
    noMiss = ma.count_masked(z_ma)
    percentCoverage = float(nobs) / float(nobs + noMiss) if (nobs + noMiss) > 0 else 0.0

    # --- Build filenames and metadata ---
    ncFile = grdFile[:-4]
    dataset = grdFile[0:2]
    offset = ncFile.find("_", 10)
    param = ncFile[(offset + 1):]
    time1 = grdFile[2:9]
    time2 = grdFile[10:17]
    interval = str(int(time2) - int(time1) + 1)
    ncFile = ncFile + ".nc"
    print(ncFile)



    # --- CDL and ncgen ---
    cdlFile = f"{CFG['CDL_DIR']}/{dataset}{param}{interval}Day.cdl"
    print(cdlFile)
    cmd = [CFG["NCGEN_BIN"], "-o", ncFile, cdlFile]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ncgen failed: {' '.join(cmd)}") from e

    # --- Populate NetCDF ---
    ncPointer = Dataset(ncFile, "a")
    lat = ncPointer.variables["lat"]
    lon = ncPointer.variables["lon"]
    mytime = ncPointer.variables["time"]

    ncPointer.files = filesUsed
    ncPointer.date_created = str(now1)
    ncPointer.date_issued = str(now1)

    paramName = dataset + param
    myparam = ncPointer.variables[paramName]

    lat[:] = y[:]
    lon[:] = x[:]
    myparam[0, 0, :, :] = z_ma[:, :]
    myparam.numberOfObservations = nobs
    myparam.percentCoverage = percentCoverage
    myparam.actual_range = np.array([z_ma.min(), z_ma.max()], dtype="float64")

    # --- Time metadata ---
    startTimeYear = int(time1[0:4])
    startTimeDoy = int(time1[4:7])
    startDate = datetime(startTimeYear, 1, 1, 0) + timedelta(days=startTimeDoy - 1)

    if interval == "1":
        centerDate = startDate + timedelta(hours=12)
    elif interval == "3":
        centerDate = startDate + timedelta(hours=36)
    elif interval == "5":
        centerDate = startDate + timedelta(hours=60)
    elif interval == "8":
        centerDate = startDate + timedelta(days=4)
    elif interval == "14":
        centerDate = startDate + timedelta(days=7)
    else:
        centerDate = startDate + timedelta(days=int(interval) / 2.0)

    udtime = date2num(centerDate, units="seconds since 1970-01-01")
    mytime[0] = udtime
    mytime.actual_range = np.array([udtime, udtime], dtype="float64")

    ncPointer.close()
    return ncFile

'''

'''
Deleted url_lines which used a wget call (vs. url_lines1 that uses urllib)
Refactored url_lines1 to speed up runtime and logic
JS 10-23-2025
'''