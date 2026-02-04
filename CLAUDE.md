# Telegram Whisper Bot

## Quick Reference

| Property | Value |
|----------|-------|
| **Version** | v3.3.0 |
| **ASR** | `qwen3-asr-flash` (REST API) |
| **LLM** | `qwen-turbo` (fallback: Gemini 2.5 Flash) |
| **Infra** | Alibaba FC 3.0 + Tablestore + MNS |
| **Region** | eu-central-1 (Frankfurt) |
| **GitHub** | https://github.com/talkstream/telegram-whisper-bot (private) |

### Critical Configuration
**Full details: [docs/ALIBABA_CRITICAL_CONFIG.md](docs/ALIBABA_CRITICAL_CONFIG.md)**

| Service | Model | Endpoint |
|---------|-------|----------|
| ASR | `qwen3-asr-flash` | `https://dashscope-intl.aliyuncs.com/api/v1` |
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

### Admin Commands
| Command | Description |
|---------|-------------|
| `/user [search]` | Search users |
| `/export [type] [days]` | Export CSV (users/logs/payments) |
| `/report [daily\|weekly]` | Trigger report |
| `/review_trials` | Review pending trials |
| `/credit <id> <min>` | Add minutes |
| `/stat` | Usage statistics |
| `/cost` | Processing costs |
| `/status` | Queue status |
| `/flush` | Clean stuck jobs |
| `/metrics [hours]` | Performance metrics |
| `/batch [user_id]` | Show user's job queue |

---

## Tariff Structure (300% markup)

| Package | Minutes | Price | Cost/min |
|---------|---------|-------|----------|
| Micro | 10 | 5 ⭐ | 0.50 ⭐ (promo, limit 3) |
| Start | 50 | 35 ⭐ | 0.70 ⭐ |
| Standard | 200 | 119 ⭐ | 0.60 ⭐ |
| Profi | 1000 | 549 ⭐ | 0.55 ⭐ |
| MAX | 8888 | 4444 ⭐ | 0.50 ⭐ |

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
| audio_jobs | job_id | user_id, status, result |
| trial_requests | user_id | status, request_timestamp |
| transcription_logs | log_id | user_id, timestamp, duration |
| payment_logs | payment_id | user_id, amount, timestamp |

---

## Technical Guidelines

### Service Layer Pattern
- All business logic in services (AudioService, TablestoreService, etc.)
- Never call APIs directly in handlers
- Pass API keys, not client objects

### Gemini Fallback (google-genai SDK)
```python
import google.genai as genai
client = genai.Client(vertexai=True, project=PROJECT_ID, location='europe-west1')
response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
```

**NEVER use deprecated `vertexai` imports.**

### FFmpeg Settings (v3.0.1)
```bash
# Standard (>10 sec)
ffmpeg -y -i input.ogg -b:a 32k -ar 16000 -ac 1 -threads 4 output.mp3

# Short audio (<10 sec)
ffmpeg -y -i input.ogg -b:a 24k -ar 8000 -ac 1 -threads 4 output.mp3
```

---

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| Model not found | Using deprecated model | Use `qwen3-asr-flash` |
| Connection timeout | Wrong endpoint | Use `-intl` endpoint |
| 400 Bad Request | Wrong API format | Use REST API, not WebSocket |

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
| [archive/MIGRATION_COMPLETED.md](docs/archive/MIGRATION_COMPLETED.md) | GCP->Alibaba migration details |

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

*Last updated: 2026-02-04*
