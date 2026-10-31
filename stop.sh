#!/bin/sh
set -eu

usage() {
  echo "Usage: $0 [--volumes]"
  echo "Stop and remove the Compose containers and network."
  echo "  --volumes  Also remove persistent data volumes (destructive)."
}

REMOVE_VOLUMES=false
if [ "$#" -gt 1 ]; then
  usage >&2
  exit 2
fi

case "${1:-}" in
  "")
    ;;
  --volumes|-v)
    REMOVE_VOLUMES=true
    ;;
  --help|-h)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: Docker is required to stop Drishti." >&2
  exit 1
fi

if [ "$REMOVE_VOLUMES" = true ]; then
  docker compose down --remove-orphans --timeout 30 --volumes
else
  docker compose down --remove-orphans --timeout 30
fi
