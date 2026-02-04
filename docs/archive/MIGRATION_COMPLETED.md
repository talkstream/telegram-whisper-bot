# Migration Plan: GCP -> Alibaba Cloud (COMPLETED)

**Status:** COMPLETED
**Date:** 2026-02-04
**Version:** v3.0.0 -> v3.0.1

---

## Architecture Transition

### Before (GCP + Alibaba ASR)
```
GCP:
  - App Engine (webhook handler)
  - Cloud Functions (audio processor)
  - Firestore (database)
  - Pub/Sub (message queue)
  - Secret Manager, Cloud Scheduler, Cloud Logging
Alibaba:
  - DashScope API (ASR only)
  - OSS (temp file storage)
```

### After (100% Alibaba Cloud)
```
Alibaba Cloud:
  - Function Compute 3.0 (webhook + audio processor)
  - Tablestore (database)
  - MNS (message queue)
  - DashScope qwen3-asr-flash (ASR via REST)
  - DashScope qwen-turbo (LLM formatting)
  - SLS (logging)
```

---

## Component Mapping

| GCP Component | Alibaba Component | Status |
|---------------|-------------------|--------|
| App Engine | Function Compute 3.0 | DONE |
| Cloud Functions | Function Compute 3.0 | DONE |
| Firestore | Tablestore | DONE |
| Pub/Sub | MNS | DONE |
| Secret Manager | Environment Variables | DONE |
| Cloud Scheduler | FC Scheduled Triggers | DONE |
| Cloud Logging | SLS | DONE |

---

## Cost Savings Achieved

| Component | GCP (was) | Alibaba (now) | Savings |
|-----------|-----------|---------------|---------|
| Compute | ~$15/mo | ~$5/mo | -67% |
| Database | ~$3/mo | ~$1/mo | -67% |
| Queue | ~$1/mo | ~$0/mo | -100% |
| ASR | ~$6/mo | ~$2/mo | -67% |
| **Total** | **~$25/mo** | **~$8/mo** | **-68%** |

---

## DashScope Models (DO NOT CHANGE)

| Service | Model | Endpoint |
|---------|-------|----------|
| ASR | `qwen3-asr-flash` | `https://dashscope-intl.aliyuncs.com/api/v1` |
| LLM | `qwen-turbo` | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| Fallback | `gemini-2.5-flash` | Google AI (for LLM only) |

---

## Project Structure (Post-Migration)

```
telegram-whisper-bot/
├── alibaba/                    # Active Alibaba Cloud code
│   ├── s.yaml                  # Serverless Devs config
│   ├── webhook-handler/        # FC webhook handler
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── services/
│   └── audio-processor/        # FC audio processor
│       ├── handler.py
│       ├── requirements.txt
│       └── services/
├── handlers/                   # GCP legacy (reference)
├── app/                        # GCP legacy (reference)
└── docs/
    ├── ALIBABA_CRITICAL_CONFIG.md
    └── archive/
        └── MIGRATION_COMPLETED.md  # This file
```

---

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DASHSCOPE_API_KEY` | DashScope API key | Yes |
| `WHISPER_BACKEND` | ASR backend (`qwen-asr`) | Yes |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | Yes |
| `TABLESTORE_ENDPOINT` | Tablestore endpoint | Yes |
| `TABLESTORE_INSTANCE` | Instance name | Yes |
| `MNS_ENDPOINT` | MNS endpoint | Yes |
| `GOOGLE_API_KEY` | Gemini API key (fallback) | Optional |

---

## Lessons Learned

1. **REST > WebSocket for Serverless**: qwen3-asr-flash REST API is simpler and more reliable than WebSocket in FC
2. **Use -intl endpoints**: Always use `dashscope-intl.aliyuncs.com` for international access
3. **Avoid deprecated models**: paraformer-v1/v2 are outdated, use qwen3-asr-flash
4. **Memory optimization**: 512MB is sufficient for webhook handler with lazy imports

---

*For current configuration details, see [ALIBABA_CRITICAL_CONFIG.md](../ALIBABA_CRITICAL_CONFIG.md)*
*For version history, see [VERSION_HISTORY.md](VERSION_HISTORY.md)*
