# Refresh a Lambda WebUI build on Amazon Linux EC2

This runbook updates an existing source installation created with
[Install Lambda WebUI on Amazon Linux 2023](install-amazon-linux-ec2.md). It
assumes the repository is `/opt/lambda-webui`, the service account is
`lambdawebui`, and the required systemd units are `lambda-webui` and
`lambda-webui-mcp`.

The update is an in-place maintenance operation and causes downtime while the
frontend and Python environments are rebuilt. For a deployment that cannot
tolerate downtime, build and validate a replacement EC2 instance, then switch
the load balancer target.

## 1. Inspect the current installation

Record the deployed commit and confirm that the checkout does not contain
uncommitted production edits:

```bash
sudo -u lambdawebui git -C /opt/lambda-webui status --short
sudo -u lambdawebui git -C /opt/lambda-webui branch --show-current
sudo -u lambdawebui git -C /opt/lambda-webui rev-parse HEAD
sudo systemctl status lambda-webui lambda-webui-mcp --no-pager
df -h / /opt /tmp
df -i / /opt /tmp
```

Save the commit ID. It is the code rollback target. Investigate unexpected
tracked changes before continuing; do not discard them automatically. The
production `.env`, databases, generated frontend, caches, and MCP data should
not be committed, but confirm that local configuration files are still ignored
before pulling.

Keep at least 6–10 GiB free during a full refresh. Python wheels,
`node_modules`, Vite intermediates, and the existing production build coexist
during the operation.

## 2. Back up persistent data and configuration

At minimum, back up:

- `/opt/lambda-webui/.env`
- `/opt/lambda-webui/.webui-secret`
- `/opt/lambda-webui/backend/data/`
- `/opt/lambda-webui/examples/managed-mcp/calendar-tools/data/`
- `/home/lambdawebui/.config/open-webui/mcp-runtime.token`
- Any custom MCP manifests, package data, reverse-proxy configuration, or local
  systemd overrides

Stop the application and managed MCP runtime before copying SQLite databases.
This gives the backup a consistent database state:

```bash
sudo systemctl stop lambda-webui
sudo systemctl stop lambda-webui-mcp
sudo install -d -m 700 /var/backups/lambda-webui
sudo tar -C / -czf \
  /var/backups/lambda-webui/lambda-webui-before-refresh-$(date +%Y%m%d-%H%M%S).tar.gz \
  opt/lambda-webui/.env \
  opt/lambda-webui/.webui-secret \
  opt/lambda-webui/backend/data \
  opt/lambda-webui/examples/managed-mcp/calendar-tools/data \
  home/lambdawebui/.config/open-webui/mcp-runtime.token
```

If one of those paths does not exist in your installation, omit it from the
archive command. Store important backups outside the instance as well; an EBS
snapshot or an encrypted S3 backup protects against instance or volume loss.

## 3. Update the source checkout

For the current `main` branch:

```bash
sudo -u lambdawebui git -C /opt/lambda-webui fetch --prune origin
sudo -u lambdawebui git -C /opt/lambda-webui checkout main
sudo -u lambdawebui git -C /opt/lambda-webui pull --ff-only origin main
sudo -u lambdawebui git -C /opt/lambda-webui rev-parse HEAD
```

For a controlled production release, replace `main` with a reviewed tag or
commit:

```bash
sudo -u lambdawebui git -C /opt/lambda-webui fetch --tags origin
sudo -u lambdawebui git -C /opt/lambda-webui checkout <release-tag-or-commit>
```

Use `--ff-only` for branch updates so an unexpected local divergence stops the
deployment instead of creating a merge commit on the server.

Review configuration changes before rebuilding:

```bash
sudo -u lambdawebui git -C /opt/lambda-webui diff HEAD@{1}..HEAD -- .env.example
sudo -u lambdawebui git -C /opt/lambda-webui diff HEAD@{1}..HEAD -- \
  scripts/systemd examples/managed-mcp
```

Apply relevant new settings to `/opt/lambda-webui/.env` manually. Never replace
the production `.env` with `.env.example`, and do not regenerate the WebUI
secret or MCP runtime token during a routine refresh.

## 4. Refresh backend dependencies

Use the existing Python 3.12 virtual environment and the same CPU-only PyTorch
constraint as the installation guide:

```bash
sudo -u lambdawebui -H bash -lc '
  set -o pipefail
  export PATH="/home/lambdawebui/.local/bin:$PATH"
  export TMPDIR=/opt/lambda-webui/.tmp
  export UV_CACHE_DIR=/opt/lambda-webui/.uv-cache
  export UV_CONCURRENT_DOWNLOADS=1
  export UV_HTTP_CONNECT_TIMEOUT=60
  export UV_HTTP_TIMEOUT=300
  export UV_HTTP_RETRIES=10

  uv pip install --python /opt/lambda-webui/backend/venv/bin/python \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --refresh-package torch \
    "torch==2.12.1" \
    -r /opt/lambda-webui/backend/requirements.txt \
    2>&1 | tee /opt/lambda-webui/requirements-refresh.log
'
```

Do not run an unconstrained requirements installation first. It can replace the
CPU PyTorch build with CUDA packages such as `nvidia-cusparse` and `triton`,
consuming several gigabytes on an instance that cannot use them.

Refresh all bundled managed MCP package environments from their committed lock
files:

```bash
sudo -u lambdawebui -H bash -lc '
  set -e
  export PATH="/home/lambdawebui/.local/bin:$PATH"
  export UV_CACHE_DIR=/opt/lambda-webui/.uv-cache

  cd /opt/lambda-webui/examples/managed-mcp/filesystem-tools && uv sync --frozen
  cd /opt/lambda-webui/examples/managed-mcp/calendar-tools && uv sync --frozen
  cd /opt/lambda-webui/examples/managed-mcp/web-research-tools && uv sync --frozen
'
```

`uv sync --frozen` fails when source dependency declarations and lockfiles do
not agree. Do not update locks directly on the production server.

## 5. Rebuild the frontend

Always rebuild after pulling unless the reviewed diff proves that no frontend
or shared files changed. A stale `build/` directory is the usual cause of seeing
old behavior after a successful backend update.

```bash
sudo -u lambdawebui -H bash -lc '
  set -e
  export TMPDIR=/opt/lambda-webui/.tmp
  export npm_config_cache=/opt/lambda-webui/.npm-cache
  export NODE_OPTIONS=--max-old-space-size=8192

  cd /opt/lambda-webui
  npm ci --no-audit --no-fund
  test -f node_modules/@sveltejs/kit/src/core/utils.js
  npm run build
  test -f build/index.html
'
```

Do not remove the existing `build/` directory before the new build succeeds.
After validation, frontend working files can be removed to recover space while
preserving the production build:

```bash
sudo rm -rf \
  /opt/lambda-webui/node_modules \
  /opt/lambda-webui/.svelte-kit \
  /opt/lambda-webui/.npm-cache
```

The next frontend refresh must run `npm ci` again.

## 6. Restart every service

The two required services are:

- `lambda-webui-mcp`: managed MCP runtime
- `lambda-webui`: backend and compiled frontend

Local Calendar, Local System Tools, and Local Web Research are child processes
owned by `lambda-webui-mcp`. Restarting that runtime stops and recreates all
enabled managed MCP processes. The Calendar REST API on port 8091 is embedded
in the Local Calendar process; it has no separate systemd unit.

Reload systemd only when a unit file or override changed, then restart the MCP
runtime before the WebUI backend:

```bash
sudo systemctl daemon-reload
sudo systemctl restart lambda-webui-mcp
sudo systemctl is-active --quiet lambda-webui-mcp
sudo systemctl restart lambda-webui
sudo systemctl is-active --quiet lambda-webui
```

Optional services depend on the installation:

- `kokoro-fastapi`: local Kokoro TTS server
- `ollama`: local Ollama model server
- `nginx` or `caddy`: local TLS reverse proxy
- An AWS Application Load Balancer is external and has no EC2 systemd service

Restart every installed optional service with:

```bash
for unit in kokoro-fastapi ollama nginx caddy; do
  if sudo systemctl cat "$unit.service" >/dev/null 2>&1; then
    sudo systemctl restart "$unit.service"
    sudo systemctl is-active --quiet "$unit.service" || \
      sudo systemctl status "$unit.service" --no-pager
  fi
done
```

To restart every installed Lambda WebUI-related and optional local service in
one ordered command block:

```bash
sudo systemctl daemon-reload

for unit in lambda-webui-mcp lambda-webui kokoro-fastapi ollama nginx caddy; do
  if sudo systemctl cat "$unit.service" >/dev/null 2>&1; then
    echo "Restarting $unit"
    sudo systemctl restart "$unit.service"
  fi
done

sudo systemctl --no-pager --full status lambda-webui-mcp lambda-webui
```

Do not use `systemctl restart` on a unit name that belongs to an MCP package;
managed MCP packages are controlled by `lambda-webui-mcp` and the Integrations
administration UI.

## 7. Validate the refreshed build

Check the backend health response. It includes the managed runtime diagnostic
without making an MCP failure mark the WebUI itself unhealthy:

```bash
curl --fail --silent http://127.0.0.1:8080/health
curl --fail --silent http://127.0.0.1:9090/healthz
sudo systemctl status lambda-webui lambda-webui-mcp --no-pager
sudo journalctl -u lambda-webui -u lambda-webui-mcp -n 150 --no-pager
```

The backend response should resemble:

```json
{
	"status": true,
	"managed_mcp": {
		"configured": true,
		"available": true,
		"status": "ready",
		"failed_server_count": 0
	}
}
```

Also verify:

1. Open the public HTTPS URL in a private browser window and confirm the new
   frontend is visible.
2. Check **Admin Panel → Settings → Integrations**. Every managed MCP should
   progress through `Starting` to `Ready`; `Failed` and `Unavailable` include
   diagnostic hover text.
3. In **User Settings → Tools**, confirm the intended MCPs remain enabled.
4. Ask for the current date, list Calendar events, and run one Web Research
   request with a model that lacks native web access.
5. Test Browser navigation and one Browser Action if the update changed Web
   Panels.
6. Test speech recognition and TTS if audio dependencies or configuration
   changed.

For a local Calendar API check:

```bash
curl --fail --silent http://127.0.0.1:8091/healthz
```

Run that check only when Local Calendar is enabled and ready.

## 8. Diagnose a failed refresh

```bash
sudo journalctl -u lambda-webui -f
sudo journalctl -u lambda-webui-mcp -f
sudo systemctl --failed
ss -lntp | grep -E ':(8080|9090|8091|8880|11434)\b'
df -h / /opt /tmp
df -i / /opt /tmp
```

Common causes:

- Old UI after restart: the frontend build failed or `build/` was not replaced.
- `JavaScript heap out of memory`: confirm the build shell used the documented
  8 GiB `NODE_OPTIONS` setting and that RAM plus swap can accommodate it.
- Missing `@sveltejs/kit/src/core/utils.js`: `npm ci` was interrupted, commonly
  because storage ran out. Free or expand storage and rerun `npm ci`.
- MCPs remain `Unavailable`: verify `lambda-webui-mcp`, port 9090, the token
  file, and `MANAGED_MCP_RUNTIME_URL` before restarting the backend.
- One MCP is `Failed`: inspect its administrator-visible runtime error and the
  `lambda-webui-mcp` journal. A package lock or manifest may have changed.
- Backend fails during startup: inspect its journal before attempting a
  rollback. Database migrations run during normal backend startup.

## 9. Roll back code

Rollback is safest when the pre-refresh backup is available. Stop both required
services, check out the recorded previous commit, repeat the backend dependency,
MCP environment, and frontend build steps for that commit, then restart and
validate:

```bash
sudo systemctl stop lambda-webui lambda-webui-mcp
sudo -u lambdawebui git -C /opt/lambda-webui checkout <previous-commit>
```

Do not assume that checking out old source reverses a database migration. If the
older application cannot use the migrated data, restore the matching
`backend/data` and Calendar backup while both services are stopped. Preserve a
copy of the failed deployment and current data until the rollback is verified.
