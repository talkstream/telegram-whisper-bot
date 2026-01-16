#!/bin/bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=audio-processor" --limit=50 --project=editorials-robot --format="value(textPayload,jsonPayload.message)" > logs.txt 2>&1
