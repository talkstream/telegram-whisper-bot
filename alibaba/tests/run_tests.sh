#!/bin/bash
# Run integration tests for Alibaba Cloud services
# Usage: ./run_tests.sh [test_name]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALIBABA_DIR="$(dirname "$SCRIPT_DIR")"

# Load environment from aliyun CLI config
export ALIBABA_ACCESS_KEY=$(aliyun configure get --profile default access-key-id 2>/dev/null || echo "")
export ALIBABA_SECRET_KEY=$(aliyun configure get --profile default access-key-secret 2>/dev/null || echo "")

# Load from Function Compute if available
if [ -z "$DASHSCOPE_API_KEY" ]; then
    export DASHSCOPE_API_KEY=$(aliyun fc-open GetFunction \
        --serviceName telegram-whisper-bot-prod \
        --functionName audio-processor \
        --region eu-central-1 2>/dev/null | \
        grep -o '"DASHSCOPE_API_KEY": "[^"]*"' | cut -d'"' -f4 || echo "")
fi

if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    export TELEGRAM_BOT_TOKEN=$(aliyun fc-open GetFunction \
        --serviceName telegram-whisper-bot-prod \
        --functionName webhook-handler \
        --region eu-central-1 2>/dev/null | \
        grep -o '"TELEGRAM_BOT_TOKEN": "[^"]*"' | cut -d'"' -f4 || echo "")
fi

# Default endpoints
export TABLESTORE_ENDPOINT="${TABLESTORE_ENDPOINT:-https://twbot-prod.eu-central-1.ots.aliyuncs.com}"
export TABLESTORE_INSTANCE="${TABLESTORE_INSTANCE:-twbot-prod}"
export MNS_ENDPOINT="${MNS_ENDPOINT:-https://5907469887573677.mns.eu-central-1.aliyuncs.com}"
export WEBHOOK_URL="${WEBHOOK_URL:-https://webhook-handler-telegrabot-prod-zmdupczvfj.eu-central-1.fcapp.run/}"

echo "=== Alibaba Cloud Integration Tests ==="
echo ""
echo "Configuration:"
echo "  TABLESTORE_ENDPOINT: $TABLESTORE_ENDPOINT"
echo "  MNS_ENDPOINT: $MNS_ENDPOINT"
echo "  WEBHOOK_URL: $WEBHOOK_URL"
echo "  DASHSCOPE_API_KEY: ${DASHSCOPE_API_KEY:0:10}..."
echo "  TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:0:10}..."
echo ""

# Add webhook-handler to PYTHONPATH
export PYTHONPATH="$ALIBABA_DIR/webhook-handler:$PYTHONPATH"

# Run tests
if [ -n "$1" ]; then
    python -m pytest "$SCRIPT_DIR/test_services.py::$1" -v --tb=short
else
    python -m pytest "$SCRIPT_DIR/test_services.py" -v --tb=short
fi
