#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <port>" >&2
  echo "Example: $0 8000" >&2
  exit 2
fi

port="$1"
if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
  echo "Invalid port: $port" >&2
  exit 2
fi

export HOST="${HOST:-127.0.0.1}"
export PORT="$port"

echo "Starting BlueTits API at http://$HOST:$PORT"
exec "${PYTHON_BIN:-python}" backend/server.py
