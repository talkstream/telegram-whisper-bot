#!/bin/bash

set -e

PROJECT_ID="editorials-robot"
REGION="europe-west1"
SERVICE_NAME="audio-processor"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME:latest"

# 1. Sync Services
echo "🔄 Syncing shared package..."
rm -rf audio-processor-deploy/services
rm -rf audio-processor-deploy/shared
cp -r shared audio-processor-deploy/

# 2. Build App Image (Fast)
echo "🚀 Building App Image..."
cd audio-processor-deploy
# Using standard gcloud build
../google-cloud-sdk/bin/gcloud builds submit --tag $IMAGE . --project=$PROJECT_ID
cd ..

# 3. Deploy
echo "☁️ Deploying to Cloud Run..."
bash deploy_audio_processor_docker.sh