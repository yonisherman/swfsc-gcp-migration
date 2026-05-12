#!/bin/bash
set -euo pipefail

# ----------------------------
# SETTINGS
# ----------------------------
PROJECT_ID="YOUR_GCP_PROJECT_ID"
REGION="YOUR_GCP_REGION"

REPO_NAME="mh1-mpoc-mpic-processor"
IMAGE_NAME="mh1-processor"
TAG="v1"

SERVICE_ACCOUNT="YOUR_CLOUD_RUN_SERVICE_ACCOUNT@${PROJECT_ID}.iam.gserviceaccount.com"
JOB_NAME="mh1-job"
SCHEDULER_NAME="mh1-mpic-mpoc-daily"

# If you're on Apple Silicon, keep this. Otherwise you can set PLATFORM=""
PLATFORM="${PLATFORM:-linux/amd64}"

# Full image path
IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:${TAG}"

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
  echo "Repo not found. Creating..."
  gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Docker repository for MH1 / MPOC / MPIC processing"
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
echo "Deploying Cloud Run Job: ${JOB_NAME}"
gcloud run jobs deploy "${JOB_NAME}" \
  --image "${IMAGE_PATH}" \
  --service-account "${SERVICE_ACCOUNT}" \
  --tasks 1 \
  --max-retries 0 \
  --task-timeout 3600s \
  --region "${REGION}" \
  --memory 2Gi \
  --cpu 1 \
  --set-secrets "/secrets/netrc/file=netrc-secret:latest,/secrets/cookies/file=urs-cookies-secret:latest,/secrets/config/config.yml=erd-config-master:latest" \
  --set-env-vars "ROYLIB_CONFIG=/secrets/config/config.yml"

# ----------------------------
# 5) Configure Cloud Scheduler
# ----------------------------
echo "Configuring Cloud Scheduler: ${SCHEDULER_NAME}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

gcloud scheduler jobs delete "${SCHEDULER_NAME}" --location "${REGION}" --quiet || true

gcloud scheduler jobs create http "${SCHEDULER_NAME}" \
  --location "${REGION}" \
  --schedule "0 10 * * *" \
  --time-zone "America/Los_Angeles" \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_NUMBER}/jobs/${JOB_NAME}:run" \
  --http-method POST \
  --oauth-service-account-email "${SERVICE_ACCOUNT}" \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform"

echo "All done."
echo "Image:     ${IMAGE_PATH}"
echo "Job:       ${JOB_NAME}"
echo "Scheduler: ${SCHEDULER_NAME}"