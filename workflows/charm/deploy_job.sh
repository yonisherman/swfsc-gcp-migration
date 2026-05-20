#!/bin/bash
set -euo pipefail

# ----------------------------
# SETTINGS
# ----------------------------
PROJECT_ID="YOUR_PROJECT_ID"
REGION="us-east4"

# IMPORTANT: Changed to match your C-HARM naming convention
REPO_NAME="edge-charm"              
IMAGE_NAME="edge-charm-cloud-run"   
TAG="v1"

SERVICE_ACCOUNT="YOUR_SERVICE_ACCOUNT@${PROJECT_ID}.iam.gserviceaccount.com"
PLATFORM="${PLATFORM:-linux/amd64}"

# Full image path
IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:${TAG}"

# C-HARM Secrets + Envs
COMMON_SECRETS="/secrets/netrc/file=netrc-secret:latest,/secrets/cookies/file=urs-cookies-secret:latest"
# Added PUBLISH_ENABLE and CONFIG_PATH
COMMON_ENVS="CONFIG_PATH=/app/config/config.yaml,SYNC_L3_FROM_WORK_BUCKET=1,PUBLISH_ENABLE=1,OMP_NUM_THREADS=8,BACKFILL_DAYS=5"


need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }; }
need gcloud
need docker

echo "Using image: ${IMAGE_PATH}"

# ----------------------------
# 1) Build image
# ----------------------------
echo "Building Docker image..."
if [[ -n "${PLATFORM}" ]]; then
  docker buildx build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${TAG}" --load .
else
  docker build -t "${IMAGE_NAME}:${TAG}" .
fi

# ----------------------------
# 2) gcloud project + Artifact Registry repo
# ----------------------------
echo "Setting gcloud project: ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "Checking Artifact Registry repo: ${REPO_NAME} (${REGION})..."
if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" >/dev/null 2>&1; then
  echo "Repo not found. Creating repo: ${REPO_NAME}..."
  gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Docker repository for C-HARM cloud processing"
else
  echo "Repo exists."
fi

echo "Configuring Docker auth for ${REGION}-docker.pkg.dev ..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# ----------------------------
# 3) Tag + push
# ----------------------------
echo "Tagging + pushing image..."
docker tag "${IMAGE_NAME}:${TAG}" "${IMAGE_PATH}"
docker push "${IMAGE_PATH}"
echo "OK: pushed ${IMAGE_PATH}"

# ----------------------------
# 4) Deploy Cloud Run Job
# ----------------------------
echo "Deploying Cloud Run Job: edge-charm-daily"

# We use 'deploy' but for jobs it's specifically 'gcloud run jobs deploy'
gcloud run jobs deploy "edge-charm-daily" \
  --image "${IMAGE_PATH}" \
  --service-account "${SERVICE_ACCOUNT}" \
  --tasks 1 \
  --max-retries 0 \
  --task-timeout "6h" \
  --region "${REGION}" \
  --memory "32Gi" \
  --cpu "8" \
  --set-secrets "${COMMON_SECRETS}" \
  --set-env-vars "${COMMON_ENVS}" \
  --quiet

# ----------------------------
# 5) Create Cloud Scheduler job
# ----------------------------
echo "Configuring Cloud Scheduler..."

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

# Run at 09:00 AM PST (adjust as needed for NASA data availability)
SCHEDULER_NAME="edge-charm-daily-sync"
gcloud scheduler jobs delete "${SCHEDULER_NAME}" --location "${REGION}" --quiet || true

gcloud scheduler jobs create http "${SCHEDULER_NAME}" \
  --location "${REGION}" \
  --schedule "0 9 * * *" \
  --time-zone "America/Los_Angeles" \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_NUMBER}/jobs/edge-charm-daily:run" \
  --http-method POST \
  --oauth-service-account-email "${SERVICE_ACCOUNT}" \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform"

echo "Success! C-HARM is deployed."