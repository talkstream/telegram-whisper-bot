# GPU Audio Processor

Cost-optimized audio transcription using faster-whisper with NVIDIA T4 GPU.

## Cost Comparison

| Backend | Cost | Notes |
|---------|------|-------|
| OpenAI Whisper API | $0.006/min audio | Pay per usage |
| **GCP Spot T4** | **$0.24/hour** | **33% cheaper** at scale |

## Model

Uses `dvislobokov/faster-whisper-large-v3-turbo-russian`:
- WER ~10% on Russian (comparable to OpenAI)
- Native letter (yo) support
- 6x faster than whisper large-v3
- CUDA optimized

## Architecture

```
Telegram -> App Engine -> Pub/Sub -> GPU VM (T4) -> Firestore
                                          |
                                    faster-whisper
                                    (local inference)
```

## Deployment

### Prerequisites

1. Deploy Terraform infrastructure first:
   ```bash
   cd ../terraform
   terraform init
   terraform apply
   ```

2. Build and push Docker image:
   ```bash
   docker build -t gcr.io/editorials-robot/whisper-gpu:latest .
   docker push gcr.io/editorials-robot/whisper-gpu:latest
   ```

3. SSH to VM and start service:
   ```bash
   gcloud compute ssh whisper-processor --zone=europe-west1-b
   docker pull gcr.io/editorials-robot/whisper-gpu:latest
   docker run -d --gpus all -p 8080:8080 gcr.io/editorials-robot/whisper-gpu:latest
   ```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GCP_PROJECT` | editorials-robot | GCP Project ID |
| `PUBSUB_SUBSCRIPTION` | audio-processing-jobs-sub | Pub/Sub subscription |
| `WHISPER_MODEL` | dvislobokov/faster-whisper-large-v3-turbo-russian | Model to use |
| `LOG_LEVEL` | INFO | Logging level |

## Preemption Handling

GCP Spot VMs can be interrupted with 30-second notice. The processor handles this:

1. Listens for SIGTERM signal
2. Sets shutdown flag
3. Current job is returned to Pub/Sub (nack)
4. Job is automatically redelivered to another worker

## Health Check

```bash
curl http://localhost:8080/health
```

Response:
```json
{
  "status": "healthy",
  "model_loaded": true,
  "current_job": null
}
```

## Monitoring

Check GPU status:
```bash
nvidia-smi
```

View logs:
```bash
docker logs -f whisper-gpu
```

## Files

- `main.py` - Main processor with Pub/Sub subscriber and Flask health server
- `Dockerfile` - Container with CUDA, faster-whisper, and model pre-loaded
- `requirements.txt` - Python dependencies
