# Telegram Whisper Bot

## Quick Reference

| Property | Value |
|----------|-------|
| **Version** | v3.6.0 |
| **ASR** | `qwen3-asr-flash` (REST), `fun-asr-mtl` + `qwen3-asr-flash-filetrans` (two-pass diarization) |
| **LLM** | `qwen-turbo` (fallback: Gemini 2.5 Flash) |
| **Infra** | Alibaba FC 3.0 + Tablestore + MNS + OSS |
| **Region** | eu-central-1 (Frankfurt) |

### Critical Endpoints

| Service | Model | Endpoint |
|---------|-------|----------|
| ASR | `qwen3-asr-flash` | `https://dashscope-intl.aliyuncs.com/api/v1` |
| Diarization (speakers) | `fun-asr-mtl` | `https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription` |
| Diarization (text) | `qwen3-asr-flash-filetrans` | `https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription` |
| LLM | `qwen-turbo` | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |

**DO NOT USE:** ~~paraformer-v1/v2~~ (China-only), ~~WebSocket ASR~~, ~~dashscope.aliyuncs.com~~ (use `-intl`)

---

## Architecture

```
alibaba/
├── shared/              # Single source of truth (v3.3.0+)
│   ├── audio.py         # ASR + LLM + chunking + diarization
│   ├── tablestore_service.py
│   ├── telegram.py      # TelegramService + send_as_file
│   ├── mns_service.py   # MNSService + MNSPublisher
│   └── utility.py       # TelegramErrorHandler, setup_logging
├── webhook-handler/     # FC 3.0 (512MB, 60s timeout)
│   └── services/ → ../shared/ (auto-copied at deploy)
└── audio-processor/     # MNS worker (1024MB, 300s timeout)
    └── services/ → ../shared/ (auto-copied at deploy)
```

`services/` auto-generated from `shared/` via pre-deploy in `s.yaml`. Never edit directly.

---

## Commands

### User
`/start` `/help` `/balance` `/trial` `/buy_minutes` `/settings` `/code` `/yo` `/output` `/dialogue` `/speakers`

### Admin (OWNER_ID only)
| Command | Description |
|---------|-------------|
| `/admin` | Help |
| `/user [search]` | Search users |
| `/credit <id> <min>` | Add/remove minutes |
| `/review_trials` | Review pending trials |
| `/stat` `/cost` `/metrics [h]` | Statistics |
| `/status` `/flush` `/batch [id]` | Queue management |
| `/mute [hours\|off]` | Mute error notifications |
| `/debug` | Toggle diarization debug output |
| `/export` `/report` | Data export |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token |
| `OWNER_ID` | yes | Admin chat ID |
| `DASHSCOPE_API_KEY` | yes | DashScope API |
| `TABLESTORE_ENDPOINT` | yes | OTS endpoint |
| `TABLESTORE_INSTANCE` | yes | OTS instance |
| `MNS_ENDPOINT` | yes | MNS endpoint |
| `ALIBABA_ACCESS_KEY` | yes | Access key |
| `ALIBABA_SECRET_KEY` | yes | Secret key |
| `OSS_BUCKET` | yes | OSS bucket for diarization |
| `OSS_ENDPOINT` | yes | OSS endpoint |
| `WHISPER_BACKEND` | no | `qwen-asr` (default) |
| `LOG_LEVEL` | no | `INFO` (default) |
| `GOOGLE_API_KEY` | no | Gemini fallback |

---

## Database (Tablestore)

| Table | PK | Key Fields |
|-------|----|------------|
| users | user_id (str) | balance_minutes, trial_status, settings |
| audio_jobs | job_id (str) | user_id, status, result, status_message_id |
| trial_requests | user_id (str) | status, request_timestamp |
| transcription_logs | log_id (str) | user_id, timestamp, duration |
| payment_logs | payment_id (str) | user_id, amount, timestamp |

---

## Key Patterns

### Progress Messages (v3.4.0)
- `status_message_id` flows: webhook → `job_data` → MNS → audio-processor
- Each stage: `edit_message_text` → `send_chat_action('typing')` → work
- Result replaces status (or delete+send if >4000 chars)
- LLM skip for text ≤100 chars

### Delivery Logic (v3.6.0, 3-way)
1. Short (≤4000 chars): edit status message in place
2. File mode (`long_text_mode: file`): delete status → send .txt with caption
3. Split mode (default): delete status → `send_long_message()`

### Diarization (v3.6.0 — two-pass)
- Pass 1: `fun-asr-mtl` — speaker labels + timestamps (no Russian)
- Pass 2: `qwen3-asr-flash-filetrans` — accurate Russian text + timestamps
- Both passes run in parallel via ThreadPoolExecutor, single OSS upload
- `_align_speakers_with_text()` merges by timestamp overlap (sliding window)
- `format_dialogue()` merges consecutive same-speaker segments, em-dash (—) format
- Fallback cascade: Pass 1 fail → text without speakers; Pass 2 fail → Pass 1 text; Both fail → regular ASR

### Error Notifications (v3.6.0)
- `TelegramErrorHandler` sends ERROR+ to OWNER_ID (60s cooldown)
- `/mute <hours>` → `/tmp/twbot_mute_until` (resets on cold start)
- `pythonjsonlogger` not on FC runtime → graceful fallback to stdlib

### Gemini Fallback
```python
import google.genai as genai
client = genai.Client(vertexai=True, project=PROJECT_ID, location='europe-west1')
response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
```
**NEVER** use deprecated `vertexai` imports.

---

## Deployment

```bash
cd alibaba && s deploy -y
```

Full guide: [alibaba/DEPLOY.md](alibaba/DEPLOY.md)

---

## Documentation

| Document | Description |
|----------|-------------|
| [DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | Architecture, setup, forking |
| [ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | Admin commands, monitoring |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues |
| [ALIBABA_CRITICAL_CONFIG.md](docs/ALIBABA_CRITICAL_CONFIG.md) | API config, FFmpeg |
| [UX_IMPROVEMENTS.md](docs/UX_IMPROVEMENTS.md) | UX patterns |
| [DEPLOY.md](alibaba/DEPLOY.md) | Deployment |
| [VERSION_HISTORY.md](docs/archive/VERSION_HISTORY.md) | Full changelog |

## Cost: ~$8/mo

FC ~$5 + Tablestore ~$1 + DashScope ~$2 (68% savings vs GCP ~$25)

---

*v3.6.0 — 2026-02-07*
