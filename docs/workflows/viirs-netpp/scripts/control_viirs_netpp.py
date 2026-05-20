#!/usr/bin/env python3
"""
Control script for VIIRS primary productivity production runs.

Purpose
-------
This script is an orchestrator only. It does not download data, compute PP,
or write NetCDF files itself. Instead, it decides which sensor / dtype /
date ranges to run, then calls make_viirs_netpp.py as a subprocess.

Typical uses
------------
1. Run one sensor + one dtype over a date range
2. Run all enabled sensors for one dtype over a date range
3. Run all enabled sensors and dtypes over a date range

Notes
-----
- Sensor enable/disable is controlled from config.yml via sensors.<sensor>.enabled
  if present. If 'enabled' is omitted, the sensor is treated as enabled.
- This lets you keep noaa21 in config but disable it for now.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml
from dateutil.parser import parse


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "config.yml"
MAKE_SCRIPT = SCRIPT_DIR / "make_viirs_netpp.py"


def load_config(config_path: str) -> dict:
    """Load and merge multi-doc YAML config into a single dict."""
    with open(config_path, "r") as fh:
        docs = list(yaml.safe_load_all(fh))
    merged = {}
    for doc in docs:
        if doc:
            merged.update(doc)
    return merged


def get_enabled_sensors(cfg: dict) -> list[str]:
    """
    Return sensors that are enabled in config.

    Rules:
    - If sensors.<sensor>.enabled is missing, treat as enabled.
    - If enabled: false, skip it.
    """
    sensors_cfg = cfg.get("sensors", {})
    enabled = []
    for sensor, meta in sensors_cfg.items():
        if meta.get("enabled", True):
            enabled.append(sensor)
    return enabled


def normalize_date(date_str: str) -> str:
    """Normalize input date to YYYYMMDD for make script."""
    return parse(date_str).strftime("%Y%m%d")


def build_make_cmd(
    start: str,
    end: str,
    sensor: str,
    dtype: str,
    config: str,
    overwrite: bool,
) -> list[str]:
    """Build subprocess command for make_viirs_netpp.py."""
    cmd = [
        sys.executable,
        str(MAKE_SCRIPT),
        "-a", start,
        "-z", end,
        "-s", sensor,
        "-t", dtype,
        "-c", config,
    ]
    if overwrite:
        cmd.append("-o")
    return cmd


def run_one(
    start: str,
    end: str,
    sensor: str,
    dtype: str,
    config: str,
    overwrite: bool,
    dry_run: bool,
) -> int:
    """Run one make job and return subprocess exit code."""
    cmd = build_make_cmd(start, end, sensor, dtype, config, overwrite)

    print("\n" + "=" * 72)
    print(f"Running: sensor={sensor} dtype={dtype} start={start} end={end}")
    print("=" * 72)
    print(" ".join(cmd))

    if dry_run:
        return 0

    result = subprocess.run(cmd)
    return result.returncode


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the daily VIIRS NetPP orchestrator."""
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
        "-s", "--sensor",
        choices=["snpp", "noaa20", "noaa21", "all"],
        default="all",
        help="Sensor to run, or 'all' for all enabled sensors",
    )
    parser.add_argument(
        "-t", "--dtype",
        choices=["nrt", "sq", "both"],
        default="both",
        help="Data type to run, or 'both'",
    )

    parser.add_argument(
        "-c", "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to config.yml",
    )
    parser.add_argument(
        "-o", "--overwrite",
        action="store_true",
        help="Pass overwrite flag through to make script",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands but do not run them",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue other runs even if one fails",
    )

    return parser.parse_args()


def main() -> None:
    """Resolve requested sensors/dtypes/date ranges and run all jobs."""
    args = parse_args()

    cfg = load_config(args.config)

    start = normalize_date(args.start)
    end = normalize_date(args.end)

    if parse(start) > parse(end):
        sys.exit("start date must be before end date")

    enabled_sensors = get_enabled_sensors(cfg)
    if not enabled_sensors:
        sys.exit("No enabled sensors found in config.")

    # Expand the user-facing "all" option only after checking config-enabled sensors.
    if args.sensor == "all":
        sensors = enabled_sensors
    else:
        sensors = [args.sensor]
        if args.sensor not in cfg.get("sensors", {}):
            sys.exit(f"Sensor '{args.sensor}' not found in config.")
        if args.sensor not in enabled_sensors:
            sys.exit(f"Sensor '{args.sensor}' is disabled in config.")

    # Expand "both" into the two concrete product streams consumed by the make script.
    dtypes = ["nrt", "sq"] if args.dtype == "both" else [args.dtype]

    failures = []

    for sensor in sensors:
        for dtype in dtypes:
            rc = run_one(
                start=start,
                end=end,
                sensor=sensor,
                dtype=dtype,
                config=args.config,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
            if rc != 0:
                failures.append((sensor, dtype, rc))
                if not args.keep_going:
                    sys.exit(rc)

    if failures:
        print("\nFailures:")
        for sensor, dtype, rc in failures:
            print(f"  {sensor}/{dtype} failed with exit code {rc}")
        sys.exit(1)

    print("\nAll requested runs completed.")


if __name__ == "__main__":
    main()