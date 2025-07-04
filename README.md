# 🎙️ Telegram Whisper Bot

<p align="center">
  <img src="https://img.shields.io/badge/version-1.8.2-blue.svg" alt="Version">
  <img src="https://img.shields.io/badge/python-3.11-green.svg" alt="Python">
  <img src="https://img.shields.io/badge/platform-GCP-orange.svg" alt="GCP">
  <img src="https://img.shields.io/badge/status-active-success.svg" alt="Status">
</p>

A high-performance Telegram bot that transcribes audio and video files using OpenAI Whisper and intelligently formats the text using Google Gemini AI. Built with a modern service-oriented architecture on Google Cloud Platform.

## ✨ Key Features

### 🎯 Core Functionality
- **🎙️ Audio Transcription** - Powered by OpenAI Whisper for accurate speech-to-text
- **🎥 Video Support** - Extract and transcribe audio from video messages and video notes
- **🤖 AI Formatting** - Google Gemini 2.5 Flash for intelligent text formatting
- **🌐 Russian Language** - Optimized for Russian transcription and formatting

### 💰 Monetization
- **⭐ Telegram Stars Integration** - Native payment system
- **📊 Flexible Pricing Tiers** - From starter to unlimited packages
- **🎁 Trial System** - 15-minute free trial for new users
- **💳 Balance Management** - Real-time minute tracking

### 🚀 Performance & Architecture
- **⚡ Async Processing** - Pub/Sub queue for scalable audio processing
- **📈 Performance Monitoring** - Detailed metrics for all processing stages
- **🏗️ Service-Oriented Architecture** - Clean separation of concerns
- **⏱️ Sub-second Warmup** - Optimized for instant response

### 👨‍💼 Admin Features
- **📊 Comprehensive Statistics** - Usage, revenue, and system health
- **👥 User Management** - Search, monitor, and manage users
- **📁 Data Export** - CSV exports for users, logs, and payments
- **📅 Automated Reports** - Daily and weekly scheduled reports
- **🔧 System Monitoring** - Real-time queue and performance metrics

## 🛠️ Tech Stack

<table>
<tr>
<td>

### Backend
- Python 3.11
- Flask (Web Framework)
- FFmpeg (Audio Processing)

</td>
<td>

### Cloud Services
- Google App Engine
- Cloud Functions
- Firestore Database
- Cloud Pub/Sub
- Secret Manager

</td>
<td>

### AI/ML APIs
- OpenAI Whisper
- Google Gemini 2.5 Flash
- Telegram Bot API

</td>
</tr>
</table>

## 📋 Commands

### 👤 User Commands
| Command | Description |
|---------|-------------|
| `/start` | Start the bot and register |
| `/help` | Show available commands |
| `/balance` | Check remaining minutes |
| `/trial` | Request 15-minute free trial |
| `/buy_minutes` | Purchase minutes with Telegram Stars |
| `/settings` | View current settings |
| `/code` | Toggle monospace font output |
| `/yo` | Toggle use of letter ё |

### 👨‍💼 Admin Commands
| Command | Description |
|---------|-------------|
| `/user [search]` | Search and manage users |
| `/export [type] [days]` | Export data to CSV |
| `/report [daily\|weekly]` | Generate reports |
| `/metrics [hours]` | View performance metrics |
| `/stat` | Show usage statistics |
| `/cost` | Calculate processing costs |
| `/status` | Queue status |
| `/flush` | Clean stuck jobs |

## 💎 Pricing Tiers

| Package | Minutes | Price | Per Minute |
|---------|---------|-------|------------|
| 🚀 Start | 50 | 75 ⭐ | 1.5 ⭐ |
| 📦 Standard | 200 | 270 ⭐ | 1.35 ⭐ |
| 💼 Profi | 1,000 | 1,150 ⭐ | 1.15 ⭐ |
| 🔥 MAX | 8,888 | 8,800 ⭐ | 0.99 ⭐ |

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│                 │     │                  │     │                 │
│  Telegram API   │────▶│   App Engine     │────▶│   Pub/Sub       │
│                 │     │  (Webhook)       │     │   Queue         │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                │                          │
                                ▼                          ▼
                        ┌──────────────────┐     ┌─────────────────┐
                        │                  │     │                 │
                        │   Firestore      │     │ Cloud Function  │
                        │   Database       │     │ (Audio Proc)    │
                        │                  │     │                 │
                        └──────────────────┘     └─────────────────┘
                                                          │
                                                          ▼
                                                  ┌───────────────┐
                                                  │   OpenAI &    │
                                                  │   Gemini APIs │
                                                  └───────────────┘
```

## 📁 Project Structure

```
telegram-whisper-bot/
├── 📱 main.py                    # Main webhook handler
├── 🚀 app/                       # Application modules
│   ├── initialization.py         # Service initialization
│   ├── routes.py                 # Flask routes
│   └── notifications.py          # Notification queue
├── 🔧 services/                  # Service layer
│   ├── telegram.py               # Telegram API wrapper
│   ├── firestore.py              # Database operations
│   ├── audio.py                  # Audio processing
│   ├── utility.py                # Utility functions
│   ├── stats.py                  # Statistics service
│   └── metrics.py                # Performance tracking
├── 🎮 handlers/                  # Command handlers
│   ├── command_router.py         # Command routing
│   ├── user_commands.py          # User commands
│   ├── admin_commands.py         # Admin commands
│   └── buy_commands.py           # Payment handling
├── ☁️ audio-processor-deploy/    # Cloud Function
│   ├── audio_processor.py        # Main processor
│   └── services/                 # Shared services
├── 📝 app.yaml                   # App Engine config
├── ⏰ cron.yaml                  # Scheduled tasks
└── 📋 requirements.txt           # Dependencies
```

## 🚀 Deployment

### Prerequisites
- Google Cloud Project with billing enabled
- Telegram Bot Token from @BotFather
- OpenAI API key

### Quick Deploy
```bash
# Clone repository
git clone https://github.com/talkstream/telegram-whisper-bot.git
cd telegram-whisper-bot

# Deploy main application
./deploy_main.sh

# Deploy audio processor
./deploy_audio_processor.sh

# Set up Pub/Sub (first time only)
./setup_pubsub.sh
```

### Environment Variables
- `GCP_PROJECT` - Your Google Cloud project ID
- `USE_ASYNC_PROCESSING` - Enable async processing (default: true)

### Secrets (in Secret Manager)
- `telegram-bot-token` - Bot token from @BotFather
- `openai-api-key` - OpenAI API key for Whisper

## 📈 Performance

- **⚡ Response Time**: < 1 second warmup
- **🎯 Processing Speed**: ~5-10 seconds per minute of audio
- **📊 Uptime**: 99.9%+ availability
- **🔄 Concurrent Processing**: Unlimited with Pub/Sub
- **💾 Memory Optimized**: 1GB Cloud Function limit

## 🔒 Security

- **🔐 Secret Management** - Google Secret Manager for API keys
- **🛡️ Input Validation** - File size and format restrictions
- **👤 User Authentication** - Telegram user ID based
- **📝 Audit Logging** - All transactions logged
- **🚫 Rate Limiting** - Built-in abuse prevention

## 📊 Monitoring

The bot includes comprehensive monitoring:
- Real-time performance metrics
- Processing stage breakdowns
- API response tracking
- Queue statistics
- Error rate monitoring
- Daily/weekly automated reports

## 🤝 Contributing

This is a private repository. For access or contributions, please contact the repository owner.

## 📝 License

Private repository - All rights reserved

---

<p align="center">
  Built with ❤️ using Google Cloud Platform
</p>