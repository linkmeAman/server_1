#!/usr/bin/env bash

set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/py-workspace/server_1}"
SERVICE_NAME="${SERVICE_NAME:-py-server-1}"
VENV_DIR="${VENV_DIR:-$APP_DIR/pyenv}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python3}"
DEFAULT_REF="origin/master"
TARGET_REF="$DEFAULT_REF"

usage() {
  cat <<'EOF'
Usage: deploy_server1.sh [--ref <branch-or-sha>] [--help]

Controlled production deploy for server_1.

Environment overrides:
  APP_DIR       Application repository directory
  SERVICE_NAME  systemd service to restart
  VENV_DIR      Virtualenv directory
  PYTHON_BIN    Python interpreter inside the virtualenv
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      [[ $# -ge 2 ]] || { echo "Missing value for --ref" >&2; exit 2; }
      TARGET_REF="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log() {
  printf '[deploy_server1] %s\n' "$*"
}

fail() {
  printf '[deploy_server1] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_command git
require_command curl
require_command sudo

[[ -d "$APP_DIR" ]] || fail "Application directory not found: $APP_DIR"
cd "$APP_DIR"

CURRENT_REMOTE="$(git remote get-url origin)"
[[ "$CURRENT_REMOTE" == "https://github.com/linkmeAman/server_1.git" ]] || fail "Unexpected origin remote: $CURRENT_REMOTE"

if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "Working tree has uncommitted changes. Refusing to deploy."
fi

if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  fail "Working tree has untracked files. Refusing to deploy."
fi

log "Fetching latest refs from origin"
git fetch --prune origin

if ! git rev-parse --verify --quiet "$TARGET_REF^{commit}" >/dev/null; then
  fail "Target ref is not reachable after fetch: $TARGET_REF"
fi

RESOLVED_SHA="$(git rev-parse "$TARGET_REF^{commit}")"
CURRENT_SHA="$(git rev-parse HEAD)"

log "Current commit: $CURRENT_SHA"
log "Target ref: $TARGET_REF"
log "Target commit: $RESOLVED_SHA"

[[ -d "$VENV_DIR" ]] || fail "Virtualenv directory not found: $VENV_DIR"
[[ -x "$PYTHON_BIN" ]] || fail "Python interpreter not executable: $PYTHON_BIN"

log "Checking out approved commit"
git checkout --detach "$RESOLVED_SHA"

restore_previous_ref() {
  if [[ -n "${CURRENT_SHA:-}" ]]; then
    log "Restoring previous commit $CURRENT_SHA"
    git checkout --detach "$CURRENT_SHA" >/dev/null 2>&1 || true
  fi
}

trap 'restore_previous_ref' ERR

REQUIREMENTS_CHANGED=false
if ! git diff --quiet "$CURRENT_SHA" "$RESOLVED_SHA" -- requirements.txt 2>/dev/null; then
  REQUIREMENTS_CHANGED=true
  log "requirements.txt changed between $CURRENT_SHA and $RESOLVED_SHA"
fi

if [[ "$REQUIREMENTS_CHANGED" == "true" ]]; then
  log "Upgrading pip and installing dependencies"
  "$PYTHON_BIN" -m pip install --upgrade pip
  "$PYTHON_BIN" -m pip install -r requirements.txt pytest pytest-asyncio
else
  log "requirements.txt unchanged, skipping dependency reinstall"
fi

log "Running compile check"
"$PYTHON_BIN" -m compileall app tests main.py routes scripts alembic

log "Validating authz manifest"
"$PYTHON_BIN" scripts/auth/validate_authz_manifest.py --manifest authz/resources_manifest.yaml

log "Running test suite"
"$PYTHON_BIN" -m pytest tests -q

log "Restarting service $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

log "Verifying service status"
sudo systemctl status "$SERVICE_NAME" --no-pager

log "Running health check"
curl --fail --silent --show-error http://127.0.0.1:8010/health >/dev/null

trap - ERR
log "Deployment verification succeeded on commit $RESOLVED_SHA"
