# VIIRS Net Primary Productivity Cloud Run Workflow

This repository contains the containerized Google Cloud Run workflow used to generate daily and monthly VIIRS net primary productivity products for ERDDAP publication.

The workflow supports the following VIIRS platforms:

- SNPP
- NOAA-20
- NOAA-21, currently disabled in `config/config.yml`

The processing chain downloads required NASA OceanColor Level-3 mapped inputs, computes net primary productivity, writes NetCDF outputs from CDL templates, compresses the files, and publishes the final products to cloud storage for ERDDAP ingestion.

## Repository layout

```text
.
├── config/
│   ├── config.yml          # Runtime configuration
│   └── requirements.txt    # Python dependencies
├── scripts/
│   ├── control_viirs_netpp.py
│   ├── make_viirs_netpp.py
│   ├── control_viirs_netpp_monthly.py
│   └── make_viirs_netpp_monthly.py
├── templates/
│   ├── nasa_noaa20_4km.cdl
│   ├── nasa_noaa20_4km_month.cdl
│   ├── nasa_snpp_4km.cdl
│   └── nasa_snpp_4km_month.cdl
├── Dockerfile
├── entrypoint.sh
├── deploy_job.sh
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Cloud Run job modes

The container is controlled by the `JOB_MODE` environment variable.

| Mode | Description |
|---|---|
| `nrt_daily` | Runs a rolling near-real-time daily sweep from three days ago through yesterday for all enabled sensors. |
| `sq_sweep` | Runs science-quality processing over the configured date range and skips outputs already present in cloud storage. |
| `monthly_composite` | Builds monthly productivity composites for a target sensor and month. |

Monthly jobs require `TARGET_SENSOR`, usually `snpp` or `noaa20`. Optional `TARGET_YEAR` and `TARGET_MONTH` values can be supplied for backfills.

## Daily workflow

For each date, sensor, and product type, the daily processor:

1. Queries the NASA OceanColor file search API.
2. Downloads chlorophyll, PAR, and SST inputs to local scratch.
3. Computes net primary productivity.
4. Writes output using the sensor-specific daily CDL template.
5. Compresses the NetCDF output.
6. Uploads the final product to the configured ERDDAP publication bucket.
7. Removes temporary local files.

Input files are temporary runtime inputs and are not archived by this workflow.

## Monthly workflow

The monthly processor:

1. Lists daily productivity files for the requested month.
2. Downloads one daily file at a time.
3. Accumulates valid observations in row chunks to limit memory and `/tmp` use.
4. Writes the monthly mean product using the sensor-specific monthly CDL template.
5. Compresses and uploads the final monthly NetCDF file.
6. Cleans up local scratch files.

## Configuration

Runtime behavior is controlled by `config/config.yml`, including:

- enabled sensors
- NASA OceanColor API IDs
- output filename templates
- cloud publication bucket and prefix pattern
- CDL template names
- SST masking and clipping thresholds
- monthly chunk size
- NetCDF compression level

Machine-specific credentials and writable paths are supplied at runtime through environment variables and Secret Manager mounts.

## Runtime credentials

The Cloud Run jobs expect NASA Earthdata credentials to be mounted as secrets:

```text
/secrets/netrc/file
/secrets/cookies/file
```

At startup, `entrypoint.sh` copies these into writable files under `/tmp` and points `NETRC` and `URS_COOKIES` at those runtime copies.

Do not commit real `.netrc`, cookie files, project IDs, bucket names, or service-account values to the repository.
