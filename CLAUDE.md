# Telegram Whisper Bot - Project Documentation

## Overview
A Telegram bot that transcribes audio files using OpenAI Whisper and formats the text using Google Gemini AI. The bot supports async processing via Google Cloud Pub/Sub and includes a payment system using Telegram Stars.

## Architecture

### Core Components
1. **Main Bot (main.py)**: Webhook handler for Telegram updates
2. **Audio Processor (audio_processor.py)**: Async worker for audio processing via Pub/Sub
3. **Services** (services/):
   - `TelegramService`: All Telegram API operations
   - `FirestoreService`: All database operations
   - `AudioService`: Audio processing, transcription, and formatting
   - `UtilityService`: Utility functions (formatting, text processing)
   - `StatsService`: Statistics and analytics operations
4. **Handlers** (handlers/):
   - `CommandRouter`: Routes commands to appropriate handlers
   - `user_commands.py`: User command handlers (help, balance, settings, etc.)
   - `admin_commands.py`: Admin command handlers (status, stats, credit, etc.)

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
- `/status` - Show current queue status
- `/batch` - Show batch queue status
- `/flush` - Clean stuck jobs (>1 hour old)

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

### Current Version: v1.1.0
This is a major release marking the completion of the service-oriented architecture refactoring.

**Key Achievements:**
- Complete service layer architecture implementation
- Command handler system with clean separation of concerns
- Reduced main.py from 1602 to 969 lines (39.5% reduction)
- Removed all legacy code paths
- Improved maintainability and scalability
- Successfully deployed and tested in production

### Version History
- **v1.0.0** - Initial stable release with all core features
- **v1.0.1** - Codebase cleanup and organization
- **v1.0.2** - Performance improvements and warmup optimization
- **v1.0.3** - Critical fixes: Flask integration and services folder
- **v1.0.4** - Fixed /settings command HTML parsing error
- **v1.0.5** - Duration accuracy improvements and progress timing optimization
- **v1.0.6** - Added /help command with full command list
- **v1.0.7** - Multiple critical fixes (June 24, 2025)
- **v1.0.8** - Improved UX and automatic cleanup (June 25, 2025)
- **v1.0.9** - Major refactoring with service layer and command handlers (June 25, 2025)
- **v1.1.0** - Stable release of service-oriented architecture (June 25, 2025)

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

#### Update Telegram Webhook (if needed)
```bash
# Get bot token
BOT_TOKEN=$(gcloud secrets versions access latest --secret="telegram-bot-token" --project=editorials-robot)

# Set webhook to App Engine
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://editorials-robot.ew.r.appspot.com/"}'
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
- [x] Test with `USE_ASYNC_PROCESSING=false` (sync mode)
- [x] Test with `USE_ASYNC_PROCESSING=true` (async mode) 
- [x] Test payment flow with Telegram Stars
- [x] Test trial request flow
- [x] Test admin commands
- [x] Test settings menu and code tag toggle - FIXED June 24
- [x] Monitor Pub/Sub queue performance
- [x] Check memory usage stays under 1GB

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
- ✅ Codebase cleanup - removed 33% duplicate code (v1.0.1)
- ✅ Git version control implemented
- ✅ Enhanced warmup performance (June 24, 2025):
  - Upgraded to F2 instance class for better performance
  - Added proper health checks (readiness and liveness)
  - Improved warmup handler with Firestore connection preloading
  - More aggressive cron jobs (warmup every 3min, health check every 2min)
  - Added Gunicorn workers and threads for better concurrency
- ✅ Fixed critical deployment issues (June 24, 2025):
  - Added Flask for WSGI compatibility with App Engine
  - Fixed missing services folder in root directory
  - All endpoints now working correctly
- ✅ Migrated webhook from Cloud Functions to App Engine (June 24, 2025):
  - Updated Telegram webhook URL to App Engine endpoint
  - Deleted old Cloud Function (was causing 16-second delays)
  - Bot now responds in <1 second with proper warmup
- ✅ Fixed /settings command (June 24, 2025):
  - Escaped HTML special characters (&lt; and &gt;)
  - Fixed "can't parse entities" error that persisted for months
  - All bot commands now working correctly
- ✅ Added /flush command for cleaning stuck jobs (December 2024):
  - Admin command to clean up jobs stuck in pending/processing state
  - Automatically removes jobs older than 1 hour
  - Fixed issue where user_name wasn't stored in Firestore for jobs
- ✅ Improved duration accuracy and timing (June 24, 2025):
  - Added dual duration tracking (Telegram metadata + FFmpeg actual)
  - Fixed average duration calculation using FFmpeg data
  - Reduced pause from 5 to 3 seconds (saves 2 seconds per audio)
  - Enhanced metadata collection for better diagnostics
  - Deployed as v1.0.5
- ✅ Fixed batch processing issues (June 24, 2025 - v1.0.7):
  - Batch file confirmation messages now properly deleted after processing
  - Fixed queue counter logic
  - Added proper Russian text declensions
  - Fixed critical parse_mode error
  - Added Gemini retry logic
- ✅ Added automatic stuck job cleanup (June 25, 2025):
  - Added /flush admin command for manual cleanup
  - Automatic cleanup runs every 30 minutes via cron
  - Detailed logging of stuck jobs before cleanup
  - Admin notification if 5+ jobs are stuck
  - Fixed missing user_name in job documents
- ✅ Major code refactoring (June 25, 2025):
  - Created service layer architecture:
    - UtilityService: Format functions (duration, size, etc.)
    - StatsService: Statistics and analytics functions
  - Implemented command handler system:
    - BaseHandler abstract class for all commands
    - Separate handler classes for each command (8 user, 7 admin)
    - CommandRouter for routing commands to handlers
  - Removed all legacy database code paths
  - Reduced main.py from 1602 to 969 lines (39.5% reduction)
  - Better code organization and maintainability

## Known Issues to Address Next Time
1. **Inline keyboards investigation** - Could try implementing them again now that HTML parsing is fixed
2. **Non-blocking pause** - Current 3-second pause still blocks processing (requires async refactoring)
3. **Progress granularity** - Could add more sub-progress updates within each stage

## Next Development Priorities
1. **Fix inline keyboards** - Investigate Telegram API compatibility issues
2. **Add more user settings** - Language preferences, notification settings
3. **Implement batch processing** - Allow multiple audio files at once
4. **Add export formats** - SRT, VTT, DOCX support
5. **Performance monitoring** - Add metrics tracking and dashboards