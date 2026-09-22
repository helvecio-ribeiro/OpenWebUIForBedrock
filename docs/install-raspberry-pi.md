# Install Lambda WebUI on Raspberry Pi

This guide installs this fork from source on Raspberry Pi OS 64-bit. A Raspberry
Pi 5 with 8 GiB RAM and USB 3/NVMe storage is recommended. A Pi 4 can host the
WebUI when inference is remote, but frontend builds, Whisper, and local models
will be slower. Avoid 32-bit Raspberry Pi OS: this project's Python ML wheels
and memory requirements make it an unsuitable target.

The most practical Pi deployment uses Amazon Bedrock, another remote provider,
or Ollama on a different computer. Small Ollama models can run locally, but that
is a separate capacity decision rather than a requirement for Lambda WebUI.

## 1. Install Raspberry Pi OS

Use Raspberry Pi Imager to write the current **Raspberry Pi OS Lite (64-bit)**
image. In Imager, configure the hostname, user, network, timezone, and SSH key
before writing the image. Boot the Pi, connect over SSH, and verify the
architecture:

```bash
uname -m
```

The expected result is `aarch64`. Use a high-endurance card at minimum; an SSD
is preferable because the WebUI and Local Calendar use persistent databases.

## 2. Install operating-system dependencies

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y \
  git curl ca-certificates build-essential libssl-dev libffi-dev \
  ffmpeg espeak-ng nodejs npm

node --version
npm --version
ffmpeg -version
ffprobe -version
```

This project supports Node.js 18.13 through 22.x. If the installed Node version
falls outside that range, stop here and install a supported Node.js release
before building. Do not use Node 23 or newer merely because it is available.

`ffmpeg` and `ffprobe` are needed for browser audio uploads. `espeak-ng` is an
optional dependency for speech features. On a headless Pi, browser-side Kokoro
or a remote OpenAI-compatible TTS service is generally simpler than running the
native Kokoro service locally.

## 3. Create the service account and install the source

```bash
sudo useradd --create-home --shell /bin/bash lambdawebui
sudo git clone https://github.com/helvecio-ribeiro/OpenWebUIForBedrock.git /opt/lambda-webui
sudo chown -R lambdawebui:lambdawebui /opt/lambda-webui
sudo -u lambdawebui git -C /opt/lambda-webui checkout <release-tag-or-commit>
```

Install `uv`, its managed Python 3.12 build, and the backend dependencies:

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

Installation downloads large ARM64 ML wheels and can take time. A dependency
with no `aarch64` wheel may try to compile locally; capture the exact failing
package rather than repeatedly rebooting or replacing the whole environment.

Build the frontend:

```bash
sudo -u lambdawebui -H bash -lc '
  cd /opt/lambda-webui
  npm ci
  npm run build
'
```

If the build is killed by the OOM killer, temporarily create swap or build the
same commit on another ARM64 Linux machine and transfer `build/`. Do not copy a
Python virtual environment between machines.

## 4. Configure Lambda WebUI

```bash
sudo -u lambdawebui cp /opt/lambda-webui/.env.example /opt/lambda-webui/.env
sudo chmod 600 /opt/lambda-webui/.env
openssl rand -hex 32 | sudo tee /opt/lambda-webui/.webui-secret >/dev/null
sudo chown lambdawebui:lambdawebui /opt/lambda-webui/.webui-secret
sudo chmod 600 /opt/lambda-webui/.webui-secret
```

Edit `/opt/lambda-webui/.env`:

```dotenv
WEBUI_NAME=Lambda WebUI
HOST=0.0.0.0
PORT=8080
WEBUI_URL=http://raspberrypi.local:8080
WEBUI_SECRET_KEY=<contents-of-/opt/lambda-webui/.webui-secret>

WEBUI_AUTH=true
ENABLE_LOGIN_FORM=true
ENABLE_SIGNUP=true
DEFAULT_USER_ROLE=pending
BYPASS_MODEL_ACCESS_CONTROL=true

WEBUI_SESSION_COOKIE_SECURE=false
WEBUI_AUTH_COOKIE_SECURE=false
FORWARDED_ALLOW_IPS=127.0.0.1
ENABLE_VERSION_UPDATE_CHECK=false

WHISPER_MODEL=tiny
WHISPER_COMPUTE_TYPE=int8
ENABLE_KOKORO_PRELOAD=false
```

Do not include angle brackets around the actual secret. `tiny` is a conservative
starting point for local transcription on a Pi; increase the model only after
measuring latency and memory. If HTTPS is added, change `WEBUI_URL`, set both
secure-cookie flags true, and preferably bind the application to `127.0.0.1`.

### Model provider

For Bedrock, add:

```dotenv
ENABLE_BEDROCK=true
AWS_REGION=us-east-1
BEDROCK_CONVERSE_MODEL_PREFIXES=ai21.jamba-,amazon.nova-,anthropic.claude-,cohere.command-,deepseek.,google.gemma-,meta.llama,minimax.,mistral.,moonshot.,nvidia.,openai.,qwen.,writer.palmyra-,xai.grok-,zai.glm-
```

`AWS_REGION` is the only credential-related setting required at application
startup. A Raspberry Pi normally has no instance profile, so configure a
standard boto3 credential provider or add a dedicated credential set:

```dotenv
AWS_ACCESS_KEY_ID=<dedicated-access-key>
AWS_SECRET_ACCESS_KEY=<dedicated-secret-key>
```

Use a dedicated least-privilege IAM principal and protect `.env`. Temporary
credentials also require `AWS_SESSION_TOKEN` and must be renewed. If Ollama runs
on another LAN host, set `OLLAMA_BASE_URL` to that private address and configure
Ollama's listener/firewall separately. Browsers never need direct Ollama access.

For local Ollama, install the ARM64 Linux build using Ollama's current install
guide, keep port 11434 off the public network, and begin with a small model:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

## 5. Install the managed local MCP runtime

```bash
sudo -u lambdawebui install -d -m 700 /home/lambdawebui/.config/open-webui
sudo -u lambdawebui bash -c \
  'openssl rand -hex 32 > /home/lambdawebui/.config/open-webui/mcp-runtime.token'
sudo chmod 600 /home/lambdawebui/.config/open-webui/mcp-runtime.token
```

Add or update these values in `/opt/lambda-webui/.env`:

```dotenv
MANAGED_MCP_RUNTIME_URL=http://127.0.0.1:9090
MANAGED_MCP_RUNTIME_TOKEN_FILE=/home/lambdawebui/.config/open-webui/mcp-runtime.token
MANAGED_MCP_RUNTIME_HOST=127.0.0.1
MANAGED_MCP_RUNTIME_PORT=9090
MANAGED_MCP_PACKAGE_ROOTS=/opt/lambda-webui/examples/managed-mcp
MANAGED_MCP_ALLOW_ROOT_RUNTIME=false
```

In `examples/managed-mcp/filesystem-tools/mcp.yaml`, replace both hard-coded
`/home/helvecio` paths with the narrow directory the MCP may access, such as
`/home/lambdawebui`. This does not grant sudo access.

Pre-create both MCP package environments:

```bash
sudo -u lambdawebui -H bash -lc '
  export PATH="$HOME/.local/bin:$PATH"
  cd /opt/lambda-webui/examples/managed-mcp/filesystem-tools && uv sync --frozen
  cd /opt/lambda-webui/examples/managed-mcp/calendar-tools && uv sync --frozen
'
```

## 6. Create systemd services

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

Enable and test:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lambda-webui-mcp lambda-webui
sudo systemctl status lambda-webui lambda-webui-mcp --no-pager
curl --fail http://127.0.0.1:8080/health
```

Open `http://<pi-address>:8080`, create the first administrator, discover the
services under **Admin Panel → Settings → Integrations**, and enable them per
user under **User Settings → Tools**.

## 7. HTTPS and Voice Mode

Browsers allow microphone capture on `localhost`, but a different computer
accessing the Pi requires a secure context. Put nginx, Caddy, or another reverse
proxy with a certificate trusted by the client in front of port 8080. Proxy
WebSocket upgrades as well as ordinary HTTP requests. Do not expose MCP port
9090, Calendar port 8091, TTS port 8880, or Ollama port 11434.

A self-signed certificate that the browser does not trust is not enough for a
reliable kiosk or Voice Mode. Use a real domain/certificate or install a local
CA root on every client. After HTTPS is active:

```dotenv
HOST=127.0.0.1
WEBUI_URL=https://lambda.example.net
WEBUI_SESSION_COOKIE_SECURE=true
WEBUI_AUTH_COOKIE_SECURE=true
```

Restart the service after editing `.env`.

### Automatic Whisper language detection

To accept English and Spanish Voice Mode input without a language toggle, use
the multilingual model and leave `WHISPER_LANGUAGE` unset:

```dotenv
WHISPER_MODEL=base
WHISPER_COMPUTE_TYPE=int8
WHISPER_MULTILINGUAL=false
```

Here `WHISPER_MULTILINGUAL=false` means one language detection at the beginning
of each recording; it does not make the model English-only. Avoid model names
ending in `.en`. Users can still force `en` or `es` in their Speech-to-Text
settings, which is useful for very short utterances that are difficult to
classify. Restart `lambda-webui` after changing these settings. No MCP, frontend,
or Kokoro restart is required.

## 8. Operations, backups, and Pi-specific checks

Back up these paths while both services are stopped:

- `/opt/lambda-webui/.env` and `.webui-secret`
- `/opt/lambda-webui/backend/data/`
- `/opt/lambda-webui/examples/managed-mcp/calendar-tools/data/`
- `/home/lambdawebui/.config/open-webui/mcp-runtime.token`

Useful diagnostics:

```bash
sudo journalctl -u lambda-webui -f
sudo journalctl -u lambda-webui-mcp -f
vcgencmd get_throttled
free -h
df -h
ss -lntp | grep -E ':(8080|9090|8091|8880|11434)\b'
```

- `get_throttled` values other than `0x0` indicate a present or historical
  power/thermal problem; use an adequate power supply and cooling.
- Repeated database or filesystem corruption points to storage or power loss,
  not an application setting. Prefer SSD storage and clean shutdowns.
- A blank UI after an update usually means the frontend was not rebuilt.
- If an MCP is installed but invisible to a model, verify that the user enabled
  it under **User Settings → Tools**.
- Missing `ffprobe` causes audio-format detection failures before Whisper runs.

## Platform references

- [Raspberry Pi setup and headless installation](https://www.raspberrypi.com/documentation/computers/getting-started.html)
- [Raspberry Pi OS editions and architectures](https://www.raspberrypi.com/documentation/computers/os.html)
- [`uv` installation](https://docs.astral.sh/uv/getting-started/installation/)
- [`uv` managed Python versions](https://docs.astral.sh/uv/guides/install-python/)
- [Ollama Linux installation](https://docs.ollama.com/linux)
