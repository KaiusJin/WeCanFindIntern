#!/bin/zsh
set -eu

PROJECT_DIR="/Users/kaius/Project/WeCanFIndIntern"

cd "$PROJECT_DIR"
set -a
source "$PROJECT_DIR/.env"
set +a
export PYTHONPATH="$PROJECT_DIR/src"

"$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/run_collection_campaign.py" \
  --batch-size 250
