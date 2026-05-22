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

# Abort any stuck rebase/merge/cherry-pick before touching the tree.
# This handles the case where a previous pull --rebase was interrupted,
# leaving the repo in REBASE-IN-PROGRESS state.
git rebase --abort 2>/dev/null || true
git merge --abort  2>/dev/null || true

# Use fetch + reset instead of pull to safely handle:
#   1. Force-pushed/history-rewritten branches (git pull --rebase diverges)
#   2. Servers whose local branch fell behind after a force-push
#   3. Any local uncommitted noise (logs, .pyc leaks, etc.)
git fetch origin
git reset --hard origin/"$BRANCH"

# Remove Python cache artifacts before startup.
find . -type d \( -name "__pycache__" -o -name "_pycache_" \) -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

source .venv/bin/activate

(lsof -ti:"$PORT" | xargs -r kill -9 || true)

python -m uvicorn main:app --host 0.0.0.0 --port "$PORT"