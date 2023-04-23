#!/bin/bash

# SPDX-FileCopyrightText: 2022 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

export NOTIFY_TOP_N="20"
export SLACK_CHANNEL="#test"

export PROJECT_ID="arxiv-summary-1"
export GCS_BUCKET_NAME="arxiv-summary"
export REGION="us-central1"
export IMAGE_NAME="arxiv-reddit-summary"
export RUN_SERVICE_ACCOUNT="run-sa"
export REPOSITORY="arxiv-reddit-summary"
export TAG_NAME="latest"
export RUN_JOB_NAME="arxiv-reddit-summary-job-1"
export SCHEDULER_JOB_NAME="arxiv-reddit-summary-job-everyday-9am"


gcloud artifacts repositories list --project=$PROJECT_ID
gcloud artifacts repositories delete $REPOSITORY --project=$PROJECT_ID --location=$REGION --quiet
gcloud artifacts repositories create $REPOSITORY \
  --project=$PROJECT_ID \
  --repository-format="docker" \
  --location=$REGION
gcloud artifacts repositories list --project=$PROJECT_ID

gcloud builds submit \
  --project=$PROJECT_ID \
  --region=$REGION \
  --tag="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${TAG_NAME}"
gcloud artifacts repositories list --project=$PROJECT_ID

gcloud beta run jobs list --project=$PROJECT_ID
gcloud beta run jobs delete $RUN_JOB_NAME --project=$PROJECT_ID --region=$REGION --quiet
gcloud beta run jobs create $RUN_JOB_NAME \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${TAG_NAME}" \
  --project=$PROJECT_ID \
  --region=$REGION \
  --service-account="${RUN_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-secrets="TWITTER_BEARER_TOKEN=TWITTER_BEARER_TOKEN:1" \
  --set-secrets="TWITTER_API_KEY=TWITTER_API_KEY:1" \
  --set-secrets="TWITTER_API_KEY_SECRET=TWITTER_API_KEY_SECRET:1" \
  --set-secrets="TWITTER_ACCESS_TOKEN=TWITTER_ACCESS_TOKEN:1" \
  --set-secrets="TWITTER_ACCESS_TOKEN_SECRET=TWITTER_ACCESS_TOKEN_SECRET:1" \
  --set-secrets="DEEPL_AUTH_KEY=DEEPL_AUTH_KEY:1" \
  --set-secrets="SLACK_BOT_TOKEN=SLACK_BOT_TOKEN:1" \
  --set-secrets="praw_client_id=praw_client_id:1" \
  --set-secrets="praw_client_secret=praw_client_secret:1" \
  --set-secrets="praw_user_agent=praw_user_agent:1" \
  --set-secrets="ATP_IDENTIFIER=ATP_IDENTIFIER:1" \
  --set-secrets="ATP_PASSWORD=ATP_PASSWORD:1" \
  --set-env-vars="NOTIFY_TOP_N=${NOTIFY_TOP_N}" \
  --set-env-vars="SLACK_CHANNEL=${SLACK_CHANNEL}" \
  --set-env-vars="GCS_BUCKET_NAME=${GCS_BUCKET_NAME}" \
  --max-retries=0 \
  --task-timeout="30m" \
  --memory="1024Mi"
gcloud beta run jobs list --project=$PROJECT_ID
