# SET VARIABLES
PROJECT_ID="ggn-nmfs-swfscerddap-dev-1"
REGION="us-east4"
REPO_NAME="mh1-mpoc-mpic-processor"
IMAGE_NAME="mh1-processor"
TAG="v1"
SERVICE_ACCOUNT="swfscerddap-run-sa1@${PROJECT_ID}.iam.gserviceaccount.com"
JOB_NAME="mh1-job"
SCHEDULER_NAME="mh1-mpic-mpoc-daily"

# Full Image Path
IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:${TAG}"

# 1. LOCAL DOCKER BUILD
echo "Building local Docker image..."
docker build -t ${IMAGE_NAME} .

# 2. AUTH & PUSH
gcloud config set project $PROJECT_ID
gcloud auth configure-docker ${REGION}-docker.pkg.dev

docker tag ${IMAGE_NAME} ${IMAGE_PATH}
docker push ${IMAGE_PATH}

# 3. CREATE OR UPDATE CLOUD RUN JOB
echo "Deploying Cloud Run Job..."

gcloud run jobs deploy ${JOB_NAME} \
    --image ${IMAGE_PATH} \
    --service-account=${SERVICE_ACCOUNT} \
    --tasks=1 \
    --max-retries=0 \
    --task-timeout=3600s \
    --region=${REGION} \
    --memory=2Gi \
    --cpu=1 \
    --remove-secrets="/secrets/config/config.yml" \
    --update-secrets="/secrets/netrc/file=netrc-secret:latest,/secrets/cookies/file=urs-cookies-secret:latest" \
    --update-env-vars="ROYLIB_CONFIG=/app/config/config.yml"




# 4. CONFIGURE AUTOMATION (SCHEDULER)
echo "Configuring Scheduler..."
# We delete first to ensure a clean slate, so we must use 'create' next.
gcloud scheduler jobs delete ${SCHEDULER_NAME} --location=${REGION} --quiet || true

gcloud scheduler jobs create http ${SCHEDULER_NAME} \
    --location=${REGION} \
    --schedule="0 10 * * *" \
    --time-zone="America/Los_Angeles" \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
    --http-method=POST \
    --oauth-service-account-email=${SERVICE_ACCOUNT} \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"

# # 5. VERIFY
# echo "Triggering manual test run..."
# gcloud scheduler jobs run ${SCHEDULER_NAME} --location=${REGION}
