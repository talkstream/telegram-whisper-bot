#!/bin/bash
# Deploy main bot to App Engine

echo "Deploying main bot to App Engine..."
gcloud app deploy --project=editorials-robot

echo "Deployment complete!"
echo "Check the logs with: gcloud app logs tail -s default --project=editorials-robot"