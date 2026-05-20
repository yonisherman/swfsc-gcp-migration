#!/bin/bash
# =============================================================================
# deploy.sh — Build, push, and configure VIIRS netpp Cloud Run jobs
#
# Jobs deployed:
#   viirs-netpp-nrt-daily        Daily NRT run (all sensors, yesterday-3d → yesterday)
#   viirs-netpp-sq-sweep         Weekly SQ sweep (all sensors, 2026-01-01 → yesterday)
#   viirs-netpp-monthly-snpp     Monthly composite for SNPP (nrt then sq)
#   viirs-netpp-monthly-noaa20   Monthly composite for NOAA-20 (nrt then sq)
#
# Usage:
#   ./deploy.sh            # build + deploy everything
#   ./deploy.sh --no-push  # skip Docker build/push (redeploy jobs only)
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Settings — edit these for your project
# ---------------------------------------------------------------------------
PROJECT_ID="YOUR_PROJECT_ID"
REGION="us-east4"

REPO_NAME="viirs-netpp-processor"
IMAGE_NAME="viirs-netpp-processor"
TAG="v1"

SERVICE_ACCOUNT="YOUR_SERVICE_ACCOUNT@${PROJECT_ID}.iam.gserviceaccount.com"

# Shared secrets mounted into every job task
# Secret names must already exist in Secret Manager
COMMON_SECRETS="/secrets/netrc/file=netrc-secret:latest,/secrets/cookies/file=urs-cookies-secret:latest"

# Cross-platform build flag (keep for Apple Silicon dev machines; no-op on Linux CI)
PLATFORM="${PLATFORM:-linux/amd64}"

IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:${TAG}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
need gcloud
need docker

PUSH=true
for arg in "$@"; do
    [[ "$arg" == "--no-push" ]] && PUSH=false
done

# ---------------------------------------------------------------------------
# 1. Build and push image
# ---------------------------------------------------------------------------
if $PUSH; then
    echo ">>> Building Docker image..."
    if [[ -n "${PLATFORM}" ]]; then
        docker buildx build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${TAG}" --load .
    else
        docker build -t "${IMAGE_NAME}:${TAG}" .
    fi

    echo ">>> Configuring gcloud project: ${PROJECT_ID}"
    gcloud config set project "${PROJECT_ID}" --quiet

    echo ">>> Ensuring Artifact Registry repo: ${REPO_NAME} (${REGION})"
    if ! gcloud artifacts repositories describe "${REPO_NAME}" \
            --location="${REGION}" >/dev/null 2>&1; then
        echo "    Repo not found — creating..."
        gcloud artifacts repositories create "${REPO_NAME}" \
            --repository-format=docker \
            --location="${REGION}" \
            --description="VIIRS primary productivity processor"
    fi

    gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

    echo ">>> Tagging and pushing image..."
    docker tag "${IMAGE_NAME}:${TAG}" "${IMAGE_PATH}"
    docker push "${IMAGE_PATH}"
    echo "    Pushed: ${IMAGE_PATH}"
else
    echo ">>> Skipping Docker build/push (--no-push)"
fi

# ---------------------------------------------------------------------------
# 2. Deploy Cloud Run Jobs
# ---------------------------------------------------------------------------
echo ">>> Deploying Cloud Run jobs..."

deploy_job() {
    local job_name="$1"
    local job_mode="$2"
    local memory="$3"
    local cpu="$4"
    local timeout="$5"   # e.g. "2h", "90m"
    local extra_env="${6:-}"

    local env_vars="JOB_MODE=${job_mode}"
    if [[ -n "${extra_env}" ]]; then
        env_vars="${env_vars},${extra_env}"
    fi

    echo ""
    echo "  Deploying: ${job_name}  (${env_vars})"

    gcloud run jobs deploy "${job_name}" \
        --image             "${IMAGE_PATH}" \
        --service-account   "${SERVICE_ACCOUNT}" \
        --region            "${REGION}" \
        --tasks             1 \
        --max-retries       1 \
        --task-timeout      "${timeout}" \
        --memory            "${memory}" \
        --cpu               "${cpu}" \
        --set-secrets       "${COMMON_SECRETS}" \
        --set-env-vars      "${env_vars}"
}

# NRT daily: 3 sensors × ~1 day each — relatively fast
deploy_job "viirs-netpp-nrt-daily" "nrt_daily" "8Gi" "4" "2h"

# SQ sweep: 3 sensors × many years on first run, then near-instant on subsequent runs
deploy_job "viirs-netpp-sq-sweep"  "sq_sweep"  "8Gi" "4" "8h"

# Monthly composites:
# One job per sensor, each runs nrt then sq in sequence.
# Give these more memory/CPU because they download daily NetCDFs and build
# monthly composites from them.
deploy_job "viirs-netpp-monthly-snpp"   "monthly_composite" "16Gi" "4" "4h" "TARGET_SENSOR=snpp"
deploy_job "viirs-netpp-monthly-noaa20" "monthly_composite" "16Gi" "4" "4h" "TARGET_SENSOR=noaa20"

# ---------------------------------------------------------------------------
# 3. Schedule jobs with Cloud Scheduler
# ---------------------------------------------------------------------------
echo ""
echo ">>> Configuring Cloud Scheduler..."

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" \
    --format='value(projectNumber)')"

# Base URI for triggering Cloud Run jobs
RUN_BASE="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_NUMBER}/jobs"

create_scheduler() {
    local scheduler_name="$1"
    local job_name="$2"
    local cron="$3"
    local tz="$4"

    echo "  Scheduler: ${scheduler_name} → ${job_name}  [${cron}  ${tz}]"

    # Delete if exists (allows idempotent redeploy)
    gcloud scheduler jobs delete "${scheduler_name}" \
        --location "${REGION}" --quiet 2>/dev/null || true

    gcloud scheduler jobs create http "${scheduler_name}" \
        --location                    "${REGION}" \
        --schedule                    "${cron}" \
        --time-zone                   "${tz}" \
        --uri                         "${RUN_BASE}/${job_name}:run" \
        --http-method                 POST \
        --oauth-service-account-email "${SERVICE_ACCOUNT}" \
        --oauth-token-scope           "https://www.googleapis.com/auth/cloud-platform"
}

# NRT daily — runs at 08:15 local time (America/Los_Angeles).
create_scheduler \
    "viirs-netpp-nrt-daily-sched" \
    "viirs-netpp-nrt-daily" \
    "15 8 * * *" \
    "America/Los_Angeles"

# SQ sweep — runs weekly on Wednesday at 23:00 local time (America/Los_Angeles).
create_scheduler \
    "viirs-netpp-sq-sweep-sched" \
    "viirs-netpp-sq-sweep" \
    "0 23 * * 3" \
    "America/Los_Angeles"

# Monthly composite — SNPP
# Runs on the 5th and 10th at 11:00 local time.
create_scheduler \
    "viirs-netpp-monthly-snpp-sched" \
    "viirs-netpp-monthly-snpp" \
    "0 11 5,10 * *" \
    "America/Los_Angeles"

# Monthly composite — NOAA-20
# Stagger by 15 minutes so both monthly jobs do not start at once.
create_scheduler \
    "viirs-netpp-monthly-noaa20-sched" \
    "viirs-netpp-monthly-noaa20" \
    "15 11 5,10 * *" \
    "America/Los_Angeles"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "Deployment complete."
echo ""
echo "Image   : ${IMAGE_PATH}"
echo ""
echo "Jobs    :"
echo "  viirs-netpp-nrt-daily        (NRT, all sensors, daily 3-day buffer, 8Gi/4CPU)"
echo "  viirs-netpp-sq-sweep         (SQ,  all sensors, full sweep from mission start, 8Gi/4CPU)"
echo "  viirs-netpp-monthly-snpp     (Monthly composite, SNPP, nrt + sq)"
echo "  viirs-netpp-monthly-noaa20   (Monthly composite, NOAA-20, nrt + sq)"
echo ""
echo "Schedules (America/Los_Angeles):"
echo "  08:15 daily         → viirs-netpp-nrt-daily"
echo "  23:00 Wednesdays    → viirs-netpp-sq-sweep"
echo "  11:00 on 5th,10th   → viirs-netpp-monthly-snpp"
echo "  11:15 on 5th,10th   → viirs-netpp-monthly-noaa20"
echo ""
echo "Manual monthly backfill examples:"
echo "  gcloud run jobs execute viirs-netpp-monthly-snpp \\"
echo "    --region ${REGION} \\"
echo "    --update-env-vars TARGET_YEAR=2026,TARGET_MONTH=1"
echo ""
echo "  gcloud run jobs execute viirs-netpp-monthly-noaa20 \\"
echo "    --region ${REGION} \\"
echo "    --update-env-vars TARGET_YEAR=2026,TARGET_MONTH=1"
echo ""
echo "First-run tip:"
echo "  The SQ sweep will backfill from 2026-01-01 on its first execution."
echo "  Monitor via: gcloud run jobs executions list --job viirs-netpp-sq-sweep --region ${REGION}"
echo "============================================================"