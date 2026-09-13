#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <api-domain> [base-path]" >&2
  echo "Example: $0 my-test-server.ru /BlueTits/" >&2
  exit 2
fi

api_url="${1%/}"
if [[ ! "$api_url" =~ ^https?:// ]]; then
  api_url="https://$api_url"
fi

base_path="${2:-/BlueTits/}"

if [[ ! -x frontend/node_modules/.bin/vite ]]; then
  npm --prefix frontend ci
fi

echo "Building frontend for $base_path with API $api_url"
VITE_API_URL="$api_url" \
VITE_BASE_PATH="$base_path" \
VITE_OUT_DIR=../docs \
npm run build:frontend
cp docs/index.html docs/404.html
touch docs/.nojekyll
echo "GitHub Pages build is ready in docs/"
