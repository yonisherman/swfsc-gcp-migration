#!/usr/bin/env python3
"""
Legacy-compatible fronts runner (uses cv2 contour logic).

Unified setup:
- Always imports: canny_lib.py
- Region selection calls:
    - WC  -> canny_lib.myCanny_WC
    - ATL -> canny_lib.myCanny_ATL

Caller provides a LOCAL MUR file path that was just downloaded by the MUR loop.

Usage:
  python3 /app/scripts/fronts/calc_mur_fronts_from_file_legacy.py \
    --src /tmp/mur_sync_YYYYMMDD/<murfile>.nc \
    --region WC \
    --out /tmp/mur_sync_YYYYMMDD/Canny_Front_YYYYMMDD.nc \
    --day YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from netCDF4 import Dataset, date2num

# Ensure we import canny_lib/Canny1/Canny2 from THIS folder (Cloud Run-safe)
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

WC_BBOX = dict(lat_min=22.0, lat_max=51.0, lon_min=-135.0, lon_max=-105.0)
ATL_BBOX = dict(lat_min=20.0, lat_max=50.0, lon_min=-90.0, lon_max=-60.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Local MUR41 .nc path (downloaded file)")
    ap.add_argument("--region", required=True, choices=["WC", "ATL"])
    ap.add_argument("--out", required=True, help="Output NetCDF path")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (UTC day being processed)")
    args = ap.parse_args()

    out_dir = os.path.dirname(os.path.abspath(args.out)) + "/"
    os.makedirs(out_dir, exist_ok=True)

    when = datetime.fromisoformat(args.day + "T00:00:00")

    # Unified import (you pasted unified code into canny_lib.py)
    import canny_lib as lib

    if args.region == "WC":
        bbox = WC_BBOX
        myCanny = lib.myCanny_WC
    else:
        bbox = ATL_BBOX
        myCanny = lib.myCanny_ATL

    # IMPORTANT: pass FULL PATH in GCP (no file_base / basename assumptions)
    sst_mur, lon_mur, lat_mur = lib.extract_mur(
        args.src,
        file_base=None,
        lat_min=bbox["lat_min"], lat_max=bbox["lat_max"],
        lon_min=bbox["lon_min"], lon_max=bbox["lon_max"],
    )

    tmp_nc = lib.create_canny_nc(
        when.year, when.month, when.day,
        base_dir=out_dir,
        lat_min=bbox["lat_min"], lat_max=bbox["lat_max"],
        lon_min=bbox["lon_min"], lon_max=bbox["lon_max"],
    )

    # Preserve original call signature / mask behavior
    edges, x_gradient, y_gradient, magnitude = myCanny(sst_mur, ~sst_mur.mask)

    contours = lib.my_contours(edges)
    contour_edges, _ = lib.contours_to_edges(contours, edges.shape)
    contour_edges = lib.ma.array(contour_edges, mask=sst_mur.mask)

    # Write exactly like the legacy cron scripts
    root = Dataset(tmp_nc, "a")
    root.variables["time"][0] = date2num(when, units="Hour since 1970-01-01T00:00:00Z")
    root.variables["edges"][0, 0, :, :] = contour_edges[:, :]
    root.variables["x_gradient"][0, 0, :, :] = x_gradient[:, :]
    root.variables["y_gradient"][0, 0, :, :] = y_gradient[:, :]
    root.variables["magnitude_gradient"][0, 0, :, :] = magnitude[:, :]
    root.close()

    # Rename to your desired output name
    if os.path.abspath(tmp_nc) != os.path.abspath(args.out):
        os.replace(tmp_nc, args.out)


if __name__ == "__main__":
    main()