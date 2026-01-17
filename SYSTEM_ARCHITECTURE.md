# System Architecture & Context (v3.0)

## Overview
**Telegram Whisper Bot** is a high-performance, distributed AI transcription service running on Google Cloud Platform. It utilizes a Split-Architecture design to separate high-concurrency user interactions from resource-intensive media processing.

## 🏗 Architecture Components

### 1. Bot Interface (`whisper-bot`)
*   **Role:** The "Front Desk". Lightweight, responsive, always available.
*   **Platform:** Cloud Run (Python 3.11, Flask).
*   **Configuration:** `min_instances: 1` (Hot start).
*   **Responsibilities:**
    *   **Secure Webhook:** Receives updates from Telegram (validated via Secret Token).
    *   **User Management:** Checks/Creates users in Firestore (Cached).
    *   **Dispatch:** Publishes jobs to `audio-processing-jobs` Pub/Sub.
    *   **Feedback:** Sends immediate "Typing..." actions and status messages.

### 2. Audio Processor (`audio-processor`)
*   **Role:** The "Factory". Heavy processing, auto-scaling.
*   **Platform:** Cloud Run (Custom Docker Image: Python + FFmpeg 8.0).
*   **Configuration:** `min_instances: 0` (Scales to zero to save cost).
*   **Core Technology:**
    *   **FFmpeg 8.0:** Compiled with `libwhisper` for local, GPU/CPU optimized transcription.
    *   **Gemini 3 Flash:** Used for intelligent text formatting and punctuation.
*   **Pipeline:**
    1.  **Smart Cache Check:** Checks `file_unique_id` to skip processing for duplicates.
    2.  **Download:** Fetches media from Telegram.
    3.  **Extraction:** Extracts audio from Video/Voice/Audio files.
    4.  **Transcription:** Local FFmpeg Whisper (no external API cost).
    5.  **Formatting:** Gemini 3 Flash (Vertex AI, `us-central1`).
    6.  **Delivery:** Sends results back to user.

### 3. Data & State (Firestore)
*   `users`: Profiles, balance, settings (`use_code_tags`, `use_yo`).
*   `audio_jobs`: Lifecycle of a job (`pending` -> `processing` -> `completed`).
*   `transcription_logs`: Analytics and history.
*   `payment_logs`: Telegram Stars transaction records.

### 4. Reliability Infrastructure
*   **Pub/Sub:** Decouples Bot from Worker.
*   **Dead Letter Queue (DLQ):** Captures failed jobs (after 5 retries) for inspection.
*   **Cloud Build:** Automated CI/CD pipeline building optimized Docker images.

## 🚀 Key Features (v2.1+)
*   **Zero-Cost Transcription:** Replaced OpenAI Whisper API with local FFmpeg Whisper.
*   **Smart Caching:** Instant results for previously processed files.
*   **Instant UX:** Immediate feedback (<1s) on file upload.
*   **Format Handling:** Supports Audio, Voice Notes, Video, and Video Notes.

## 🛡 Security
*   **Secrets:** Managed via Google Secret Manager.
*   **Validation:** File constraints (20MB, 60min).
*   **Dependencies:** Pinned versions, minimal container footprint.