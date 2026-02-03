# Whisper GPU Infrastructure (Terraform)

Cost-optimized Whisper transcription using GCP Spot VMs with T4 GPU.

## Cost Comparison

| Solution | Price/hour | vs OpenAI API |
|----------|------------|---------------|
| OpenAI Whisper API | $0.36 | baseline |
| **GCP Spot T4** | **$0.24** | **-33%** |

## Prerequisites

1. Install Terraform:
   ```bash
   brew install terraform
   ```

2. Authenticate with GCP:
   ```bash
   gcloud auth application-default login
   ```

3. Enable required APIs:
   ```bash
   gcloud services enable compute.googleapis.com
   gcloud services enable secretmanager.googleapis.com
   ```

## Deployment

```bash
cd terraform

# Initialize Terraform
terraform init

# Preview changes
terraform plan

# Apply (creates VM)
terraform apply

# View outputs
terraform output
```

## SSH Access

```bash
gcloud compute ssh whisper-processor --zone=europe-west1-b --project=editorials-robot
```

## Monitoring

```bash
# Check GPU status
gcloud compute ssh whisper-processor --command="nvidia-smi"

# View logs
gcloud compute ssh whisper-processor --command="journalctl -u whisper-processor -f"
```

## Preemption Handling

Spot VMs can be preempted with 30-second notice. The service handles this by:
1. Listening for preemption signals
2. Returning unfinished jobs to Pub/Sub queue
3. Allowing automatic retry on another instance

## Destroy

```bash
terraform destroy
```

## Architecture

```
Telegram -> App Engine -> Pub/Sub -> Spot VM (T4 GPU) -> Firestore
                                          |
                                    faster-whisper
                                    (local inference)
```

## Model

Using `dvislobokov/faster-whisper-large-v3-turbo-russian`:
- WER ~10% on Russian (comparable to OpenAI)
- Native letter (yo) support
- 6x faster than large-v3
