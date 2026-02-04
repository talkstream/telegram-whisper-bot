#!/bin/bash
# Deploy script for Alibaba Cloud Function Compute
# Requires: aliyun CLI configured with proper credentials

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALIBABA_DIR="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Alibaba Cloud Function Compute Deployment ===${NC}"
echo ""

# Check if aliyun CLI is installed
if ! command -v aliyun &> /dev/null; then
    echo -e "${RED}Error: aliyun CLI not found. Install with: brew install aliyun-cli${NC}"
    exit 1
fi

# Configuration
SERVICE_NAME="telegram-whisper-bot-prod"
REGION="eu-central-1"

# Function names
WEBHOOK_FUNCTION="webhook-handler"
AUDIO_FUNCTION="audio-processor"

echo -e "${YELLOW}Region: $REGION${NC}"
echo -e "${YELLOW}Service: $SERVICE_NAME${NC}"
echo ""

# Create deployment packages
echo -e "${GREEN}Creating deployment packages...${NC}"

# Webhook handler
echo "  -> Packaging webhook-handler..."
cd "$ALIBABA_DIR/webhook-handler"
rm -f code.zip
zip -r code.zip . -x "*.pyc" -x "__pycache__/*" -x "*.zip" > /dev/null
echo "     Created: $(ls -lh code.zip | awk '{print $5}')"

# Audio processor
echo "  -> Packaging audio-processor..."
cd "$ALIBABA_DIR/audio-processor"
rm -f code.zip
zip -r code.zip . -x "*.pyc" -x "__pycache__/*" -x "*.zip" > /dev/null
echo "     Created: $(ls -lh code.zip | awk '{print $5}')"

echo ""
echo -e "${GREEN}Deployment packages created!${NC}"
echo ""

# Ask for confirmation
read -p "Deploy to Alibaba Cloud? (y/n) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled."
    exit 0
fi

echo ""
echo -e "${GREEN}Deploying functions...${NC}"

# Deploy webhook handler
echo "  -> Deploying $WEBHOOK_FUNCTION..."
cd "$ALIBABA_DIR/webhook-handler"
aliyun fc PUT /services/$SERVICE_NAME/functions/$WEBHOOK_FUNCTION/code \
    --region $REGION \
    --header "Content-Type:application/zip" \
    --body "file://code.zip" > /dev/null 2>&1 && \
    echo -e "     ${GREEN}✓ $WEBHOOK_FUNCTION deployed${NC}" || \
    echo -e "     ${RED}✗ Failed to deploy $WEBHOOK_FUNCTION${NC}"

# Deploy audio processor
echo "  -> Deploying $AUDIO_FUNCTION..."
cd "$ALIBABA_DIR/audio-processor"
aliyun fc PUT /services/$SERVICE_NAME/functions/$AUDIO_FUNCTION/code \
    --region $REGION \
    --header "Content-Type:application/zip" \
    --body "file://code.zip" > /dev/null 2>&1 && \
    echo -e "     ${GREEN}✓ $AUDIO_FUNCTION deployed${NC}" || \
    echo -e "     ${RED}✗ Failed to deploy $AUDIO_FUNCTION${NC}"

echo ""
echo -e "${GREEN}=== Deployment complete! ===${NC}"
echo ""
echo "Next steps:"
echo "1. Add DASHSCOPE_API_KEY to function environment variables"
echo "2. Test with /start command in Telegram"
echo "3. Send a voice message to test transcription"
