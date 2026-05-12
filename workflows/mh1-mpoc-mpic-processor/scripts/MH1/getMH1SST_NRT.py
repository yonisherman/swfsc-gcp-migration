"""
Overview
--------
This script automates the retrieval and distribution of Level 3 near-real-time (NRT) MODIS-Aqua
SST satellite products for multiple time periods (1-day, 8-day, monthly).

For each downloaded file it also computes a masked variant (qual_sst >= 0 only) and uploads
both the raw and masked files to GCS via send_to_servers().

It is the Cloud Run / GCS equivalent of the legacy getMHSST*_NRT_sftp.py scripts.
Key differences from legacy:
  - Staging checks use list_bucket_content() (GCS) instead of pysftp.
  - All writes go to /tmp (the only writable path in Cloud Run).
  - Masked NetCDF is created with netCDF4 directly; no ncgen CDL required.
  - send_to_servers() handles all GCS uploads.

Usage
-----
::
    python getMH1SST_NRT.py

Description
-----------
1. Iterates over periods: DAY (1day), 8D (8day), MO (mday).
2. Queries NASA OceanColor file_search API using dtid=1061 (MODIS-Aqua SST NRT L3m).
3. Checks GCS for existing files; downloads only missing ones.
4. For each downloaded file:
   a. Uploads raw SST file to GCS via send_to_servers().
   b. Applies qual_sst >= 0 mask, writes a new NetCDF, uploads masked file.
   c. Removes local copies.
"""

if __name__ == "__main__":
    from datetime import datetime
    import os
    import subprocess
    from pathlib import Path
    from netCDF4 import Dataset, date2num
    import numpy as np
    import numpy.ma as ma
    from roylib import CFG, list_bucket_content, send_to_servers

    # --- Config Validation ---
    for _k in ("ERDPROD_BUCKET", "HOME_DIR", "NASA_FILE_SEARCH_URL", "NASA_GETFILE_URL"):
        if not CFG.get(_k):
            raise KeyError(f"CFG['{_k}'] is required but not set")

    # --- Constants ---
    # dtid=1061: MODIS-Aqua SST NRT L3 mapped
    DTID = "1061"

    periods = {
        "DAY": {"dir": "1day", "flag": "1"},
        "8D":  {"dir": "8day", "flag": "8"},
        "MO":  {"dir": "mday", "flag": "m"},
    }

    now = datetime.now()
    start_date = f"{now.year}-01-01"
    end_date = now.strftime("%Y-%m-%d")
    bucket_name = CFG.get("ERDPROD_BUCKET")

    home_dir = Path(CFG.get("HOME_DIR", "/tmp"))
    cookie_path = home_dir / ".urs_cookies"
    download_dir = home_dir / "data"
    resources_dir = home_dir / "resources"
    download_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    def _make_masked_nc(src_path: Path, period_flag: str) -> Path | None:
        """
        Read src_path, mask SST where qual_sst < 0, write a new NetCDF to /tmp.
        Returns the path of the masked file, or None if creation failed.
        """
        try:
            with Dataset(str(src_path), "r") as ds:
                sst_raw = ds.variables["sst"][:, :]
                qual = ds.variables["qual_sst"][:, :]
                # Preserve global attributes we want to copy
                global_attrs = {a: getattr(ds, a) for a in ds.ncattrs()}
                lat_vals = ds.variables["lat"][:]
                lon_vals = ds.variables["lon"][:]
        except Exception as exc:
            print(f"[WARN] Could not read {src_path.name} for masking: {exc}")
            return None

        sst_masked = ma.array(sst_raw, mask=(qual < 0), fill_value=-999.0)

        # Build output filename: insert 'Masked' before the final product suffix.
        # Legacy pattern: AQUA_MODIS.20240101_20240101.L3m.DAY.SST.sst.4km.NRT.nc
        #             ->  AQUA_MODIS.20240101_20240101.L3m.DAY.SST.sstMasked.4km.NRT.nc
        stem = src_path.stem  # filename without .nc
        masked_name = stem.replace(".sst.", ".sstMasked.") + ".nc"
        masked_path = download_dir / masked_name

        # Derive a representative centre time from the filename date stamp.
        # AQUA_MODIS.YYYYMMDD... — date starts at char 11
        try:
            fname = src_path.name
            y, m, d = int(fname[11:15]), int(fname[15:17]), int(fname[17:19])
            if period_flag == "1":
                centre = datetime(y, m, d, 12, 0, 0)
            elif period_flag == "8":
                # end date follows underscore at position 20 for 8-day files
                y2, m2, d2 = int(fname[20:24]), int(fname[24:26]), int(fname[26:28])
                t1 = date2num(datetime(y, m, d), units="seconds since 1970-01-01")
                t2 = date2num(datetime(y2, m2, d2), units="seconds since 1970-01-01")
                centre_ts = (t1 + t2) / 2.0
            else:  # monthly: use the 16th
                centre = datetime(y, m, 16, 0, 0)
        except Exception:
            centre = now  # fallback

        if period_flag == "8":
            time_val = centre_ts
        else:
            time_val = date2num(centre, units="seconds since 1970-01-01")

        try:
            with Dataset(str(masked_path), "w", format="NETCDF4_CLASSIC") as out:
                # Copy dimensions
                out.createDimension("lat", len(lat_vals))
                out.createDimension("lon", len(lon_vals))
                out.createDimension("time", 1)

                # Copy global attrs and add provenance
                for attr, val in global_attrs.items():
                    try:
                        setattr(out, attr, val)
                    except Exception:
                        pass
                out.source_data = src_path.name
                out.creation_date = str(now)
                out.mask_applied = "qual_sst >= 0"

                # Time
                t_var = out.createVariable("time", "f8", ("time",))
                t_var.units = "seconds since 1970-01-01"
                t_var[0] = time_val

                # Lat / Lon
                lat_var = out.createVariable("lat", "f4", ("lat",))
                lat_var.units = "degrees_north"
                lat_var[:] = lat_vals

                lon_var = out.createVariable("lon", "f4", ("lon",))
                lon_var.units = "degrees_east"
                lon_var[:] = lon_vals

                # Masked SST
                sst_var = out.createVariable(
                    "sstMasked", "f4", ("time", "lat", "lon"),
                    fill_value=-999.0, zlib=True, complevel=2
                )
                sst_var.long_name = "Sea Surface Temperature (qual_sst >= 0 mask applied)"
                sst_var.units = "degree_C"
                sst_var[0, :, :] = sst_masked[:, :]

        except Exception as exc:
            print(f"[WARN] Could not write masked file {masked_name}: {exc}")
            return None

        return masked_path
    # -----------------------------------------------------------------------

    for nasa_per, per_info in periods.items():

        # --- 1. GCS staging inventory ---
        stage_dir_raw    = f"satellite/MH1_NRT/sst/{per_info['dir']}"
        stage_dir_masked = f"satellite/MH1_NRT/sstMask/{per_info['dir']}"

        raw_staged    = list_bucket_content(bucket_name, stage_dir_raw)
        staged_flist  = [os.path.basename(f).strip() for f in raw_staged] if raw_staged else []

        print(f"\n[START] NRT SST {nasa_per} -> {stage_dir_raw}")

        # --- 2. Query NASA file_search API ---
        query_params = (
            f"results_as_file=1&sensor_id=7&dtid={DTID}&subType=1&resolution_id=4km"
            f"&sdate={start_date} 00:00:00&edate={end_date} 23:59:59"
            f"&prod_id=sst&period={nasa_per}"
        )
        query_url = f'{CFG["NASA_FILE_SEARCH_URL"]} --post-data="{query_params}"'
        query_out = resources_dir / f"sst_nrt_{nasa_per}_list.txt"

        subprocess.run(
            f"wget -4 --no-check-certificate -O {query_out} {query_url}",
            shell=True, capture_output=True
        )

        if not query_out.exists():
            print(f"[WARN] Query output not found for {nasa_per}, skipping.")
            continue

        # --- 3. Download & process missing files ---
        with open(query_out, "r") as f:
            for line in f:
                fname = line.strip()
                if not fname or "No Results Found" in fname or "<html" in fname.lower():
                    continue

                if fname in staged_flist:
                    continue  # already in GCS

                out_path = download_dir / fname
                print(f"[DOWNLOAD] {fname}")

                wget_cmd = (
                    f'wget -4 --netrc --auth-no-challenge=on --keep-session-cookies '
                    f'--load-cookies {cookie_path} --save-cookies {cookie_path} '
                    f'--content-disposition --no-check-certificate '
                    f'-O "{out_path}" {CFG["NASA_GETFILE_URL"]}/{fname}'
                )
                result = subprocess.run(wget_cmd, shell=True, capture_output=True)

                if result.returncode != 0:
                    print(f"[WARN] wget failed for {fname}, skipping.")
                    continue

                # -- Upload raw SST --
                try:
                    send_to_servers(str(out_path), "MH1_NRT/sst", per_info["flag"])
                except Exception as exc:
                    print(f"[ERROR] Raw upload failed for {fname}: {exc}")

                # -- Build and upload masked SST --
                masked_path = _make_masked_nc(out_path, per_info["flag"])
                if masked_path and masked_path.exists():
                    try:
                        send_to_servers(str(masked_path), "MH1_NRT/sstMask", per_info["flag"])
                    except Exception as exc:
                        print(f"[ERROR] Masked upload failed for {masked_path.name}: {exc}")
                    finally:
                        masked_path.unlink(missing_ok=True)

                # -- Cleanup raw download --
                out_path.unlink(missing_ok=True)

    print("\n[COMPLETE] NRT SST Suite finished.")