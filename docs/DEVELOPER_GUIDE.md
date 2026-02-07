# Developer Guide

Architecture & config: see [CLAUDE.md](../CLAUDE.md)

## Stack

| Component | Technology |
|-----------|------------|
| Runtime | Python 3.10 |
| Cloud | Alibaba FC 3.0 |
| Database | Tablestore |
| Queue | MNS |
| Storage | OSS (diarization temp files) |
| ASR | Qwen3-ASR-Flash, Fun-ASR |
| LLM | Qwen-turbo (fallback: Gemini 2.5 Flash) |
| Deploy | Serverless Devs (`s`) |

## Local Setup

```bash
git clone https://github.com/talkstream/telegram-whisper-bot.git
cd telegram-whisper-bot
python3.10 -m venv venv && source venv/bin/activate
pip install -r alibaba/webhook-handler/requirements.txt
pip install -r alibaba/audio-processor/requirements.txt
```

### Environment

```bash
export TELEGRAM_BOT_TOKEN="..."
export OWNER_ID="..."
export DASHSCOPE_API_KEY="..."
export WHISPER_BACKEND="qwen-asr"
export TABLESTORE_ENDPOINT="https://instance.eu-central-1.ots.aliyuncs.com"
export TABLESTORE_INSTANCE="..."
export ALIBABA_ACCESS_KEY="..."
export ALIBABA_SECRET_KEY="..."
export MNS_ENDPOINT="https://account.mns.eu-central-1.aliyuncs.com"
export OSS_BUCKET="twbot-prod-audio"
export OSS_ENDPOINT="oss-eu-central-1.aliyuncs.com"
# Optional
export GOOGLE_API_KEY="..."  # Gemini fallback
export LOG_LEVEL="INFO"
```

## Deploy

```bash
npm install -g @serverless-devs/s
cd alibaba && s deploy -y
```

Pre-deploy auto-copies `shared/` → `services/` for both functions.

### Webhook

```bash
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://fc-endpoint/webhook"}'
```

## Database

See [CLAUDE.md](../CLAUDE.md#database-tablestore). Patterns: see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## Fork

1. `gh repo fork talkstream/telegram-whisper-bot --clone`
2. Create bot via [@BotFather](https://t.me/BotFather)
3. Set up Alibaba Cloud (FC, Tablestore, MNS, OSS, DashScope)
4. `cp alibaba/s.yaml.example alibaba/s.yaml` → edit
5. `cd alibaba && s deploy -y`

## Testing

```bash
pytest alibaba/tests/ -v
```

## Monitoring

```bash
s logs --tail webhook-handler
s logs --tail audio-processor
```

Admin: `/status`, `/metrics 24`, `/cost`

Issues: see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

*v3.6.0*
