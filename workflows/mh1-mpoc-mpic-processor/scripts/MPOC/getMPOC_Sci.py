"""
Overview
--------
This script automates the retrieval and distribution of Level-3 Science Quality (Standard) 
MODIS-Aqua Particulate Organic Carbon (POC) products for 1-day, 8-day, and monthly 
periods. It queries NASA for refined science products (dtid=1102) and publishes 
to ``ERDprod/satellite/MPOC/<period>``.

Usage
-----
::
    python getMPOC_Sci.py
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
        stage_dir = f"satellite/MPOC/{per_info['dir']}"
        print(f"\n[START] Processing MPOC Science {nasa_per} -> {stage_dir}")

        raw_staged = list_bucket_content(bucket_name, stage_dir)
        staged_flist = [os.path.basename(f).strip() for f in raw_staged] if raw_staged else []

        # dtid=1102 for Standard Science Quality
        query_params = (
            f"results_as_file=1&sensor_id=7&dtid=1102&subType=1&resolution_id=4km"
            f"&sdate={start_date} 00:00:00&edate={end_date} 23:59:59"
            f"&prod_id=poc&period={nasa_per}"
        )
        
        query_url = f'{CFG["NASA_FILE_SEARCH_URL"]} --post-data="{query_params}"'
        query_out = home_dir / "resources" / f"mpoc_sci_{nasa_per}_fileNames.txt"
        query_out.parent.mkdir(parents=True, exist_ok=True)

        subprocess.run(f"wget -4 --no-check-certificate -O {query_out} {query_url}", shell=True, capture_output=True)

        if not query_out.exists(): continue
        
        with open(query_out, "r") as f:
            for line in f:
                fname = line.strip()
                if not fname or "No Results Found" in fname or "<html" in fname.lower():
                    continue
                
                if fname not in staged_flist:
                    out_path = download_dir / fname
                    print(f"[PROCESS] Downloading {fname}")
                    
                    wget_cmd = (
                        f'wget -4 --netrc --auth-no-challenge=on --keep-session-cookies '
                        f'--load-cookies {cookie_path} --save-cookies {cookie_path} '
                        f'--content-disposition --no-check-certificate '
                        f'-O "{out_path}" {CFG["NASA_GETFILE_URL"]}/{fname}'
                    )
                    
                    result = subprocess.run(wget_cmd, shell=True, capture_output=True)

                    if result.returncode == 0:
                        try:
                            # Published to MPOC (no _NRT suffix)
                            send_to_servers(str(out_path), "MPOC", per_info['flag'])
                        finally:
                            if out_path.exists(): os.remove(out_path)
            
    print("\n[COMPLETE] MPOC Science Suite finished.")