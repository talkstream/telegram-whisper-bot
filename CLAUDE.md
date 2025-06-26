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
- **Video transcription support**: Extract and transcribe audio from video files and video notes

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

## Settings
Users can customize their experience through the following commands:
- `/settings` - Show current settings
- `/code_on` - Enable code tags (`<code>`) for monospace font output
- `/code_off` - Disable code tags for plain text output

Note: Inline keyboard buttons have been re-enabled in v1.3.0 for trial request management and payment selections.

## Version Control

### GitHub Repository
- **Remote URL**: https://github.com/talkstream/telegram-whisper-bot.git
- **Visibility**: Private
- **Main Branch**: main
- **Latest Tag**: v1.4.1

### Git Commands
```bash
# Check current status
git status

# View commit history
git log --oneline

# Create a new version tag
git tag -a v1.0.1 -m "Description of changes"

# Push to GitHub
git push
git push origin --tags

# Pull latest changes
git pull

# Revert to previous version
git checkout v1.0.0

# View all tags
git tag -l

# Clone repository
git clone https://github.com/talkstream/telegram-whisper-bot.git
```

### Current Version: v1.7.3
Added detection for "Продолжение следует..." phrase to properly handle audio without speech.

**Bug Fixes (v1.7.3):**
- **"Продолжение следует..." Detection**:
  - Added exact match check for this specific Whisper response
  - When detected, returns user-friendly error message
  - Message: "❌ На записи не обнаружено речи или текст не был распознан"
  - Prevents confusing AI-generated text from reaching users

**Bug Fixes (v1.7.2):**
- **Gemini Instruction Prevention**:
  - Added check for texts shorter than 10 words - return without formatting
  - Enhanced prompt to explicitly prohibit dialogue with users
  - Added explicit rule: "НИКОГДА не веди диалог с пользователем"
  - Added rule for incomplete texts: "Просто верни его без изменений"
  - Prevents Gemini from explaining what it needs to users

**Bug Fixes (v1.7.1):**
- **Migration to Google Gen AI SDK**:
  - Fixed deprecation warning for `vertexai.generative_models`
  - Migrated from deprecated Vertex AI SDK to new Google Gen AI SDK
  - Continues to use `gemini-2.5-flash` model (available in new SDK)
  - Prevents future breaking changes (deprecated API removal scheduled for June 2026)

**New Features (v1.7.0):**
- **Video Transcription Support**:
  - Support for regular video messages (`message.video`)
  - Support for round video notes (`message.video_note`)
  - Automatic audio extraction from video files using FFmpeg
  - Supports popular video formats: MP4, AVI, MOV, MKV, WebM, MPEG
  - Shows "🎥 Видео получено" notification for video files
  - Displays "🎬 Извлекаю аудиодорожку..." progress message
  - Handles videos without audio tracks gracefully with error message
  - Same 20MB file size limit applies to video files
  - Billing based on video duration (same as audio)

**Previous Features (v1.6.0):**
- **Automated Scheduled Reports**:
  - Daily reports at 9:00 AM Moscow time
  - Weekly reports every Monday at 9:00 AM Moscow time
  - Comprehensive statistics: users, usage, revenue, top users, system health
  - Manual trigger with `/report [daily|weekly]` command
  - Cloud Scheduler integration for reliable delivery
  - Configurable report times via cron.yaml

**Previous Features (v1.5.0):**
- **CSV Export Command (/export)**:
  - Export user data with full details and activity stats
  - Export transcription logs with date filtering
  - Export payment history with revenue totals
  - Flexible date range (default 30 days)
  - UTF-8 encoded CSV files for Excel compatibility
  - Automatic file cleanup after sending

**Previous Features (v1.4.0):**
- **Performance Monitoring System:**
  - MetricsService for tracking execution times and API performance
  - Stage-by-stage timing: download, conversion, transcription, formatting
  - API response time tracking for Whisper and Gemini
  - Queue statistics and wait time analysis
  - Error rate tracking and success metrics
- **New /metrics Command:**
  - View performance data for last N hours (default 24)
  - Processing stage breakdowns with percentiles
  - API performance metrics with success rates
  - Current queue status and average wait times
  - Accessible to admin only
- **User Management Dashboard:**
  - /user command for searching users by name or ID
  - Shows detailed user info: balance, trial status, join date, last activity
  - Total transcriptions and minutes processed
  - Search functionality with partial name matching
  - Comprehensive user activity tracking

**Previous Updates (v1.3.1):**
- Clarified /cost command to show data is from editorials-robot project only
- Added warning that infrastructure costs are estimates
- Added link to GCP billing console for exact costs

**v1.3.0 Features:**
- **Trial Request Improvements:**
  - Trial requests are now automatically deleted after approval/denial
  - Re-implemented inline keyboards for one-click approve/deny actions
  - Enhanced /credit command to properly handle trial approvals
  - Better UI with formatted messages and real-time status updates
  - Trial requests are cleaned up to prevent clutter
  
**v1.2.0 Features:**
- Redesigned tariff structure with progressive pricing
- Added per-minute price display for all packages
- Implemented payment notification system for owner
- Added infrastructure cost tracking in /cost command

**Tariff Structure:**
- Start: 50 minutes for 75 ⭐ (300% markup)
- Standard: 200 minutes for 270 ⭐ 
- Profi: 1000 minutes for 1150 ⭐
- MAX: 8888 minutes for 8800 ⭐ (200% markup)

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
- **v1.2.0** - New tariff system and payment notifications (June 25, 2025)
- **v1.3.0** - Optimized trial request handling and re-enabled inline keyboards (June 25, 2025)
- **v1.3.1** - Improved cost tracking clarity for editorials-robot project (June 25, 2025)
- **v1.4.0** - Performance monitoring system with metrics tracking (June 25, 2025)
- **v1.4.1** - Documentation updates and GitHub repository setup (June 25, 2025)
- **v1.5.0** - CSV export functionality for admin reports (June 25, 2025)
- **v1.6.0** - Automated daily and weekly reports with Cloud Scheduler (June 25, 2025)
- **v1.6.1** - Fixed CSV export send_document parameter error (June 25, 2025)
- **v1.7.0** - Video transcription support for video messages and video notes (June 26, 2025)
- **v1.7.1** - Migrated to Google Gen AI SDK to fix deprecation warning (June 26, 2025)
- **v1.7.2** - Fixed Gemini instruction leak to users on short transcripts (June 26, 2025)
- **v1.7.3** - Added "Продолжение следует..." detection for speechless audio (June 26, 2025)

## Summary of June 25, 2025 Work

Today was an incredibly productive day with 7 major releases and 1 patch (v1.1.0 → v1.6.1):

1. **Morning**: Complete service-oriented architecture refactor
   - Reduced codebase by 40%, improved maintainability
   - Created dedicated service modules and command handlers

2. **Afternoon**: Business improvements
   - New progressive tariff system (300% → 200% markup)
   - Payment notifications for owner
   - Trial request workflow optimization with inline keyboards

3. **Evening**: Technical enhancements
   - Performance monitoring system implementation
   - Comprehensive metrics tracking for all processing stages
   - New /metrics command for performance analysis
   - Set up private GitHub repository for version control
   - Pushed all code and version history to remote repository
   - Implemented CSV export functionality for admin reporting
   - Added automated daily/weekly reports with Cloud Scheduler

**Total Progress**: 20+ commits, 7 stable releases, ~2700 lines of well-structured code added/refactored
**GitHub Repository**: https://github.com/talkstream/telegram-whisper-bot (private)

The bot is now in excellent shape with professional architecture, comprehensive monitoring, and all business features implemented.

## Today's Major Accomplishments

### ✅ Complete Service Architecture Refactor (v1.1.0)
- Reduced main.py from 1602 to 969 lines (40% reduction)
- Created service layer: TelegramService, FirestoreService, AudioService, UtilityService, StatsService
- Implemented command handler pattern with BaseHandler and CommandRouter
- Eliminated all duplicate code paths

### ✅ Business Features Implementation (v1.2.0 - v1.3.1)
- Redesigned tariff system with progressive pricing (300% → 200% markup)
- Added real-time payment notifications for owner
- Optimized trial request handling with inline keyboards
- Fixed critical bugs and improved user experience

### ✅ Technical Excellence (v1.4.0 - v1.6.1)
- Performance monitoring system with detailed metrics
- User management dashboard with search functionality
- CSV export for all data types (users, logs, payments)
- Automated daily and weekly reports via Cloud Scheduler
- GitHub repository setup with full version history

### 📊 By The Numbers
- **Releases**: 7 major versions + 1 patch
- **Commits**: 20+
- **Code Changes**: ~2700 lines added/refactored
- **New Features**: 15+ major features
- **Bug Fixes**: 10+ critical fixes
- **Architecture**: 100% service-oriented
- **Test Coverage**: All features deployed and tested

## Development

### GitHub Collaboration
```bash
# Clone the repository
git clone https://github.com/talkstream/telegram-whisper-bot.git
cd telegram-whisper-bot

# Create a new feature branch
git checkout -b feature/new-feature

# Make changes and commit
git add .
git commit -m "feat: Add new feature"

# Push branch and create pull request
git push origin feature/new-feature
```

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

## Recent Updates (June 25, 2025)

### Today's Major Achievements:

- ✅ **Complete Service-Oriented Architecture Refactor (v1.1.0)**:
  - Extracted all services into dedicated modules (FirestoreService, AudioService, StatsService, UtilityService)
  - Implemented command handler pattern with CommandRouter
  - Reduced main.py from 1602 to 969 lines (40% reduction)
  - Improved code maintainability and testability
  
- ✅ **New Tariff System with Progressive Pricing (v1.2.0)**:
  - Redesigned packages: Start (50min), Standard (200min), Profi (1000min), MAX (8888min)
  - Progressive pricing from 300% to 200% markup
  - Added per-minute price display for transparency
  - Implemented owner payment notifications with batching
  - Enhanced /cost command with full infrastructure estimates
  
- ✅ **Trial Request Handling Optimization (v1.3.0)**:
  - Re-enabled inline keyboards for one-click approve/deny
  - Trial requests now auto-delete after processing
  - Fixed /credit command to properly handle trial approvals
  - Better formatted UI with real-time status updates
  
- ✅ **Performance Monitoring System (v1.4.0)**:
  - Created MetricsService for comprehensive performance tracking
  - Stage-by-stage execution time monitoring
  - API response time tracking with success rates
  - Queue statistics and wait time analysis
  - New /metrics admin command with configurable time periods
  - **User Management Dashboard**: /user command with search by name/ID, detailed user info, activity tracking
  
- ✅ **GitHub Repository Setup (v1.4.1)**:
  - Created private repository at https://github.com/talkstream/telegram-whisper-bot
  - Pushed complete code history with all 15 version tags
  - Updated documentation with repository information
  - Added collaboration workflow instructions

### Previous Improvements:
- ✅ Async processing via Pub/Sub
- ✅ Memory optimization (2GB → 1GB)
- ✅ Gemini 2.5-flash upgrade
- ✅ User settings with command-based interface
- ✅ Enhanced warmup performance with F2 instance
- ✅ Fixed critical deployment issues (Flask integration)
- ✅ Migrated webhook from Cloud Functions to App Engine
- ✅ Fixed /settings command HTML parsing
- ✅ Added /flush command for stuck job cleanup
- ✅ Improved duration accuracy and progress timing
- ✅ Fixed batch processing with proper message deletion
- ✅ Automatic stuck job cleanup every 30 minutes
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

## Current State Assessment

### ✅ Completed Features:
1. **Inline keyboards** - FIXED in v1.3.0, working perfectly
2. **Batch processing** - Already implemented with media_group_id support
3. **Performance monitoring** - DONE in v1.4.0 with comprehensive metrics
4. **Service architecture** - Professional structure with clean separation
5. **All core features** - Working and deployed

### 🔧 Technical Debt:
1. **Non-blocking pause** - Current 3-second pause still blocks processing
   - Would require async refactoring of progress updates
   - Low priority as current implementation works well
2. **Progress granularity** - Could add more sub-progress within stages
   - Current 5-stage progress is sufficient for users

## Next Development Priorities

### High Priority:
1. **Advanced Admin Features** ✅ (Complete):
   - ✅ User management dashboard with search/filtering
   - ✅ Export usage reports (CSV)
   - ✅ Automated daily/weekly reports
   - ✅ User activity monitoring (via /user command)

### Medium Priority:
3. **Enhanced Batch Processing**:
   - Combined result delivery option
   - Batch cancellation feature
   - Better batch progress tracking

4. **Error Recovery**:
   - Auto-retry for transient failures
   - Better error messages for users
   - Fallback options for API failures

### Low Priority (Future):
5. **Export Formats** - SRT, VTT, DOCX support (on hold)
6. **Language Support** - Russian only per requirements
7. **Non-blocking Progress** - Requires major async refactor