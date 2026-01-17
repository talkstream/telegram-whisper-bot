# Telegram Whisper Bot

🤖 **Professional AI Transcription Bot for Telegram**

A highly optimized, cost-effective, and scalable bot that converts Voice, Audio, and Video messages into perfectly formatted text using local FFmpeg Whisper technology and Google Gemini 3 Flash.

## 🌟 Key Features

*   **🎙 Universal Transcription:** Handles Voice Notes, Audio files (MP3/WAV/etc), and **Video** files.
*   **⚡ High Performance:**
    *   **Local Processing:** Uses FFmpeg 8.0 with embedded Whisper for fast, free transcription.
    *   **Smart Caching:** Instant responses for duplicate files.
    *   **Hot-Start UI:** Immediate feedback (<1s) upon file receipt.
*   **🧠 AI Formatting:** Uses **Gemini 3 Flash** to add punctuation, fix grammar, and format paragraphs.
*   **💰 Cost Efficient:**
    *   **$0 Transcription Costs** (Removed OpenAI API).
    *   **Serverless:** Scales to zero when not in use.
*   **🔒 Secure:** Privacy-first design, secure webhook handling.

## 🛠 Tech Stack

*   **Language:** Python 3.11
*   **Cloud:** Google Cloud Platform (Cloud Run, Firestore, Pub/Sub, Build)
*   **Core Libs:** `flask`, `google-cloud-*`, `ffmpeg` (8.0 custom build)
*   **AI Models:**
    *   Transcription: `whisper-1` (via FFmpeg/whisper.cpp)
    *   Formatting: `gemini-3-flash-preview` (Vertex AI)

## 🚀 Deployment

### Prerequisites
*   Google Cloud Project with Billing enabled.
*   `gcloud` CLI installed.
*   Telegram Bot Token.

### 1. Setup Environment
```bash
# Set project ID
export PROJECT_ID="your-project-id"
gcloud config set project $PROJECT_ID

# Enable APIs
gcloud services enable run.googleapis.com pubsub.googleapis.com firestore.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com
```

### 2. Infrastructure Setup
Run the setup script to create Pub/Sub topics and secrets:
```bash
./setup_pubsub.sh
```

### 3. Build & Deploy
The project uses Cloud Build for a streamlined pipeline.

**Step 1: Build Base Image (One-time)**
Builds the heavy image containing FFmpeg 8.0 and Whisper models.
```bash
gcloud builds submit --config cloudbuild.base.yaml .
```

**Step 2: Deploy Application**
Deploys both the Bot and the Worker (Audio Processor).
```bash
# Deploy Worker
gcloud builds submit --config cloudbuild.app.yaml .

# Deploy Bot
./deploy_bot_cloudrun.sh
```

## 📂 Project Structure

*   `main.py` - The Bot Interface (Webhooks).
*   `audio-processor-deploy/` - The Worker Service (Docker + Logic).
*   `services/` - Shared business logic (Audio, Telegram, Firestore).
*   `handlers/` - Bot command handlers.

## 🤝 Contributing

See `GEMINI-evolution-plan.md` for the roadmap.
1.  Fork the repo.
2.  Create a feature branch.
3.  Commit your changes.
4.  Push to the branch.
5.  Create a Pull Request.

## 📄 License

MIT
