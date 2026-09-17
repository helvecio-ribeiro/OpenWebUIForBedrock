# Install Lambda WebUI on Amazon Linux 2023

This guide installs this fork from source on one Amazon EC2 instance. It uses a
production frontend build, a dedicated unprivileged service account, systemd,
and the managed local MCP runtime. The public browser connects only to the WebUI;
Ollama, MCP, Calendar, and TTS ports stay on loopback.

## 1. Choose and prepare the instance

Use the current **Amazon Linux 2023** AMI. For Bedrock or another remote model
provider, start with 2 vCPU, 4 GiB RAM, and at least 30 GiB of gp3 storage; 8 GiB
RAM makes the frontend build and local speech models more comfortable. Running
Ollama on the same host requires substantially more RAM or a suitable GPU and
should be sized for the chosen model.

Configure the EC2 security group as follows:

- TCP 22 from an administrator's IP only.
- TCP 80 and 443 from the intended clients.
- Do **not** expose 8080, 9090, 8091, 8880, or 11434.

If Bedrock will be used, attach an EC2 IAM role with only the required Bedrock
model-listing and invocation permissions. An instance role is preferable to
putting long-lived AWS access keys in `.env`.

Connect as `ec2-user`, update the instance, and install build/runtime packages:

```bash
sudo dnf update -y
sudo dnf install -y \
  git curl ca-certificates gcc gcc-c++ make openssl-devel libffi-devel \
  nodejs22 nodejs22-npm ffmpeg-free

node --version
npm --version
ffmpeg -version
ffprobe -version
```

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
```

Install `uv` and Python 3.12 for the service account, then create the backend
virtual environment:

```bash
sudo -u lambdawebui -H bash -lc \
  'curl -LsSf https://astral.sh/uv/install.sh | sh'

sudo -u lambdawebui -H bash -lc '
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.12
  uv venv --python 3.12 /opt/lambda-webui/backend/venv
  uv pip install --python /opt/lambda-webui/backend/venv/bin/python \
    -r /opt/lambda-webui/backend/requirements.txt
'
```

Build the frontend. `npm ci` uses the committed lockfile and is preferred over
`npm install` for a repeatable deployment.

```bash
sudo -u lambdawebui -H bash -lc '
  cd /opt/lambda-webui
  npm ci
  npm run build
'
```

The backend serves the generated `build/` directory; Node.js is not needed by
the running service after this step.

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

### Bedrock

For an EC2 instance role, enable Bedrock without adding access keys:

```dotenv
ENABLE_BEDROCK=true
AWS_REGION=us-east-1
BEDROCK_CONVERSE_MODEL_PREFIXES=ai21.jamba-,amazon.nova-,anthropic.claude-,cohere.command-,deepseek.,google.gemma-,meta.llama,minimax.,mistral.,moonshot.,nvidia.,openai.,qwen.,writer.palmyra-,xai.grok-,zai.glm-
```

The AWS SDK automatically uses the instance-role credential provider. Confirm
that the desired models are available in the configured region. The allowlist
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
```

- A blank UI after an update usually means `npm run build` was not rerun.
- A microphone permission error on a remote client usually means the site is
  using HTTP or has an untrusted certificate.
- MCP services shown as installed but absent from model requests must also be
  enabled by that user under **User Settings → Tools**.
- Missing `ffprobe` can cause otherwise valid WAV uploads to fail before Whisper.
- If the frontend build is killed, temporarily use a larger instance or add
  swap; do not leave a production host dependent on undersized swap-backed RAM.

## Platform references

- [Amazon Linux 2023 package management](https://docs.aws.amazon.com/linux/al2023/ug/package-management.html)
- [Node.js packages in Amazon Linux 2023](https://docs.aws.amazon.com/linux/al2023/ug/nodejs.html)
- [EC2 IAM roles and instance profiles](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html)
- [EC2 security-group rule examples](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html)
- [`uv` installation](https://docs.astral.sh/uv/getting-started/installation/)
- [Ollama Linux installation](https://docs.ollama.com/linux)
