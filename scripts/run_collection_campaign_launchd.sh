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

# Also covers active jobs not returned by the latest source query window. Cached
# title/JD hashes make this a no-op for jobs already checked by the campaign.
"$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/backfill_recruiting_terms.py"
