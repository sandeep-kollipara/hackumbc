#!/usr/bin/env bash
# Run once against the current S3 listing; safe to rerun for new uploads.
set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "Usage: $0" >&2
    echo "Processes all new/changed S3 images, then clusters objects and prints Chroma counts." >&2
    exit 2
fi

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "Missing virtual environment: $PROJECT_DIR/.venv" >&2
    echo "Create it and install batch-enrichment/requirements.txt first." >&2
    exit 1
fi

# Use the top-level environment directly; activation is not required.
cd -- "$PROJECT_DIR/batch-enrichment"
export PYTHONUNBUFFERED=1

echo "Processing new or changed S3 images (completed images are skipped)..."
"$PYTHON" -m enrichment run

# Stop on ingestion failure so an incomplete run is never reported as successful.
echo "Updating object groups in Chroma..."
"$PYTHON" -m enrichment cluster

echo "Current Chroma records:"
"$PYTHON" -m enrichment stats
echo "Batch enrichment completed."
