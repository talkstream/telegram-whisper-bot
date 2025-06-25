# Telegram Whisper Bot

A Telegram bot that transcribes audio files using OpenAI Whisper and formats the text using Google Gemini AI.

## Features

- 🎙️ Audio transcription using OpenAI Whisper
- 📝 Text formatting with Google Gemini AI
- 💰 Payment system using Telegram Stars
- 🚀 Async processing via Google Cloud Pub/Sub
- 📊 Performance monitoring and metrics
- 👥 User management dashboard
- 🔧 Admin tools and statistics

## Tech Stack

- **Language**: Python 3.11
- **Cloud Platform**: Google Cloud Platform (App Engine, Cloud Functions, Firestore, Pub/Sub)
- **APIs**: OpenAI Whisper, Google Gemini 2.5 Flash, Telegram Bot API
- **Architecture**: Service-oriented with command handlers

## Project Structure

```
.
├── main.py                 # Main webhook handler
├── app.yaml               # App Engine configuration
├── requirements.txt       # Python dependencies
├── services/              # Service layer
│   ├── telegram.py       # Telegram API operations
│   ├── firestore.py      # Database operations
│   ├── audio.py          # Audio processing
│   ├── utility.py        # Utility functions
│   ├── stats.py          # Statistics service
│   └── metrics.py        # Performance metrics
├── handlers/              # Command handlers
│   ├── command_router.py # Routes commands
│   ├── user_commands.py  # User commands
│   ├── admin_commands.py # Admin commands
│   └── buy_commands.py   # Purchase commands
└── audio-processor-deploy/ # Cloud Function for audio processing
```

## Documentation

See [CLAUDE.md](CLAUDE.md) for detailed project documentation, deployment instructions, and development guidelines.

## Version

Current version: v1.4.1

## License

Private repository - All rights reserved