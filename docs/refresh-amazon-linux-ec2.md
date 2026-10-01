# Refresh a Lambda WebUI build on Amazon Linux EC2

This runbook updates an existing source installation created with
[Install Lambda WebUI on Amazon Linux 2023](install-amazon-linux-ec2.md). It
assumes the repository is `/opt/lambda-webui` and the service account is
`lambdawebui`. Unit names can differ between installations, so resolve them
before running the remaining commands.

The update is an in-place maintenance operation and causes downtime while the
frontend and Python environments are rebuilt. For a deployment that cannot
tolerate downtime, build and validate a replacement EC2 instance, then switch
the load balancer target.

## Operator contract

This document is intended to be executable by an operator who did not develop
Lambda WebUI. The operator needs:

- SSH access to the EC2 instance with `sudo` permission.
- Read access to the deployment repository and the approved release identifier.
- Access to the backup destination and, when applicable, the AWS console for
  EBS snapshots, EC2 status, IAM roles, and load-balancer target health.
- A maintenance window long enough to reinstall dependencies and rebuild the
  frontend. A full in-place refresh can take tens of minutes.
- A named application owner who can approve a rollback when schema or behavior
  validation fails.

Never improvise a merge, regenerate application secrets, update dependency
locks, or edit application source on the production instance. Stop and escalate
when the checkout is dirty, the backup fails, the approved revision is
ambiguous, or a command produces a result not covered here.

### What runs on the instance

| Component                            | Owner                      | Default endpoint          | Persistent state                        | Restart mechanism                       |
| ------------------------------------ | -------------------------- | ------------------------- | --------------------------------------- | --------------------------------------- |
| Lambda WebUI backend and compiled UI | `lambda-webui.service`     | `127.0.0.1:8080`          | `backend/data`, `.env`, `.webui-secret` | systemd                                 |
| Managed MCP runtime                  | `lambda-webui-mcp.service` | `127.0.0.1:9090`          | Runtime registry and token              | systemd                                 |
| Local Calendar MCP and REST API      | MCP runtime child          | `127.0.0.1:8091`          | `calendar-tools/data`                   | Restart MCP runtime or use Integrations |
| Local System Tools MCP               | MCP runtime child          | stdio behind runtime      | Package configuration                   | Restart MCP runtime or use Integrations |
| Local Web Research MCP               | MCP runtime child          | stdio behind runtime      | Temporary artifacts only                | Restart MCP runtime or use Integrations |
| Kokoro TTS, when installed           | `kokoro-fastapi.service`   | Commonly `127.0.0.1:8880` | Voice/model cache                       | systemd                                 |
| Ollama, when installed               | `ollama.service`           | `127.0.0.1:11434`         | Ollama model store                      | systemd                                 |
| TLS ingress                          | ALB, nginx, or Caddy       | Public TCP 443            | Certificates/configuration              | AWS or systemd                          |

Only ports 80/443 should be public. Ports 8080, 9090, 8091, 8880, and 11434
must remain private or loopback-only unless a separately reviewed network design
says otherwise.

### Canonical deployment paths

| Purpose                     | Path                                                         |
| --------------------------- | ------------------------------------------------------------ |
| Source checkout             | `/opt/lambda-webui`                                          |
| Production environment      | `/opt/lambda-webui/.env`                                     |
| Backend virtual environment | `/opt/lambda-webui/backend/venv`                             |
| Compiled frontend           | `/opt/lambda-webui/build`                                    |
| WebUI application data      | `/opt/lambda-webui/backend/data`                             |
| Managed MCP packages        | `/opt/lambda-webui/examples/managed-mcp`                     |
| Calendar database directory | `/opt/lambda-webui/examples/managed-mcp/calendar-tools/data` |
| MCP bearer-token file       | `/home/lambdawebui/.config/open-webui/mcp-runtime.token`     |
| Local backups               | `/var/backups/lambda-webui`                                  |

Treat a different path as a site-specific deviation and record it in the
operations handoff before using this runbook.

Kokoro must be installed as `/etc/systemd/system/kokoro-fastapi.service` on the
documented EC2 topology. Do not use bare `systemctl --user` commands from an AWS
SSM session: SSM does not normally create a per-user systemd bus. If the unit is
absent, follow **Kokoro TTS (optional)** in the zero-install guide before trying
to restart it.

## Resolve the installed service names

The source-install guide creates `lambda-webui.service` and
`lambda-webui-mcp.service`. Earlier or manually adapted installations may use
`open-webui.service` and `open-webui-mcp-runtime.service` instead. Resolve the
actual system units once in the administrator shell used for the refresh:

```bash
resolve_system_service() {
  for candidate in "$@"; do
    if sudo systemctl cat "$candidate.service" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

WEBUI_SERVICE=$(resolve_system_service lambda-webui open-webui) || {
  echo "No Lambda/Open WebUI system service was found" >&2
  exit 1
}

MCP_SERVICE=$(resolve_system_service lambda-webui-mcp open-webui-mcp-runtime) || {
  echo "No managed MCP runtime system service was found" >&2
  exit 1
}

export WEBUI_SERVICE MCP_SERVICE
printf 'WebUI service: %s\nMCP runtime service: %s\n' "$WEBUI_SERVICE" "$MCP_SERVICE"
```

If the MCP lookup fails, inspect both system and user units:

```bash
sudo systemctl list-unit-files --type=service | grep -E 'lambda-webui|open-webui.*mcp'
sudo -u lambdawebui XDG_RUNTIME_DIR=/run/user/$(id -u lambdawebui) \
  systemctl --user list-unit-files --type=service | grep -E 'lambda-webui|open-webui.*mcp'
```

A user unit named `open-webui-mcp-runtime.service` must be controlled with
the same `sudo -u lambdawebui XDG_RUNTIME_DIR=... systemctl --user` form, not
`sudo systemctl`. The repository's example user unit assumes the checkout is
`~/open-webui`; do not use it unchanged for an `/opt` installation. If neither
command finds an MCP unit, install the missing system unit directly.

Create the shared bearer-token file only when it does not already exist. Do not
replace a working token during a routine refresh:

```bash
sudo install -d -o lambdawebui -g lambdawebui -m 700 \
  /home/lambdawebui/.config/open-webui

if ! sudo test -s /home/lambdawebui/.config/open-webui/mcp-runtime.token; then
  sudo -u lambdawebui sh -c '
    umask 077
    openssl rand -hex 32 > /home/lambdawebui/.config/open-webui/mcp-runtime.token
  '
fi

sudo chown lambdawebui:lambdawebui \
  /home/lambdawebui/.config/open-webui/mcp-runtime.token
sudo chmod 600 /home/lambdawebui/.config/open-webui/mcp-runtime.token
sudo -u lambdawebui test -r \
  /home/lambdawebui/.config/open-webui/mcp-runtime.token
```

Edit `/opt/lambda-webui/.env` and confirm these values are present exactly once:

```dotenv
MANAGED_MCP_RUNTIME_URL=http://127.0.0.1:9090
MANAGED_MCP_RUNTIME_TOKEN_FILE=/home/lambdawebui/.config/open-webui/mcp-runtime.token
MANAGED_MCP_RUNTIME_HOST=127.0.0.1
MANAGED_MCP_RUNTIME_PORT=9090
MANAGED_MCP_PACKAGE_ROOTS=/opt/lambda-webui/examples/managed-mcp
MANAGED_MCP_ALLOW_ROOT_RUNTIME=false
```

The token file is read independently by the WebUI backend and the managed MCP
runtime. Do not paste its contents into logs, shell history, or the browser.
Then create the unit:

```bash
sudo tee /etc/systemd/system/lambda-webui-mcp.service >/dev/null <<'EOF'
[Unit]
Description=Lambda WebUI managed MCP runtime
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=lambdawebui
Group=lambdawebui
WorkingDirectory=/opt/lambda-webui
EnvironmentFile=/opt/lambda-webui/.env
Environment=PYTHONPATH=/opt/lambda-webui/backend
Environment=PATH=/home/lambdawebui/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/lambda-webui/backend/venv/bin/python -m open_webui.mcp_runtime.app
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/lambda-webui/backend/data /opt/lambda-webui/examples/managed-mcp /home/lambdawebui

[Install]
WantedBy=multi-user.target
EOF
```

Load, enable, and verify it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lambda-webui-mcp.service
sudo systemctl status lambda-webui-mcp.service --no-pager
curl --fail http://127.0.0.1:9090/healthz
```

If startup fails, inspect:

```bash
sudo journalctl -u lambda-webui-mcp.service -n 150 --no-pager
```

After installation succeeds, rerun the service-resolution block so
`MCP_SERVICE` is set for the rest of the refresh.

The rest of this runbook assumes system units and uses `$WEBUI_SERVICE` and
`$MCP_SERVICE`. Re-run the resolution block after opening a new shell.

## Establish the service baseline

Run this section once during handoff and whenever unit files change. Confirm
both units are enabled and show their effective definitions:

```bash
sudo systemctl is-enabled "$WEBUI_SERVICE" "$MCP_SERVICE"
sudo systemctl cat "$WEBUI_SERVICE.service"
sudo systemctl cat "$MCP_SERVICE.service"
sudo systemctl show "$WEBUI_SERVICE.service" --property=FragmentPath,User,Group,WorkingDirectory
sudo systemctl show "$MCP_SERVICE.service" --property=FragmentPath,User,Group,WorkingDirectory
```

Both services must run as `lambdawebui`, use the `/opt/lambda-webui` checkout,
and load:

```ini
EnvironmentFile=/opt/lambda-webui/.env
```

If the WebUI unit is locally maintained under `/etc/systemd/system`, add that
line to its existing `[Service]` section. If the unit is supplied from
`/usr/lib/systemd/system`, preserve the vendor file and add a drop-in:

```bash
sudo install -d -m 755 \
  "/etc/systemd/system/$WEBUI_SERVICE.service.d"
sudo tee \
  "/etc/systemd/system/$WEBUI_SERVICE.service.d/environment.conf" \
  >/dev/null <<'EOF'
[Service]
EnvironmentFile=/opt/lambda-webui/.env
EOF
sudo systemctl daemon-reload
```

Validate required files and permissions without printing secret values:

```bash
sudo test -d /opt/lambda-webui/.git
sudo test -x /opt/lambda-webui/backend/venv/bin/python
sudo test -f /opt/lambda-webui/backend/start.sh
sudo test -f /opt/lambda-webui/build/index.html
sudo test -f /opt/lambda-webui/.env
sudo -u lambdawebui test -r /opt/lambda-webui/.env
sudo -u lambdawebui test -s \
  /home/lambdawebui/.config/open-webui/mcp-runtime.token
```

Confirm that the production environment contains every managed-runtime key and
that none is empty:

```bash
for key in \
  MANAGED_MCP_RUNTIME_URL \
  MANAGED_MCP_RUNTIME_TOKEN_FILE \
  MANAGED_MCP_RUNTIME_HOST \
  MANAGED_MCP_RUNTIME_PORT \
  MANAGED_MCP_PACKAGE_ROOTS; do
  if ! sudo grep -Eq "^${key}=.+" /opt/lambda-webui/.env; then
    echo "Missing or empty: $key" >&2
  fi
done
```

Also confirm the core WebUI settings. The exact values are site-specific, but
these keys must be non-empty:

```bash
for key in WEBUI_SECRET_KEY HOST PORT WEBUI_URL; do
  if ! sudo grep -Eq "^${key}=.+" /opt/lambda-webui/.env; then
    echo "Missing or empty: $key" >&2
  fi
done
```

For Bedrock deployments, `ENABLE_BEDROCK=true` and `AWS_REGION` must also be
present. Prefer the EC2 instance role; static AWS keys are not required and
should not be placed in `.env` when the role supplies credentials.

The expected values are documented in the missing-service recovery section
above. The backend and runtime must read the same token file. Never print the
token to validate it.

## 1. Inspect the current installation

Record the deployed commit and confirm that the checkout does not contain
uncommitted production edits:

```bash
sudo -u lambdawebui git -C /opt/lambda-webui status --short
sudo -u lambdawebui git -C /opt/lambda-webui branch --show-current
sudo -u lambdawebui git -C /opt/lambda-webui rev-parse HEAD
sudo systemctl status "$WEBUI_SERVICE" "$MCP_SERVICE" --no-pager
sudo systemctl --failed
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

Do not begin the maintenance window unless all of the following are true:

- The approved target is an exact commit or tag in this fork.
- `git status --short` contains no unexplained tracked changes.
- Both required services are currently understood, even if one is unhealthy.
- The root filesystem and inode counts have sufficient headroom.
- A current backup destination is writable.
- The operator knows who can approve rollback or database restoration.

## 2. Back up persistent data and configuration

At minimum, back up:

- `/opt/lambda-webui/.env`
- `/opt/lambda-webui/.webui-secret`
- `/opt/lambda-webui/backend/data/`
- `/opt/lambda-webui/examples/managed-mcp/calendar-tools/data/`
- `/home/lambdawebui/.config/open-webui/mcp-runtime.token`
- Any custom MCP manifests, package data, reverse-proxy configuration, or local
  systemd overrides

Check the documented paths before stopping services. A missing optional path is
not necessarily an incident, but the archive command must be adjusted rather
than allowed to fail halfway through maintenance:

```bash
for path in \
  /opt/lambda-webui/.env \
  /opt/lambda-webui/.webui-secret \
  /opt/lambda-webui/backend/data \
  /opt/lambda-webui/examples/managed-mcp/calendar-tools/data \
  /home/lambdawebui/.config/open-webui/mcp-runtime.token; do
  sudo test -e "$path" || echo "Review missing backup path: $path"
done
```

Stop the application and managed MCP runtime before copying SQLite databases.
This gives the backup a consistent database state:

```bash
sudo systemctl stop "$WEBUI_SERVICE"
sudo systemctl stop "$MCP_SERVICE"
sudo install -d -m 700 /var/backups/lambda-webui
BACKUP_ARCHIVE="/var/backups/lambda-webui/lambda-webui-before-refresh-$(date +%Y%m%d-%H%M%S).tar.gz"
export BACKUP_ARCHIVE
sudo tar -C / -czf \
  "$BACKUP_ARCHIVE" \
  opt/lambda-webui/.env \
  opt/lambda-webui/.webui-secret \
  opt/lambda-webui/backend/data \
  opt/lambda-webui/examples/managed-mcp/calendar-tools/data \
  home/lambdawebui/.config/open-webui/mcp-runtime.token
sudo chmod 600 "$BACKUP_ARCHIVE"
sudo test -s "$BACKUP_ARCHIVE"
sudo tar -tzf "$BACKUP_ARCHIVE" >/dev/null
sudo sha256sum "$BACKUP_ARCHIVE"
sudo ls -lh "$BACKUP_ARCHIVE"
```

If one of those paths does not exist in your installation, omit it from the
archive command. Store important backups outside the instance as well; an EBS
snapshot or an encrypted S3 backup protects against instance or volume loss.

## 3. Update the source checkout

Production should normally deploy an approved immutable tag or commit. Deploy a
moving `main` branch only when the change process explicitly identifies its
resolved commit and approves it.

Capture the old revision in the current shell before fetching:

```bash
OLD_COMMIT=$(sudo -u lambdawebui git -C /opt/lambda-webui rev-parse HEAD)
export OLD_COMMIT
printf 'Previous commit: %s\n' "$OLD_COMMIT"
```

For the current `main` branch:

```bash
sudo -u lambdawebui git -C /opt/lambda-webui fetch --prune origin
sudo -u lambdawebui git -C /opt/lambda-webui checkout main
sudo -u lambdawebui git -C /opt/lambda-webui pull --ff-only origin main
NEW_COMMIT=$(sudo -u lambdawebui git -C /opt/lambda-webui rev-parse HEAD)
export NEW_COMMIT
printf 'Target commit: %s\n' "$NEW_COMMIT"
```

For a controlled production release, replace `main` with a reviewed tag or
commit:

```bash
sudo -u lambdawebui git -C /opt/lambda-webui fetch --tags origin
sudo -u lambdawebui git -C /opt/lambda-webui checkout <release-tag-or-commit>
NEW_COMMIT=$(sudo -u lambdawebui git -C /opt/lambda-webui rev-parse HEAD)
export NEW_COMMIT
printf 'Target commit: %s\n' "$NEW_COMMIT"
```

Use `--ff-only` for branch updates so an unexpected local divergence stops the
deployment instead of creating a merge commit on the server.

Review configuration changes before rebuilding:

```bash
sudo -u lambdawebui git -C /opt/lambda-webui diff "$OLD_COMMIT..$NEW_COMMIT" -- .env.example
sudo -u lambdawebui git -C /opt/lambda-webui diff "$OLD_COMMIT..$NEW_COMMIT" -- \
  scripts/systemd examples/managed-mcp
```

Apply relevant new settings to `/opt/lambda-webui/.env` manually. Never replace
the production `.env` with `.env.example`, and do not regenerate the WebUI
secret or MCP runtime token during a routine refresh.

Record the old and new commits in the change ticket. If `OLD_COMMIT` equals
`NEW_COMMIT`, there is no source update; document why a rebuild or restart is
still being performed.

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

The two required services are normally:

- `lambda-webui-mcp`: managed MCP runtime
- `lambda-webui`: backend and compiled frontend

Local Calendar, Local System Tools, and Local Web Research are child processes
owned by the unit selected in `$MCP_SERVICE`. Restarting that runtime stops and
recreates all enabled managed MCP processes. The Calendar REST API on port 8091
is embedded in the Local Calendar process; it has no separate systemd unit.

Reload systemd only when a unit file or override changed, then restart the MCP
runtime before the WebUI backend:

```bash
sudo systemctl daemon-reload
sudo systemctl restart "$MCP_SERVICE"
sudo systemctl is-active --quiet "$MCP_SERVICE"
sudo systemctl restart "$WEBUI_SERVICE"
sudo systemctl is-active --quiet "$WEBUI_SERVICE"
```

Optional services depend on the installation:

- `kokoro-fastapi`: local Kokoro TTS server
- `ollama`: local Ollama model server
- `nginx` or `caddy`: local TLS reverse proxy
- An AWS Application Load Balancer is external and has no EC2 systemd service

The documented EC2 Kokoro installation is a system unit, so the same commands
work from SSH and AWS SSM sessions. A `Unit kokoro-fastapi.service not found`
error means Kokoro was not installed; it is not a reason to retry with
`systemctl --user`.

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

for unit in "$MCP_SERVICE" "$WEBUI_SERVICE" kokoro-fastapi ollama nginx caddy; do
  if sudo systemctl cat "$unit.service" >/dev/null 2>&1; then
    echo "Restarting $unit"
    sudo systemctl restart "$unit.service"
  fi
done

sudo systemctl --no-pager --full status "$MCP_SERVICE" "$WEBUI_SERVICE"
```

Do not use `systemctl restart` on a unit name that belongs to an MCP package;
managed MCP packages are controlled by `$MCP_SERVICE` and the Integrations
administration UI.

## 7. Validate the refreshed build

Check the backend health response. It includes the managed runtime diagnostic
without making an MCP failure mark the WebUI itself unhealthy:

```bash
curl --fail --silent http://127.0.0.1:8080/health
curl --fail --silent http://127.0.0.1:9090/healthz
sudo systemctl status "$WEBUI_SERVICE" "$MCP_SERVICE" --no-pager
sudo journalctl -u "$WEBUI_SERVICE" -u "$MCP_SERVICE" -n 150 --no-pager
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

When Kokoro is installed, validate it independently of Lambda WebUI before
testing Voice Mode:

```bash
if sudo systemctl cat kokoro-fastapi.service >/dev/null 2>&1; then
  sudo systemctl is-active --quiet kokoro-fastapi.service
  curl --fail --silent http://127.0.0.1:8880/v1/audio/voices >/dev/null
  curl --fail --silent --show-error --max-time 120 \
    http://127.0.0.1:8880/v1/audio/speech \
    -H 'Content-Type: application/json' \
    -d '{"model":"kokoro","voice":"bf_emma","input":"Kokoro is ready.","response_format":"mp3","speed":1}' \
    -o /tmp/kokoro-refresh-smoke-test.mp3
  test -s /tmp/kokoro-refresh-smoke-test.mp3
  file /tmp/kokoro-refresh-smoke-test.mp3
fi
```

For a local Calendar API check:

```bash
curl --fail --silent http://127.0.0.1:8091/healthz
```

Run that check only when Local Calendar is enabled and ready.

## 8. Diagnose a failed refresh

```bash
sudo journalctl -u "$WEBUI_SERVICE" -f
sudo journalctl -u "$MCP_SERVICE" -f
sudo journalctl -u kokoro-fastapi.service -n 150 --no-pager
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
- MCPs remain `Unavailable`: verify `$MCP_SERVICE`, port 9090, the token file,
  and `MANAGED_MCP_RUNTIME_URL` before restarting the backend.
- One MCP is `Failed`: inspect its administrator-visible runtime error and the
  `$MCP_SERVICE` journal. A package lock or manifest may have changed.
- Backend fails during startup: inspect its journal before attempting a
  rollback. Database migrations run during normal backend startup.

## 9. Roll back code

Rollback is safest when the pre-refresh backup is available. Stop both required
services, check out the recorded previous commit, repeat the backend dependency,
MCP environment, and frontend build steps for that commit, then restart and
validate:

```bash
sudo systemctl stop "$WEBUI_SERVICE" "$MCP_SERVICE"
sudo -u lambdawebui git -C /opt/lambda-webui checkout <previous-commit>
```

Do not assume that checking out old source reverses a database migration. If the
older application cannot use the migrated data, restore the matching
`backend/data` and Calendar backup while both services are stopped. Preserve a
copy of the failed deployment and current data until the rollback is verified.

## 10. Interpret health and status correctly

Use all layers when deciding whether the deployment is healthy:

| Signal                                 | Healthy result               | Meaning of failure                           |
| -------------------------------------- | ---------------------------- | -------------------------------------------- |
| `systemctl is-active "$WEBUI_SERVICE"` | `active`                     | Backend process is not running               |
| `GET 127.0.0.1:8080/health`            | HTTP 200 and `status: true`  | Backend is unreachable                       |
| `GET 127.0.0.1:8080/ready`             | HTTP 200                     | Startup, database, or Redis readiness failed |
| `systemctl is-active "$MCP_SERVICE"`   | `active`                     | MCP supervisor process is not running        |
| `GET 127.0.0.1:9090/healthz`           | HTTP 200                     | MCP runtime is unreachable                   |
| WebUI `/health` → `managed_mcp.status` | `ready`                      | See state meanings below                     |
| Integrations service state             | `Ready` for enabled services | One managed child is not usable              |

The backend `/health` endpoint deliberately remains HTTP 200 when MCP is
unavailable, because chat without managed MCPs can still operate. Interpret its
`managed_mcp` object explicitly:

- `disabled`: the backend did not receive a runtime URL and token setting.
- `unavailable`: the runtime could not be reached or authenticated.
- `degraded`: the runtime answered, but at least one enabled managed MCP failed.
- `ready`: the runtime answered and every enabled managed MCP is ready.

Do not declare the refresh successful merely because the WebUI login page
loads. Validate the model provider and the three local MCP behaviors listed in
section 7.

## 11. Routine operations after handoff

### Daily automated checks

At minimum, monitoring should alert on:

- EC2 instance or load-balancer target health failure.
- `lambda-webui` or the resolved WebUI unit becoming inactive.
- The managed MCP runtime becoming inactive or unreachable on port 9090.
- `/ready` returning a non-200 response.
- Root filesystem usage above 80% and inode usage above 80%.
- Repeated systemd restarts or error bursts in either journal.
- TLS certificate expiry when TLS terminates on the instance.

The unauthenticated loopback probes are:

```bash
curl --fail --max-time 5 http://127.0.0.1:8080/health
curl --fail --max-time 5 http://127.0.0.1:8080/ready
curl --fail --max-time 5 http://127.0.0.1:9090/healthz
```

Run them locally or through a host monitoring agent. Do not expose port 9090 or
the token to an external monitoring service.

### Weekly review

```bash
sudo systemctl --failed
sudo systemctl show "$WEBUI_SERVICE" "$MCP_SERVICE" \
  --property=Id,ActiveState,SubState,NRestarts
sudo journalctl -u "$WEBUI_SERVICE" -u "$MCP_SERVICE" \
  --since '7 days ago' --priority=warning --no-pager
df -h / /opt
df -i / /opt
sudo du -sh \
  /opt/lambda-webui/backend/data \
  /opt/lambda-webui/.uv-cache \
  /opt/lambda-webui/examples/managed-mcp/*/data 2>/dev/null
```

Review growth before deleting anything. Web Research artifacts are temporary;
the WebUI database and Calendar data are not.

### Backup policy

Choose and document retention appropriate to the deployment. A reasonable
starting point is daily encrypted backups with seven daily and four weekly
restore points, plus an EBS snapshot before every application refresh. Back up
configuration and data, not caches, virtual environments, `node_modules`, or
temporary Web Research artifacts. Test restoration on a separate instance;
the existence of an archive alone is not evidence that it can be restored.

## 12. Incident decision table

| Symptom                            | First checks                                                     | Corrective action                                                       |
| ---------------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Public site is unreachable         | ALB/nginx/Caddy, security group, `lambda-webui`, port 8080       | Restore ingress or restart only the failed layer                        |
| `/health` works but `/ready` fails | WebUI journal, database, Redis                                   | Repair the reported dependency; do not repeatedly restart blindly       |
| `managed_mcp` is `disabled`        | Effective WebUI unit and its `EnvironmentFile`                   | Load `/opt/lambda-webui/.env`, then restart WebUI                       |
| `managed_mcp` is `unavailable`     | MCP unit, port 9090, shared token path, both journals            | Start runtime or correct URL/token mismatch, then restart both units    |
| One MCP is `Failed`                | Integrations hover error and MCP journal                         | Repair that package/configuration; avoid resetting all application data |
| MCPs are ready but absent in chat  | User Settings → Tools and model assignment                       | Enable the intended MCP for that user/model                             |
| UI looks old after deployment      | `build/index.html` timestamp and frontend build log              | Rebuild frontend and restart WebUI                                      |
| Model calls fail but UI works      | Provider settings, EC2 IAM role, region, provider journal errors | Repair provider credentials/permissions; MCP restart is unrelated       |
| Voice input fails                  | HTTPS/microphone permission, `ffprobe`, Whisper settings         | Repair the failed audio layer and restart WebUI                         |
| TTS fails                          | `kokoro-fastapi`, port 8880, direct synthesis, effective `.env`  | Repair the failed TTS layer; do not restart MCP runtime unnecessarily   |
| Disk is full                       | `df`, `du`, uv/npm caches, journals                              | Expand EBS or remove reproducible caches; preserve application data     |

When collecting evidence, record UTC time, affected user, request ID shown in
the UI, deployed commit, service states, and relevant bounded journal lines.
Never attach `.env`, bearer tokens, AWS credentials, session cookies, raw user
content, or full databases to an incident ticket.

## 13. Definition of done for a refresh

The operator may close the change only when all items are recorded:

- Previous and deployed commit IDs.
- Backup archive or snapshot identifier and successful archive validation.
- Backend dependency, MCP `uv sync`, and frontend build results.
- Both required systemd units active with no restart loop.
- `/health`, `/ready`, and MCP `/healthz` successful.
- `managed_mcp.status` is `ready` and enabled Integrations entries show `Ready`.
- Public HTTPS login and one normal chat response successful.
- Current-date, Calendar, and Web Research MCP checks successful.
- Browser and audio smoke tests completed when affected by the release.
- No unexpected warnings in the post-deployment journals.
- Rollback target retained until the observation window ends.

If any required item fails, keep the change open and either repair forward or
perform the documented rollback with application-owner approval.
