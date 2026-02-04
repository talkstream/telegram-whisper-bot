# Telegram Whisper Bot

**Professional AI Transcription Bot for Telegram**

A production-ready bot that converts Voice, Audio, and Video messages into perfectly formatted Russian text using Alibaba Qwen3-ASR and Qwen LLM (with Gemini fallback).

## Key Features

- **Universal Transcription:** Handles Voice Notes, Audio files (MP3/WAV/OGG/etc), and Video files (MP4/MOV/etc)
- **High Performance:**
  - Async processing via Alibaba MNS (Message Service)
  - Sub-second response times with Function Compute 3.0
  - Cost-effective serverless architecture
- **AI Formatting:** Uses Qwen-plus LLM to add punctuation, fix grammar, and format paragraphs (Gemini 2.5 Flash fallback)
- **User Settings:**
  - `/code` - Toggle monospace font output
  - `/yo` - Toggle letter "ё" usage
- **Payment System:** Telegram Stars integration with progressive pricing
- **Admin Tools:** User management, metrics, CSV export, automated reports

## Tech Stack

- **Language:** Python 3.11
- **Cloud:** Alibaba Cloud (Function Compute 3.0, Tablestore, MNS, OSS)
- **Framework:** FastAPI (webhook-handler), Python handler (audio-processor)
- **AI Models:**
  - Transcription: Alibaba `qwen3-asr-flash` via DashScope REST API
  - Formatting: Alibaba `qwen-plus` (Gemini 2.5 Flash fallback)

## Architecture

```
Telegram API
    │ Webhook
    ▼
Function Compute (webhook-handler)
  ├─ FastAPI application
  ├─ Tablestore for user data
  └─ Sends job to MNS queue
    │ MNS Queue
    ▼
Function Compute (audio-processor)
  ├─ Qwen3-ASR-Flash (DashScope API)
  ├─ Qwen-plus LLM formatting
  └─ Memory: 512MB, Timeout: 5 min
    │
    ▼
Tablestore + KMS (secrets)
```

## Deployment

### Prerequisites
- Alibaba Cloud account with Function Compute enabled
- `aliyun` CLI or Serverless Devs (`s`) installed
- Telegram Bot Token from @BotFather
- DashScope API key for ASR and LLM

### Deploy

```bash
# Deploy using Serverless Devs
cd alibaba
s deploy

# Or using deployment script
./alibaba/scripts/deploy.sh
```

## Project Structure

```
├── main.py                    # GCP FastAPI entry (legacy)
├── alibaba/                   # Alibaba Cloud deployment
│   ├── webhook-handler/       # Main bot (FC trigger: HTTP)
│   │   ├── main.py           # FastAPI application
│   │   └── services/         # Alibaba-specific services
│   ├── audio-processor/       # Worker (FC trigger: MNS)
│   │   ├── handler.py        # MNS message handler
│   │   └── services/         # Audio processing services
│   ├── terraform/            # Infrastructure as Code
│   └── scripts/              # Deployment scripts
├── handlers/                  # Command handlers (reference)
├── shared/                    # Shared services package
│   └── telegram_bot_shared/
│       └── services/
└── requirements.txt
```

## Version History

Current version: **v3.0.1**

### v3.0.x - Alibaba Cloud Migration
- Complete migration from GCP to Alibaba Cloud
- Qwen3-ASR-Flash for transcription (faster than OpenAI Whisper)
- Qwen-plus LLM with Gemini fallback
- Function Compute 3.0 serverless architecture
- Tablestore for user data storage
- MNS for async job processing

See CLAUDE.md for detailed changelog and development notes.

## License

MIT
