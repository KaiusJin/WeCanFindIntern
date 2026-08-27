#!/bin/zsh
set -eu

PROJECT_DIR="/Users/kaius/Project/WeCanFIndIntern"

cd "$PROJECT_DIR"
set -a
source "$PROJECT_DIR/.env"
set +a
export PYTHONPATH="$PROJECT_DIR/src"
export PYTHONUNBUFFERED=1

"$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/collection/run_collection_campaign.py" \
  --batch-size 250 \
  --concurrency 4

# Also covers active jobs not returned by the latest source query window. Cached
# title/JD hashes make this a no-op for jobs already checked by the campaign.
"$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/maintenance/backfill_recruiting_terms.py"
