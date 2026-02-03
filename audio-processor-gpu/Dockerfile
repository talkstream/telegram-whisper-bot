# Dockerfile for GPU-based Whisper processing
# Uses faster-whisper with CUDA for cost-effective transcription
# Target: GCP Spot VM with T4 GPU

FROM nvidia/cuda:12.1-runtime-ubuntu22.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /opt/whisper-processor

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Pre-download the Whisper model to bake into image
# This reduces cold start time significantly
RUN python3 -c "\
from faster_whisper import WhisperModel; \
import os; \
os.makedirs('/root/.cache/huggingface/hub', exist_ok=True); \
WhisperModel('dvislobokov/faster-whisper-large-v3-turbo-russian', device='cpu', compute_type='int8')"

# Copy application code
COPY . .

# Health check endpoint
EXPOSE 8080

# Run the processor
CMD ["python3", "main.py"]
