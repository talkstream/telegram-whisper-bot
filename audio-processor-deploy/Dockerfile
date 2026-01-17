# Use custom base image with FFmpeg 8.0 and Whisper
FROM gcr.io/editorials-robot/audio-processor-base:v1

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PORT 8080

# Install runtime dependencies (if any extra are needed, otherwise base has most)
# Note: ffmpeg is already in base
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared package and install it
# Expects build context to include shared/ (handled by deploy script)
COPY shared /app/shared
RUN pip install --no-cache-dir /app/shared

# Copy application code
COPY . .

# Cloud Functions entry point
CMD ["functions-framework", "--target=handle_pubsub_message", "--signature-type=event", "--port=8080"]
