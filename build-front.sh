#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <api-domain>" >&2
  echo "Example: $0 api.example.com" >&2
  exit 2
fi

api_url="${1%/}"
if [[ ! "$api_url" =~ ^https?:// ]]; then
  api_url="https://$api_url"
fi

echo "Building frontend for API: $api_url"
npm --prefix frontend ci
VITE_API_URL="$api_url" npm run build:frontend
echo "Frontend build is ready in frontend/dist/"
