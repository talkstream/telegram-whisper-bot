# Telegram Whisper Bot

**Professional AI Transcription Bot for Telegram**

A production-ready bot that converts Voice, Audio, and Video messages into perfectly formatted Russian text using OpenAI Whisper API and Google Gemini 2.5 Flash.

## Key Features

- **Universal Transcription:** Handles Voice Notes, Audio files (MP3/WAV/OGG/etc), and Video files (MP4/MOV/etc)
- **High Performance:**
  - Async processing via Google Cloud Pub/Sub
  - Smart Cold Start UX - instant feedback even during service warmup
  - Sub-second response times with warm instances
- **AI Formatting:** Uses Gemini 2.5-flash to add punctuation, fix grammar, and format paragraphs
- **User Settings:**
  - `/code` - Toggle monospace font output
  - `/yo` - Toggle letter "ё" usage
- **Payment System:** Telegram Stars integration with progressive pricing
- **Admin Tools:** User management, metrics, CSV export, automated reports

## Tech Stack

- **Language:** Python 3.11
- **Cloud:** Google Cloud Platform (App Engine, Cloud Functions, Firestore, Pub/Sub)
- **Framework:** FastAPI + Gunicorn
- **AI Models:**
  - Transcription: OpenAI `whisper-1` API ($0.006/min)
  - Formatting: Google `gemini-2.5-flash` (Vertex AI)

## Architecture

```
Telegram API
    │ Webhook
    ▼
App Engine (F2, FastAPI)
  ├─ min_instances: 0 (scale to zero)
  ├─ Smart Cold Start UX
  └─ Warmup: every 10 min
    │ Pub/Sub
    ▼
Cloud Function (Audio Processor)
  ├─ OpenAI Whisper API
  ├─ Gemini 2.5-flash
  └─ Memory: 1GB, Timeout: 9 min
    │
    ▼
Firestore + Secret Manager
```

## Deployment

### Prerequisites
- Google Cloud Project with billing enabled
- `gcloud` CLI installed and authenticated
- Telegram Bot Token from @BotFather

### Deploy

```bash
# Main bot (App Engine)
gcloud app deploy --project=editorials-robot

# Audio processor (Cloud Function)
cd audio-processor-deploy
gcloud functions deploy audio-processor \
  --runtime python311 \
  --trigger-topic audio-processing-jobs \
  --memory 1GB \
  --timeout 540s \
  --project editorials-robot \
  --region europe-west1

# Deploy cron jobs
gcloud app deploy cron.yaml --project=editorials-robot
```

## Project Structure

```
├── main.py                    # FastAPI application entry
├── app/                       # Application modules
│   ├── initialization.py      # Service container
│   ├── routes_fastapi.py      # Route handlers
│   ├── logic.py               # Business logic
│   └── notifications.py       # Notification service
├── handlers/                  # Command handlers
├── shared/                    # Shared services package
│   └── telegram_bot_shared/
│       └── services/
├── audio-processor-deploy/    # Cloud Function worker
└── requirements.txt
```

## Version History

Current version: **v1.9.0**

See CLAUDE.md for detailed changelog and development notes.

## License

MIT
