# C-HARM Cloud Run Workflow

This repository contains the containerized Google Cloud Run workflow used to generate daily C-HARM forecast products for ERDDAP publication.

The workflow combines recent VIIRS ocean-color inputs, WCOFS model fields, DINEOF gap filling, and C-HARM model functions to produce daily forecast NetCDF outputs. It is designed to run as a scheduled Cloud Run Job with Earthdata credentials mounted at runtime and cloud storage used for intermediate history files and final publication.

## Repository layout

```text
.
├── config/
│   ├── config.yaml                 # Runtime configuration and publish settings
│   ├── requirements.txt            # Python dependencies
│   ├── dineof/                     # Operational DINEOF init files
│   └── bf_dineof/                  # Backfill DINEOF init files
├── scripts/
│   ├── control_charm_cron_v1.py    # Cloud Run controller for NRT and backfill runs
│   └── make_charm_cloud_v1.py      # Main C-HARM processing workflow
├── src/python/
│   ├── charm_data_process_functions.py
│   ├── charm_dineof_functions.py
│   ├── charm_helper_functions.py
│   └── charm_model_functions.py
├── templates/
│   ├── viirs_L3.cdl
│   ├── charm2022_out_tmpl.cdf
│   ├── charm_3k_grid.nc
│   └── themask.nc
├── Dockerfile
├── entrypoint.sh
├── delpoy_job.sh
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Cloud Run execution model

The container entrypoint prepares a writable `/tmp` runtime environment, copies static configuration and templates into writable locations, sets up NASA Earthdata credentials, and launches the controller:

```text
entrypoint.sh
  -> scripts.control_charm_cron_v1
  -> scripts.make_charm_cloud_v1
```

The controller runs one near-real-time job and then optional backfill jobs. The number of backfill days is controlled by the `BACKFILL_DAYS` environment variable or by the `--backfill_days` command-line option.

## Processing workflow

Each processing date follows this general sequence:

1. Sync recent processed VIIRS Level-3 history from the work bucket.
2. Identify missing 180-day VIIRS history files needed for DINEOF.
3. Download and process missing NASA OceanColor inputs.
4. Sync newly created processed L3 files back to the work bucket.
5. Concatenate recent L3 files into DINEOF input files.
6. Apply masking and log transforms.
7. Download, merge, and regrid WCOFS salinity and temperature fields.
8. Run first-round DINEOF gap filling.
9. Prepare second-round DINEOF inputs.
10. Build the advection step from WCOFS surface currents.
11. Update EOF files with advected forecast fields.
12. Run second-round DINEOF.
13. Run C-HARM forecast models.
14. Write daily forecast NetCDF files.
15. Archive outputs locally by year.
16. Publish outputs to cloud storage when `PUBLISH_ENABLE=1`.

## Runtime configuration

Runtime behavior is controlled by `config/config.yaml`. Important sections include:

- base paths and writable runtime directories
- satellite variables used by DINEOF
- NASA OceanColor source URL and file patterns
- CHARM spatial bounds and grid/template files
- WCOFS forecast windows
- work and production bucket settings
- publication target root and interval formatting
- C-HARM forecast-day settings

In Cloud Run, the container copies `/app/config` and `/app/templates` to writable directories under `/tmp` because DINEOF and supporting tools write status files, logs, and intermediate products alongside their inputs.

## Runtime credentials

The Cloud Run job expects NASA Earthdata credentials to be mounted as secrets:

```text
/secrets/netrc/file
/secrets/cookies/file
```

At startup, `entrypoint.sh` copies these files to `${HOME}/.netrc` and `${HOME}/.urs_cookies`, where NASA tools and download utilities can read them.

Do not commit real `.netrc`, `.urs_cookies`, cookie files, project IDs, service accounts, private bucket names, or other deployment secrets to the repository.

## Main components

| File | Purpose |
|---|---|
| `scripts/control_charm_cron_v1.py` | Top-level Cloud Run controller. Runs NRT and optional backfill dates. |
| `scripts/make_charm_cloud_v1.py` | Main end-to-end daily processing workflow. |
| `src/python/charm_data_process_functions.py` | Regridding, WCOFS retrieval, advection, and data-processing utilities. |
| `src/python/charm_dineof_functions.py` | DINEOF preparation, execution, and post-processing utilities. |
| `src/python/charm_helper_functions.py` | Configuration loading, argument parsing, file management, NetCDF writing, and publishing helpers. |
| `src/python/charm_model_functions.py` | C-HARM model probability calculations. |
| `config/dineof/` | DINEOF init files used for operational nowcast/forecast runs. |
| `config/bf_dineof/` | DINEOF init files used for backfill runs. |
| `templates/` | Static NetCDF/CDL templates and CHARM grid/mask files. |
