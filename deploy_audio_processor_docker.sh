#!/bin/bash

set -e  # Exit on any error

PROJECT_ID="editorials-robot"
REGION="europe-west1"
SERVICE_NAME="audio-processor"
TOPIC="audio-processing-jobs"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME:latest"

# Ensure gcloud is in PATH (using local installation if available)
if [ -f "./google-cloud-sdk/bin/gcloud" ]; then
    export PATH="$(pwd)/google-cloud-sdk/bin:$PATH"
fi

echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image=$IMAGE \
  --region=$REGION \
  --platform=managed \
  --memory=2Gi \
  --cpu=2 \
  --timeout=600s \
  --max-instances=10 \
  --no-allow-unauthenticated \
  --set-env-vars=GCP_PROJECT=$PROJECT_ID,WHISPER_MODEL_PATH=/opt/whisper/models/ggml-base.bin,TELEGRAM_OWNER_ID=775707 \
  --project=$PROJECT_ID \
  --quiet

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)' --project=$PROJECT_ID)

echo "Service deployed at: $SERVICE_URL"

# Get Project Number
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
SERVICE_ACCOUNT="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

# Create or update Pub/Sub subscription to push to Cloud Run
SUBSCRIPTION_NAME="$SERVICE_NAME-push-sub"

echo "Setting up Pub/Sub push subscription..."
if gcloud pubsub subscriptions describe $SUBSCRIPTION_NAME --project=$PROJECT_ID >/dev/null 2>&1; then
  gcloud pubsub subscriptions update $SUBSCRIPTION_NAME \
    --push-endpoint=$SERVICE_URL \
    --push-auth-service-account=$SERVICE_ACCOUNT \
    --project=$PROJECT_ID
else
  gcloud pubsub subscriptions create $SUBSCRIPTION_NAME \
    --topic=$TOPIC \
    --push-endpoint=$SERVICE_URL \
    --push-auth-service-account=$SERVICE_ACCOUNT \
    --project=$PROJECT_ID
fi

echo "Deployment and trigger setup completed successfully!"
