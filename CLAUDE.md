# Telegram Whisper Bot

## Quick Reference

| Property | Value |
|----------|-------|
| **Version** | v5.0.0 |
| **ASR** | `qwen3-asr-flash` (REST), diarization: DashScope two-pass + AssemblyAI + Gemini backends |
| **LLM** | `qwen3.5-397b-a17b` (fallback: Gemini 3 Flash via AssemblyAI Gateway) |
| **Infra** | Alibaba FC 3.0 + Tablestore + MNS + OSS |
| **Region** | eu-central-1 (Frankfurt) |

### Critical Endpoints

| Service | Model | Endpoint |
|---------|-------|----------|
| ASR | `qwen3-asr-flash` | `https://dashscope-intl.aliyuncs.com/api/v1` |
| Diarization (speakers) | `fun-asr-mtl` | `https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription` |
| Diarization (text) | `qwen3-asr-flash-filetrans` | `https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription` |
| LLM (Qwen) | `qwen3.5-397b-a17b` | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| LLM (Gemini) | `gemini-3-flash-preview` | `https://llm-gateway.assemblyai.com/v1/chat/completions` |

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
`/start` `/help` `/balance` `/buy_minutes` `/settings` `/code` `/yo` `/output` `/speakers`

### Admin (OWNER_ID only)
| Command | Description |
|---------|-------------|
| `/admin` | Help |
| `/user [search]` | Search users |
| `/credit <id> <min>` | Add/remove minutes |
| `/stat` `/cost` `/metrics [h]` | Statistics |
| `/status` `/flush` `/batch [id]` | Queue management |
| `/mute [hours\|off]` | Mute error notifications |
| `/debug` | Toggle diarization debug output |
| `/llm [backend]` | LLM backend (qwen/assemblyai) |
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
| `DIARIZATION_BACKEND` | no | `dashscope` (default), `assemblyai`, `gemini` |
| `ASSEMBLYAI_API_KEY` | no | AssemblyAI diarization |
| `LLM_BACKEND` | no | `qwen` (default), `assemblyai` |
| `AUDIO_PROCESSOR_URL` | no | Direct HTTP fallback URL |
| `GEMINI_API_KEY` | no | Alias for `GOOGLE_API_KEY` (Gemini diarization/LLM) |
| `REGION` | no | FC region, default: `eu-central-1` |
| `AUDIO_JOBS_QUEUE` | no | MNS queue name, default: `telegram-whisper-bot-prod-audio-jobs` |

---

## Database (Tablestore)

| Table | PK | Key Fields |
|-------|----|------------|
| users | user_id (str) | balance_minutes, trial_status, settings |
| audio_jobs | job_id (str) | user_id, status, result, status_message_id |
| trial_requests | user_id (str) | status, request_timestamp (legacy, post-v4.2.0) |
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

### Diarization Backends (v4.0.0)
- `dashscope` (default): two-pass (fun-asr-mtl + qwen3-asr-flash-filetrans)
- `assemblyai`: Universal-2 (requires ASSEMBLYAI_API_KEY)
- `gemini`: Gemini 2.5 Flash (requires GOOGLE_API_KEY)
- All → same segment format, auto-fallback to dashscope on failure

### Diarization (v3.6.0 — two-pass)
- Pass 1: `fun-asr-mtl` — speaker labels + timestamps (no Russian)
- Pass 2: `qwen3-asr-flash-filetrans` — accurate Russian text + timestamps
- Both passes run in parallel via ThreadPoolExecutor, single OSS upload
- `_align_speakers_with_text()` merges by timestamp overlap (sliding window)
- `format_dialogue()` merges consecutive same-speaker segments, em-dash (—) format
- Fallback cascade: Pass 1 fail → text without speakers; Pass 2 fail → Pass 1 text; Both fail → regular ASR

### Parallel Processing (v4.3.0)
- Audio <15s: sync (webhook-handler, immediate response)
- Audio 15-59s: async (audio-processor, simple ASR without diarization)
- Audio ≥60s: async (audio-processor, two-pass diarization + speaker detection)

### Pipeline Logging (v4.3.1)
- Every pipeline step tagged: `[routing]`, `[download]`, `[transcribe]`, `[diarize]`, `[align]`, `[dialogue]`, `[format]`, `[llm]`, `[llm-qwen]`, `[llm-assemblyai]`, `[deliver]`, `[duration]`, `[telegram]`
- Logs metadata only (sizes, durations, decisions) — never content/transcripts
- LLM truncation safety: `finish_reason='length'` → return original text
- Fetch logs: `aliyun sls GetLogs` (use `/logs` skill, not `s logs`)

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

### Smart Chunking (v5.0.0)
- `LLM_CHUNK_THRESHOLD = 4000` chars — texts longer than this split into semantic chunks
- `LLM_MAX_CHUNKS = 20` — safety limit, returns unformatted if exceeded
- Dialogue: splits on `Спикер N:` boundaries, monologue: on paragraphs then sentences
- Each chunk gets 1-line overlap context `[...]` for LLM continuity, stripped on reassembly
- Hallucination guard: chunk output <10% of input → use original chunk
- `progress_callback(current, total)` propagated to ProgressManager

### ProgressManager (v5.0.0)
- Rate-limited Telegram progress updates (MIN_UPDATE_INTERVAL=3s)
- Stages with ETA: download → transcribe → diarize → align → format → deliver
- ETA based on audio duration: <5min→1min, 5-30min→2-3min, 30-60min→3-5min, 60+min→5-8min

### Auto-File Delivery (v5.0.0)
- `AUTO_FILE_THRESHOLD = 8000` chars → automatically send as `.txt` file
- Filename: `transcript_YYYY-MM-DD_HHMM.txt`

### Time Budget Watchdog (v5.0.0)
- `FC_TIMEOUT=600`, `SAFETY_MARGIN=30` — deadline = start + 570s
- If remaining < 60s before LLM → skip formatting, return raw text

### Quality Safeguards (v5.0.0)
- Micro-segment filter: <500ms + ≤2 words → merge with neighbor
- Gap ratio: >30% unmatched words → discard speaker labels
- Windowed timeline normalization for audio >15 min
- Dynamic diarization timeout: <30min→180s, 30-60min→240s, 60+min→300s

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
| [README.md](README.md) | Public: features, tariffs, commands (RU) |
| [DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | Setup, fork, testing |
| [ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | Admin commands, monitoring |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Error → cause → fix tables |
| [ALIBABA_CRITICAL_CONFIG.md](docs/ALIBABA_CRITICAL_CONFIG.md) | Endpoints, models, FFmpeg |
| [UX_IMPROVEMENTS.md](docs/UX_IMPROVEMENTS.md) | Progress messages, delivery modes |
| [DEPLOY.md](alibaba/DEPLOY.md) | Deploy + webhook setup |
| [VERSION_HISTORY.md](docs/archive/VERSION_HISTORY.md) | Full changelog |
| [pitch.html](pitch.html) | Investment pitch (18 slides) |
| [market-strategy.md](docs/plans/2026-02-09-market-strategy.md) | Market capture & exit strategy |
| [audit-roadmap.md](docs/plans/2026-02-19-audit-roadmap.md) | Expert panel audit & improvement roadmap |

**Archive** (GCP era, obsolete): `docs/archive/FFMPEG_BUILD.md`, `docs/archive/TERRAFORM_GCP.md`

## Cost: ~$8/mo

FC ~$5 + Tablestore ~$1 + DashScope ~$2 (68% savings vs GCP ~$25)

---

*v5.0.0 — 2026-02-20*
