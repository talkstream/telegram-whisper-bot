# System Architecture & Context

## Overview
**Telegram Whisper Bot** is a distributed application on Google Cloud Platform (GCP) that transcribes and formats voice notes, audio files, and video files from Telegram. It uses a split architecture (Bot Interface + Worker) to handle long-running processing tasks asynchronously.

## Architecture Components

### 1. Bot Interface (`whisper-bot`)
*   **Platform:** Cloud Run (Python 3.11, Flask).
*   **Entry Point:** `main.py`.
*   **Responsibilities:**
    *   Handles Telegram Webhooks (`/webhook`).
    *   Parses user commands (`/start`, `/balance`, etc.) via `handlers/command_router.py`.
    *   Validates incoming files (size, type).
    *   **State Management:** Checks user balance and batch state in Firestore.
    *   **Dispatch:** Publishes job metadata to Pub/Sub topic `audio-processing-jobs`.
    *   *Legacy/Technical Debt:* Currently handles Video->Audio conversion locally using FFmpeg before dispatching. This is a bottleneck.

### 2. Audio Processor (`audio-processor`)
*   **Platform:** Cloud Run (Custom Docker Image with FFmpeg 8.0).
*   **Entry Point:** `audio-processor-deploy/main.py` (Flask/Gunicorn) listening for Pub/Sub Push events.
*   **Trigger:** Pub/Sub Push Subscription `audio-processor-push-sub` -> POST `/`.
*   **Core Logic:** `handle_pubsub_message` in `audio_processor.py`.
*   **Pipeline:**
    1.  **Download:** Fetches file from Telegram API using `file_id`.
    2.  **Convert:** FFmpeg `input -> mp3`.
    3.  **Transcribe:** FFmpeg 8.0 Native Whisper Filter (`ggml-base.bin`).
    4.  **Format:** Gemini 3 Flash (Vertex AI) for punctuation/paragraphs.
    5.  **Deliver:** Sends result back to user via Telegram (`editMessageText` or `sendDocument`).
    6.  **Billing:** Deducts minutes from User Balance in Firestore.

### 3. Data Storage (Firestore)
*   **Collections:**
    *   `users`: User profiles, balance, settings (`settings.use_code_tags`, etc.).
    *   `audio_jobs`: State of each processing job (`pending` -> `processing` -> `completed`/`failed`).
    *   `transcription_logs`: Historical record of attempts for stats.
    *   `user_states`: Temporary state for batch file uploads.
    *   `payment_logs`: Transaction records (Telegram Stars).

### 4. Infrastructure
*   **Pub/Sub:** Topic `audio-processing-jobs`. Decouples ingestion from processing.
*   **Secret Manager:** Stores `telegram-bot-token`.
*   **Container Registry:** `gcr.io/editorials-robot/audio-processor`.

## Key Configuration
*   **Limits:** Max 20MB file size (Telegram Bot API limit for downloading). Max 60 min duration.
*   **Models:**
    *   Whisper: `ggml-base.bin` (local in container).
    *   LLM: `gemini-3-flash-preview` (Vertex AI).
*   **Region:** `europe-west1`.

## Known Issues (Post-v2.0 Analysis)
1.  **DevOps Efficiency:** Audio Processor build takes ~10-15 mins due to compiling FFmpeg from source every deploy.
2.  **Error Handling:** Worker returns `200 OK` on all errors to prevent infinite loops, masking infrastructure failures.
3.  **Video Processing:** `main.py` performs local FFmpeg conversion, causing timeouts/memory issues on the Bot service.
4.  **Code Duplication:** `services/` directory is manually synced between root and `audio-processor-deploy/`.
5.  **Parsing Fragility:** Regex parsing of FFmpeg stderr is brittle.

## Future Context (Gemini CLI 3.*)
*   **Goal:** Move to a stable, observable, and cost-efficient architecture.
*   **Refactoring:** Optimize Docker, centralize FFmpeg in Worker, improve Error Handling (DLQ), and automate code sync.
