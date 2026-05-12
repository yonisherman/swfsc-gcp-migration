from pathlib import Path

pages = [
    # =========================================================
    # MH1 / MPOC / MPIC PROCESSOR
    # =========================================================

    ("code-pages/mh1-processor/dockerfile.qmd",
     "Dockerfile",
     "dockerfile",
     "Container environment for the MH1/MPOC/MPIC processor workflow.",
     "workflows/mh1-mpoc-mpic-processor/Dockerfile"),

    ("code-pages/mh1-processor/entrypoint.qmd",
     "entrypoint.sh",
     "bash",
     "Cloud Run entrypoint; runs all NRT then all Science suites in sequence.",
     "workflows/mh1-mpoc-mpic-processor/entrypoint.sh"),

    ("code-pages/mh1-processor/deploy_job.qmd",
     "deploy_job.sh",
     "bash",
     "Builds, pushes, and deploys the Cloud Run job and scheduler.",
     "workflows/mh1-mpoc-mpic-processor/deploy_job.sh"),

    ("code-pages/mh1-processor/config.qmd",
     "config.yml",
     "yaml",
     "Runtime configuration — GCS buckets, NASA endpoints, publish targets.",
     "workflows/mh1-mpoc-mpic-processor/config/config.yml"),

    ("code-pages/mh1-processor/requirements.qmd",
     "requirements.txt",
     "text",
     "Python dependencies.",
     "workflows/mh1-mpoc-mpic-processor/config/requirements.txt"),

    ("code-pages/mh1-processor/roylib.qmd",
     "roylib.py",
     "python",
     "Shared GCS helpers, NASA API queries, and processing utilities.",
     "workflows/mh1-mpoc-mpic-processor/src/roylib.py"),

    ("code-pages/mh1-processor/getMH1OceanColor_NRT.qmd",
     "getMH1OceanColor_NRT.py",
     "python",
     "NRT MODIS-Aqua ocean color retrieval (chlorophyll-a, nFLH, PAR, Kd490).",
     "workflows/mh1-mpoc-mpic-processor/scripts/MH1/getMH1OceanColor_NRT.py"),

    ("code-pages/mh1-processor/getMH1OceanColor_Sci.qmd",
     "getMH1OceanColor_Sci.py",
     "python",
     "Science quality MODIS-Aqua ocean color retrieval (chlorophyll-a, nFLH, PAR, Kd490).",
     "workflows/mh1-mpoc-mpic-processor/scripts/MH1/getMH1OceanColor_Sci.py"),

    ("code-pages/mh1-processor/getMH1SST_NRT.qmd",
     "getMH1SST_NRT.py",
     "python",
     "NRT MODIS-Aqua SST retrieval and qual_sst masking.",
     "workflows/mh1-mpoc-mpic-processor/scripts/MH1/getMH1SST_NRT.py"),

    ("code-pages/mh1-processor/getMH1SST_Sci.qmd",
     "getMH1SST_Sci.py",
     "python",
     "Science quality MODIS-Aqua SST retrieval and qual_sst masking.",
     "workflows/mh1-mpoc-mpic-processor/scripts/MH1/getMH1SST_Sci.py"),

    ("code-pages/mh1-processor/getMPIC_NRT.qmd",
     "getMPIC_NRT.py",
     "python",
     "NRT MODIS-Aqua particulate inorganic carbon (PIC) retrieval.",
     "workflows/mh1-mpoc-mpic-processor/scripts/MPIC/getMPIC_NRT.py"),

    ("code-pages/mh1-processor/getMPIC_Sci.qmd",
     "getMPIC_Sci.py",
     "python",
     "Science quality MODIS-Aqua particulate inorganic carbon (PIC) retrieval.",
     "workflows/mh1-mpoc-mpic-processor/scripts/MPIC/getMPIC_Sci.py"),

    ("code-pages/mh1-processor/getMPOC_NRT.qmd",
     "getMPOC_NRT.py",
     "python",
     "NRT MODIS-Aqua particulate organic carbon (POC) retrieval.",
     "workflows/mh1-mpoc-mpic-processor/scripts/MPOC/getMPOC_NRT.py"),

    ("code-pages/mh1-processor/getMPOC_Sci.qmd",
     "getMPOC_Sci.py",
     "python",
     "Science quality MODIS-Aqua particulate organic carbon (POC) retrieval.",
     "workflows/mh1-mpoc-mpic-processor/scripts/MPOC/getMPOC_Sci.py"),

    # =========================================================
    # MUR SST
    # =========================================================

    ("code-pages/mur-sst/dockerfile.qmd",
     "Dockerfile",
     "dockerfile",
     "Container environment for the MUR SST workflow.",
     "workflows/mur-daily-workflows/Dockerfile"),

    ("code-pages/mur-sst/entrypoint.qmd",
     "entrypoint.sh",
     "bash",
     "Cloud Run entrypoint and job mode selector.",
     "workflows/mur-daily-workflows/entrypoint.sh"),

    ("code-pages/mur-sst/deploy_job.qmd",
     "deploy_job.sh",
     "bash",
     "Builds, deploys, and schedules Cloud Run jobs.",
     "workflows/mur-daily-workflows/deploy_job.sh"),

    ("code-pages/mur-sst/config.qmd",
     "config.yml",
     "yaml",
     "Workflow configuration.",
     "workflows/mur-daily-workflows/config/config.yml"),

    ("code-pages/mur-sst/requirements.qmd",
     "requirements.txt",
     "text",
     "Python dependencies.",
     "workflows/mur-daily-workflows/config/requirements.txt"),

    ("code-pages/mur-sst/mur_v41_downloader_dailyproc.qmd",
     "mur_v41_downloader_dailyproc.sh",
     "bash",
     "Daily MUR v4.1 download and processing driver.",
     "workflows/mur-daily-workflows/scripts/mur_v41_downloader_dailyproc.sh"),

    ("code-pages/mur-sst/mur_v42_downloader.qmd",
     "mur_v42_downloader.sh",
     "bash",
     "MUR v4.2 download driver.",
     "workflows/mur-daily-workflows/scripts/mur_v42_downloader.sh"),

    ("code-pages/mur-sst/MURanom1day.qmd",
     "MURanom1day.py",
     "python",
     "Daily anomaly computation for MUR v4.1.",
     "workflows/mur-daily-workflows/scripts/MURanom1day.py"),

    ("code-pages/mur-sst/calc_mur_fronts.qmd",
     "calc_mur_fronts.py",
     "python",
     "Front detection workflow for MUR SST.",
     "workflows/mur-daily-workflows/scripts/fronts/calc_mur_fronts.py"),

    ("code-pages/mur-sst/canny_lib.qmd",
     "canny_lib.py",
     "python",
     "Shared Canny edge-detection utilities.",
     "workflows/mur-daily-workflows/scripts/fronts/canny_lib.py"),

    ("code-pages/mur-sst/Canny1.qmd",
     "Canny1.py",
     "python",
     "First-stage Canny front processing.",
     "workflows/mur-daily-workflows/scripts/fronts/Canny1.py"),

    ("code-pages/mur-sst/Canny2.qmd",
     "Canny2.py",
     "python",
     "Second-stage Canny front processing.",
     "workflows/mur-daily-workflows/scripts/fronts/Canny2.py"),

    ("code-pages/mur-sst/MUR41_MonProc.qmd",
     "MUR41_MonProc.sh",
     "bash",
     "Monthly MUR v4.1 processing driver.",
     "workflows/mur-daily-workflows/scripts/MonthlyProc/MUR41_MonProc.sh"),

    ("code-pages/mur-sst/CompMURmon.qmd",
     "CompMURmon.py",
     "python",
     "Monthly MUR composite generation.",
     "workflows/mur-daily-workflows/scripts/MonthlyProc/CompMURmon.py"),

    ("code-pages/mur-sst/CompMurAnomMon.qmd",
     "CompMurAnomMon.py",
     "python",
     "Monthly MUR anomaly composite generation.",
     "workflows/mur-daily-workflows/scripts/MonthlyProc/CompMurAnomMon.py"),

    # =========================================================
    # VIIRS NETPP
    # =========================================================

    ("code-pages/viirs-netpp/dockerfile.qmd",
     "Dockerfile",
     "dockerfile",
     "Container environment for the VIIRS NetPP workflow.",
     "workflows/viirs-netpp/Dockerfile"),

    ("code-pages/viirs-netpp/entrypoint.qmd",
     "entrypoint.sh",
     "bash",
     "Cloud Run entrypoint and job mode selector.",
     "workflows/viirs-netpp/entrypoint.sh"),

    ("code-pages/viirs-netpp/deploy_job.qmd",
     "deploy_job.sh",
     "bash",
     "Builds, deploys, and schedules Cloud Run jobs.",
     "workflows/viirs-netpp/deploy_job.sh"),

    ("code-pages/viirs-netpp/config.qmd",
     "config.yml",
     "yaml",
     "Workflow configuration.",
     "workflows/viirs-netpp/config/config.yml"),

    ("code-pages/viirs-netpp/requirements.qmd",
     "requirements.txt",
     "text",
     "Python dependencies.",
     "workflows/viirs-netpp/config/requirements.txt"),

    ("code-pages/viirs-netpp/control_viirs_netpp.qmd",
     "control_viirs_netpp.py",
     "python",
     "Daily VIIRS NetPP controller.",
     "workflows/viirs-netpp/scripts/control_viirs_netpp.py"),

    ("code-pages/viirs-netpp/control_viirs_netpp_monthly.qmd",
     "control_viirs_netpp_monthly.py",
     "python",
     "Monthly VIIRS NetPP controller.",
     "workflows/viirs-netpp/scripts/control_viirs_netpp_monthly.py"),

    ("code-pages/viirs-netpp/make_viirs_netpp.qmd",
     "make_viirs_netpp.py",
     "python",
     "Daily Net Primary Productivity generation.",
     "workflows/viirs-netpp/scripts/make_viirs_netpp.py"),

    ("code-pages/viirs-netpp/make_viirs_netpp_monthly.qmd",
     "make_viirs_netpp_monthly.py",
     "python",
     "Monthly Net Primary Productivity generation.",
     "workflows/viirs-netpp/scripts/make_viirs_netpp_monthly.py"),

    # =========================================================
    # CHARM
    # =========================================================

    ("code-pages/charm/dockerfile.qmd",
     "Dockerfile",
     "dockerfile",
     "Container environment for the CHARM workflow.",
     "workflows/charm/Dockerfile"),

    ("code-pages/charm/entrypoint.qmd",
     "entrypoint.sh",
     "bash",
     "Cloud Run entrypoint and job mode selector.",
     "workflows/charm/entrypoint.sh"),

    ("code-pages/charm/delpoy_job.qmd",
     "delpoy_job.sh",
     "bash",
     "Builds, deploys, and schedules the CHARM Cloud Run job.",
     "workflows/charm/delpoy_job.sh"),

    ("code-pages/charm/config.qmd",
     "config.yaml",
     "yaml",
     "Main CHARM workflow configuration.",
     "workflows/charm/config/config.yaml"),

    ("code-pages/charm/requirements.qmd",
     "requirements.txt",
     "text",
     "Python dependencies for the CHARM container.",
     "workflows/charm/config/requirements.txt"),

    ("code-pages/charm/control_charm_cron_v1.qmd",
     "control_charm_cron_v1.py",
     "python",
     "Main CHARM cron controller.",
     "workflows/charm/scripts/control_charm_cron_v1.py"),

    ("code-pages/charm/make_charm_cloud_v1.qmd",
     "make_charm_cloud_v1.py",
     "python",
     "Primary CHARM cloud processing script.",
     "workflows/charm/scripts/make_charm_cloud_v1.py"),

    ("code-pages/charm/charm_data_process_functions.qmd",
     "charm_data_process_functions.py",
     "python",
     "Data processing utilities for CHARM inputs and outputs.",
     "workflows/charm/src/python/charm_data_process_functions.py"),

    ("code-pages/charm/charm_dineof_functions.qmd",
     "charm_dineof_functions.py",
     "python",
     "DINEOF gap-filling utilities used by CHARM.",
     "workflows/charm/src/python/charm_dineof_functions.py"),

    ("code-pages/charm/charm_helper_functions.qmd",
     "charm_helper_functions.py",
     "python",
     "General helper functions for the CHARM workflow.",
     "workflows/charm/src/python/charm_helper_functions.py"),

    ("code-pages/charm/charm_model_functions.qmd",
     "charm_model_functions.py",
     "python",
     "Model functions for CHARM habitat prediction.",
     "workflows/charm/src/python/charm_model_functions.py"),
         # CHARM DINEOF init files
    ("code-pages/charm/for_chlor_a_v4.qmd",
     "for_chlor_a_v4.init",
     "text",
     "DINEOF forecast configuration for chlorophyll-a.",
     "workflows/charm/config/dineof/chlor_a/for_chlor_a_v4.init"),

    ("code-pages/charm/now_chlor_a_v4.qmd",
     "now_chlor_a_v4.init",
     "text",
     "DINEOF nowcast configuration for chlorophyll-a.",
     "workflows/charm/config/dineof/chlor_a/now_chlor_a_v4.init"),

    ("code-pages/charm/for_Rrs_489_v4.qmd",
     "for_Rrs_489_v4.init",
     "text",
     "DINEOF forecast configuration for Rrs_489.",
     "workflows/charm/config/dineof/Rrs_489/for_Rrs_489_v4.init"),

    ("code-pages/charm/now_Rrs_489_v4.qmd",
     "now_Rrs_489_v4.init",
     "text",
     "DINEOF nowcast configuration for Rrs_489.",
     "workflows/charm/config/dineof/Rrs_489/now_Rrs_489_v4.init"),

    ("code-pages/charm/for_Rrs_556_v4.qmd",
     "for_Rrs_556_v4.init",
     "text",
     "DINEOF forecast configuration for Rrs_556.",
     "workflows/charm/config/dineof/Rrs_556/for_Rrs_556_v4.init"),

    ("code-pages/charm/now_Rrs_556_v4.qmd",
     "now_Rrs_556_v4.init",
     "text",
     "DINEOF nowcast configuration for Rrs_556.",
     "workflows/charm/config/dineof/Rrs_556/now_Rrs_556_v4.init"),

    ("code-pages/charm/for_bf_chlor_a_v4.qmd",
     "for_bf_chlor_a_v4.init",
     "text",
     "Backfill DINEOF forecast configuration for chlorophyll-a.",
     "workflows/charm/config/bf_dineof/chlor_a/for_bf_chlor_a_v4.init"),

    ("code-pages/charm/now_bf_chlor_a_v4.qmd",
     "now_bf_chlor_a_v4.init",
     "text",
     "Backfill DINEOF nowcast configuration for chlorophyll-a.",
     "workflows/charm/config/bf_dineof/chlor_a/now_bf_chlor_a_v4.init"),

    ("code-pages/charm/for_bf_Rrs_489_v4.qmd",
     "for_bf_Rrs_489_v4.init",
     "text",
     "Backfill DINEOF forecast configuration for Rrs_489.",
     "workflows/charm/config/bf_dineof/Rrs_489/for_bf_Rrs_489_v4.init"),

    ("code-pages/charm/now_bf_Rrs_489_v4.qmd",
     "now_bf_Rrs_489_v4.init",
     "text",
     "Backfill DINEOF nowcast configuration for Rrs_489.",
     "workflows/charm/config/bf_dineof/Rrs_489/now_bf_Rrs_489_v4.init"),

    ("code-pages/charm/for_bf_Rrs_556_v4.qmd",
     "for_bf_Rrs_556_v4.init",
     "text",
     "Backfill DINEOF forecast configuration for Rrs_556.",
     "workflows/charm/config/bf_dineof/Rrs_556/for_bf_Rrs_556_v4.init"),

    ("code-pages/charm/now_bf_Rrs_556_v4.qmd",
     "now_bf_Rrs_556_v4.init",
     "text",
     "Backfill DINEOF nowcast configuration for Rrs_556.",
     "workflows/charm/config/bf_dineof/Rrs_556/now_bf_Rrs_556_v4.init"),

    ("code-pages/charm/now_Rrs_556_v4.qmd",
     "now_Rrs_556_v4.init",
     "text",
     "Additional backfill DINEOF nowcast configuration for Rrs_556.",
     "workflows/charm/config/bf_dineof/Rrs_556/now_Rrs_556_v4.init"),

    ("code-pages/charm/viirs_L3.qmd",
     "viirs_L3.cdl",
     "text",
     "CDL template for VIIRS L3 NetCDF formatting.",
     "workflows/charm/templates/viirs_L3.cdl"),
]

template = """---
title: "{title}"
toc: false
page-layout: full
number-sections: false
---

[← Back to workflow](../../{workflow}.html)

{description}

::: {{.code-shell}}

```{{.{language}}}
{{{{< include ../../{include_path} >}}}}
```

:::
"""

for out, title, language, description, include_path in pages:
    out_path = Path(out)

    # Create output directories automatically
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # workflow name from folder
    workflow = Path(out).parent.name

    out_path.write_text(
        template.format(
            title=title,
            language=language,
            description=description,
            include_path=include_path,
            workflow=workflow,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {out}")

print("\nDone.")