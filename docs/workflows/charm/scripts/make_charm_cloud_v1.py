#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Iterable
import sys
import numpy as np
from dateutil.parser import parse
import re
from pathlib import Path
from src.python.charm_helper_functions import (
    charms_nc3,
    parse_args,
    ensure_dirs,
    list_local_l3_files,
    needed_l3_filenames_for_period,
    archive_charm_outputs,
    load_config,
    delete_files_in_directory,
    publish_results_to_gcs,
    sync_processed_l3_from_work_bucket
)

from src.python.charm_data_process_functions import (
    regrid_stack_wcofs_st,
    create_advection_step,
    advect,
)

from src.python.charm_dineof_functions import (
    get_eof2_filled_vars,
    process_charm_files_for_day,
    concat_l3_files,
    mask_and_log_transform,
    run_first_dineof,
    run_second_dineof,
    prepare_second_dineof_inputs,
    update_eof_files,
)

from src.python.charm_model_functions import forecast


def main(argv: Iterable[str] | None = None) -> int:
    """Run the end-to-end C-HARM processing pipeline for one target date."""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    config_path = os.getenv("CONFIG_PATH", "/app/config/config.yaml")
    config = load_config(config_path)


    # Use configured base dir if present; otherwise just stay where we are.
    try:
        os.chdir(str(config.base_dir))
    except Exception:
        logger.warning("Could not chdir to config.base_dir=%s; continuing", config.base_dir)

    # Parse args: (date, backfill flag, overwrite)
    now, arg_bkfill, overwrite = parse_args(argv)

    eof_nc = config.eof_nc
    sat_vars = config.sat_vars

    # Local persistent-ish (within container lifetime) dirs
    viirs_in_dir = config.data_dir / "nasa_source_data"
    processed_viirs_dir = config.data_dir / "processed_nasa_data"
    results_dir = config.data_dir / "results"


    sync_processed_l3_from_work_bucket(processed_viirs_dir, config)

    # Cloud Run ephemeral scratch root (from config)
    stage_root = getattr(config, "stage_root", Path("/tmp/charm"))
    stage_root = Path(stage_root)
    ensure_dirs(stage_root)

    # Put heavy intermediates under stage_root (Cloud Run friendly)
    work_root = stage_root / "work_dirs"
    dineof_root = stage_root / "dineof"
    ensure_dirs(work_root, dineof_root)

    if arg_bkfill:
        work_dir = work_root / "bf_work"
        l3_work_dir = work_root / "bf_L3_work"
        eof_init_dir = config.config_dirs / "bf_dineof"
        eof_work_dir = config.data_dir / "bf_dineof"   # matches /tmp/data/bf_dineof/ in .init files
        dine_now_pre = "now_bf_"
        dine_for_pre = "for_bf_"
        now_satellite = now
        now_wcofs = now + timedelta(days=1)
    else:
        work_dir = work_root / "work"
        l3_work_dir = work_root / "L3_work"
        eof_init_dir = config.config_dirs / "dineof"
        eof_work_dir = config.data_dir / "dineof"      # matches /tmp/data/dineof/ in .init files
        dine_now_pre = "now_"
        dine_for_pre = "for_"
        now_satellite = now - timedelta(days=1)
        now_wcofs = now

    combotimes = [
        now_satellite,
        now_satellite + timedelta(days=1),
        now_satellite + timedelta(days=2),
        now_satellite + timedelta(days=3),
    ]

    ensure_dirs(
        work_dir,
        l3_work_dir,
        eof_work_dir,
        viirs_in_dir,
        processed_viirs_dir,
        results_dir,
    )

    logger.info("Clean work dir: %s", work_dir)
    patterns = [re.compile(r"o\d+wcofs"), re.compile(r"output_*"), re.compile(r"st*")]
    for pattern in patterns:
        delete_files_in_directory(work_dir, pattern=pattern)
        logger.info("Deletion of files in work dir with pattern: %s", pattern.pattern)

    # variables for DINEOF
    eof_init_template = "{eof}{{param}}_v4.init"
    pre_int_eof1 = eof_init_template.format(eof=dine_now_pre)
    pre_int_eof2 = eof_init_template.format(eof=dine_for_pre)

    now_date_str = f"{now_satellite:%Y%m%d}"
    logger.info("Begin processing %s", now_satellite)

    # Check for existing results
    results_year_dir = results_dir / str(now_satellite.year)
    ensure_dirs(results_year_dir)
    results_list = (
        [f for f in os.listdir(results_year_dir) if "charm_v3_forecast_0day" in f]
        if os.path.isdir(results_year_dir)
        else []
    )
    results_dates = [f.split("_")[-1].replace(".nc", "") for f in results_list]

    if now_date_str in results_dates and not overwrite:
        logger.error("C-HARM products exist for this date. No overwrite selected.")
        raise SystemExit(1)

    # Prepare L3 inputs
    logger.info(
        "Starting L3 creation for reference date %s (backfill=%s)",
        now.date(), arg_bkfill
    )
    local_l3 = list_local_l3_files(processed_viirs_dir)
    needed = needed_l3_filenames_for_period(now_satellite, days=getattr(config, "history_days_required", 180))
    missing = [fn for fn in needed if fn not in local_l3]
    logger.info("Need %d L3 files, missing %d", len(needed), len(missing))

    created = 0
    for fn in missing:
        try:
            date_part = fn.split("_")[1]
            fd = parse(date_part)
            process_charm_files_for_day(
                fd,
                config.nasa_base_url,
                viirs_in_dir,
                l3_work_dir,
                config.res_dir,
                config.cwutil_dir,
                processed_viirs_dir,
                config.gridding,
                sat_vars.gappy,
            )
            created += 1
        except Exception as exc:
            logger.exception("Error processing %s: %s", fn, exc)
    logger.info("L3 creation complete: created %d new files", created)
    
    # Sync any newly created L3 files back to work bucket for future runs
    if created > 0 and config.erdwork_bucket:
        gcs_dst = f"gs://{config.erdwork_bucket}/{config.work_l2_prefix}"
        logger.info("Syncing %d new L3 files back to work bucket: %s", created, gcs_dst)
        from src.python.charm_helper_functions import run_cmd as _run_cmd
        _run_cmd(
            ["gsutil", "-m", "rsync", "-r", str(processed_viirs_dir), gcs_dst],
            msg="gsutil rsync L3 back to work bucket"
        )

    # Verify L3 file availability
    local_l3 = list_local_l3_files(processed_viirs_dir)
    l3_files_to_use = sorted(fn for fn in needed if fn in local_l3)
    if not l3_files_to_use:
        raise SystemExit("No L3 files available to use.")

    if max(needed) not in l3_files_to_use:
        logger.error("Latest L3 not found. Found: %s, Needed: %s",
                     max(l3_files_to_use), max(needed))
        sys.exit(1)

    concat_l3_files(l3_files_to_use, sat_vars.gappy, eof_nc.first, processed_viirs_dir, eof_work_dir)
    mylatm, mylonm, themask = mask_and_log_transform(
        sat_vars.gappy, eof_nc.first, eof_work_dir, config.res_dir
    )

    model_lon_grid, model_lat_grid = np.meshgrid(mylonm, mylatm)

    # WCOFS merge and regrid
    data_dict = regrid_stack_wcofs_st(
        now_wcofs,
        work_dir,
        config.res_dir,
        model_lon_grid,
        model_lat_grid,
        config.wcofs_st_config,
    )

    # First DINEOF
    missing = [
        str(eof_work_dir / band / eof_nc.first.format(band))
        for band in sat_vars.gappy
        if not (eof_work_dir / band / eof_nc.first.format(band)).exists()
        ]
    if missing:
        logger.error("Skipping DINEOF — input files not created by concat_l3_files: %s", missing)
        raise RuntimeError(f"DINEOF inputs missing: {missing}")

    run_first_dineof(sat_vars.gappy, pre_int_eof1, config.eof_dir, eof_init_dir)
    
    prepare_second_dineof_inputs(
        eof_nc.first, eof_nc.second, sat_vars.gappy, sat_vars.filled, eof_work_dir
    )

    # Advection step
    x, y, rtime = create_advection_step(
        now_wcofs, 7, work_dir, mylonm, mylatm,
        model_lon_grid, model_lat_grid, themask
    )

    time_last = update_eof_files(
        eof_work_dir, eof_nc.first, eof_nc.second,
        sat_vars.filled, sat_vars.gappy,
        x, y,
        model_lon_grid, model_lat_grid,
        themask, rtime, advect
    )

    time_last = [time_last.item()]
    rtime = rtime.tolist()
    model_time = [*time_last, *rtime]
    logger.info("model_time: %s", model_time)

    # Second DINEOF
    run_second_dineof(sat_vars.gappy, pre_int_eof2, config.eof_dir, eof_init_dir)
    logger.info("C-HARM processing complete for %s", now_satellite)

    for cntr, ovar in enumerate(sat_vars.gappy):
        data_dict[ovar] = get_eof2_filled_vars(
            ovar, sat_vars.filled[cntr], eof_nc.second, eof_work_dir
        )

    # Run C-HARM model
    pn, pda, pca, mchla, m486, m551 = forecast(
        np.flip(data_dict["chlor_a"], axis=1),
        np.flip(data_dict["Rrs_489"], axis=1),
        np.flip(data_dict["Rrs_556"], axis=1),
        data_dict["thesalt"], data_dict["thetemp"], combotimes
    )

    # Save results to netCDF and archive
    charms_out_files = charms_nc3(
        pn, pda, pca, model_time,
        np.flip(data_dict["chlor_a"], axis=1),
        np.flip(data_dict["Rrs_489"], axis=1),
        np.flip(data_dict["Rrs_556"], axis=1),
        data_dict["thesalt"], data_dict["thetemp"],
        work_dir, config.res_dir,
        data_dict["salt_mins"], data_dict["temp_mins"]
    )

    archive_charm_outputs(charms_out_files, work_dir, results_dir, now_satellite)

    # OPTIONAL: publish to GCS if enabled (Cloud Run Job-friendly)
    if os.getenv("PUBLISH_ENABLE", "0") == "1":
        publish_results_to_gcs(
            results_year_dir=(results_dir / str(now_satellite.year)),
            config=config,
            interval_days=int((getattr(config, "c_harm", None) or {}).get("interval", 1)),
        )

    # Clean up
    for pattern in patterns:
        delete_files_in_directory(work_dir, pattern=pattern)
        logger.info("Deletion of files in work dir with pattern: %s", pattern.pattern)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
