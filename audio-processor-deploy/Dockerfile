# Use our custom base image with pre-compiled FFmpeg 8.0
FROM gcr.io/editorials-robot/audio-processor-base:v1

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Cloud Functions entry point
CMD ["functions-framework", "--target=handle_pubsub_message", "--signature-type=event", "--port=8080"]