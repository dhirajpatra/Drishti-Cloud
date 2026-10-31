#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: Docker is required to start Drishti." >&2
  exit 1
fi

# to keep only the main branch and delete all other merged branches
git checkout main && git remote prune origin && git branch -r --merged | grep -v 'origin/main' | sed 's/origin\///' | xargs -I {} git push origin --delete {}

docker compose up -d --build --remove-orphans
