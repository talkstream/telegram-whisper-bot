# Deployment Guide

## Quick Deploy (Serverless Devs)

```bash
cd alibaba && s deploy -y
```

Pre-deploy auto-copies `shared/` → `webhook-handler/services/` and `audio-processor/services/`.

### First-time Setup

```bash
npm install -g @serverless-devs/s
s config add  # configure Alibaba Cloud credentials
```

### Set Webhook

```bash
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://fc-endpoint/webhook"}'
```

### Verify

```bash
# Health check
curl "https://fc-endpoint/"

# Webhook info
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"

# Logs
s logs webhook-handler --tail
```

## Architecture

```
Telegram → Webhook Handler (512MB, 60s) → MNS → Audio Processor (1024MB, 300s)
                ↓                                        ↓
          Tablestore                              Qwen ASR + LLM + OSS
                                                         ↓
                                                   Tablestore (results)
```

## Environment Variables

See [CLAUDE.md](../CLAUDE.md#environment-variables)

## Troubleshooting

See [docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md)

---

*v4.0.0*
