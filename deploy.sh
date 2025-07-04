#!/bin/bash
# Fast deployment script for Telegram Whisper Bot

set -e  # Exit on error

echo "🚀 Starting deployment..."

# Check if we're in the right directory
if [ ! -f "app.yaml" ]; then
    echo "❌ Error: app.yaml not found. Run this script from the project root."
    exit 1
fi

# Parse command line arguments
DEPLOY_MAIN=true
DEPLOY_AUDIO=false
AUTO_SWITCH=true

while [[ $# -gt 0 ]]; do
    case $1 in
        --audio)
            DEPLOY_AUDIO=true
            shift
            ;;
        --all)
            DEPLOY_AUDIO=true
            shift
            ;;
        --no-switch)
            AUTO_SWITCH=false
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: ./deploy.sh [--audio] [--all] [--no-switch]"
            exit 1
            ;;
    esac
done

# Deploy main app to App Engine
if [ "$DEPLOY_MAIN" = true ]; then
    echo "📦 Deploying main app to App Engine..."
    
    if [ "$AUTO_SWITCH" = true ]; then
        gcloud app deploy --project=editorials-robot --quiet --promote --stop-previous-version
    else
        gcloud app deploy --project=editorials-robot --quiet
    fi
    
    echo "✅ Main app deployed successfully"
fi

# Deploy audio processor if requested
if [ "$DEPLOY_AUDIO" = true ]; then
    echo "🎵 Deploying audio processor Cloud Function..."
    
    cd audio-processor-deploy
    gcloud functions deploy audio-processor \
        --runtime python311 \
        --trigger-topic audio-processing-jobs \
        --memory 1GB \
        --timeout 540s \
        --no-gen2 \
        --set-env-vars GCP_PROJECT=editorials-robot \
        --project editorials-robot \
        --region europe-west1 \
        --quiet
    
    cd ..
    echo "✅ Audio processor deployed successfully"
fi

# Show deployment info
echo ""
echo "🎉 Deployment complete!"
echo ""
echo "📊 View logs:"
echo "  Main app: gcloud app logs tail -s default --project=editorials-robot"
echo "  Audio processor: gcloud functions logs read audio-processor --project=editorials-robot"
echo ""
echo "🌐 Application URL: https://editorials-robot.ew.r.appspot.com"