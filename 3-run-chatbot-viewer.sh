#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "Missing $PROJECT_DIR/.venv. Create the environment and install chatbot-viewer/requirements.txt." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import dash, panel' >/dev/null 2>&1; then
    echo "Install viewer dependencies: $PYTHON -m pip install -r $PROJECT_DIR/chatbot-viewer/requirements.txt" >&2
    exit 1
fi
cd -- "$PROJECT_DIR/chatbot-viewer"
exec "$PYTHON" chatbot_viewer.py "$@"
