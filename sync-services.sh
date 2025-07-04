#!/bin/bash
# Sync services between main app and audio processor

echo "🔄 Syncing services to audio-processor-deploy..."

# Remove old services in audio processor
rm -rf audio-processor-deploy/services

# Copy services from main directory
cp -r services audio-processor-deploy/

echo "✅ Services synced successfully!"
echo ""
echo "📝 Note: Remember to deploy audio processor after syncing:"
echo "   ./deploy.sh --audio"