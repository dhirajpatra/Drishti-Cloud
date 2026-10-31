#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: Docker is required to start Drishti." >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "No .env found - copying .env.example (runs in MOCK_MODE until you add LLAMA_API_KEY)."
  cp .env.example .env
fi

docker compose up -d --build --force-recreate --remove-orphans
