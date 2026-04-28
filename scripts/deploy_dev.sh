#!/usr/bin/env bash

set -euo pipefail

DEV_APP_DIR="${DEV_APP_DIR:-/var/www/py-workspace/server_1_dev}"
DEV_SERVICE_NAME="${DEV_SERVICE_NAME:-ddev}"
DEV_VENV_DIR="${DEV_VENV_DIR:-$DEV_APP_DIR/pyenv}"
DEV_PYTHON_BIN="${DEV_PYTHON_BIN:-$DEV_VENV_DIR/bin/python3}"
DEV_HEALTH_URL="${DEV_HEALTH_URL:-http://127.0.0.1:8011/health}"

log()  { printf '[deploy_dev] %s\n' "$*"; }
fail() { printf '[deploy_dev] ERROR: %s\n' "$*" >&2; exit 1; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_command git
require_command curl
require_command sudo

[[ -d "$DEV_APP_DIR" ]] || fail "Dev directory not found: $DEV_APP_DIR"
cd "$DEV_APP_DIR"

REMOTE_URL="$(git remote get-url origin)"
[[ "$REMOTE_URL" == "https://github.com/linkmeAman/server_1.git" ]] \
  || fail "Unexpected origin remote: $REMOTE_URL"

if git rev-parse --verify HEAD >/dev/null 2>&1; then
  PREVIOUS_SHA="$(git rev-parse HEAD)"
else
  PREVIOUS_SHA=""
fi

log "Previous commit: ${PREVIOUS_SHA:-none}"

rollback_and_exit() {
  if [[ -n "${PREVIOUS_SHA:-}" ]]; then
    log "Rolling back to $PREVIOUS_SHA"
    git checkout --detach "$PREVIOUS_SHA" >/dev/null 2>&1 || true
    "$DEV_PYTHON_BIN" -m pip install -r requirements.txt --quiet || true
    sudo systemctl restart "$DEV_SERVICE_NAME" || true
    log "Rollback complete. Service restored to $PREVIOUS_SHA."
  else
    log "No previous commit to roll back to."
  fi
}

trap 'rollback_and_exit' ERR

log "Fetching origin"
git fetch --prune origin

TARGET_SHA="$(git rev-parse origin/dev)"
log "Target commit: $TARGET_SHA"

if [[ "$PREVIOUS_SHA" == "$TARGET_SHA" ]]; then
  log "Already at $TARGET_SHA, nothing to deploy."
  trap - ERR
  exit 0
fi

REQUIREMENTS_CHANGED=false
if [[ -n "$PREVIOUS_SHA" ]]; then
  if ! git diff --quiet "$PREVIOUS_SHA" "$TARGET_SHA" -- requirements.txt 2>/dev/null; then
    REQUIREMENTS_CHANGED=true
    log "requirements.txt changed, will reinstall dependencies"
  fi
else
  REQUIREMENTS_CHANGED=true
fi

log "Checking out $TARGET_SHA"
git checkout --detach "$TARGET_SHA"

if [[ "$REQUIREMENTS_CHANGED" == "true" ]]; then
  log "Upgrading pip and installing dependencies"
  "$DEV_PYTHON_BIN" -m pip install --upgrade pip
  "$DEV_PYTHON_BIN" -m pip install -r requirements.txt
else
  log "requirements.txt unchanged, skipping dependency reinstall"
fi

log "Restarting service $DEV_SERVICE_NAME"
sudo systemctl restart "$DEV_SERVICE_NAME"

log "Verifying service status"
sudo systemctl status "$DEV_SERVICE_NAME" --no-pager

log "Running health check against $DEV_HEALTH_URL"
curl --fail --silent --show-error "$DEV_HEALTH_URL" >/dev/null

trap - ERR
log "Dev deployment succeeded: $TARGET_SHA"
