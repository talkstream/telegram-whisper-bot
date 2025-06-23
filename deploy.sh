#!/bin/bash

# Deployment script for Telegram Whisper Bot

echo "=== Deploying Telegram Whisper Bot ==="
echo ""

# Deploy main bot to App Engine
echo "1. Deploying main bot to App Engine..."
echo "Run this command:"
echo "gcloud app deploy --project=editorials-robot"
echo ""

# Deploy audio processor to Cloud Functions
echo "2. Deploying audio processor to Cloud Functions..."
echo "First, navigate to the audio processor directory:"
echo "cd audio-processor-deploy"
echo ""
echo "Then run this command:"
cat << 'EOF'
gcloud functions deploy audio-processor \
  --runtime python311 \
  --trigger-topic audio-processing-jobs \
  --memory 1GB \
  --timeout 540s \
  --no-gen2 \
  --set-env-vars GCP_PROJECT=editorials-robot \
  --project editorials-robot \
  --region europe-west1 \
  --entry-point handle_pubsub_message
EOF
echo ""
echo "=== Deployment commands ready ==="