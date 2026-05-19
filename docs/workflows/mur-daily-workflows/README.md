# MUR Daily Workflows

This repository contains the Cloud Run workflow used to retrieve, process, and publish Multi-scale Ultra-high Resolution (MUR) sea surface temperature products for ERDDAP.

The workflow replaces legacy server-based processing with containerized Google Cloud Run jobs. It synchronizes daily MUR v4.1 and v4.2 files from NASA/PO.DAAC, detects whether existing files are near-real-time (NRT) or final/science-quality, updates the production GCS archive when improved files are available, generates MUR v4.1 daily anomalies, creates daily SST fronts, and builds monthly SST and anomaly composites.

## Products

| Product family | Output | Description |
|---|---|---|
| MUR v4.1 daily SST | `MUR41/ssta/1day` | Daily global MUR v4.1 SST files synchronized from PO.DAAC |
| MUR v4.1 daily anomaly | `MUR41/anom/1day` | Daily SST anomaly computed from the MUR v4.1 daily SST file and the configured daily climatology |
| MUR v4.1 fronts | `MUR41/erdMurFront41USWest/1day`, `MUR41/erdMurFront41USAtlantic/1day` | Daily frontal edge and SST gradient products for West Coast and Atlantic regions |
| MUR v4.1 monthly SST | `MUR41/ssta/mday` | Monthly mean SST composite generated from daily MUR v4.1 SST files |
| MUR v4.1 monthly anomaly | `MUR41/anom/mday` | Monthly mean anomaly composite generated from daily MUR v4.1 anomaly files |
| MUR v4.2 daily SST | `MUR42/ssta/1day` | Daily MUR v4.2 SST files synchronized from PO.DAAC |

## Processing model

The workflow runs as Cloud Run jobs triggered by Cloud Scheduler.

```text
Cloud Scheduler
  → Cloud Run Job
  → Resolve runtime mode from JOB_MODE
  → Query NASA CMR / PO.DAAC for available MUR granules
  → Inspect existing GCS files and NASA metadata for NRT vs final stage
  → Download missing or upgraded files to /tmp
  → Publish final outputs to the configured production GCS paths
  → Generate derived products where applicable
  → Remove temporary local files
```

The daily sync is metadata-aware. If a final file is already present in GCS, the job skips that day. If an NRT file exists and NASA has since promoted the granule to final, the job downloads and republishes the final version. If no file exists, the job downloads the best available version.

## Repository structure

```text
mur-daily-workflows/
├── README.md
├── config/
│   ├── config.yml              # Runtime settings, GCS targets, NASA/PO.DAAC endpoints
│   └── requirements.txt        # Python dependencies
├── deploy_job.sh               # Build, push, deploy, and schedule the Cloud Run jobs
├── Dockerfile                  # Container definition
├── entrypoint.sh               # Selects the workflow based on JOB_MODE
├── scripts/
│   ├── mur_v41_downloader_dailyproc.sh   # MUR v4.1 daily sync + daily derived products
│   ├── mur_v42_downloader.sh             # MUR v4.2 daily sync
│   ├── MURanom1day.py                    # Daily MUR v4.1 anomaly computation
│   ├── MonthlyProc/
│   │   ├── MUR41_MonProc.sh              # Monthly processing driver
│   │   ├── CompMURmon.py                 # Monthly SST composite
│   │   └── CompMurAnomMon.py             # Monthly anomaly composite
│   └── fronts/
│       ├── calc_mur_fronts.py            # Daily fronts runner
│       ├── canny_lib.py                  # Shared fronts utilities
│       ├── Canny1.py                     # West Coast Canny implementation
│       └── Canny2.py                     # Atlantic Canny implementation
├── src/
│   ├── __init__.py
│   └── roylib.py                         # Shared configuration, GCS, and NetCDF helpers
└── templates/
    ├── murAnomTemplate.nc
    ├── murSSTmday.nc
    └── murSSTmdayAnom.nc
```

## Runtime modes

The entrypoint selects the processing path from `JOB_MODE`.

| `JOB_MODE` | Script | Purpose |
|---|---|---|
| `mur41` | `scripts/mur_v41_downloader_dailyproc.sh` | Daily MUR v4.1 sync, anomaly generation, and fronts generation |
| `mur42` | `scripts/mur_v42_downloader.sh` | Daily MUR v4.2 sync |
| `mur41_monthly` | `scripts/MonthlyProc/MUR41_MonProc.sh` | Monthly MUR v4.1 SST and anomaly composites |

If `JOB_MODE` is not set, the container defaults to `mur41`.

## Configuration

Runtime settings are stored in `config/config.yml` and are read by the shell drivers and Python utilities. In Cloud Run, the config file is available inside the container at `/app/config/config.yml`, and `entrypoint.sh` sets `ROYLIB_CONFIG` so Python scripts can load the same configuration.

The workflow distinguishes between two GCS destinations:

| Setting | Purpose |
|---|---|
| `ERDPROD_BUCKET` | Production bucket for final ERDDAP-facing NetCDF products |
| `ERDWORK_BUCKET` | Work/staging bucket for intermediate or legacy support files |

MUR daily, anomaly, fronts, and monthly outputs are published through the production target configured under `PUBLISH_TARGETS.prod`.

Example destination patterns:

```text
gs://<prod_bucket>/<prod_root>/MUR41/ssta/1day/<YYYY>/<filename>
gs://<prod_bucket>/<prod_root>/MUR41/anom/1day/<YYYY>/<filename>
gs://<prod_bucket>/<prod_root>/MUR41/erdMurFront41USWest/1day/<YYYY>/<filename>
gs://<prod_bucket>/<prod_root>/MUR41/ssta/mday/<YYYY>/<filename>
```

Bucket values in `config.yml` should be plain bucket names, not `gs://<bucket>/<prefix>` URIs, unless a setting explicitly represents a full GCS URI such as the climatology prefix.

## Daily MUR v4.1 processing

The v4.1 daily workflow performs four main tasks:

1. Query CMR / PO.DAAC for recent MUR v4.1 granules.
2. Compare NASA metadata against existing GCS files to decide whether each day should be skipped, downloaded, or upgraded from NRT to final.
3. Publish the selected daily SST file to the configured production GCS path.
4. Generate derived daily products from the downloaded SST file:
   - daily anomaly using `MURanom1day.py`
   - West Coast SST fronts using `calc_mur_fronts.py --region WC`
   - Atlantic SST fronts using `calc_mur_fronts.py --region ATL`

The lookback window is controlled by the daily shell driver. This allows recently promoted final files to replace earlier NRT files.

## Daily anomaly processing

`MURanom1day.py` computes daily SST anomaly as:

```text
analysed_sst - daily_climatology_mean_sst
```

The script downloads the matching daily climatology file from the configured climatology GCS prefix, copies the anomaly NetCDF template, processes the data in latitude blocks to control memory use, writes the `sstAnom` variable, and uploads the output to the configured anomaly destination.

Leap-year day 366 is mapped to climatology day 365.

## Front detection

Daily fronts are generated from the local MUR v4.1 SST file downloaded by the daily workflow. The fronts runner supports two regions:

| Region | Bounding box | Algorithm path |
|---|---|---|
| `WC` | U.S. West Coast | `canny_lib.myCanny_WC()` / `Canny1.py` |
| `ATL` | U.S. Atlantic | `canny_lib.myCanny_ATL()` / `Canny2.py` |

The fronts output includes frontal edges, east-west SST gradient, north-south SST gradient, and gradient magnitude.

## Monthly processing

The monthly workflow is selected with `JOB_MODE=mur41_monthly`. It processes the last completed month by default.

`MONTH_OFFSET` controls which month is processed:

| `MONTH_OFFSET` | Target month |
|---:|---|
| `1` | Last completed month |
| `2` | Two months ago |
| `3` | Three months ago |

The monthly driver runs:

1. `CompMURmon.py` to build the monthly SST composite from daily SST files.
2. `CompMurAnomMon.py` to build the monthly anomaly composite from daily anomaly files.

Both monthly scripts discover matching daily files in GCS, download them to `/tmp`, aggregate them in latitude blocks, write a NetCDF output from a template, and publish the result to the configured production path.

## Credentials and secrets

Earthdata Login credentials are required for NASA/PO.DAAC downloads. Credentials should not be committed to this repository.

In Cloud Run, `entrypoint.sh` expects Secret Manager mounts at:

```text
/secrets/netrc/file
/secrets/cookies/file
```

The entrypoint copies these files to writable runtime paths:

```text
/tmp/.netrc
/tmp/.urs_cookies
```

The shell drivers use `NETRC` and `URS_COOKIES` to authenticate with NASA services.

## Deployment

`deploy_job.sh` builds the container image, pushes it to Artifact Registry, deploys Cloud Run jobs, and creates Cloud Scheduler triggers.

Typical deployment flow:

```bash
./deploy_job.sh
```

Before deploying, confirm that:

- the GCP project and region are correct
- Artifact Registry exists or can be created
- the Cloud Run service account has the required GCS permissions
- Secret Manager contains the Earthdata credential files
- `config/config.yml` points to the intended buckets, prefixes, and product destinations
- the NetCDF templates are present under `templates/`

## Local development

Install dependencies:

```bash
python -m pip install -r config/requirements.txt
```

Set the config path:

```bash
export ROYLIB_CONFIG=config/config.yml
```

Run a Python component directly, for example:

```bash
python scripts/MURanom1day.py /path/to/MUR41_daily_file.nc
```

Most workflow paths are designed for Cloud Run and expect `/tmp`, GCS access, Earthdata credentials, and the containerized dependencies installed by the Dockerfile.

## Notes

This workflow is designed for operational processing. Local disk is treated as ephemeral, and `/tmp` is used for downloads, intermediate products, monthly aggregation workspaces, and logs.
