#!/usr/bin/env python3
"""
Control script for VIIRS monthly primary productivity composites.

Purpose
-------
Orchestrator only - decides which sensor / dtype / year-month combinations
to run, then calls make_viirs_netpp_monthly.py as a subprocess for each.

Typical uses
------------
1. Run previous month for all enabled sensors (default cron mode)
2. Run a specific year-month for one sensor
3. Backfill a full year of monthly composites

Examples
--------
  # Previous month, all enabled sensors, both dtypes (cron mode)
  python control_viirs_netpp_monthly.py

  # Specific month, one sensor
  python control_viirs_netpp_monthly.py -y 2025 -m 3 -s noaa20 -t nrt

  # Backfill all of 2024 for SNPP SQ
  python control_viirs_netpp_monthly.py -y 2024 --all-months -s snpp -t sq

  # Dry run to see what would be submitted
  python control_viirs_netpp_monthly.py --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path

import yaml
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta

SCRIPT_DIR    = Path(__file__).resolve().parent
REPO_ROOT     = SCRIPT_DIR.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "config.yml"
MAKE_SCRIPT   = SCRIPT_DIR / "make_viirs_netpp_monthly.py"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    with open(config_path, "r") as fh:
        docs = list(yaml.safe_load_all(fh))
    merged = {}
    for doc in docs:
        if doc:
            merged.update(doc)
    return merged


def get_enabled_sensors(cfg: dict) -> list[str]:
    return [
        s for s, meta in cfg.get("sensors", {}).items()
        if meta.get("enabled", True)
    ]


def sensor_start_ym(cfg: dict, sensor: str) -> tuple[int, int]:
    """Return (year, month) of the sensor's operational start."""
    raw = cfg.get("sensors", {}).get(sensor, {}).get("start_date", "2012-01-02")
    dt  = parse(str(raw))
    return dt.year, dt.month


# ---------------------------------------------------------------------------
# Run helpers
# ---------------------------------------------------------------------------

def build_cmd(
    year: int,
    month: int,
    sensor: str,
    dtype: str,
    config: str,
    overwrite: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        str(MAKE_SCRIPT),
        "-y", str(year),
        "-m", str(month),
        "-s", sensor,
        "-t", dtype,
        "-c", config,
    ]
    if overwrite:
        cmd.append("-o")
    return cmd


def run_one(
    year: int,
    month: int,
    sensor: str,
    dtype: str,
    config: str,
    overwrite: bool,
    dry_run: bool,
) -> int:
    cmd = build_cmd(year, month, sensor, dtype, config, overwrite)

    print("\n" + "=" * 72)
    print(f"Monthly composite: {year}-{month:02d}  sensor={sensor}  dtype={dtype}")
    print("=" * 72)
    print(" ".join(cmd))

    if dry_run:
        return 0

    result = subprocess.run(cmd)
    return result.returncode


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    now = datetime.utcnow()
    prev = date(now.year, now.month, 1) - relativedelta(months=1)

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-y", "--year", type=int, default=prev.year,
        help=f"Year to composite (default: previous month's year = {prev.year})",
    )
    parser.add_argument(
        "-m", "--month", type=int, default=prev.month,
        choices=range(1, 13), metavar="MONTH",
        help=f"Month to composite 1-12 (default: previous month = {prev.month})",
    )
    parser.add_argument(
        "--all-months", action="store_true",
        help="Composite every month of --year instead of just --month",
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
        help="Data type: nrt, sq, or both",
    )

    parser.add_argument(
        "-c", "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to config.yml",
    )
    parser.add_argument(
        "-o", "--overwrite", action="store_true",
        help="Overwrite existing GCS output files",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print commands but do not execute",
    )
    parser.add_argument(
        "--keep-going", action="store_true",
        help="Continue after failures",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    cfg  = load_config(args.config)

    # Resolve sensors
    enabled = get_enabled_sensors(cfg)
    if args.sensor == "all":
        sensors = enabled
    else:
        if args.sensor not in cfg.get("sensors", {}):
            sys.exit(f"Sensor '{args.sensor}' not found in config.")
        if args.sensor not in enabled:
            sys.exit(f"Sensor '{args.sensor}' is disabled in config.")
        sensors = [args.sensor]

    # Resolve dtypes
    dtypes = ["nrt", "sq"] if args.dtype == "both" else [args.dtype]

    # Resolve months to process
    if args.all_months:
        months = list(range(1, 13))
    else:
        months = [args.month]

    # Current UTC year-month - never composite an incomplete month
    now        = datetime.utcnow()
    cur_year   = now.year
    cur_month  = now.month

    failures: list[tuple] = []

    for sensor in sensors:
        start_yr, start_mo = sensor_start_ym(cfg, sensor)

        for dtype in dtypes:
            for month in months:
                year = args.year

                # Skip months before the sensor was operational
                if (year, month) < (start_yr, start_mo):
                    print(
                        f"  ⏭  {year}-{month:02d} [{sensor}/{dtype}] "
                        f"before sensor start ({start_yr}-{start_mo:02d}) - skipping."
                    )
                    continue

                # Skip the current (incomplete) month
                if (year, month) >= (cur_year, cur_month):
                    print(
                        f"  ⏭  {year}-{month:02d} [{sensor}/{dtype}] "
                        "is the current or a future month - skipping."
                    )
                    continue

                rc = run_one(
                    year=year,
                    month=month,
                    sensor=sensor,
                    dtype=dtype,
                    config=args.config,
                    overwrite=args.overwrite,
                    dry_run=args.dry_run,
                )

                if rc != 0:
                    failures.append((sensor, dtype, year, month, rc))
                    if not args.keep_going:
                        sys.exit(rc)

    if failures:
        print("\nFailures:")
        for sensor, dtype, yr, mo, rc in failures:
            print(f"  {sensor}/{dtype}  {yr}-{mo:02d}  exit={rc}")
        sys.exit(1)

    print("\nAll monthly composites completed.")


if __name__ == "__main__":
    main()