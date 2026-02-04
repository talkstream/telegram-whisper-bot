# Telegram Whisper Bot - Full Documentation Archive

> **Note:** This is the archived full version of CLAUDE.md as of 2026-02-04 (v3.0.1).
> For current documentation, see the compact [CLAUDE.md](/CLAUDE.md).

---

## Overview
A Telegram bot that transcribes audio files using Alibaba Qwen3-ASR and formats the text using Qwen LLM (with Gemini fallback). The bot supports async processing via Alibaba MNS and includes a payment system using Telegram Stars.

## KRITICAL CONFIGURATION (v3.0.1)

**Full docs: [docs/ALIBABA_CRITICAL_CONFIG.md](../ALIBABA_CRITICAL_CONFIG.md)**

### ASR Model
| Parameter | Value |
|-----------|-------|
| **Model** | `qwen3-asr-flash` |
| **Endpoint** | `https://dashscope-intl.aliyuncs.com/api/v1` |
| **Protocol** | REST API via `dashscope.MultiModalConversation.call()` |
| **SDK** | `dashscope>=1.20.0` |

### LLM Model
| Parameter | Value |
|-----------|-------|
| **Model** | `qwen-plus` |
| **Endpoint** | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| **Fallback** | Gemini 2.5 Flash (`gemini-2.5-flash`) |

### DO NOT USE
- ~~paraformer-realtime-v2~~ (deprecated 2024)
- ~~paraformer-v1~~ (deprecated)
- ~~qwen3-asr-flash-realtime WebSocket~~ (complex in serverless, use REST)
- ~~dashscope.aliyuncs.com~~ (Beijing, use `-intl` for international)

## Architecture

### Alibaba Cloud Components (v3.0.1)
1. **Webhook Handler (alibaba/webhook-handler/)**: FastAPI app on Function Compute 3.0
2. **Audio Processor (alibaba/audio-processor/)**: MNS-triggered worker for audio processing
3. **Services** (alibaba/*/services/):
   - `TelegramService`: All Telegram API operations
   - `TablestoreService`: User data storage (OTS)
   - `AudioService`: Audio processing, transcription (Qwen3-ASR), formatting (Qwen-plus)
   - `MNSService`: Message queue operations
   - `UtilityService`: Utility functions (formatting, text processing)
4. **GCP Legacy** (handlers/, app/): Reference code for future Alibaba feature parity

### Key Features
- Async audio processing with MNS (Message Service)
- Memory optimized to 512MB on Function Compute
- User balance system with trial access
- Payment integration with Telegram Stars
- User settings menu for output customization
- FFmpeg audio normalization (MP3 128kbps, 44.1kHz, mono)
- **Video transcription support**: Extract and transcribe audio from video files and video notes

## Commands

### User Commands
- `/start` - Registration/welcome message
- `/help` - Show help information
- `/balance` - Check remaining minutes and average audio duration
- `/trial` - Request trial access (15 minutes)
- `/buy_minutes` - Purchase minutes with Telegram Stars
- `/settings` - Show current settings
- `/code` - Toggle code tags in output (monospace font)
- `/yo` - Toggle use of letter yo (default: enabled)

### Admin Commands (owner only)
- `/user [search]` - Search and manage users (by name or ID)
- `/export [users|logs|payments] [days]` - Export data to CSV (default: users, 30 days)
- `/report [daily|weekly]` - Manually trigger scheduled reports
- `/review_trials` - Review pending trial requests
- `/credit <user_id> <minutes>` - Add minutes to user
- `/remove_user` - Remove user from system
- `/stat` - Show usage statistics
- `/cost` - Calculate processing costs
- `/status` - Show current queue status
- `/batch` - Show batch queue status
- `/flush` - Clean stuck jobs (>1 hour old)
- `/metrics [hours]` - View performance metrics (default 24h)

## Database Schema (Tablestore)

### Tables
- **users**: User profiles and balances
  - `balance_minutes`: Remaining transcription minutes
  - `trial_status`: Trial access status
  - `settings`: User preferences (e.g., `use_code_tags`)

- **audio_jobs**: Async processing jobs
  - `status`: pending/processing/completed/failed
  - `result`: Transcription results

- **trial_requests**: Trial access requests
  - `status`: pending/approved/denied/pending_reconsideration

- **transcription_logs**: Usage tracking
- **payment_logs**: Payment history

## Tariff Structure
- Start: 50 minutes for 75 stars (300% markup)
- Standard: 200 minutes for 270 stars
- Profi: 1000 minutes for 1150 stars
- MAX: 8888 minutes for 8800 stars (200% markup)

## Technical Notes

### Google AI SDK Migration (for Gemini fallback)
Use `google-genai` SDK, NOT deprecated `vertexai` imports:

```python
import google.genai as genai
client = genai.Client(vertexai=True, project=PROJECT_ID, location='europe-west1')
response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
```

### Architecture Guidelines
1. **Service Layer**: All business logic should be in services
2. **No Direct API Calls**: Never call APIs directly in handlers
3. **Dependency Injection**: Pass API keys, not client objects
4. **Memory Optimization**: Use lazy imports for heavy libraries

---

*For version history, see [VERSION_HISTORY.md](VERSION_HISTORY.md)*
*For migration details, see [MIGRATION_COMPLETED.md](MIGRATION_COMPLETED.md)*
