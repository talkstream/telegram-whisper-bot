#!/bin/bash
# Synchronize shared/ folder to audio-processor-deploy/shared/
# This ensures both deployments have identical service code

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/shared"
DEST_DIR="$SCRIPT_DIR/audio-processor-deploy/shared"

echo "Syncing shared code..."

# Remove old telegram_bot_shared folder in destination (keep setup.py and egg-info)
if [ -d "$DEST_DIR/telegram_bot_shared" ]; then
    rm -rf "$DEST_DIR/telegram_bot_shared"
fi

# Copy fresh telegram_bot_shared folder
cp -r "$SRC_DIR/telegram_bot_shared" "$DEST_DIR/"

# Count files synced
FILE_COUNT=$(find "$DEST_DIR/telegram_bot_shared" -type f | wc -l | tr -d ' ')

echo "Synced $FILE_COUNT files to audio-processor-deploy/shared/"
