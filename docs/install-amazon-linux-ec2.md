# Install Lambda WebUI on Amazon Linux 2023

This guide installs this fork from source on one Amazon EC2 instance. It uses a
production frontend build, a dedicated unprivileged service account, systemd,
and the managed local MCP runtime. The public browser connects only to the WebUI;
Ollama, MCP, Calendar, and TTS ports stay on loopback.

## 1. Choose and prepare the instance

Use the current **Amazon Linux 2023** AMI. For Bedrock or another remote model
provider, start with 2 vCPU and at least 30 GiB of gp3 storage. The production
frontend build needs an 8 GiB Node.js heap, so use 16 GiB RAM for the simplest
installation path. An 8 GiB instance can build with additional swap, but a 4
GiB instance is not recommended for building this source tree. Running Ollama
on the same host requires substantially more RAM or a suitable GPU and should
be sized for the chosen model.

Do not accept the EC2 launch wizard's 8 GiB root-volume default. Source builds
temporarily hold Python wheels, the virtual environment, npm packages, and the
compiled frontend at the same time; 8 GiB is insufficient even when the final
runtime footprint would fit.

Configure the EC2 security group as follows:

- TCP 22 from an administrator's IP only.
- TCP 80 and 443 from the intended clients.
- Do **not** expose 8080, 9090, 8091, 8880, or 11434.

If Bedrock will be used, attach an EC2 IAM role with only the required Bedrock
model-listing and invocation permissions. An instance role is preferable to
putting long-lived AWS access keys in `.env`.

Connect as `ec2-user`, update the instance, and install the core build/runtime
packages:

```bash
sudo dnf update -y
sudo dnf install -y \
  git curl ca-certificates gcc gcc-c++ make openssl-devel libffi-devel \
  nodejs22 nodejs22-npm

node --version
npm --version
```

`ffmpeg-free` is supplied by the separate Amazon Linux SPAL repository, not the
base AL2023 repository. SPAL requires AL2023 release `2023.9.20251117` or later.
Check the installed release first:

```bash
rpm -q system-release --qf '%{VERSION}\n'
sudo dnf check-release-update
```

If the release is older than `2023.9.20251117`, use the specific upgrade command
printed by `dnf check-release-update` (or replace the instance with the current
AL2023 AMI), reboot, and check the version again. AWS recommends testing and
pinning a dated AL2023 release rather than blindly using `--releasever=latest`.

On a compatible release, enable SPAL and install the audio tools:

```bash
sudo dnf install -y spal-release
sudo dnf config-manager --enable amazonlinux-spal
sudo dnf clean metadata
sudo dnf makecache --refresh --enablerepo=amazonlinux-spal
sudo dnf --disablerepo='*' --enablerepo=amazonlinux-spal list available ffmpeg-free
sudo dnf --enablerepo=amazonlinux-spal install -y ffmpeg-free

ffmpeg -version
ffprobe -version
```

If `spal-release` itself cannot be found, the instance is still pinned to an
older AL2023 repository snapshot. Updating only the package cache will not fix
that; upgrade to a supported dated AL2023 release or launch a current AMI.
If the repository-only `list available` command still cannot find
`ffmpeg-free`, inspect `dnf repolist --all` and
`/etc/yum.repos.d/amazonlinux-spal.repo`; the repository is not active or its
metadata could not be downloaded. Do not add a Fedora or an AL2 repository to
work around that condition.

This project supports Node.js 18.13 through 22.x; Node.js 22 is deliberately
selected above. `ffprobe` is required for reliable browser-audio transcription.

## 2. Create the service account and install the source

Replace the clone URL if this repository has moved. Pinning a release tag or
known commit is strongly recommended for a production installation.

```bash
sudo useradd --create-home --shell /bin/bash lambdawebui
sudo git clone https://github.com/helvecio-ribeiro/OpenWebUIForBedrock.git /opt/lambda-webui
sudo chown -R lambdawebui:lambdawebui /opt/lambda-webui
sudo -u lambdawebui git -C /opt/lambda-webui checkout <release-tag-or-commit>
sudo install -d -o lambdawebui -g lambdawebui -m 700 \
  /opt/lambda-webui/.tmp \
  /opt/lambda-webui/.uv-cache \
  /opt/lambda-webui/.npm-cache
df -h / /opt /tmp
```

Keep at least 6–10 GiB free during dependency installation. On some EC2 images,
`/tmp` is a 2 GiB `tmpfs` even when the EBS volume has ample space. The two
directories above keep wheel downloads and extraction on the disk-backed `/opt`
filesystem instead. `/dev/shm` capacity is unrelated to this installation.

Install `uv` and Python 3.12 for the service account, then create the backend
virtual environment:

```bash
sudo -u lambdawebui -H bash -lc \
  'curl -LsSf https://astral.sh/uv/install.sh | sh'

sudo -u lambdawebui -H bash -lc '
  set -o pipefail
  export PATH="$HOME/.local/bin:$PATH"
  export TMPDIR=/opt/lambda-webui/.tmp
  export UV_CACHE_DIR=/opt/lambda-webui/.uv-cache
  export UV_CONCURRENT_DOWNLOADS=1
  export UV_HTTP_CONNECT_TIMEOUT=60
  export UV_HTTP_TIMEOUT=300
  export UV_HTTP_RETRIES=10

  uv python install 3.12
  uv venv --python 3.12 /opt/lambda-webui/backend/venv

  uv pip install --python /opt/lambda-webui/backend/venv/bin/python \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --refresh-package torch \
    "torch==2.12.1" \
    -r /opt/lambda-webui/backend/requirements.txt \
    2>&1 | tee /opt/lambda-webui/requirements-install.log
'
```

The explicit version and additional PyTorch index deliberately select the CPU
build. Both must be present in the **same** `uv pip install` transaction: merely
preinstalling a CPU wheel does not prevent a later unconstrained resolver pass
from upgrading it. Without the constraint, PyPI can select a newer CUDA build
and download large `nvidia-*` packages or `triton`, even when the EC2 instance
has no NVIDIA GPU. The version matches this repository's lockfile. For a GPU
instance, replace it with the PyTorch version/index matching the installed
NVIDIA driver and CUDA platform; do not mix arbitrary CUDA wheels into a CPU
deployment.

The serialized downloads, longer timeouts, and retries make large wheels such
as `av` and `ctranslate2` more reliable on a small instance. If installation is
interrupted, rerun the same command against the existing virtual environment;
do not recreate it merely because one wheel download failed.

Build the frontend. `npm ci` uses the committed lockfile and is preferred over
`npm install` for a repeatable deployment. Keeping npm's cache and temporary
files under `/opt` avoids filling a small `/tmp` mount.

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
'
```

The backend serves the generated `build/` directory; Node.js is not needed by
the running service after this step. After verifying that the build completed,
you may recover the frontend installation space with:

```bash
sudo rm -rf \
  /opt/lambda-webui/node_modules \
  /opt/lambda-webui/.svelte-kit \
  /opt/lambda-webui/.npm-cache
```

Keep `/opt/lambda-webui/build`; it is the production frontend served by the
backend. Run `npm ci` again before a future frontend rebuild. A missing
`node_modules/@sveltejs/kit/src/core/utils.js` means the dependency installation
was incomplete, normally because storage ran out; do not patch the import.

## 3. Configure Lambda WebUI

Create the environment file and restrict it because it contains the application
secret and may later contain provider credentials:

```bash
sudo -u lambdawebui cp /opt/lambda-webui/.env.example /opt/lambda-webui/.env
sudo chmod 600 /opt/lambda-webui/.env
openssl rand -hex 32 | sudo tee /opt/lambda-webui/.webui-secret >/dev/null
sudo chown lambdawebui:lambdawebui /opt/lambda-webui/.webui-secret
sudo chmod 600 /opt/lambda-webui/.webui-secret
```

Edit `/opt/lambda-webui/.env` and set at least:

```dotenv
WEBUI_NAME=Lambda WebUI
HOST=127.0.0.1
PORT=8080
WEBUI_URL=https://chat.example.com
WEBUI_SECRET_KEY=<contents-of-/opt/lambda-webui/.webui-secret>

WEBUI_AUTH=true
ENABLE_LOGIN_FORM=true
ENABLE_SIGNUP=true
DEFAULT_USER_ROLE=pending
BYPASS_MODEL_ACCESS_CONTROL=true

WEBUI_SESSION_COOKIE_SECURE=true
WEBUI_AUTH_COOKIE_SECURE=true
FORWARDED_ALLOW_IPS=127.0.0.1
ENABLE_VERSION_UPDATE_CHECK=false
```

Do not include angle brackets around the actual secret. For an initial test
without a reverse proxy, set `HOST=0.0.0.0`, use
`WEBUI_URL=http://<instance-address>:8080`, keep both secure-cookie flags false,
and temporarily allow TCP 8080 in the security group. This is not suitable for
remote Voice Mode because browsers require HTTPS for microphone access.

For automatic English/Spanish recognition in Voice Mode, add:

```dotenv
WHISPER_MODEL=base
WHISPER_COMPUTE_TYPE=int8
WHISPER_MULTILINGUAL=false
```

Do not set `WHISPER_LANGUAGE`: an unset value makes faster-whisper detect the
language at the beginning of each utterance. The model name must not end in
`.en`, because those variants are English-only. A user's explicit
Speech-to-Text language selection still overrides automatic detection when the
server does not force `WHISPER_LANGUAGE`. Restart `lambda-webui` after changing
these values; the frontend and TTS service do not need to be restarted.

### Bedrock

For an EC2 instance role, enable Bedrock without adding access keys:

```dotenv
ENABLE_BEDROCK=true
AWS_REGION=us-east-1
BEDROCK_CONVERSE_MODEL_PREFIXES=ai21.jamba-,amazon.nova-,anthropic.claude-,cohere.command-,deepseek.,google.gemma-,meta.llama,minimax.,mistral.,moonshot.,nvidia.,openai.,qwen.,writer.palmyra-,xai.grok-,zai.glm-
```

The AWS SDK automatically uses the instance-role credential provider. Confirm
that the desired models are available in the configured region. `AWS_REGION` is
the only credential-related setting required at application startup. Leave
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` unset; empty
values are ignored before boto3 creates its sessions. If the instance has no IAM
role or another standard AWS credential provider, startup succeeds but model
discovery and invocation fail with an AWS credential error. The allowlist
filters discovery; it does not grant AWS permissions.

### Ollama (optional)

If Ollama runs on this same instance, keep it on loopback and use:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Install Ollama using its current Linux instructions, then pull only models that
fit the instance. Do not open port 11434 in the EC2 security group. For a remote
Ollama server, use its private VPC address and restrict that server's firewall
to the WebUI instance.

## 4. Install the managed local MCP runtime

Generate the shared bearer token:

```bash
sudo -u lambdawebui install -d -m 700 /home/lambdawebui/.config/open-webui
sudo -u lambdawebui bash -c \
  'openssl rand -hex 32 > /home/lambdawebui/.config/open-webui/mcp-runtime.token'
sudo chmod 600 /home/lambdawebui/.config/open-webui/mcp-runtime.token
```

Set these absolute paths in `/opt/lambda-webui/.env`:

```dotenv
MANAGED_MCP_RUNTIME_URL=http://127.0.0.1:9090
MANAGED_MCP_RUNTIME_TOKEN_FILE=/home/lambdawebui/.config/open-webui/mcp-runtime.token
MANAGED_MCP_RUNTIME_HOST=127.0.0.1
MANAGED_MCP_RUNTIME_PORT=9090
MANAGED_MCP_PACKAGE_ROOTS=/opt/lambda-webui/examples/managed-mcp
MANAGED_MCP_ALLOW_ROOT_RUNTIME=false
```

Edit `examples/managed-mcp/filesystem-tools/mcp.yaml` before discovery. Replace
both `/home/helvecio` values with the directory the Filesystem tool may access,
for example `/home/lambdawebui`. Keep that root as narrow as practical.

Pre-create the package environments:

```bash
sudo -u lambdawebui -H bash -lc '
  export PATH="$HOME/.local/bin:$PATH"
  cd /opt/lambda-webui/examples/managed-mcp/filesystem-tools && uv sync --frozen
  cd /opt/lambda-webui/examples/managed-mcp/calendar-tools && uv sync --frozen
'
```

## 5. Create systemd services

Create `/etc/systemd/system/lambda-webui.service`:

```ini
[Unit]
Description=Lambda WebUI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=lambdawebui
Group=lambdawebui
WorkingDirectory=/opt/lambda-webui/backend
EnvironmentFile=/opt/lambda-webui/.env
ExecStart=/opt/lambda-webui/backend/start.sh
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/lambda-webui-mcp.service`:

```ini
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
```

Start and verify both services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lambda-webui-mcp lambda-webui
sudo systemctl status lambda-webui lambda-webui-mcp --no-pager
curl --fail http://127.0.0.1:8080/health
sudo journalctl -u lambda-webui -u lambda-webui-mcp -n 100 --no-pager
```

## 6. Put HTTPS in front of the service

Use an Application Load Balancer, Caddy, nginx, or another reverse proxy to
terminate TLS and proxy to `http://127.0.0.1:8080`. Preserve WebSocket upgrades
and forwarded host/protocol headers. Set `WEBUI_URL` to the final HTTPS origin.
Only the proxy should listen publicly.

HTTPS is required for microphone access from another computer. After TLS works,
make sure the two secure-cookie flags are true and restart Lambda WebUI.

## 7. First login and MCP registration

1. Open the HTTPS URL and create the first account; it becomes the administrator.
2. After that account exists, verify signup and the default `pending` role under
   **Admin Panel → Settings → General**. These values are persisted in the DB.
3. Open **Admin Panel → Settings → Integrations**, select **Discover Services**,
   and add Local Calendar and Local System Tools.
4. Each user enables the desired services under **User Settings → Tools**.
5. Test “What is today's date?” and “List all appointments.”

## 8. Upgrades and backups

Before changing commits, stop both services and back up:

- `/opt/lambda-webui/.env` and `.webui-secret`
- `/opt/lambda-webui/backend/data/`
- `/opt/lambda-webui/examples/managed-mcp/calendar-tools/data/`
- `/home/lambdawebui/.config/open-webui/mcp-runtime.token`

Then update the checkout, rerun the Python dependency installation and
`npm ci && npm run build`, and restart both services. Do not treat an upstream
Open WebUI update notification as safe for this customized fork.

## Troubleshooting

```bash
sudo journalctl -u lambda-webui -f
sudo journalctl -u lambda-webui-mcp -f
ss -lntp | grep -E ':(8080|9090|8091|8880|11434)\b'
curl --fail http://127.0.0.1:8080/health
df -h / /opt /tmp
df -i / /opt /tmp
```

- A blank UI after an update usually means `npm run build` was not rerun.
- A microphone permission error on a remote client usually means the site is
  using HTTP or has an untrusted certificate.
- MCP services shown as installed but absent from model requests must also be
  enabled by that user under **User Settings → Tools**.
- Missing `ffprobe` can cause otherwise valid WAV uploads to fail before Whisper.
- A failure downloading `nvidia-cusparse`, `triton`, or another GPU-oriented
  wheel on a CPU instance means the requirements transaction did not constrain
  `torch` to the documented CPU build. Rerun the complete command above with the
  existing virtual environment; `uv` resumes safely and replaces the unsuitable
  resolution.
- Failures that move between legitimate packages such as `ctranslate2` and
  `av` usually indicate exhausted storage or an unreliable download, not several
  incompatible dependencies. Inspect the final `Caused by:` line in
  `/opt/lambda-webui/requirements-install.log` and check both blocks and inodes
  with the commands above.
- To recover space after a failed dependency attempt, run
  `sudo -u lambdawebui -H /home/lambdawebui/.local/bin/uv cache clean` and
  `sudo dnf clean all`. Removing `requirements-install.log` frees only a small
  text file; partial wheels in the uv cache are normally much larger. Cache and
  log deletion is permanent but does not remove application data.
- A 2 GiB `/tmp` mount is not enough reason to resize it: the documented
  `TMPDIR` and `UV_CACHE_DIR` move installation work to `/opt`. If `/opt` is on
  the same full root filesystem, expand the EBS volume instead.
- If the frontend build is killed, temporarily use a larger instance or add
  swap; do not leave a production host dependent on undersized swap-backed RAM.
- `JavaScript heap out of memory` while Vite reports a package such as
  `missing-exports-condition` identifies Node's memory ceiling, not a missing
  application export. Confirm that the build shell contains
  `NODE_OPTIONS=--max-old-space-size=8192` and that RAM plus swap has enough
  headroom for that heap and the operating system.

## Platform references

- [Amazon Linux 2023 package management](https://docs.aws.amazon.com/linux/al2023/ug/package-management.html)
- [Configure the Amazon Linux SPAL repository](https://docs.aws.amazon.com/linux/al2023/ug/configure-spal-repository.html)
- [Versioned Amazon Linux 2023 upgrades](https://docs.aws.amazon.com/linux/al2023/ug/updating.html)
- [Node.js packages in Amazon Linux 2023](https://docs.aws.amazon.com/linux/al2023/ug/nodejs.html)
- [EC2 IAM roles and instance profiles](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html)
- [EC2 security-group rule examples](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html)
- [`uv` installation](https://docs.astral.sh/uv/getting-started/installation/)
- [Ollama Linux installation](https://docs.ollama.com/linux)
