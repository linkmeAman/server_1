# CI/CD Reference For `server_1`

This document is the operational source of truth for CI, production-gate validation, and the controlled production deploy flow for `server_1`.

## Deployment Model

- `master` is the only production source branch.
- `dev` is CI-only and never deploys production directly.
- GitHub Actions validates code and produces an approved commit SHA.
- Production deployment happens on the server by pulling or checking out the approved commit, verifying it there, and only then restarting `py-server-1`.
- Database migrations are not part of the standard deploy path. Run them as a separate, intentional operation when a release requires them.

## GitHub Workflows

### CI

Workflow: `.github/workflows/ci.yml`

Triggers:

- push to `master`
- push to `dev`
- pull request targeting `master`
- pull request targeting `dev`

Checks:

```bash
python3 -m compileall app tests main.py routes scripts alembic
python3 scripts/auth/validate_authz_manifest.py --manifest authz/resources_manifest.yaml
python3 -m pytest tests -q
```

### Authz Manifest Validation

Workflow: `.github/workflows/authz-manifest.yml`

Purpose:

- revalidate the authz resource manifest when the manifest, the validator, or the workflow changes

Canonical validator path:

```text
scripts/auth/validate_authz_manifest.py
```

### Production Gate

Workflow: `.github/workflows/deploy-prod.yml`

Trigger:

- manual `workflow_dispatch`

Input:

- `ref`: branch, tag, or commit SHA to validate, default `master`

Behavior:

- checks out the requested ref
- reruns compile, authz manifest validation, and test suite
- prints the approved ref and exact commit SHA for the server deploy
- does not SSH into the server
- does not run Alembic migrations

## Production Defaults

These values are defaults, not hard requirements. Override them with environment variables if your server layout differs.

```bash
APP_DIR=/var/www/py-workspace/server_1
SERVICE_NAME=py-server-1
VENV_DIR=/var/www/py-workspace/server_1/pyenv
PYTHON_BIN=/var/www/py-workspace/server_1/pyenv/bin/python3
HEALTH_URL=http://127.0.0.1:8010/health
```

## Server Access Model

Use a dedicated deploy user. That user should have write access to the repo and virtualenv, but only narrow sudo access for the service restart and status commands.

### Repo ownership and permissions

Example using deploy user `deploy` and service group `www-data`:

```bash
sudo chown -R deploy:www-data /var/www/py-workspace/server_1
sudo chmod -R u=rwX,g=rX,o= /var/www/py-workspace/server_1
```

If the service user also needs read access to the repo, keep group read/execute enabled and ensure the service account is in the shared group.

### Virtualenv ownership and permissions

```bash
sudo chown -R deploy:www-data /var/www/py-workspace/server_1/pyenv
sudo chmod -R u=rwX,g=rX,o= /var/www/py-workspace/server_1/pyenv
```

### Service secrets

- keep production `.env` outside git or in a protected file within the repo path
- do not make `.env` broadly writable
- only the deploy user and the service user should be able to read it

Example:

```bash
sudo chown deploy:www-data /var/www/py-workspace/server_1/.env
sudo chmod 640 /var/www/py-workspace/server_1/.env
```

### Minimal sudoers rule

Create a dedicated sudoers entry with `visudo`:

```text
deploy ALL=NOPASSWD: /bin/systemctl restart py-server-1, /bin/systemctl status py-server-1 --no-pager
```

If your distribution uses `/usr/bin/systemctl`, adjust the path accordingly after confirming with `command -v systemctl`.

## Remote Verification

Before the first deploy, confirm the server repo points to the correct GitHub repository:

```bash
cd /var/www/py-workspace/server_1
git remote -v
git remote get-url origin
```

Expected origin:

```text
https://github.com/linkmeAman/server_1.git
```

## Standard Production Deploy Sequence

1. Run the production-gate workflow in GitHub for the target `master` ref and note the approved commit SHA.
2. SSH into the server as the deploy user.
3. Run the controlled deploy script from the repo:

```bash
cd /var/www/py-workspace/server_1
bash scripts/deploy_server1.sh --ref <approved-branch-or-sha>
```

The script performs:

- origin remote verification
- dirty-tree protection, including untracked files
- `git fetch --prune origin`
- checkout of the approved commit
- dependency installation
- compile check
- authz manifest validation
- full test suite
- `sudo systemctl restart py-server-1`
- `sudo systemctl status py-server-1 --no-pager`
- `curl --fail --silent --show-error http://127.0.0.1:8010/health`

If any step fails, the script exits nonzero and restores the previous checked-out commit before ending.

## Manual Step-By-Step Deploy

If you need to run the sequence manually instead of using the script:

```bash
cd /var/www/py-workspace/server_1
git remote get-url origin
git fetch --prune origin
git status --short
git checkout --detach <approved-sha>
source pyenv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pytest pytest-asyncio
python3 -m compileall app tests main.py routes scripts alembic
python3 scripts/auth/validate_authz_manifest.py --manifest authz/resources_manifest.yaml
python3 -m pytest tests -q
sudo systemctl restart py-server-1
sudo systemctl status py-server-1 --no-pager
curl --fail --silent --show-error http://127.0.0.1:8010/health
```

Do not restart the service until compile, manifest validation, and tests all pass.

## Rollback

Use the previous known-good commit SHA.

```bash
cd /var/www/py-workspace/server_1
git fetch --prune origin
bash scripts/deploy_server1.sh --ref <previous-known-good-sha>
```

If you need to identify candidates:

```bash
git log --oneline --decorate -n 20
```

## Troubleshooting

### Dirty working tree

Symptoms:

- deploy script exits before checkout

Checks:

```bash
git status --short
git ls-files --others --exclude-standard
```

Resolution:

- remove accidental untracked files
- commit intentional tracked changes
- do not deploy from a modified production working tree

### Test or dependency failure

Checks:

```bash
python3 -m pip --version
python3 -m pytest tests -q
```

Resolution:

- fix the code in GitHub first
- rerun CI and the production-gate workflow
- redeploy only after the approved commit is green

### Service restart failure

Checks:

```bash
sudo systemctl status py-server-1 --no-pager
journalctl -u py-server-1 -n 200 --no-pager
```

Resolution:

- inspect missing secrets, invalid environment variables, port conflicts, or DB connectivity
- roll back to the last known-good SHA if needed

### Health check failure

Checks:

```bash
curl -I http://127.0.0.1:8010/health
```

Resolution:

- inspect service status and logs
- verify the loaded `.env`
- confirm the service is listening on the expected port

## Release Acceptance Checklist

- CI passed for the target change
- production-gate workflow passed for the exact deploy ref
- server repo remote matches `https://github.com/linkmeAman/server_1.git`
- production working tree was clean before deploy
- compile check passed on the server
- authz manifest validation passed on the server
- tests passed on the server
- `py-server-1` restarted successfully
- `/health` returned HTTP 200
