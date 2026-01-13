# Base image - Python 3.11 на Debian
FROM python:3.11-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    cmake \
    build-essential \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install FFmpeg 8.0 from source с Whisper support
WORKDIR /tmp/ffmpeg-build

# Clone and build whisper.cpp
RUN git clone https://github.com/ggerganov/whisper.cpp.git && \
    cd whisper.cpp && \
    cmake -B build && \
    cmake --build build --config Release && \
    make -C build && \
    cp build/libwhisper.so /usr/local/lib/ && \
    cp ggml.h /usr/local/include/ && \
    cp whisper.h /usr/local/include/ && \
    ldconfig

# Download Whisper base model (best balance: quality/speed/size)
RUN cd whisper.cpp && \
    bash ./models/download-ggml-model.sh base && \
    mkdir -p /opt/whisper/models && \
    cp models/ggml-base.bin /opt/whisper/models/

# Build FFmpeg 8.0 with Whisper filter enabled
RUN git clone --depth 1 --branch release/8.0 https://github.com/FFmpeg/FFmpeg.git && \
    cd FFmpeg && \
    ./configure \
        --enable-gpl \
        --enable-version3 \
        --enable-nonfree \
        --enable-whisper \
        --extra-cflags="-I/usr/local/include" \
        --extra-ldflags="-L/usr/local/lib" && \
    make -j$(nproc) && \
    make install && \
    ldconfig

# Verify FFmpeg installation
RUN ffmpeg -version && ffmpeg -filters | grep whisper

# Set working directory for app
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables
ENV WHISPER_MODEL_PATH=/opt/whisper/models/ggml-base.bin
ENV PYTHONUNBUFFERED=1

# Cloud Functions entry point
CMD ["functions-framework", "--target=handle_pubsub_message", "--signature-type=event"]
