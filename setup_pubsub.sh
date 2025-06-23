#!/bin/bash

# Setup script for Pub/Sub infrastructure
# Run this before enabling async processing

PROJECT_ID="editorials-robot"
REGION="europe-west1"

echo "Setting up Pub/Sub infrastructure for project: $PROJECT_ID"

# Create the Pub/Sub topic
echo "Creating Pub/Sub topic..."
gcloud pubsub topics create audio-processing-jobs \
    --project=$PROJECT_ID

if [ $? -eq 0 ]; then
    echo "✅ Topic created successfully"
else
    echo "⚠️  Topic might already exist or creation failed"
fi

# Create the subscription
echo "Creating Pub/Sub subscription..."
gcloud pubsub subscriptions create audio-processing-jobs-sub \
    --topic=audio-processing-jobs \
    --ack-deadline=600 \
    --max-retry-delay=600 \
    --min-retry-delay=10 \
    --project=$PROJECT_ID

if [ $? -eq 0 ]; then
    echo "✅ Subscription created successfully"
else
    echo "⚠️  Subscription might already exist or creation failed"
fi

# Create dead letter topic (optional but recommended)
echo "Creating dead letter topic..."
gcloud pubsub topics create audio-processing-jobs-dlq \
    --project=$PROJECT_ID

if [ $? -eq 0 ]; then
    echo "✅ Dead letter topic created successfully"
    
    # Update subscription with dead letter policy
    echo "Updating subscription with dead letter policy..."
    gcloud pubsub subscriptions update audio-processing-jobs-sub \
        --dead-letter-topic=audio-processing-jobs-dlq \
        --max-delivery-attempts=5 \
        --project=$PROJECT_ID
        
    if [ $? -eq 0 ]; then
        echo "✅ Dead letter policy configured"
    fi
fi

echo ""
echo "Pub/Sub setup complete! Now you can deploy the audio processor function:"
echo ""
echo "gcloud functions deploy audio-processor \\"
echo "    --runtime=python311 \\"
echo "    --trigger-topic=audio-processing-jobs \\"
echo "    --entry-point=handle_pubsub_message \\"
echo "    --source=. \\"
echo "    --timeout=540s \\"
echo "    --memory=512MB \\"
echo "    --set-env-vars=\"GCP_PROJECT=$PROJECT_ID\" \\"
echo "    --region=$REGION \\"
echo "    --project=$PROJECT_ID \\"
echo "    --service-account=editorials-robot@appspot.gserviceaccount.com"