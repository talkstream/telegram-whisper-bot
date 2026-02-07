# Telegram Whisper Bot

## Quick Reference

| Property | Value |
|----------|-------|
| **Version** | v3.6.0 |
| **ASR** | `qwen3-asr-flash` (REST API), `fun-asr` (diarization) |
| **LLM** | `qwen-turbo` (fallback: Gemini 2.5 Flash) |
| **Infra** | Alibaba FC 3.0 + Tablestore + MNS |
| **Region** | eu-central-1 (Frankfurt) |
| **GitHub** | https://github.com/talkstream/telegram-whisper-bot (private) |

### Critical Configuration
**Full details: [docs/ALIBABA_CRITICAL_CONFIG.md](docs/ALIBABA_CRITICAL_CONFIG.md)**

| Service | Model | Endpoint |
|---------|-------|----------|
| ASR | `qwen3-asr-flash` | `https://dashscope-intl.aliyuncs.com/api/v1` |
| Diarization | `fun-asr` | `https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription` |
| LLM | `qwen-turbo` | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |

### DO NOT USE
- ~~paraformer-v1/v2~~ (deprecated)
- ~~WebSocket ASR~~ (complex in serverless)
- ~~dashscope.aliyuncs.com~~ (use `-intl`)

---

## Architecture

```
alibaba/
├── shared/              # Single source of truth for services (v3.3.0+)
│   ├── audio.py         # AudioService (Qwen3-ASR + Qwen-turbo)
│   ├── tablestore_service.py
│   ├── telegram.py      # TelegramService
│   ├── mns_service.py   # MNSService + MNSPublisher
│   └── utility.py
├── webhook-handler/     # FastAPI on FC 3.0 (512MB)
│   └── services/ → ../shared/ (auto-copied at deploy)
└── audio-processor/     # MNS-triggered worker (1024MB)
    └── services/ → ../shared/ (auto-copied at deploy)
```

**Note**: `services/` directories are auto-generated from `shared/` during deployment via pre-deploy actions in `s.yaml`. Do not edit them directly.

### Key Features
- Async audio processing via MNS
- Video transcription support (MP4, MOV, WebM)
- User balance system with trial access
- Payment integration (Telegram Stars)
- FFmpeg audio normalization
- Evolving progress messages (edit_message_text through stages)

---

## Commands

### User Commands
| Command | Description |
|---------|-------------|
| `/start` | Registration/welcome |
| `/help` | Show help |
| `/balance` | Check remaining minutes |
| `/trial` | Request trial (15 min) |
| `/buy_minutes` | Purchase minutes |
| `/settings` | Show settings |
| `/code` | Toggle monospace output |
| `/yo` | Toggle letter yo |
| `/output` | Toggle long text mode (split/file) |
| `/dialogue` | Toggle diarization mode (Fun-ASR) |

### Admin Commands
| Command | Description |
|---------|-------------|
| `/admin` | Show admin help |
| `/user [search]` | Search users |
| `/credit <id> <min>` | Add minutes |
| `/review_trials` | Review trial requests |
| `/stat` | Statistics |
| `/cost` | Processing costs |
| `/metrics [hours]` | Performance metrics |
| `/status` | MNS queue status |
| `/flush` | Clear stuck jobs |
| `/batch [user_id]` | User queue |
| `/mute [hours\|off]` | Mute error notifications |
| `/export` | Export CSV |
| `/report` | Daily/weekly report |

**Full admin guide:** see [ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md)

**Tariffs:** see [README.md](README.md#тарифы)

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DASHSCOPE_API_KEY` | DashScope API key |
| `WHISPER_BACKEND` | `qwen-asr` |
| `TELEGRAM_BOT_TOKEN` | Bot token |
| `TABLESTORE_ENDPOINT` | Tablestore endpoint |
| `TABLESTORE_INSTANCE` | Instance name |
| `MNS_ENDPOINT` | MNS endpoint |
| `GOOGLE_API_KEY` | Gemini fallback (optional) |
| `OSS_BUCKET` | OSS bucket for diarization |
| `OSS_ENDPOINT` | OSS endpoint |

---

## Deployment

### Deploy via Serverless Devs
```bash
cd alibaba
s deploy
```

### Update Webhook
```bash
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -d '{"url": "https://[fc-endpoint]/webhook"}'
```

**Full deployment guide: [alibaba/DEPLOY.md](alibaba/DEPLOY.md)**

---

## Database (Tablestore)

| Table | Primary Key | Fields |
|-------|-------------|--------|
| users | user_id | balance_minutes, trial_status, settings |
| audio_jobs | job_id | user_id, status, result, status_message_id |
| trial_requests | user_id | status, request_timestamp |
| transcription_logs | log_id | user_id, timestamp, duration |
| payment_logs | payment_id | user_id, amount, timestamp |

---

## Technical Guidelines

### Service Layer Pattern
- All business logic in services (AudioService, TablestoreService, etc.)
- Never call APIs directly in handlers
- Pass API keys, not client objects

### Progress Message Pattern (v3.4.0)
- `status_message_id` captured in `handle_audio_message` and passed through entire pipeline
- Sync: passed as parameter to `process_audio_sync`
- Async: stored in `job_data['status_message_id']` → MNS → audio-processor reads it
- Each stage: `edit_message_text` → `send_chat_action('typing')` → heavy work
- Result replaces status message (or delete+send if >4000 chars)
- LLM formatting skipped for text <= 100 chars

### Error Notifications (v3.6.0)
- `TelegramErrorHandler` in `utility.py` sends ERROR+ logs to OWNER_ID
- 60s cooldown between messages; `/mute <hours>` to silence
- Mute stored in `/tmp/twbot_mute_until` (resets on cold start)
- `pythonjsonlogger` not on FC runtime — fallback to stdlib formatter

### Gemini Fallback (google-genai SDK)
```python
import google.genai as genai
client = genai.Client(vertexai=True, project=PROJECT_ID, location='europe-west1')
response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
```

**NEVER use deprecated `vertexai` imports.**

**FFmpeg settings:** see [ALIBABA_CRITICAL_CONFIG.md](docs/ALIBABA_CRITICAL_CONFIG.md#ffmpeg)

**Troubleshooting:** see [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

---

## Documentation

| Document | Description |
|----------|-------------|
| [DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | Architecture, deployment, forking |
| [ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | Admin commands, monitoring |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues & solutions |
| [ALIBABA_CRITICAL_CONFIG.md](docs/ALIBABA_CRITICAL_CONFIG.md) | Critical Alibaba configuration |
| [UX_IMPROVEMENTS.md](docs/UX_IMPROVEMENTS.md) | UI/UX patterns and templates |
| [DEPLOY.md](alibaba/DEPLOY.md) | Deployment instructions |
| [archive/VERSION_HISTORY.md](docs/archive/VERSION_HISTORY.md) | Full version history |

---

## Cost Summary

| Component | Monthly Cost |
|-----------|--------------|
| Function Compute | ~$5 |
| Tablestore | ~$1 |
| MNS | ~$0 |
| DashScope (ASR+LLM) | ~$2 |
| **Total** | **~$8** |

*68% savings vs GCP (~$25/mo)*

---

*Last updated: 2026-02-07*
