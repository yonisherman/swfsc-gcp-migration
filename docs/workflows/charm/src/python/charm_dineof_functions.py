# charm_dineof_functions.py

from __future__ import annotations
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from types import SimpleNamespace
from netCDF4 import Dataset
import numpy as np
import numpy.ma as ma
import xarray as xr
import concurrent.futures
import subprocess
# from src.charm_helper_functions import run_cmd_parallel
from .charm_helper_functions import run_cmd as run_cmd2
import requests
from bs4 import BeautifulSoup
# from scipy.ndimage import distance_transform_edt
from scipy.ndimage import distance_transform_edt, uniform_filter
from requests.adapters import HTTPAdapter
import urllib3


# logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

def run_cmd(
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
        logger.info("[run_cmd] %s: %s", msg, printable)
    else:
        logger.info("[run_cmd] %s", printable)

    return subprocess.run(
        cmd,
        shell=isinstance(cmd, str),  # shell only if string provided
        check=check,
        capture_output=capture_output,
        text=True,
        cwd=cwd,
    )


def get_soup_href(http_url: str) -> List[str]:
    """
    Retrieve all hyperlink targets (`href` attributes) from an HTML index page.

    This function fetches an HTML page via HTTP and parses it using BeautifulSoup.
    It extracts all `<a>` tag `href` values, strips leading/trailing slashes, and
    returns a clean list of link strings.

    Args:
        http_url (str): The full HTTP or HTTPS URL of the HTML index page.

    Returns:
        List[str]: A list of href values (without surrounding slashes) extracted
        from the HTML document.

    Raises:
        requests.exceptions.RequestException: If the URL cannot be reached or the
            response status code indicates an error.
        ValueError: If the URL does not appear to be valid or the page has no links.

    Example:
        >>> urls = get_soup_href("https://coastwatch.noaa.gov/thredds/catalog.html")
        >>> len(urls) > 0
        True
        >>> urls[:3]
        ['dataset1', 'dataset2', 'dataset3']

    Notes:
        - The function logs both the fetch attempt and successful parsing steps.
        - Returned links are stripped of leading/trailing slashes to simplify concatenation.
        - Only `<a>` tags with valid `href` attributes are included.
    """
    session = requests.Session()
    retry_kwargs = dict(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    try:
        from packaging.version import Version
        if Version(urllib3.__version__) >= Version("1.26.0"):
            retry_kwargs["allowed_methods"] = ["GET"]
        else:
            retry_kwargs["method_whitelist"] = ["GET"]
    except Exception:
        pass  # skip method restriction if packaging unavailable
    adapter = requests.adapters.HTTPAdapter(max_retries=urllib3.util.retry.Retry(**retry_kwargs))
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    resp = session.get(http_url, timeout=120)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    links = [
        a.get("href").strip("/")
        for a in soup.find_all("a")
        if a.get("href")
    ]

    if not links:
        logger.warning("No links found at URL: %s", http_url)
        raise ValueError(f"No <a href> links found at {http_url}")

    logger.debug("Extracted %d links from %s", len(links), http_url)
    return links


def update_eof_files(
    eof_work_dir: str | Path,
    first_eof_nc: str,
    second_eof_nc: str,
    filled_vars: list[str],
    gap_vars: list[str],
    x: np.ndarray,
    y: np.ndarray,
    model_lon_grid: np.ndarray,
    model_lat_grid: np.ndarray,
    themask: np.ndarray,
    rtime: np.ndarray,
    advect_func,
) -> None:
    """Update EOF NetCDF files with advected fields.

    Args:
        eof_work_dir: Directory containing input/output NetCDF files.
        first_eof_nc: Filename of the first EOF NetCDF (read-only).
        second_eof_nc: Filename of the second EOF NetCDF (append mode).
        idx: Array of time indices; the first element selects the time slice.
        filled_vars: Variable names to read from the first EOF file.
        eof2_vars: Variable names to update in the second EOF file.
        x, y: Coordinate arrays for advection.
        model_lon_grid, model_lat_grid: Model longitude/latitude grids.
        themask: Mask to apply during advection.
        rtime: Reference time array for advection.
        time_last: Array of last time values (use time_last[0]).
        advect_func: Function to run advection, must return (band_var, longtime).

    Returns:
        None. Updates the second EOF NetCDF file in place.
    """

    oirange = np.arange(0, 3)

    cntr = 0
    for cntr, gvar in enumerate(gap_vars):
        with Dataset(eof_work_dir / gvar / first_eof_nc.format(gvar), "r") as nc_eof1, \
             Dataset(eof_work_dir / gvar / second_eof_nc.format(gvar), "a") as nc_eof2:

            time_eof1 = nc_eof1['time'][:]
            idx, = np.where(time_eof1 == time_eof1.max())
            time_last = time_eof1[idx[0]:idx[0]+1]

            var_eof1 = nc_eof1[filled_vars[cntr]][idx[0]:idx[0]+1, :, :]

            # Run advection
            band_var2, longtime = advect_func(
                x, y,
                model_lon_grid, model_lat_grid,
                np.flip(var_eof1, axis=1),
                oirange, themask,
                rtime, time_last[0]
            )

            # Update time variable (only on first variable loop)
            if cntr == 0:
                eof2_time = nc_eof2.variables["time"][:]
                new_time = np.append(eof2_time, longtime[1:])
                nc_eof2.variables["time"][:] = new_time
                cntr = 1
            else:
                nc_eof2.variables["time"][:] = new_time

            # Insert new data into last 3 time slots
            eof2_var = nc_eof2.variables[gvar][:, :, :]
            eof2_var[-3:, :, :] = np.flip(band_var2, axis=1)

            # Vectorized masking in one step
            data = np.array(eof2_var, copy=False)  # avoid extra copy
            mask = np.isnan(data) | (data > 9000)
            if gvar == 'Rrs_489' or gvar == 'Rrs_556':
                mask |= (data <= 0.0)

            eof2_var_masked = ma.array(data, mask=mask)

            # Write back to file
            nc_eof2.variables[gvar][:, :, :] = eof2_var_masked

    return time_last[0]

#######################################################################################################################
#######################################################################################################################
#######################################################################################################################
def _run_commands_parallel(
    cmd_list: List[list[str] | str],
    max_workers: int | None = None,
    print_output: bool = True,
    msg: str | None = None
) -> Dict[str, Dict[str, Any]]:
    """
    Run multiple shell commands in parallel with structured logging.

    IMPORTANT DINEOF NOTE:
      DINEOF init files often contain *relative paths* (e.g. data/dineof/...).
      Those paths are resolved relative to the process working directory.
      So when we run dineof, we must set cwd to the directory containing the .init file.
    """
    if max_workers is None:
        max_workers = min(len(cmd_list), os.cpu_count() or 4)

    logger.info("Running %d commands in parallel (max_workers=%d)", len(cmd_list), max_workers)
    results: Dict[str, Dict[str, Any]] = {}

    def _infer_cwd_and_capture(cmd: list[str] | str) -> tuple[Path | None, bool]:
        """
        Infer cwd and capture_output settings for a command.

        FIX (DINEOF):
          If cmd is [<...>/dineof, <...>/<something>.init], set cwd to init parent dir
          so relative paths inside the .init resolve correctly.
        """
        cwd: Path | None = None
        force_capture = False

        if isinstance(cmd, list) and len(cmd) >= 2:
            exe = str(cmd[0])
            arg1 = str(cmd[1])

            is_dineof = exe.endswith("/dineof") or os.path.basename(exe) == "dineof"
            is_init = arg1.endswith(".init")

            if is_dineof and is_init:
                # -------------------------
                # ✅ FIX HERE: set cwd
                # -------------------------
                cwd = Path(arg1).resolve().parent
                # Also capture output so Cloud Run logs show DINEOF stderr on failure
                force_capture = True

        return cwd, force_capture

    def _run_one(cmd):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        logger.info("START: %s", cmd_str)

        cwd, force_capture = _infer_cwd_and_capture(cmd)

        if cwd is not None:
            logger.info("DINEOF detected - running with cwd=%s", cwd)

        # For DINEOF we want captured output always; for other cmds, respect print_output
        capture = force_capture or print_output

        # Run the command
        # NOTE: use check=False so we can always log stdout/stderr on failure
        result = run_cmd(
            cmd,
            msg=f"{msg or 'parallel'}: {cmd_str}",
            check=False,
            capture_output=capture,
            cwd=cwd,
        )

        rc = result.returncode
        out = result.stdout or ""   
        err = result.stderr or ""   

        if rc != 0:
            logger.error("DINEOF STDOUT:\n%s", out.strip())
            logger.error("DINEOF STDERR:\n%s", err.strip())
            raise subprocess.CalledProcessError(rc, cmd, output=out, stderr=err)

        # Log output
        if print_output or force_capture:
            if out.strip():
                logger.info("[OUT] %s:\n%s", cmd_str, out.strip())
            if err.strip():
                logger.warning("[ERR] %s:\n%s", cmd_str, err.strip())

        if rc != 0:
            # Raise with captured output (extremely helpful in Cloud Run)
            raise subprocess.CalledProcessError(rc, cmd, output=out, stderr=err)

        logger.info("END: %s (rc=%d)", cmd_str, rc)
        return cmd_str, {"returncode": rc, "stdout": out, "stderr": err}

    # Run commands in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_run_one, cmd) for cmd in cmd_list]
        for fut in concurrent.futures.as_completed(futures):
            cmd_str, result = fut.result()
            results[cmd_str] = result

    logger.info("✅ All commands completed successfully.")
    return results


# def _run_commands_parallel(
#     cmd_list: List[list[str] | str],
#     max_workers: int | None = None,
#     print_output: bool = True,
#     msg: str | None = None
# ) -> Dict[str, Dict[str, Any]]:
#     """
#     Run multiple shell commands in parallel with structured logging.

#     Executes several system commands concurrently using a thread pool.
#     Each command is run via the `run_cmd2()` helper (which handles subprocess
#     creation, output capture, and error reporting). Results are collected
#     and returned as a dictionary once all processes finish.

#     Logging provides real-time visibility into command execution, including
#     start, output, errors, and completion status. If any command fails
#     (non-zero return code), the function logs the failures and raises
#     `SystemExit(1)` after all commands have completed.

#     Args:
#         cmd_list (List[list[str] | str]):
#             List of commands to execute. Each command may be specified as:
#             - A list of command tokens (e.g., `["/usr/bin/echo", "hello"]`), or
#             - A single command string (e.g., `"/usr/bin/echo hello"`).
#         max_workers (int, optional):
#             Maximum number of commands to execute concurrently.
#             Defaults to `min(len(cmd_list), os.cpu_count() or 4)`.
#         print_output (bool, optional):
#             Whether to log each command’s stdout and stderr after completion.
#             Defaults to True.
#         msg (str, optional):
#             Optional context string included in log messages for clarity.

#     Returns:
#         Dict[str, Dict[str, Any]]:
#             A dictionary mapping each command string to its result data:
#             {
#                 "returncode": int,
#                 "stdout": str,
#                 "stderr": str
#             }

#             Example:
#                 {
#                     "/usr/bin/echo hello": {
#                         "returncode": 0,
#                         "stdout": "hello\\n",
#                         "stderr": ""
#                     }
#                 }

#     Raises:
#         SystemExit:
#             If one or more commands fail (non-zero return code).
#         TypeError:
#             If `run_cmd2()` returns an unexpected result type.

#     Example:
#         >>> cmd = [
#         ...     ["/home/cwatch/DINEOF/dineof",
#         ...      "/home/cwatch/production/charm_v4_2025/bin/now_bf_chlor_a_v4.init"],
#         ...     ["/home/cwatch/DINEOF/dineof",
#         ...      "/home/cwatch/production/charm_v4_2025/bin/now_bf_Rrs_489_v4.init"],
#         ... ]
#         >>> _run_commands_parallel(cmd)
#         INFO:__main__:Running 2 commands in parallel (max_workers=2)
#         INFO:__main__:START: /home/cwatch/DINEOF/dineof ...
#         INFO:__main__:✅ All commands completed successfully.
#     """
#     if max_workers is None:
#         max_workers = min(len(cmd_list), os.cpu_count() or 4)

#     logger.info("Running %d commands in parallel (max_workers=%d)", len(cmd_list), max_workers)
#     results: Dict[str, Dict[str, Any]] = {}

#     def _run_one(cmd):
#         cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
#         logger.info("START: %s", cmd_str)

#         # Run the command using the external helper
#         result = run_cmd(cmd, msg=f"{msg or 'parallel'}: {cmd_str}")

#         # Normalize output
#         if hasattr(result, "returncode"):
#             rc = result.returncode
#             out = getattr(result, "stdout", "") or ""
#             err = getattr(result, "stderr", "") or ""
#         elif isinstance(result, tuple) and len(result) == 3:
#             rc, out, err = result
#         else:
#             raise TypeError(f"Unexpected return type from run_cmd2(): {type(result)}")

#         # Log output
#         if print_output:
#             if out.strip():
#                 logger.info("[OUT] %s:\n%s", cmd_str, out.strip())
#             if err.strip():
#                 logger.warning("[ERR] %s:\n%s", cmd_str, err.strip())

#         logger.info("END: %s (rc=%d)", cmd_str, rc)
#         return cmd_str, {"returncode": rc, "stdout": out, "stderr": err}

#     # Run commands in parallel
#     with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
#         futures = [executor.submit(_run_one, cmd) for cmd in cmd_list]
#         for fut in concurrent.futures.as_completed(futures):
#             cmd_str, result = fut.result()
#             results[cmd_str] = result

#     # Identify and report failed commands
#     failed = {k: v for k, v in results.items() if v["returncode"] != 0}
#     if failed:
#         logger.error("❌ Some commands failed:")
#         for cmd, res in failed.items():
#             logger.error("  - %s: rc=%d", cmd, res["returncode"])
#         raise SystemExit(1)

#     logger.info("✅ All commands completed successfully.")
#     return results

#######################################################################################################################
#######################################################################################################################
#######################################################################################################################

#######################################################################################################################
#######################################################################################################################
#######################################################################################################################

# def concat_l3_files(
#     l3_files_to_use: list[str], out_var_list: list[str],
#     dineof1_nc_templ: list[str], l3_dir: Path, eof_work_dir: Path
# ) -> None:
#     """Concatenate 180 days of L3 files by variable."""
#     paths_to_use = [l3_dir / fn.split("_")[1][:4] / fn for fn in l3_files_to_use]
#     for out_var in out_var_list:
#         out_file = eof_work_dir / out_var / dineof1_nc_templ.format(out_var)
#         cmd = [
#             "ncrcat", "-v", out_var, "-O", "-h"
#         ] + [str(p) for p in paths_to_use] + [str(out_file)]
#         run_cmd2(cmd, msg=f"Concatenate 180-day stack for {out_var}")

def concat_l3_files(
    l3_files_to_use: list[str],
    out_var_list: list[str],
    dineof1_nc_templ: list[str],
    l3_dir: Path,
    eof_work_dir: Path,
) -> None:
    """Concatenate 180 days of L3 files by variable.

    Cloud Run note:
      NCO (ncrcat) creates temp files in the output directory. We must ensure
      that directory exists and is writable by the runtime UID.
    """
    paths_to_use = [l3_dir / fn.split("_")[1][:4] / fn for fn in l3_files_to_use]

    for out_var in out_var_list:
        out_file = eof_work_dir / out_var / dineof1_nc_templ.format(out_var)

        # --- Ensure output directory exists and is writable ---
        out_dir = out_file.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        # Force NCO temp to a known-writable directory (avoid weird perms)
        nco_tmp = out_dir / ".nco_tmp"
        nco_tmp.mkdir(parents=True, exist_ok=True)

        # Write probe (real check; chmod can silently fail under Cloud Run)
        try:
            probe = out_dir / f".write_probe_{os.getpid()}"
            probe.write_text("ok")
            probe.unlink()
        except Exception as e:
            raise PermissionError(
                f"NCO output directory not writable: {out_dir} (uid={os.getuid()} gid={os.getgid()})"
            ) from e

        # Ensure NCO uses our temp dir
        os.environ["TMPDIR"] = str(nco_tmp)
        os.environ["NCO_TMPDIR"] = str(nco_tmp)

        # Clean up any prior leftover temp files from failed runs
        for tmp in out_dir.glob("*.ncrcat.tmp"):
            try:
                tmp.unlink()
            except Exception:
                pass

        cmd = ["ncrcat", "-v", out_var, "-O", "-h"] + [str(p) for p in paths_to_use] + [str(out_file)]
        run_cmd2(cmd, msg=f"Concatenate 180-day stack for {out_var}")
#######################################################################################################################
#######################################################################################################################
#######################################################################################################################

def mask_and_log_transform(
    out_var_list: list[str], dineof1_nc_templ: list[str],
    eof_work_dir: Path, res_dir: Path
) -> None:
    """Apply land mask and log-transform chlor_a in DINEOF inputs."""
    maskfile = Dataset(res_dir / "themask.nc", "r")
    mylatm = maskfile["latitude"][:]
    mylonm = maskfile["longitude"][:]
    themask = maskfile["mask"][:, :]
    maskfile.close()

    for out_var in out_var_list:
        nc_path = eof_work_dir / out_var / dineof1_nc_templ.format(out_var)
        with Dataset(nc_path, "a") as nc_add:
            if "mask" not in nc_add.variables:
                dine_mask = nc_add.createVariable("mask", "f4", ("latitude", "longitude"))
            else:
                dine_mask = nc_add.variables["mask"]
            dine_mask[:, :] = np.flip(themask, axis=0)

            if "chlor_a" in nc_add.variables:
                try:
                    log_chl = np.log(nc_add.variables["chlor_a"][:, :, :])
                    nc_add.variables["chlor_a"][:, :, :] = log_chl
                except Exception as e:
                    logger.warning("Could not log-transform chlor_a: %s", e)

    return mylatm, mylonm, themask


def run_first_dineof(
    out_var_list: list[str], pre_int_eof1: str, eof_dir: Path, eof_init_dir: Path
) -> None:
    """Run the first round of DINEOF in parallel."""
    cmds = [
        [str(eof_dir / "dineof"), str(eof_init_dir / band / pre_int_eof1.format(param=band))]
        for band in out_var_list
    ]
    _run_commands_parallel(cmds)


def run_second_dineof(
    out_var_list: list[str], pre_int_eof2: str, eof_dir: Path, eof_init_dir: Path
) -> None:
    """Run the second round of DINEOF in parallel."""
    cmds = [
        [str(eof_dir / "dineof"), str(eof_init_dir / band / pre_int_eof2.format(param=band))]
        for band in out_var_list
    ]
    _run_commands_parallel(cmds)


def prepare_second_dineof_inputs(
    first_dineof_nc, second_dineof_nc,
    out_var_list: list[str], filled_vars_list: list[str], eof_work_dir: Path
) -> None:
    """Prepare second DINEOF input NetCDFs by copying and stripping."""

    for cntr, out_var in enumerate(out_var_list):
        src = eof_work_dir / out_var / first_dineof_nc.format(out_var)
        dst = eof_work_dir / out_var / second_dineof_nc.format(out_var)

        run_cmd2(["nccopy", str(src), str(dst)], msg=f"Copy {out_var} for EOF2")
        run_cmd2(
            ["ncks", "-O", "-x", "-v", out_var, str(dst), str(dst)],
            msg=f"Strip vars from {dst.name}",
        )

        myCmd = [
            "ncrename", "-h", "-O",
            "-v", f"{filled_vars_list[cntr]},{out_var}",
            str(dst), str(dst)
        ]
        run_cmd2(myCmd, msg=f"Replace {filled_vars_list[cntr]} for {out_var} in 2nd EOF")


def process_charm_files_for_day(
    fd: datetime, nasa_base_url: str, l3_nasa_dir: Path,
    l3_work_dir: Path, res_dir: Path, cwutl_dir: Path,
    l3_dir: Path, grid_cnfg: SimpleNamespace, gap_var_list: list
) -> None:
    """Download and process CHARM L3 files for one day."""

    # Template NC file
    nc_template = l3_work_dir / grid_cnfg.nc_template
    nc_cdl = res_dir / grid_cnfg.nc_cdl
    run_cmd2(["ncgen", "-o", str(nc_template), str(nc_cdl)], msg="Create L3 template")

    url_yr = f"{fd:%Y}"
    url_doy = f"{fd:%d-%b-%Y}"

    # Build NASA URL for directory listing
    files_url = "/".join([str(nasa_base_url), url_yr, url_doy])
    files_parts = get_soup_href(str(files_url))

    # Files of interest
    charm_files = [
        ln for ln in files_parts
        if grid_cnfg.chl_pattern in ln
        or grid_cnfg.r489_pattern in ln  # JPSS1_VIIRS.20251007.L3m.DAY.RRS.Rrs_489.4km.NRT.nc
        or grid_cnfg.r556_pattern in ln
    ]

    if len(charm_files) != 3:
        logger.warning("Skip %s - missing files", fd.date())
        return

    for ct, fl in enumerate(charm_files):
        # Use obdaac_download instead of requests
        target_dir = l3_nasa_dir / url_yr
        target_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "obdaac_download",
            "-v",
            "--odir", str(target_dir),
            fl,
        ]
        run_cmd2(cmd, msg=f"Download {fl}")

        # --- regrid and export ---
        local_fl = target_dir / Path(fl).name
        ds = xr.open_dataset(local_fl)

        if "palette" in ds:
            del ds["palette"]

        # Subset region
        lat_min, lat_max = grid_cnfg.LAT_MIN, grid_cnfg.LAT_MAX
        lon_min, lon_max = grid_cnfg.LON_MIN, grid_cnfg.LON_MAX
        if ds.lat[0] > ds.lat[-1]:
            lat_min, lat_max = grid_cnfg.LAT_MAX, grid_cnfg.LAT_MIN

        chl_subset = ds.sel(lat=slice(lat_min, lat_max), lon=slice(lon_min, lon_max))
        ofile = l3_work_dir / grid_cnfg.ofile
        chl_subset.to_netcdf(ofile)

        cwutil_ofile_hdf = l3_work_dir / grid_cnfg.cwutil_ofile_hdf
        cwutil_ofile_nc = l3_work_dir / grid_cnfg.cwutil_ofile_nc

        run_cmd2([
            str(cwutl_dir / "cwregister2"),
            "--clobber",
            f"--master={res_dir / grid_cnfg.cwutil_master_nc}",
            str(ofile),
            str(cwutil_ofile_hdf),
        ], msg="Regrid NASA file")

        run_cmd2([
            str(cwutl_dir / "cwexport"),
            "-v",
            str(cwutil_ofile_hdf),
            str(cwutil_ofile_nc),
        ], msg="Convert to NetCDF")

        # Insert into mod_nc.nc4
        with Dataset(cwutil_ofile_nc, "r") as nc_s, \
             Dataset(nc_template, "a") as nc_out:

            myvar = nc_s.variables[gap_var_list[ct]][0, 0, :, :]
            myvar_masked = ma.masked_where(myvar <= 0, myvar)
            nc_out.variables[gap_var_list[ct]][0, :, :] = np.flip(myvar_masked, axis=0)

            if ct == 0:
                nc_out.variables["time"][:] = fd.toordinal()

    # Compress and save output
    nc_out_file = f"vcharm_{fd:%Y%m%d}_l2toL3.nc4"
    out_path = l3_dir / str(fd.year)
    out_path.mkdir(parents=True, exist_ok=True)

    run_cmd2([
        "nccopy",
        "-d6",
        str(nc_template),
        str(out_path / nc_out_file),
    ], msg="Compress L3 data")

    logger.info("Done: %s", nc_out_file)


def clip_edges(recon, original_data, mask, edge_width=10):
    """
    Clip DINEOF reconstructed values near the coast/edges.

    Args:
        recon (ndarray): Reconstructed array (time, y, x) or (y, x)
        original_data (ndarray): Original data array (same shape as recon)
        mask (ndarray): Ocean mask, 1=ocean, 0=land
        edge_width (int): Number of pixels from coast to apply clipping

    Returns:
        ndarray: Modified reconstruction
    """
    # Compute distance to land
    dist_to_land = distance_transform_edt(mask)  # pixels from ocean to nearest land
    # Create a mask of edge pixels
    edge_pixels = (dist_to_land <= edge_width)

    # Clip values at edges to min/max of nearby observed pixels
    min_vals = np.nanmin(original_data, axis=0)
    max_vals = np.nanmax(original_data, axis=0)

    recon_clipped = recon.copy()

    if recon.ndim == 3:  # time series
        for t in range(recon.shape[0]):
            recon_clipped[t][edge_pixels] = np.clip(
                recon[t][edge_pixels],
                min_vals[edge_pixels],
                max_vals[edge_pixels]
            )
    else:  # single snapshot
        recon_clipped[edge_pixels] = np.clip(
            recon[edge_pixels],
            min_vals[edge_pixels],
            max_vals[edge_pixels]
        )

    return recon_clipped


def clip_and_smooth_edges(
    recon,
    original_data,
    mask,
    edge_width=10,
    smooth_size=3,
    replace_edges=False
):
    """
    Smooth or replace DINEOF-reconstructed edge values near the coast.

    This function mitigates unrealistic DINEOF values at the boundaries
    (especially near coasts) by clipping or replacing data within a narrow
    edge zone defined by distance to land.

    Parameters
    ----------
    recon : np.ndarray or np.ma.MaskedArray
        Reconstructed data array of shape (time, y, x) or (y, x).
        Typically the output from DINEOF reconstruction.
    original_data : np.ndarray or np.ma.MaskedArray
        Original data array (same shape as `recon`).
        Used to define valid data ranges and neighborhood statistics.
    mask : np.ndarray
        Binary mask where ocean = 1 and land = 0. Shape (y, x).
    edge_width : int, optional
        Width (in pixels) from coastline to treat as "edge" (default=10).
    smooth_size : int, optional
        Size of the uniform smoothing kernel (default=3).
    replace_edges : bool, optional
        If True, replaces edge pixels with local mean values from the
        interior ocean region. If False (default), values are clipped
        to the local min/max range and then lightly smoothed.

    Returns
    -------
    np.ndarray
        Modified reconstruction array with improved coastal behavior.

    Notes
    -----
    * Edge detection uses a Euclidean distance transform from the land mask.
    * `replace_edges=True` is safer for eliminating spurious edge values
      (fills them with representative ocean means near the coast).
    * `replace_edges=False` clips edges to realistic ranges and applies
      optional smoothing for continuity.
    * Works for 2D or 3D arrays (time, lat, lon).

    Example
    -------
    >>> recon_adj = clip_and_smooth_edges(
    ...     recon=dineof_out,
    ...     original_data=chl_obs,
    ...     mask=ocean_mask,
    ...     edge_width=12,
    ...     smooth_size=5,
    ...     replace_edges=True
    ... )
    """
    # --- Prepare data ---
    orig = np.where(ma.getmaskarray(original_data), np.nan, original_data)
    recon_f = np.where(ma.getmaskarray(recon), np.nan, recon)

    # Compute distance from land and define edge/interior regions
    dist_to_land = distance_transform_edt(mask)
    edge_pixels = dist_to_land <= edge_width
    interior_pixels = dist_to_land > edge_width

    # Compute global per-pixel statistics from original data
    min_vals = np.nanmin(orig, axis=0)
    max_vals = np.nanmax(orig, axis=0)
    mean_vals = np.nanmean(orig, axis=0)

    recon_clipped = recon_f.copy()

    def compute_local_mean(frame, valid_mask, size):
        """Compute neighborhood mean ignoring NaNs."""
        filled = np.nan_to_num(frame, nan=0.0)
        local_sum = uniform_filter(filled, size=size, mode='nearest')
        local_count = uniform_filter(valid_mask.astype(float), size=size, mode='nearest')
        with np.errstate(invalid='ignore', divide='ignore'):
            local_mean = np.where(local_count > 0, local_sum / local_count, np.nan)
        return local_mean

    # --- Process data ---
    if recon.ndim == 3:  # (time, y, x)
        for t in range(recon.shape[0]):
            frame = recon_f[t].copy()
            valid_mask = np.isfinite(frame)

            if replace_edges:
                local_mean = compute_local_mean(
                    frame, valid_mask & interior_pixels, smooth_size
                )
                # Fill edge pixels using local mean, fallback to long-term mean
                frame[edge_pixels] = np.where(
                    np.isnan(local_mean[edge_pixels]),
                    mean_vals[edge_pixels],
                    local_mean[edge_pixels]
                )
            else:
                # Clip to local range and smooth
                frame[edge_pixels] = np.clip(
                    frame[edge_pixels],
                    min_vals[edge_pixels],
                    max_vals[edge_pixels]
                )
                frame = uniform_filter(frame, size=smooth_size, mode='nearest')

            recon_clipped[t] = frame

    else:  # (y, x)
        frame = recon_f.copy()
        valid_mask = np.isfinite(frame)

        if replace_edges:
            local_mean = compute_local_mean(frame, valid_mask & interior_pixels, smooth_size)
            frame[edge_pixels] = np.where(
                np.isnan(local_mean[edge_pixels]),
                mean_vals[edge_pixels],
                local_mean[edge_pixels]
            )
        else:
            frame[edge_pixels] = np.clip(
                frame[edge_pixels],
                min_vals[edge_pixels],
                max_vals[edge_pixels]
            )
            frame = uniform_filter(frame, size=smooth_size, mode='nearest')

        recon_clipped = frame

    return recon_clipped


def blend_coastal_forecast(
    forecast, day0, mask, edge_width=10, blend_width=10
):
    """
    Blend coastal forecast pixels with baseline (day 0) values to reduce
    DINEOF edge artifacts.

    Parameters
    ----------
    forecast : np.ndarray
        Forecast array of shape (y, x) or (time, y, x).
        Each frame corresponds to a DINEOF forecast (e.g., +1, +2, +3 days).
    day0 : np.ndarray
        Baseline day-0 field of shape (y, x), fully valid (gap-free).
    mask : np.ndarray
        Binary mask where ocean = 1, land = 0. Shape (y, x).
    edge_width : int, optional
        Maximum distance (in pixels) from land to be affected by blending.
        Pixels within this distance will be replaced or blended.
    blend_width : int, optional
        Width of the smooth transition zone between the pure day0 zone
        and pure forecast zone (default=10).

    Returns
    -------
    np.ndarray
        Forecast array of the same shape as input, but with coastal
        regions smoothly replaced/blended with day0 values.

    Notes
    -----
    - Uses Euclidean distance from land to identify the coast band.
    - Within `edge_width - blend_width`: forecast is fully replaced by day0.
    - Within the outer `blend_width` zone: linear blending is applied.
    - Outside the edge zone: forecast is unchanged.
    - Works for 2D or 3D forecasts.
    """

    # Compute distance to land (in pixels)
    dist_to_land = distance_transform_edt(mask)

    # Create blending weights based on distance from land
    w = np.ones_like(dist_to_land, dtype=float)

    # Fully replace near the coast
    near_coast = dist_to_land <= (edge_width - blend_width)
    w[near_coast] = 0.0

    # Linearly blend in transition zone
    blend_zone = (dist_to_land > (edge_width - blend_width)) & (dist_to_land <= edge_width)
    w[blend_zone] = (dist_to_land[blend_zone] - (edge_width - blend_width)) / blend_width

    # Beyond edge width, full forecast retained (w=1)

    # Expand weights for time dimension if needed
    if forecast.ndim == 3:
        w3d = np.broadcast_to(w, forecast.shape)
        blended = w3d * forecast + (1 - w3d) * day0[None, :, :]
    else:
        blended = w * forecast + (1 - w) * day0

    return blended


def get_eof2_filled_vars(gap_var, filled_var, second_dineof_nc, eof_work_dir):
    """
    Retrieve and combine EOF2 gap-filled variables from a secondary DINEOF output file.

    This function opens a DINEOF-generated NetCDF file (EOF2 reconstruction),
    extracts a short temporal sequence of both the original gap variable (`gap_var`)
    and the corresponding filled variable (`filled_var`), concatenates them along
    the time dimension, and performs necessary transformations and masking.

    Specifically, it:
      * Extracts the last 4th frame of the original gap variable.
      * Extracts the last 3 frames of the filled variable.
      * Concatenates them along the time axis.
      * If the variable is `"chlor_a"`, exponentiates it (reversing log-transform)
        and masks physically implausible values (`>1000` mg m⁻³).
      * Returns a masked array with invalid values removed.

    Args:
        gap_var (str):
            Name of the original variable with missing data to fill
            (e.g. `"chlor_a"` or `"sst"`).
        filled_var (str):
            Name of the DINEOF-filled counterpart variable
            (e.g. `"chlor_a_filled"`).
        second_dineof_nc (str | Path):
            Filename template or name of the secondary DINEOF NetCDF file.
            May include a format placeholder for variable names (e.g. `"eof2_{:s}.nc"`).
        eof_work_dir (str | Path):
            Directory where the DINEOF NetCDF file is located.

    Returns:
        numpy.ma.MaskedArray:
            3D masked array `(ntime, ny, nx)` containing the stacked gap-filled data.

    Raises:
        FileNotFoundError:
            If the specified DINEOF NetCDF file does not exist.
        KeyError:
            If either `gap_var` or `filled_var` is not found in the dataset.
        Exception:
            For unexpected I/O or processing errors (logged as error).

    Example:
        >>> arr = get_eof2_filled_vars(
        ...     gap_var="chlor_a",
        ...     filled_var="chlor_a_filled",
        ...     second_dineof_nc="dineof_stage2_{:s}.nc",
        ...     eof_work_dir=Path("/data/work")
        ... )
        >>> arr.shape
        (4, 720, 1440)
        >>> arr.mean()
        2.35

    Notes:
        * For `"chlor_a"`, log-transformed values are exponentiated back to linear scale.
        * The returned array is masked for NaNs and unrealistic values (>1000 mg m⁻³).
        * The last 4th frame of the original variable is typically the most recent
          available observed data before the DINEOF prediction frames.
    """
    nc_path = eof_work_dir / gap_var / second_dineof_nc.format(gap_var)

    if not nc_path.exists():
        msg = f"EOF2 NetCDF file not found: {nc_path}"
        logger.error(msg)
        raise FileNotFoundError(msg)

    logger.info("Opening EOF2 NetCDF: %s", nc_path)
    try:
        with Dataset(nc_path, 'r') as nc_eof2:
            if gap_var not in nc_eof2.variables or filled_var not in nc_eof2.variables:
                missing = [v for v in [gap_var, filled_var] if v not in nc_eof2.variables]
                raise KeyError(f"Missing variables in EOF2 file: {', '.join(missing)}")

            now_data = nc_eof2[gap_var][-4:-3, :, :]
            now_data_2d = nc_eof2[gap_var][-4, :, :]
            # orig_data = nc_eof2[gap_var][-3:, :, :]
            recon_data = nc_eof2[filled_var][-3:, :, :]
            mask = nc_eof2['mask'][:, :]
            for_data = blend_coastal_forecast(
                recon_data, now_data_2d, mask, edge_width=15, blend_width=8
            )

            # ✅ concatenate slices
            thevar = ma.concatenate([now_data, for_data], axis=0)

            if gap_var == 'chlor_a':
                logger.debug("Applying exponential transform and masking to chlor_a")
                thevar = ma.exp(thevar)
                thevar = ma.masked_where(thevar > 1000, thevar)
            else:
                thevar = ma.masked_where(thevar > 1, thevar)

            thevar = ma.masked_invalid(thevar)

        logger.info("EOF2 data loaded and masked successfully for %s", gap_var)
        return thevar

    except Exception as e:
        logger.error("Error processing EOF2 file %s: %s", nc_path, e)
        raise
