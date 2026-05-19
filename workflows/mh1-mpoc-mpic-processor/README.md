# MH1 / MPOC / MPIC Cloud Run Processor

This repository contains the Cloud Run workflow used to retrieve and publish MODIS-Aqua Level-3 satellite products for the MH1, MPOC, and MPIC ERDDAP datasets.

The workflow replaces legacy server-based retrieval scripts with a containerized Google Cloud workflow. It queries the NASA OceanColor file search API, checks which files are already present in Google Cloud Storage, downloads only missing products, publishes them to ERDDAP-facing storage paths, and removes temporary files from the Cloud Run runtime.

## Products

The processor handles both near-real-time (NRT) and science-quality streams.

| Dataset family | Products | Description |
|---|---|---|
| MH1 Ocean Color | `chlor_a`, `nflh`, `par`, `Kd_490` | MODIS-Aqua ocean color products |
| MH1 SST | `sst`, `sstMasked` | MODIS-Aqua sea surface temperature and qual_sst-masked SST |
| MPOC | `poc` | Particulate organic carbon |
| MPIC | `pic` | Particulate inorganic carbon |

Each product stream supports 1-day, 8-day, and monthly composites where available.

## Processing model

Each retrieval script follows the same operational pattern:

```text
Cloud Scheduler
  → Cloud Run Job
  → Query NASA OceanColor file search API
  → Compare NASA file list against existing GCS objects
  → Download missing files to /tmp
  → Publish outputs to configured GCS destinations
  → Remove local temporary files
```

The workflow is intentionally incremental. It does not re-download products already present in the production bucket.

## Repository structure

```text
erd_cloud_run_MH1/
├── config/
│   ├── config.yml              # Runtime settings, GCS targets, NASA endpoints
│   └── requirements.txt        # Python dependencies
├── deploy_job.sh               # Build, push, deploy, and schedule the Cloud Run job
├── Dockerfile                  # Container definition
├── entrypoint.sh               # Runs all NRT suites first, then science-quality suites
├── scripts/
│   ├── MH1/
│   │   ├── getMH1OceanColor_NRT.py
│   │   ├── getMH1OceanColor_Sci.py
│   │   ├── getMH1SST_NRT.py
│   │   └── getMH1SST_Sci.py
│   ├── MPIC/
│   │   ├── getMPIC_NRT.py
│   │   └── getMPIC_Sci.py
│   └── MPOC/
│       ├── getMPOC_NRT.py
│       └── getMPOC_Sci.py
└── src/
    ├── __init__.py
    └── roylib.py
```

## Main components

### `entrypoint.sh`

The Cloud Run entrypoint prepares the runtime environment and runs the workflow suites in sequence:

1. Prepare writable runtime directories under `/tmp`
2. Copy Earthdata credential files from Secret Manager mounts
3. Run all near-real-time retrieval suites
4. Run all science-quality retrieval suites

### `scripts/MH1/`

Contains MODIS-Aqua MH1 retrieval scripts.

- `getMH1OceanColor_NRT.py` retrieves NRT chlorophyll-a, nFLH, PAR, and Kd490.
- `getMH1OceanColor_Sci.py` retrieves science-quality chlorophyll-a, nFLH, PAR, and Kd490.
- `getMH1SST_NRT.py` retrieves NRT SST and creates masked SST outputs.
- `getMH1SST_Sci.py` retrieves science-quality SST and creates masked SST outputs.

### `scripts/MPOC/`

Contains MODIS-Aqua particulate organic carbon retrieval scripts.

- `getMPOC_NRT.py` retrieves NRT POC products.
- `getMPOC_Sci.py` retrieves science-quality POC products.

### `scripts/MPIC/`

Contains MODIS-Aqua particulate inorganic carbon retrieval scripts.

- `getMPIC_NRT.py` retrieves NRT PIC products.
- `getMPIC_Sci.py` retrieves science-quality PIC products.

### `src/roylib.py`

Shared utility library used by the retrieval scripts. It handles:

- configuration loading
- NASA file search query helpers
- GCS bucket listing
- file download helpers
- GCS upload helpers
- production and work-bucket publishing logic

## Configuration

Runtime settings are stored in `config/config.yml`.

The workflow distinguishes between two major GCS destinations:

| Setting | Purpose |
|---|---|
| `ERDPROD_BUCKET` | Production bucket for final ERDDAP-facing NetCDF products |
| `ERDWORK_BUCKET` | Work/staging bucket for intermediate, mirrored, or operational support files |

The publishing logic is implemented in `send_to_servers()`:

1. Every output is uploaded to the production bucket.
2. For selected 1-day MB/MW outputs, a second copy is also uploaded to the work bucket.

Example destination patterns:

```text
gs://<prod_bucket>/<prod_root>/<dst_dir>/<interval>day/<filename>
gs://<work_bucket>/<work_root>/<region>/1day/<filename>
```

Bucket names in `config.yml` should be plain bucket names, not `gs://` URIs.

## NASA OceanColor access

The workflow uses NASA OceanColor file search and download endpoints configured in `config/config.yml`.

Earthdata Login credentials are required for authenticated downloads. These credentials should not be committed to the repository. In Cloud Run, they are provided through Secret Manager and copied into writable `/tmp` locations by `entrypoint.sh`.

Expected runtime credential files include:

```text
/tmp/.netrc
/tmp/.urs_cookies
```

Do not commit `.netrc`, `.urs_cookies`, or cookie files to this repository.

## NASA query settings

The retrieval scripts use NASA OceanColor dataset identifiers for MODIS-Aqua Level-3 mapped products.

| Stream | sensor_id | dtid | Notes |
|---|---:|---:|---|
| MH1 Ocean Color NRT | 7 | 1055 | MODIS-Aqua NRT ocean color |
| MH1 Ocean Color Science | 7 | 1043 | MODIS-Aqua science-quality ocean color |
| MH1 SST NRT | 7 | 1061 | MODIS-Aqua NRT SST |
| MH1 SST Science | 7 | 1049 | MODIS-Aqua science-quality SST |
| MPOC / MPIC NRT | 7 | 1055 | MODIS-Aqua NRT ocean color stream used for carbon products |
| MPOC / MPIC Science | 7 | 1102 | MODIS-Aqua science-quality carbon products |

## SST masking

The MH1 SST scripts create both raw SST outputs and masked SST outputs.

For each downloaded SST file, the masking step:

1. Reads the raw `sst` variable
2. Reads `qual_sst`
3. Sets SST values to fill where `qual_sst < 0`
4. Retains SST values where `qual_sst >= 0`
5. Writes the retained values to `sstMasked`

The raw SST and masked SST outputs are published to separate configured destination paths.

## Local development

Install dependencies:

```bash
python -m pip install -r config/requirements.txt
```

Set the config path:

```bash
export ROYLIB_CONFIG=config/config.yml
```

Run one script manually:

```bash
python scripts/MH1/getMH1OceanColor_NRT.py
```

For local testing, make sure Earthdata credentials are available in the expected runtime paths or adjust the config/environment accordingly.

## Deployment

The `deploy_job.sh` script builds the container image, pushes it to Artifact Registry, deploys the Cloud Run job, and configures the scheduler trigger.

Typical deployment flow:

```bash
./deploy_job.sh
```

Before deploying, confirm that:

- the GCP project and region are correct
- Artifact Registry exists
- the Cloud Run service account has required GCS permissions
- Secret Manager contains the Earthdata credential files
- `config/config.yml` points to the intended buckets and prefixes

## Public documentation copy

This repository may be copied into the SWFSC ERDDAP Cloud Migration Quarto book as a sanitized public documentation snapshot.

Before publishing a copy of this workflow, remove or replace:

- real bucket names
- project IDs
- internal hostnames
- local filesystem paths
- email addresses
- `.netrc`
- `.urs_cookies`
- cookie files
- any other credential-like files

The Quarto book should display only sanitized source code and configuration examples.

## Notes

This workflow is intended for operational data retrieval and publication. The scripts are designed to be run in Cloud Run, where local disk is ephemeral and `/tmp` is used for temporary downloads and intermediate files.