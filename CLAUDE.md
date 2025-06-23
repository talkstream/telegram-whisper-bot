# Telegram Whisper Bot - Project Documentation

## Overview
A Telegram bot that transcribes audio files using OpenAI Whisper and formats the text using Google Gemini AI. The bot supports async processing via Google Cloud Pub/Sub and includes a payment system using Telegram Stars.

## Architecture

### Core Components
1. **Main Bot (main.py)**: Webhook handler for Telegram updates
2. **Audio Processor (audio_processor.py)**: Async worker for audio processing via Pub/Sub
3. **Services**:
   - `TelegramService`: All Telegram API operations
   - `FirestoreService`: All database operations
   - `AudioService`: Audio processing, transcription, and formatting

### Key Features
- Async audio processing with Pub/Sub (can be toggled with `USE_ASYNC_PROCESSING`)
- Memory optimized to 1GB (from 2GB)
- User balance system with trial access
- Payment integration with Telegram Stars
- User settings menu for output customization
- FFmpeg audio normalization (MP3 128kbps, 44.1kHz, mono)

## Commands

### User Commands
- `/start` - Registration/welcome message
- `/help` - Show help information
- `/balance` - Check remaining minutes and average audio duration
- `/trial` - Request trial access (15 minutes)
- `/buy_minutes` - Purchase minutes with Telegram Stars
- `/settings` - Show current settings
- `/code_on` - Enable code tags in output
- `/code_off` - Disable code tags in output

### Admin Commands (owner only)
- `/review_trials` - Review pending trial requests
- `/credit <user_id> <minutes>` - Add minutes to user
- `/remove_user` - Remove user from system
- `/stat` - Show usage statistics
- `/cost` - Calculate processing costs

## Settings
Users can customize their experience through the following commands:
- `/settings` - Show current settings
- `/code_on` - Enable code tags (`<code>`) for monospace font output
- `/code_off` - Disable code tags for plain text output

Note: Inline keyboard buttons were removed due to compatibility issues.

## Version Control

### Git Commands
```bash
# Check current status
git status

# View commit history
git log --oneline

# Create a new version tag
git tag -a v1.0.1 -m "Description of changes"

# Revert to previous version
git checkout v1.0.0

# View all tags
git tag -l
```

### Current Version: v1.0.1
- Cleaned up codebase (removed 33% duplicate code)
- All duplicate files removed
- Better organized structure
- No functional changes from v1.0.0

### Version History
- **v1.0.0** - Initial stable release with all core features
- **v1.0.1** - Codebase cleanup and organization

## Development

### Local Testing
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export GCP_PROJECT=editorials-robot
export USE_ASYNC_PROCESSING=false  # For sync testing

# Run webhook handler
python main.py
```

### Key Points to Save in Git

Save a new version when:
- Major feature is added and tested
- Critical bug is fixed
- Before any risky changes
- After successful deployment

## Deployment

#### Main Bot (App Engine)
```bash
gcloud app deploy --project=editorials-robot
```

#### Audio Processor (Cloud Function)
```bash
cd audio-processor-deploy
gcloud functions deploy audio-processor \
  --runtime python311 \
  --trigger-topic audio-processing-jobs \
  --memory 1GB \
  --timeout 540s \
  --no-gen2 \
  --set-env-vars GCP_PROJECT=editorials-robot \
  --project editorials-robot \
  --region europe-west1
```

### Environment Variables
- `GCP_PROJECT` - Google Cloud project ID
- `USE_ASYNC_PROCESSING` - Enable/disable async processing (default: true)
- `AUDIO_PROCESSING_TOPIC` - Pub/Sub topic name (default: audio-processing-jobs)

### Secrets (in Secret Manager)
- `telegram-bot-token` - Bot token from @BotFather
- `openai-api-key` - OpenAI API key for Whisper

## Database Schema (Firestore)

### Collections
- **users**: User profiles and balances
  - `balance_minutes`: Remaining transcription minutes
  - `trial_status`: Trial access status
  - `settings`: User preferences (e.g., `use_code_tags`)
  - `micro_package_purchases`: Count of promo purchases

- **audio_jobs**: Async processing jobs
  - `status`: pending/processing/completed/failed
  - `result`: Transcription results

- **trial_requests**: Trial access requests
  - `status`: pending/approved/denied/pending_reconsideration

- **transcription_logs**: Usage tracking
- **payment_logs**: Payment history

## Testing Checklist
- [ ] Test with `USE_ASYNC_PROCESSING=false` (sync mode)
- [ ] Test with `USE_ASYNC_PROCESSING=true` (async mode)
- [ ] Test payment flow with Telegram Stars
- [ ] Test trial request flow
- [ ] Test admin commands
- [ ] Test settings menu and code tag toggle
- [ ] Monitor Pub/Sub queue performance
- [ ] Check memory usage stays under 1GB

## Common Issues & Solutions

### Memory Exceeded
- Audio processor is optimized for 1GB
- Uses mono audio and single FFmpeg thread
- Implements lazy loading and garbage collection

### FFmpeg Errors
- Ensure FFmpeg is installed in Cloud Function
- Check audio format compatibility
- Monitor timeout settings (60s for conversion)

### Async Processing Issues
- Check Pub/Sub topic exists
- Verify Cloud Function deployment
- Monitor logs in Cloud Logging

## Recent Updates
- ✅ Async processing via Pub/Sub
- ✅ Service-oriented architecture
- ✅ Memory optimization (2GB → 1GB)
- ✅ Gemini 2.5-flash upgrade
- ✅ User settings with simple command-based interface (inline buttons removed)
- ✅ Added warmup handler and cron job to keep bot responsive
- ✅ Fixed 5-second pause timing for time estimates
- ✅ Improved progress display for formatting stage
- ✅ Added detailed logging for average audio duration