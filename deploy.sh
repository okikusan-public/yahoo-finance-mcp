#!/usr/bin/env bash
# Deploy to Cloud Run. On the first run, a client secret is generated and stored in Secret Manager.
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-yahoo-finance-mcp}"
CLIENT_ID="${CLIENT_ID:-yahoo-finance-mcp}"
SECRET="${SECRET:-yahoo-finance-mcp-client-secret}"
SA_NAME="${SA_NAME:-yahoo-finance-mcp}"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
# Deterministic Cloud Run URL. The OAuth issuer and resource are pinned to it.
# (Cloud Run reserves paths ending in "z" such as /healthz, so the health check is /health.)
BASE_URL="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

echo "project=$PROJECT region=$REGION service=$SERVICE url=$BASE_URL"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com --project "$PROJECT"

if ! gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" --display-name "Yahoo Finance MCP runtime"
fi

if ! gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  echo "Creating secret $SECRET"
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets create "$SECRET" \
    --project "$PROJECT" --replication-policy=automatic --data-file=-
fi

# Only the dedicated service account can read this secret
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor >/dev/null

gcloud run deploy "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --source . \
  --service-account "$RUNTIME_SA" \
  --set-env-vars "OAUTH_CLIENT_ID=${CLIENT_ID},PUBLIC_BASE_URL=${BASE_URL}" \
  --set-secrets "OAUTH_CLIENT_SECRET=${SECRET}:latest" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 2 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 120 \
  --quiet

curl -fsS "$BASE_URL/health" >/dev/null && echo "health OK"

cat <<EOF

Deployed: $BASE_URL
  MCP endpoint        : $BASE_URL/mcp
  OAuth client ID     : $CLIENT_ID
  OAuth client secret : gcloud secrets versions access latest --secret $SECRET --project $PROJECT

See "Connecting clients" in README.md.
EOF
