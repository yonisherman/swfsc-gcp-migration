#!/usr/bin/env python3
"""
C-HARM Cloud Control Runner

Runs:
1) NRT run (default) — executes make_charm_cloud_v1 in nowcast mode
2) Backfill runs — executes make_charm_cloud_v1 for earlier dates

Key Cloud Run / Docker behaviors:
- Uses CONFIG_PATH env var (default /app/config/config.yaml)
- Uses UTC consistently
- Streams logs to stdout
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
from datetime import datetime, timedelta
import types
import yaml
from src.python.charm_helper_functions import results_exist_in_gcs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MODULE_TO_RUN = "scripts.make_charm_cloud_v1"


def load_config(filepath: str):
    """Load first YAML document containing key 'eof'."""
    with open(filepath, "r") as f:
        for doc in yaml.safe_load_all(f):
            if isinstance(doc, dict) and "eof" in doc:
                return types.SimpleNamespace(**doc)
    raise ValueError(f"No document containing key 'eof' found in config file: {filepath}")


def load_cloud_config(filepath: str) -> dict:
    """Load the GCP/publish YAML doc containing ERDPROD_BUCKET."""
    with open(filepath, "r") as f:
        for doc in yaml.safe_load_all(f):
            if isinstance(doc, dict) and "ERDWORK_BUCKET" in doc:
                return doc
    return {}


def parse_args() -> int:
    """Parse controller options and return the exclusive backfill offset limit."""
    parser = argparse.ArgumentParser(description="Run C-HARM cloud NRT + backfill tasks.")
    parser.add_argument(
        "-b", "--backfill_days",
        type=int,
        default=int(os.getenv("BACKFILL_DAYS", "5")),
        help="Number of additional days to backfill beyond the NRT run. Default 5.",
    )
    args = parser.parse_args()
    backfill_range_end = args.backfill_days + 2
    logger.info("Backfill days requested=%d => running offsets i=2..%d (exclusive upper bound=%d)",
                args.backfill_days, backfill_range_end - 1, backfill_range_end)
    return backfill_range_end


def main() -> None:
    """Run the near-real-time C-HARM job and optional backfill dates."""
    config_path = os.getenv("CONFIG_PATH", "/app/config/config.yaml")
    logger.info("Using config: %s", config_path)

    CONFIG = load_config(config_path)
    cloud_config = load_cloud_config(config_path)
    prod_bucket = cloud_config.get("ERDPROD_BUCKET", "")
    prod_root = cloud_config.get("PUBLISH_TARGETS", {}).get("prod", {}).get("root", "edge")
    
    if not os.path.isdir(CONFIG.home):
        raise SystemExit(f"Configured home directory does not exist: {CONFIG.home}")
    os.chdir(CONFIG.home)

    backfill_range_end = parse_args()

    nrt_cmd = (
        f"PYTHONPATH={CONFIG.home} "
        f"{CONFIG.python} -m {MODULE_TO_RUN}"
    )

    # NRT run — make_charm_cloud_v1 handles its own duplicate check via local results dir
    # but since that's ephemeral, also check the bucket for today's NRT date (today - 1)
    now_utc = datetime.utcnow()
    nrt_date_str = (now_utc - timedelta(days=1)).strftime("%Y%m%d")
    


    if results_exist_in_gcs(nrt_date_str, prod_bucket, prod_root):
        logger.info("Skipping NRT — results for %s already in bucket", nrt_date_str)
    else:
        logger.info("NRT command: %s", nrt_cmd)
        subprocess.run(nrt_cmd, shell=True, check=False)

    # Backfill runs
    for i in range(2, backfill_range_end):
        date_obj = now_utc - timedelta(days=i)
        date_str = date_obj.strftime("%Y%m%d")

        if results_exist_in_gcs(date_str, prod_bucket, prod_root):
            logger.info("Skipping backfill %s — results already in bucket", date_str)
            continue

        backfill_cmd = f"{nrt_cmd} -d {date_str} -b"
        logger.info("Backfill command for %s (UTC): %s", date_str, backfill_cmd)
        subprocess.run(backfill_cmd, shell=True, check=False)


if __name__ == "__main__":
    main()
