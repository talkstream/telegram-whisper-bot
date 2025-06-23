#!/bin/bash

# Deploy script for audio processor function
# This ensures proper Pub/Sub trigger configuration

PROJECT_ID="editorials-robot"
REGION="europe-west1"
TOPIC="audio-processing-jobs"

echo "Deploying audio processor function..."

# First, let's make sure we're using the right requirements
cp requirements-audio-processor.txt requirements.txt.backup 2>/dev/null || true
cp requirements.txt requirements-original.txt.backup 2>/dev/null || true

# Use the audio processor requirements
cp requirements-audio-processor.txt requirements.txt

# Deploy the function with explicit Pub/Sub trigger
~/Downloads/google-cloud-sdk/bin/gcloud functions deploy audio-processor \
    --runtime=python311 \
    --trigger-topic=$TOPIC \
    --entry-point=handle_pubsub_message \
    --source=. \
    --timeout=540s \
    --memory=512MB \
    --max-instances=10 \
    --set-env-vars="GCP_PROJECT=$PROJECT_ID" \
    --region=$REGION \
    --project=$PROJECT_ID \
    --service-account=editorials-robot@appspot.gserviceaccount.com \
    --no-allow-unauthenticated

# Restore original requirements
cp requirements-original.txt.backup requirements.txt 2>/dev/null || true

echo ""
echo "Deployment complete! Check the logs with:"
echo "gcloud functions logs read audio-processor --region=$REGION --limit=50"