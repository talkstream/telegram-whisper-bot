#!/bin/bash

set -e

PROJECT_ID="editorials-robot"
IMAGE_NAME="audio-processor-base"
TAG="v1"
FULL_IMAGE="gcr.io/$PROJECT_ID/$IMAGE_NAME:$TAG"

echo "🏗 Building Base Image (FFmpeg 8.0 + Whisper)..."
echo "This may take 10-15 minutes, but only needs to be done once."

cd audio-processor-deploy

# Rename files temporarily to satisfy gcloud expectations
mv Dockerfile Dockerfile.app
mv Dockerfile.base Dockerfile

# Submit build
gcloud builds submit --tag $FULL_IMAGE --project=$PROJECT_ID .

# Restore file names
mv Dockerfile Dockerfile.base
mv Dockerfile.app Dockerfile

echo "✅ Base image built and pushed: $FULL_IMAGE"