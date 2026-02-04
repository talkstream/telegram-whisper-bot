# Alibaba Cloud Deployment Guide

## Quick Deploy

### Option 1: Using Alibaba Console (Recommended)

1. **Webhook Handler:**
   - Go to: Function Compute → Services → telegram-whisper-bot-prod → Functions → webhook-handler
   - Click "Code" tab → Upload → Select `alibaba/webhook-handler/code.zip`
   - Click "Deploy"

2. **Audio Processor:**
   - Go to: Function Compute → Services → telegram-whisper-bot-prod → Functions → audio-processor
   - Click "Code" tab → Upload → Select `alibaba/audio-processor/code.zip`
   - Click "Deploy"

3. **Set Environment Variables:**
   - For both functions, go to Configuration → Environment Variables
   - Add: `DASHSCOPE_API_KEY` = (your DashScope API key)

### Option 2: Using aliyun CLI

```bash
./alibaba/scripts/deploy.sh
```

## Environment Variables Required

| Variable | Description | Required |
|----------|-------------|----------|
| `DASHSCOPE_API_KEY` | Alibaba DashScope API key for Qwen LLM | Yes |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token | Yes |
| `TABLESTORE_ENDPOINT` | Tablestore endpoint URL | Yes |
| `LOG_LEVEL` | Logging level (INFO, WARNING, ERROR) | No (default: WARNING) |
| `GOOGLE_API_KEY` | Gemini API key (fallback) | No |

## Architecture

```
Telegram → Webhook Handler → MNS Queue → Audio Processor
                ↓                              ↓
          Tablestore                    Qwen ASR + LLM
                                              ↓
                                      Tablestore (results)
```

## Testing

1. Send `/start` to the bot - should get welcome message
2. Send `/balance` - should show user balance
3. Send a voice message - should get transcription

## Troubleshooting

### "DASHSCOPE_API_KEY not set"
- Add the environment variable in Function Compute console

### "Failed to connect to Tablestore"
- Check STS credentials are automatically provided by FC runtime
- Verify TABLESTORE_ENDPOINT is correct

### Transcription empty/failed
- Check logs in Function Compute console
- Verify audio file is not corrupted
- Try sending a different voice message
