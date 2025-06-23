# Pub/Sub Async Audio Processing Deployment Guide

This guide helps you deploy the async audio processing infrastructure using Google Cloud Pub/Sub.

## Prerequisites

- Google Cloud SDK (`gcloud`) installed and configured
- Project ID: `editorials-robot` (or your custom project)
- Appropriate permissions for Pub/Sub, Cloud Functions, and Firestore

## Step 1: Create Pub/Sub Topic

```bash
# Create the topic for audio processing jobs
gcloud pubsub topics create audio-processing-jobs \
    --project=editorials-robot

# Create a subscription for the topic
gcloud pubsub subscriptions create audio-processing-jobs-sub \
    --topic=audio-processing-jobs \
    --ack-deadline=600 \
    --max-retry-delay=600 \
    --min-retry-delay=10 \
    --project=editorials-robot
```

## Step 2: Deploy Audio Processor Function

```bash
# Deploy the audio processor as a Cloud Function
gcloud functions deploy audio-processor \
    --runtime=python39 \
    --trigger-topic=audio-processing-jobs \
    --entry-point=handle_pubsub_message \
    --source=. \
    --timeout=540s \
    --memory=512MB \
    --set-env-vars="GCP_PROJECT=editorials-robot" \
    --region=europe-west1 \
    --project=editorials-robot
```

## Step 3: Update Main Webhook Function

Update your existing webhook function with the new main.py:

```bash
# Deploy the updated webhook handler
gcloud functions deploy telegram-webhook \
    --runtime=python39 \
    --trigger-http \
    --allow-unauthenticated \
    --entry-point=handle_telegram_webhook \
    --source=. \
    --timeout=60s \
    --memory=256MB \
    --set-env-vars="GCP_PROJECT=editorials-robot,USE_ASYNC_PROCESSING=true,AUDIO_PROCESSING_TOPIC=audio-processing-jobs" \
    --region=europe-west1 \
    --project=editorials-robot
```

## Step 4: Create Dead Letter Topic (Optional but Recommended)

```bash
# Create dead letter topic for failed messages
gcloud pubsub topics create audio-processing-jobs-dlq \
    --project=editorials-robot

# Update subscription with dead letter policy
gcloud pubsub subscriptions update audio-processing-jobs-sub \
    --dead-letter-topic=audio-processing-jobs-dlq \
    --max-delivery-attempts=5 \
    --project=editorials-robot
```

## Step 5: Set up Monitoring

```bash
# Create alert for failed jobs
gcloud alpha monitoring policies create \
    --notification-channels=YOUR_CHANNEL_ID \
    --display-name="Audio Processing Failures" \
    --condition-display-name="High failure rate" \
    --condition-metric-type="cloudfunctions.googleapis.com/function/execution_count" \
    --condition-filter='resource.type="cloud_function" AND resource.labels.function_name="audio-processor" AND metric.labels.status!="ok"' \
    --condition-comparison=COMPARISON_GT \
    --condition-threshold-value=10 \
    --condition-duration=300s
```

## Testing

### Test with Async Processing Enabled (Default)

Send an audio file to your bot. You should see:
1. "⏳ Файл получен. Обрабатываю..." message
2. Progress updates as the file is processed
3. Final result with the transcription

### Test with Sync Processing (Fallback)

Set `USE_ASYNC_PROCESSING=false` and redeploy the webhook function to test synchronous processing.

## Rollback Plan

If issues arise, you can quickly rollback to synchronous processing:

```bash
# Disable async processing
gcloud functions deploy telegram-webhook \
    --update-env-vars="USE_ASYNC_PROCESSING=false" \
    --region=europe-west1 \
    --project=editorials-robot
```

## Monitoring Queries

### Check processing latency
```sql
SELECT 
  timestamp,
  jsonPayload.job_id,
  jsonPayload.progress,
  jsonPayload.status
FROM `editorials-robot.audio_jobs`
WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
ORDER BY timestamp DESC
```

### Monitor error rates
```sql
SELECT 
  COUNT(*) as error_count,
  jsonPayload.error
FROM `editorials-robot.transcription_logs`
WHERE status != 'success'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
GROUP BY jsonPayload.error
```

## Notes

- The audio processor function needs FFmpeg installed. It's included in the Python runtime by default.
- Ensure all necessary secrets are accessible to both functions
- Monitor your Pub/Sub message acknowledgment deadline - 600s should be enough for most audio files
- Consider implementing batch processing for multiple files from the same user