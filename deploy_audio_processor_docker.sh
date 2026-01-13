#!/bin/bash

set -e  # Exit on any error

PROJECT_ID="editorials-robot"
REGION="europe-west1"
FUNCTION_NAME="audio-processor"
TOPIC="audio-processing-jobs"

echo "Building Docker image..."
cd audio-processor-deploy
docker build -t gcr.io/$PROJECT_ID/$FUNCTION_NAME:ffmpeg8 .

echo "Pushing Docker image to GCR..."
docker push gcr.io/$PROJECT_ID/$FUNCTION_NAME:ffmpeg8

echo "Deploying Cloud Function (2nd gen) with custom container..."
gcloud functions deploy $FUNCTION_NAME \
  --gen2 \
  --region=$REGION \
  --entry-point=process_audio \
  --trigger-topic=$TOPIC \
  --memory=1GB \
  --timeout=540s \
  --max-instances=10 \
  --set-env-vars=GCP_PROJECT=$PROJECT_ID,WHISPER_MODEL_PATH=/opt/whisper/models/ggml-base.bin \
  --image=gcr.io/$PROJECT_ID/$FUNCTION_NAME:ffmpeg8 \
  --quiet

echo "Deployment completed successfully!"

# Verify deployment
echo "Verifying FFmpeg version..."
gcloud functions logs read $FUNCTION_NAME --region=$REGION --limit=10
