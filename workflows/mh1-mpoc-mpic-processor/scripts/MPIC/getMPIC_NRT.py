"""
Overview
--------
This script automates the retrieval and distribution of Level-3 near-real-time (NRT) 
MODIS-Aqua Particulate Inorganic Carbon (PIC) products for 1-day, 8-day, and monthly 
periods. It queries the NASA OceanColor file search API for available PIC products 
in the current year, checks which files are already staged in the production GCS 
bucket under ``ERDprod/satellite/MPIC_NRT/<period>``, and downloads only those 
that are missing. Each new file is then uploaded to the production bucket using 
``send_to_servers`` and removed locally.

Usage
-----
::
    python getMPIC_NRT.py

- No command-line arguments required.
- Must be run from a system with valid Earthdata Login and GCS access.

Description
-----------
1. **Configuration and Date Setup**
   - Validates presence of required configuration keys.
   - Defines start date (Jan 1 of the current year) and end date (today).

2. **Check for Existing Files in GCS**
   - Iterates through periods (1day, 8day, mday) and retrieves staged file list.

3. **Query NASA OceanColor API**
   - Constructs POST requests to the OceanColor API using dtid=1055 and prod_id=pic.

4. **Download Missing Files**
   - Uses ``wget`` with Earthdata credentials to download missing files to writable /tmp.

5. **Upload to Production Bucket**
   - Publishes each file to ``satellite/MPIC_NRT/<period>/<filename>``.
"""

if __name__ == "__main__":
    from datetime import datetime
    import os
    import subprocess
    from pathlib import Path
    from roylib import *

    for _k in ("ERDPROD_BUCKET", "HOME_DIR", "NASA_OCEANDATA_URL"):
        if not CFG.get(_k):
            raise KeyError(f"CFG['{_k}'] is required but not set")

    now = datetime.now()
    start_date = f"{now.year}-01-01"
    end_date = now.strftime("%Y-%m-%d")
    bucket_name = CFG.get("ERDPROD_BUCKET")

    # Map NASA periods to GCS directories and interval flags
    periods = {
        "DAY": {"dir": "1day", "flag": "1"},
        "8D":  {"dir": "8day", "flag": "8"},
        "MO":  {"dir": "mday", "flag": "m"}
    }

    home_dir = Path(CFG.get("HOME_DIR", "/tmp"))
    cookie_path = home_dir / ".urs_cookies"
    download_dir = home_dir / "data"
    download_dir.mkdir(parents=True, exist_ok=True)

    for nasa_per, per_info in periods.items():
        stage_dir = f"satellite/MPIC_NRT/{per_info['dir']}"
        print(f"\n[START] Processing MPIC NRT {nasa_per} -> {stage_dir}")

        raw_staged = list_bucket_content(bucket_name, stage_dir)
        staged_flist = [os.path.basename(f).strip() for f in raw_staged] if raw_staged else []

        query_params = (
            f"results_as_file=1&sensor_id=7&dtid=1055&subType=1&resolution_id=4km"
            f"&sdate={start_date} 00:00:00&edate={end_date} 23:59:59"
            f"&prod_id=pic&period={nasa_per}"
        )
        
        query_url = f'{CFG["NASA_FILE_SEARCH_URL"]} --post-data="{query_params}"'
        query_out = home_dir / "resources" / f"mpic_nrt_{nasa_per}_fileNames.txt"
        query_out.parent.mkdir(parents=True, exist_ok=True)

        subprocess.run(f"wget -4 --no-check-certificate -O {query_out} {query_url}", shell=True, capture_output=True)

        if not query_out.exists(): continue
        
        query_flist = []
        with open(query_out, "r") as f:
            for line in f:
                fname = line.strip()
                if not fname or "No Results Found" in fname or "<html" in fname.lower():
                    continue
                query_flist.append(fname)

        if len(query_flist) > 0:
            for fname in query_flist:
                if fname not in staged_flist:
                    out_path = download_dir / fname
                    print(f"[PROCESS] {fname} is missing. Downloading...")
                    
                    wget_cmd = (
                        f'wget -4 --netrc --auth-no-challenge=on --keep-session-cookies '
                        f'--load-cookies {cookie_path} --save-cookies {cookie_path} '
                        f'--content-disposition --no-check-certificate '
                        f'-O "{out_path}" {CFG["NASA_GETFILE_URL"]}/{fname}'
                    )
                    
                    result = subprocess.run(wget_cmd, shell=True, capture_output=True)

                    if result.returncode == 0:
                        try:
                            send_to_servers(str(out_path), "MPIC_NRT", per_info['flag'])
                        finally:
                            if out_path.exists(): os.remove(out_path)
        else:
            print(f"[INFO] No new {nasa_per} files found.")

    print("\n[COMPLETE] MPIC NRT Suite finished.")