#!/bin/bash

set -e

PROJECT_ID="editorials-robot"
REGION="europe-west1"
SERVICE_NAME="whisper-bot"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME:latest"

echo "Building and pushing image to GCR..."

# Use lightweight Dockerfile if available
if [ -f "Dockerfile.bot.light" ]; then
    echo "⚡ Using lightweight Dockerfile (no FFmpeg)..."
    mv Dockerfile Dockerfile.original
    cp Dockerfile.bot.light Dockerfile
    
    # Ensure cleanup on exit/error
    trap "mv Dockerfile.original Dockerfile" EXIT
fi

gcloud builds submit --tag $IMAGE . --project=$PROJECT_ID

# Restore original Dockerfile if we swapped it (trap handles it, but explicit is good too)
if [ -f "Dockerfile.original" ]; then
    mv Dockerfile.original Dockerfile
    trap - EXIT # Clear trap
fi

# Generate a random secret token
WEBHOOK_SECRET=$(openssl rand -hex 32)
echo "Generated webhook secret."

echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image=$IMAGE \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --set-env-vars=GCP_PROJECT=$PROJECT_ID,USE_ASYNC_PROCESSING=true,AUDIO_PROCESSING_TOPIC=audio-processing-jobs,TELEGRAM_WEBHOOK_SECRET=$WEBHOOK_SECRET \
  --project=$PROJECT_ID \
  --quiet

echo "Service deployed!"
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)' --project=$PROJECT_ID)
echo "Bot URL: $SERVICE_URL"

echo ""
echo "Setting Telegram Webhook..."
# Get bot token from Secret Manager (assuming it's already there as we used it in app)
TOKEN=$(gcloud secrets versions access latest --secret="telegram-bot-token" --project=$PROJECT_ID)

curl -X POST "https://api.telegram.org/bot$TOKEN/setWebhook" \
     -d "url=$SERVICE_URL" \
     -d "secret_token=$WEBHOOK_SECRET"

echo ""
echo "Deployment and Webhook setup complete!"
