#!/usr/bin/env bash


# This script detects the current git branch and starts a server on a port mapped to that branch.
#chmod +x run_branch_server.sh && bash -n run_branch_server.sh && ls -l run_branch_server.sh
set -euo pipefail

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"

if [[ -z "$BRANCH" ]]; then
  echo "Error: Could not detect git branch. Run this script inside the repository."
  exit 1
fi

case "$BRANCH" in
  mann)
    PORT=6100
    ;;
  vicky)
    PORT=6101
    ;;
  jaan)
    PORT=6102
    ;;
  *)
    echo "Error: No port mapping configured for branch '$BRANCH'."
    echo "Configured mappings: mann -> 6100, vicky -> 6101, jaan -> 6102"
    exit 1
    ;;
esac

echo "Branch: $BRANCH"
echo "Mapped port: $PORT"

git pull

# Remove Python cache artifacts before startup.
find . -type d \( -name "__pycache__" -o -name "_pycache_" \) -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

source .venv/bin/activate

(lsof -ti:"$PORT" | xargs -r kill -9 || true)

python -m uvicorn main:app --host 0.0.0.0 --port "$PORT"