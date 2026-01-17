FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PORT 8080

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared package and install it
COPY shared /app/shared
RUN pip install --no-cache-dir /app/shared

# Copy project files
COPY . .
RUN rm -rf /app/services

# Expose port
EXPOSE 8080

# Start application
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-b", ":8080", "main_fastapi:app", "--workers", "2", "--timeout", "120"]

