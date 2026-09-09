# Open WebUI for AWS Bedrock

This repository is a project-specific fork of [Open WebUI](https://github.com/open-webui/open-webui) with native AWS Bedrock model discovery and chat support, plus a set of production-oriented Voice Mode improvements for local speech recognition and text-to-speech.

This fork is branded as **Lambda WebUI**. Its canonical vector mark is `static/branding/lambda-mark.svg`; run `node scripts/generate-lambda-branding.mjs` after editing it to regenerate the favicon, application icon, PWA icons, and light/dark splash assets.

It also adds a per-user **Browser** (internally called Web Panels): persistent browser-like tabs that replace the main Chat content while selected. Select **New Tab**, enter a public HTTP(S) address, and optionally rename it. Tabs support back, forward, reload, individual deletion, and Select/bulk deletion. Remote pages are fetched through a signed, panel-scoped backend proxy, their navigable resources are rewritten, and they run inside an origin-isolated sandbox. Selecting page text exposes **Explain Text**, **Find Bias**, and **Challenge Text** actions; **Summarize Page** is available from the same Panel Actions popup. These actions invoke the user's currently selected model and display the result over the page. Copying a selection-based result includes both the highlighted passage and the model's response. Private/local network destinations, embedded URL credentials, nonstandard ports, oversized responses, and non-HTTP protocols are rejected. Complex authentication, DRM, service workers, anti-bot systems, and JavaScript that depends strongly on the original origin may still be incompatible; the Browser is an integrated research surface, not a complete replacement for Chrome.

The upstream project provides the core chat application, frontend, Ollama integration, OpenAI-compatible providers, and general documentation. This fork adds Bedrock discovery and invocation through the Converse APIs. It also makes hands-free conversations more reliable by tightening microphone activation, making TTS playback deterministic, supporting centrally enforced TTS settings, and providing a low-latency local Kokoro deployment path.

For upstream features, configuration, and general troubleshooting, see the [Open WebUI repository](https://github.com/open-webui/open-webui) and [Open WebUI documentation](https://docs.openwebui.com/).

Because this repository contains changes that are not maintained as a directly upgradeable upstream fork, the example configuration disables Open WebUI release checks:

```dotenv
ENABLE_VERSION_UPDATE_CHECK=false
```

With this setting, the backend does not query GitHub for the latest upstream release, and the frontend does not show update notifications or links. Restart the Open WebUI backend after changing it. Set it to `true` only when deliberately tracking upstream releases and reviewing how an upgrade will interact with the local changes.

The integrated local MCP package/runtime architecture is recorded in [Managed MCP Runtime Design Decisions](docs/managed-mcp-runtime-design.md), with remaining work tracked separately in the [Managed MCP Runtime Implementation Backlog](docs/managed-mcp-runtime-todo.md). The runnable packages and their configuration are documented in the [Managed MCP Examples guide](examples/managed-mcp/README.md). Backend/runtime support, the management API, and the initial Admin discovery/Add/Remove UI are present; advanced lifecycle and access administration remain deferred.

### Intentional feature removals

This fork is not intended to remain directly upgrade-compatible with upstream Open WebUI. Two upstream feature areas were deliberately removed to keep the product focused and to avoid competing tool implementations:

| Upstream feature | State in this fork | Replacement |
| --- | --- | --- |
| Calendar | Native UI, API routes, models, database tables, permissions, flags, alerts, and built-in model tools removed. | The optional **Local Calendar** MCP owns a single shared SQLite calendar, MCP tools, and a loopback REST API. |
| Notes | Native UI, API routes, models, database tables, permissions, flags, collaboration code, and built-in model tools removed. | No replacement is currently provided. Use chats, files, knowledge collections, or add a purpose-built local MCP package if persistent notes become necessary. |

Alembic retains no-op markers for the historical revision IDs so an existing installation can still traverse the migration chain. Cleanup migrations permanently remove the former Calendar and Notes tables and configuration. They do not migrate old Calendar or Notes data; take a database backup before upgrading an installation that still contains data from either feature.

The removal is an architectural boundary, not merely hidden navigation. Models cannot accidentally receive both a native Calendar tool and the Local Calendar MCP tool, and disabling or removing the MCP leaves no fallback Calendar tool inside Open WebUI.

### Batch chat maintenance

The Chats section of the sidebar has a **Select** mode. In this mode, checkboxes replace normal chat navigation and the section header exposes archive and delete actions. Both operations accept up to 500 explicitly selected chats, verify ownership on the backend, refresh the sidebar, and leave selection mode after success.

Batch archive creates a ZIP under `DATA_DIR/archives/<user-id>/` before marking the chats archived. Each ZIP contains `manifest.json` plus one complete chat JSON document per selected chat. Chat metadata and messages are included; uploaded attachment binaries are not copied into the archive. Batch delete permanently removes the selected chats and requires confirmation.

### Managed MCP runtime

The runtime is a separate loopback service because it must own long-lived stdio sessions independently of chat requests and Uvicorn workers. Configure `MANAGED_MCP_RUNTIME_URL`, a shared runtime token or token file, and one or more colon-separated `MANAGED_MCP_PACKAGE_ROOTS` in `.env`. Do not expose port 9090 to the LAN.

The local integration path is:

```text
Browser
  -> Open WebUI backend (login, Admin API, user/group authorization)
  -> managed MCP runtime on 127.0.0.1:9090 (registry and process supervision)
  -> persistent stdio MCP package process (tool implementation and owned data)
```

Package directories are deployed beneath a configured package root rather than uploaded through the browser. Each package declares its identity, command, configuration, privilege profile, and allowed environment in `mcp.yaml`; `pyproject.toml` and `uv.lock` make the Python environment reproducible. The runtime starts it with `uv run --frozen`, maintains the stdio session, and exposes its discovered tools to Open WebUI through a private Streamable HTTP endpoint. The browser never launches the process, reads its environment, or receives the runtime token.

Generate a token file:

```bash
install -d -m 700 ~/.config/open-webui
openssl rand -hex 32 > ~/.config/open-webui/mcp-runtime.token
chmod 600 ~/.config/open-webui/mcp-runtime.token
```

The file contains only the generated token, with no variable name or quotes. Then add the following to the repository's `.env`, replacing `/home/user` with the account's real home directory:

```dotenv
MANAGED_MCP_RUNTIME_URL=http://127.0.0.1:9090
MANAGED_MCP_RUNTIME_TOKEN_FILE=/home/user/.config/open-webui/mcp-runtime.token
MANAGED_MCP_RUNTIME_HOST=127.0.0.1
MANAGED_MCP_RUNTIME_PORT=9090
MANAGED_MCP_PACKAGE_ROOTS=/home/user/open-webui/examples/managed-mcp
MANAGED_MCP_ALLOW_ROOT_RUNTIME=false
```

The runtime and Open WebUI backend must both be restarted after changing `.env`. Start the runtime from the repository environment:

```bash
PYTHONPATH=backend backend/venv/bin/python -m open_webui.mcp_runtime.app
```

The optional [`scripts/systemd/open-webui-mcp-runtime.service`](scripts/systemd/open-webui-mcp-runtime.service) user unit assumes the repository is at `~/open-webui`. Copy it to `~/.config/systemd/user/`, then run `systemctl --user daemon-reload` and `systemctl --user enable --now open-webui-mcp-runtime`.

Local registration is available under **Admin Panel -> Settings -> Integrations -> Local MCP Services**. Select **Discover Services** to scan the immediate child directories of every `MANAGED_MCP_PACKAGE_ROOTS` entry. Valid unregistered `confined` packages can be added with one click; registered packages can be removed with confirmation; ID conflicts, invalid manifests, and packages requiring privileged setup are shown separately. Removal stops the managed process and deletes its registry entry, but leaves the package files available for later rediscovery. Discovery happens on the server and the browser never receives the runtime token or connects directly to port 9090.

Registration is also available through the backend API. Endpoints under `/api/v1/managed-mcp` require an Open WebUI administrator session. `GET /api/v1/managed-mcp/discover` returns the same catalogue used by the Admin UI. For example, POST a package definition to `/api/v1/managed-mcp/`:

```json
{
	"package_path": "/home/user/open-webui/examples/managed-mcp/filesystem-tools",
	"environment": {
		"MCP_LOCAL_TIMEZONE": "America/Mexico_City",
		"MCP_FILESYSTEM_ROOT": "/home/user"
	},
	"access_grants": [],
	"enabled": true
}
```

An empty grant list makes the server administrator-only. User and group grants use the existing `principal_type`, `principal_id`, and `permission: read` structure. Runtime state is stored atomically in `backend/data/managed-mcp/registry.json` by default. Copy that file and the registered package directories to back up definitions.

The **Local Calendar** managed MCP owns its shared SQLite repository and loopback REST API; Open WebUI no longer contains native Calendar models, routes, permissions, flags, alerts, or tool definitions. **Local System Tools** supplies current date/time and confined filesystem operations. These packages are independent: enabling one does not enable the other, and each must be discovered and registered separately. Users enable registered tools from the chat integrations menu; that selection is persisted for subsequent chats. Setup, persistence, and tool behavior are documented in the [Managed MCP Examples guide](examples/managed-mcp/README.md).

The runtime exposes health at `/healthz`, authenticated readiness at `/readyz`, and authenticated management under `/api/servers`. Open WebUI connects to each ready server at `/mcp/{server-id}` using Streamable HTTP. Failed initialization leaves a server unavailable and exposes bounded stderr through its admin-only logs endpoint.

Whole-system Filesystem access is deliberately separate. A `system-admin` manifest is accepted only when its ID, canonical package path, digest, and `system-read` capability match the root-owned privilege policy. The runtime must also be started as root with `MANAGED_MCP_ALLOW_ROOT_RUNTIME=true`; manifests cannot elevate themselves. When that runtime also hosts confined servers, `MANAGED_MCP_UNPRIVILEGED_UID` and `MANAGED_MCP_UNPRIVILEGED_GID` are mandatory so their child processes drop privileges. The example [privilege policy](examples/managed-mcp/privilege-policy.example.json) and [privileged systemd unit](scripts/systemd/open-webui-mcp-runtime-privileged.service) assume an installation at `/opt/open-webui` and must be adapted explicitly. The supplied unit keeps the host filesystem read-only. Root writes require both a `system-write` policy capability and removal or narrowing of systemd's `ProtectSystem=strict`; do not enable them merely to test discovery.

![GitHub stars](https://img.shields.io/github/stars/open-webui/open-webui?style=social)
![GitHub forks](https://img.shields.io/github/forks/open-webui/open-webui?style=social)
![GitHub watchers](https://img.shields.io/github/watchers/open-webui/open-webui?style=social)
![GitHub repo size](https://img.shields.io/github/repo-size/open-webui/open-webui)
![GitHub language count](https://img.shields.io/github/languages/count/open-webui/open-webui)
![GitHub top language](https://img.shields.io/github/languages/top/open-webui/open-webui)
![GitHub last commit](https://img.shields.io/github/last-commit/open-webui/open-webui?color=red)
[![Discord](https://img.shields.io/badge/Discord-Open_WebUI-blue?logo=discord&logoColor=white)](https://discord.gg/5rJgQTnV4s)
[![](https://img.shields.io/static/v1?label=Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86)](https://github.com/sponsors/open-webui)

![Open WebUI Banner](./banner.png)

**Open WebUI is an [extensible](https://docs.openwebui.com/features/extensibility/plugin), feature-rich, and user-friendly self-hosted AI platform designed to operate entirely offline.** It supports various LLM runners like **Ollama** and **OpenAI-compatible APIs**, with **built-in inference engine** for RAG, making it a **powerful AI deployment solution**.

Passionate about open-source AI? [Join our team →](https://careers.openwebui.com/)

![Open WebUI Demo](./demo.png)

> [!TIP]  
> **Looking for an [Enterprise Plan](https://docs.openwebui.com/enterprise)?** – **[Speak with Our Sales Team Today!](https://docs.openwebui.com/enterprise)**
>
> Get **enhanced capabilities**, including **custom theming and branding**, **Service Level Agreement (SLA) support**, **Long-Term Support (LTS) versions**, and **more!**

For more information, be sure to check out our [Open WebUI Documentation](https://docs.openwebui.com/).

## Key Features of Open WebUI ⭐

- 🚀 **Source-First Setup**: Run this fork directly from a Python virtual environment and the SvelteKit development toolchain.

- 🤝 **Broad Model & API Integration**: Connect any OpenAI-compatible API alongside local Ollama models. Point the API URL at **LMStudio, GroqCloud, Mistral, OpenRouter, vLLM, and more** to mix and match providers freely.

- 🔐 **Granular RBAC & User Groups**: Administrators define detailed roles, groups, and permissions, giving each user exactly the access they need. Secure by default, with tailored experiences per group.

- 🧩 **Plugin Support**: Extend Open WebUI with **Filters**, **Actions**, **Pipes**, **Tools**, and **Skills**. Connect external services through **MCP**, **MCPO**, and **OpenAPI tool servers**. Build custom integrations, rate limits, approval flows, data connections, and more.

- 🤖 **Models & Agents**: Wrap any base model with custom instructions, tools, and knowledge to build specialized agents. Supports dynamic variables, per-user/group access control, and community preset imports via [Open WebUI Community](https://openwebui.com/).


- 📢 **Channels**: Real-time shared spaces where your team and AI models collaborate in one timeline. Tag models to draft or critique, with threads, reactions, pins, and access control.

- 🧠 **Persistent Memory**: The AI remembers facts about you across conversations, carrying context from one chat to the next.

- ✅ **Live Workflow & Message Flow**: Watch the AI build and work through checklists in real time. Queue messages while the AI is still responding; they send automatically when it's ready.

- 📅 **Shared Calendar MCP**: A locally managed, single-calendar service with its own SQLite repository, REST API, and model tools for searching, creating, updating, and safely deleting events.

- ⏱️ **Automations**: Schedule prompts to run on recurring schedules, with completed runs linking back to the chat they produced.

- 📱 **Responsive Design & PWA**: Seamless experience across desktop, laptop, and mobile, with a Progressive Web App for native app-like feel and offline access on localhost.

- ✒️🔢 **Full Markdown and LaTeX Support**: Comprehensive Markdown and LaTeX capabilities for enriched interaction.

- 🎤📹 **Hands-Free Voice/Video Call**: Integrated voice and video calls with multiple Speech-to-Text providers (Local Whisper, OpenAI, Deepgram, Azure) and Text-to-Speech engines (Azure, ElevenLabs, OpenAI, Transformers, WebAPI).

- 💾 **Persistent Artifact Storage**: Built-in key-value storage API for artifacts, enabling journals, trackers, leaderboards, and collaborative tools with personal and shared data scopes.

- 📚 **Local RAG Integration**: Retrieval Augmented Generation backed by 9 vector databases and multiple content-extraction engines (Tika, Docling, Document Intelligence, Mistral OCR, PaddleOCR-vl, external loaders). Supports hybrid search (BM25 + vector) with reranking and full-context mode. Load documents into chat or pull them from your library with the `#` command.

- 🔍 **Web Search for RAG**: Search the web through dozens of providers including `SearXNG`, `Google PSE`, `Brave Search`, `Kagi`, `Mojeek`, `Tavily`, `Perplexity`, `Firecrawl`, `serpstack`, `serper`, `Serply`, `DuckDuckGo`, `SearchApi`, `SerpApi`, `Bing`, `Jina`, `Exa`, `Sougou`, `Azure AI Search`, and `Ollama Cloud`, injecting results directly into the conversation.

- 🌐 **Web Browsing Capability**: Pull websites into chat with the `#` command followed by a URL, or let the model fetch them on its own when needed.

- 🎨 **Image Generation & Editing**: Create and edit images with multiple engines including OpenAI DALL·E, Gemini, ComfyUI (local), and AUTOMATIC1111 (local), supporting both generation and prompt-based editing.

- ⚙️ **Multi-Model Conversations**: Engage several models at once, harnessing their individual strengths in parallel for the best possible responses.

- 📊 **Usage Analytics & Model Evaluation**: Admin dashboards track message volume, token consumption, and cost across users and models. Evaluate models with a built-in arena, A/B testing, and ELO-based leaderboards.

- 🗄️ **Flexible Database & Storage**: Choose SQLite (with optional encryption) or PostgreSQL, and store files locally or on S3, Google Cloud Storage, or Azure Blob Storage.

- 🧬 **Advanced Vector Database Support**: Pick from 9 vector databases: ChromaDB, PGVector, Qdrant, Milvus, Elasticsearch, OpenSearch, Pinecone, S3Vector, and Oracle 23ai.

- 🪪 **Enterprise Authentication & Provisioning**: Full LDAP/Active Directory integration, SSO via trusted headers and OAuth providers, and SCIM 2.0 automated provisioning for identity providers like Okta, Azure AD, and Google Workspace.

- ☁️ **Cloud-Native File Integration**: Native Google Drive and OneDrive/SharePoint file picking for seamless document import from enterprise cloud storage.

- 🔭 **Production Observability**: Built-in OpenTelemetry support for traces, metrics, and logs, plugging into your existing monitoring stack.

- ⚖️ **Horizontal Scalability**: Redis-backed session management and WebSocket support for multi-worker, multi-node deployments behind load balancers.

- 🌐🌍 **Multilingual Support**: Use Open WebUI in your preferred language with i18n support. We're actively seeking contributors to expand language coverage!

- 🌟 **Continuous Updates**: We're committed to improving Open WebUI with regular updates, fixes, and new features.

- 🛡️ **Transparent Security Process**: Security reports are triaged, fixed, and published as open advisories through a documented responsible-disclosure process. See our [Security Policy](https://github.com/open-webui/open-webui/security).

Want to learn more about Open WebUI's features? Check out our [Open WebUI documentation](https://docs.openwebui.com/features) for a comprehensive overview!

## The Open WebUI Ecosystem 🌐

Open WebUI is the core, surrounded by companion apps and infrastructure that extend what your AI can do, where it can reach, and how you run it:

- 💻 **Open WebUI Computer** ([open-webui/computer](https://github.com/open-webui/computer)): A standalone, mobile-first computer and coding agent that runs on the machine you own. Files, terminal, and git in a browser tab, reachable from your phone. Connect it into Open WebUI as a model, or reach it from Telegram, WhatsApp, and more.

- ⚡ **Open Terminal** and **Terminals (Enterprise)** ([open-webui/open-terminal](https://github.com/open-webui/open-terminal) & [open-webui/terminals](https://github.com/open-webui/terminals)): A self-hosted computing environment that plugs into Open WebUI, giving the AI a place to write code, run it, read output, fix errors, and iterate inside the chat.

- 🔄 **oikb** ([open-webui/oikb](https://github.com/open-webui/oikb)): Feed your Knowledge Bases from 45+ sources (GitHub, Confluence, ServiceNow, Salesforce, Jira, Slack, SharePoint, Notion, and more), keeping the tools your team already uses continuously in sync.

- 🖥️ **Native Desktop App** ([open-webui/desktop](https://github.com/open-webui/desktop)): Run Open WebUI as a native app on macOS, Windows, and Linux. System-wide Spotlight chat bar with screenshot capture, push-to-talk voice, and optional fully-local inference via a built-in llama.cpp engine.

Want to learn more? Check out our [Open WebUI documentation](https://docs.openwebui.com) for more details!

---

We are incredibly grateful for the generous support of our sponsors. Their contributions help us to maintain and improve our project, ensuring we can continue to deliver quality work to our community. Thank you!

## Run From Source 🚀

### Requirements

- Linux or macOS
- Python 3.11 or 3.12
- Node.js 18.13 through 22.x
- npm

On Ubuntu, install the runtime tools used by local audio processing and the native Kokoro service:

```bash
sudo apt update
sudo apt install -y ffmpeg espeak-ng git curl
```

`ffmpeg` supplies both `ffmpeg` and `ffprobe`. Without `ffprobe`, uploaded browser recordings may fail format detection before they reach Whisper. Microphone capture requires a secure browser context: use `localhost` during local development and HTTPS when accessing Open WebUI from another machine.

### Backend setup

From the repository root:

```bash
cd backend
python3.11 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
```

If the repository already contains `backend/venv`, reuse it instead of creating another environment.

### Frontend setup

In a second terminal, from the repository root:

```bash
npm install
```

### AWS Bedrock configuration

Bedrock support is disabled by default. Add the following to `.env` in the repository root:

```env
ENABLE_BEDROCK=true
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key-id
AWS_SECRET_ACCESS_KEY=your-secret-access-key
BEDROCK_CONVERSE_MODEL_PREFIXES=ai21.jamba-,amazon.nova-,anthropic.claude-,cohere.command-,deepseek.,google.gemma-,meta.llama,minimax.,mistral.,moonshot.,nvidia.,openai.,qwen.,writer.palmyra-,xai.grok-,zai.glm-
```

For temporary AWS credentials, also add:

```env
AWS_SESSION_TOKEN=your-session-token
```

Only text-chat models compatible with this integration's Bedrock Converse API
are shown. Discovery requires both `TEXT` input and `TEXT` output, then checks
the foundation model ID against the comma-separated
`BEDROCK_CONVERSE_MODEL_PREFIXES` allowlist.

The default allowlist currently covers these Bedrock model families:

| Provider    | Model ID prefix     |
| ----------- | ------------------- |
| AI21 Labs   | `ai21.jamba-`       |
| Amazon      | `amazon.nova-`      |
| Anthropic   | `anthropic.claude-` |
| Cohere      | `cohere.command-`   |
| DeepSeek    | `deepseek.`         |
| Google      | `google.gemma-`     |
| Meta        | `meta.llama`        |
| MiniMax     | `minimax.`          |
| Mistral AI  | `mistral.`          |
| Moonshot AI | `moonshot.`         |
| NVIDIA      | `nvidia.`           |
| OpenAI      | `openai.`           |
| Qwen        | `qwen.`             |
| Writer      | `writer.palmyra-`   |
| xAI         | `xai.grok-`         |
| Z.AI        | `zai.glm-`          |

Inference profiles are shown only when at least one of their referenced
foundation models has text input and output and matches the same allowlist. To
add a newly supported model or restrict the selector further, set the complete
replacement list in `.env`:

```env
BEDROCK_CONVERSE_MODEL_PREFIXES=amazon.nova-,anthropic.claude-
```

The environment variable replaces the complete default list; it does not append
to it. Entries are matched against the beginning of the AWS foundation model
ID, are case-sensitive, and should not include the `bedrock:` prefix used by
Open WebUI. Whitespace around comma-separated entries is ignored. An empty value
hides all Bedrock models.

AWS does not return Converse compatibility in `ListFoundationModels`. Before
adding a prefix, confirm that the model supports `Converse` or `ConverseStream`
in AWS's model/API compatibility documentation. A prefix may cover non-chat
models from the same family, but the required text input/output checks keep
those models out of the selector.

The backend loads these values at startup and passes them explicitly to boto3. The access key and secret must belong to the same active AWS credential set. The AWS identity needs permission to list models and inference profiles:

```json
{
	"Effect": "Allow",
	"Action": [
		"bedrock:ListFoundationModels",
		"bedrock:ListInferenceProfiles",
		"bedrock:InvokeModel",
		"bedrock:InvokeModelWithResponseStream"
	],
	"Resource": "*"
}
```

The corresponding Bedrock models must also be enabled for the AWS account and region. Long-lived keys should not be committed to `.env`; use an IAM role or another secure credential provider for production.

Selected Open WebUI tools, including managed MCP servers, are translated to Bedrock Converse `toolConfig`. Bedrock `toolUse` responses and subsequent tool results are translated back to OpenAI-compatible tool-call messages for the existing Open WebUI execution loop. Tool selection takes effect on the next message in the current chat; starting a new chat is not required. For tool-enabled Amazon Nova requests, the adapter removes unsupported top-level JSON Schema metadata, maps namespaced tool names to Nova-safe underscore names and back, and applies AWS's recommended greedy-decoding settings (`temperature=0`, `topK=1`) to avoid malformed ToolUse sequences; ordinary Nova chat parameters are unchanged.

### Voice Mode improvements in this fork

The upstream Voice Mode implementation has been adapted for long-running, hands-free local conversations:

- Microphone input uses an adaptive ambient-noise floor and requires sustained speech-level energy before submitting audio. A short grace period after reopening the microphone prevents playback tails and keyboard transients from triggering a new turn.
- A rolling pre-roll preserves the beginning of real speech while activation is being confirmed. The browser recording's original container header, MIME type, and filename extension are retained so WebM/Opus input is not incorrectly submitted as WAV.
- Silence detection ends a confirmed utterance promptly, while hysteresis prevents normal variations in speaking volume from chopping it prematurely.
- The microphone is restored after every model response, including TTS synthesis or playback failure, so a failed audio response cannot leave the call stuck waiting.
- Synthesized sentences are produced and played through a promise-based FIFO pipeline. Each item is played once and awaited to completion rather than repeatedly polling and re-enqueuing cache entries.
- Voice Mode playback is isolated from the normal message audio queue, preventing unrelated queue state from truncating or replacing the active response.
- During TTS playback, the waiting dots are replaced with a five-bar waveform driven by the actual audio signal, with the tallest bars centered.
- Rich display text and speech text are separated. Completed assistant messages persist a `speechContent` projection, and both Voice Mode and manual read-aloud prefer it while the UI retains the original Markdown. The projection removes non-speech code/details, converts headings and lists into sentences, and rewrites common structured fields such as `Date`, `Time`, and `Location` into natural spoken phrases. Older messages without the field are projected when played.
- A complete utterance of “exit”, “exit voice mode”, “close voice mode”, “end voice conversation”, or “stop listening” is handled locally as a Voice Mode control command. It is not sent to the model: microphone activation is muted, the client speaks a deterministic “Goodbye,” and Voice Mode closes only after that TTS playback finishes. Longer sentences that merely contain those words do not trigger exit.

For low-latency local speech recognition, the example environment uses Whisper `base`, English-only transcription, greedy decoding, and `int8` computation:

```env
WHISPER_MODEL=base
WHISPER_COMPUTE_TYPE=int8
WHISPER_LANGUAGE=en
```

Remove `WHISPER_LANGUAGE` when automatic language detection is required. These defaults favor conversational latency over maximum transcription accuracy; use a larger model if accuracy is more important than response time.

### Preload browser Kokoro TTS

Kokoro.js normally downloads and initializes when a user first selects it. To initialize it and warm the configured voice with a short synthesis in each authenticated browser as soon as the UI starts, set:

```env
ENABLE_KOKORO_PRELOAD=true
KOKORO_PRELOAD_DTYPE=q8
KOKORO_DEFAULT_VOICE=bf_emma
KOKORO_DEVICE=wasm
```

Supported dtypes are `fp32`, `fp16`, `q8`, `q4`, and `q4f16`; invalid values fall back to `q8`. When preloading is enabled, browser bootstrap treats these environment settings as authoritative: it overwrites the user's runtime TTS engine, dtype, and voice with `browser-kokoro`, `KOKORO_PRELOAD_DTYPE`, and `KOKORO_DEFAULT_VOICE`. `bf_emma` is a British female voice. `KOKORO_DEVICE` accepts `auto`, `webgpu`, or `wasm`; use `wasm` on machines where Chrome reports no WebGPU adapter. The model runs and is cached in each browser, so this setting does not download it into the backend at server startup. In `auto` mode, WebGPU is used only when Chrome reports a usable adapter, with an automatic WASM fallback.

### Local Kokoro TTS service

For lower and more consistent latency, run Kokoro-FastAPI locally and use its OpenAI-compatible API. The included CPU service binds only to localhost:

```bash
docker compose -f docker-compose.kokoro.yaml up -d
```

Configure a host-run Open WebUI backend with:

```env
ENABLE_KOKORO_PRELOAD=false
FORCE_AUDIO_TTS_CONFIG=true
AUDIO_TTS_ENGINE=openai
AUDIO_TTS_OPENAI_API_BASE_URL=http://127.0.0.1:8880/v1
AUDIO_TTS_OPENAI_API_KEY=not-needed
AUDIO_TTS_MODEL=kokoro
AUDIO_TTS_VOICE=bf_emma
AUDIO_TTS_OPENAI_PARAMS='{"response_format":"mp3","speed":1.0}'
```

`FORCE_AUDIO_TTS_CONFIG=true` makes these server-side settings authoritative over persisted administrator and user TTS selections. If Open WebUI also runs in Compose, attach both services to the same Compose network and use `http://kokoro-tts:8880/v1` instead. Set `KOKORO_FASTAPI_TAG` to a tested release tag rather than relying on `latest` for a stable deployment. The first service start downloads or initializes its model; wait for readiness before testing Voice Mode.

#### Native Kokoro installation with `uv`

Install `uv`, clone Kokoro-FastAPI into the location expected by the supplied service, synchronize its CPU dependencies, and download the model weights. Voice files are included in the Kokoro-FastAPI checkout:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
mkdir -p ~/.local/share
git clone https://github.com/remsky/Kokoro-FastAPI.git ~/.local/share/kokoro-fastapi
cd ~/.local/share/kokoro-fastapi
uv sync --extra cpu
uv run python docker/scripts/download_model.py --output api/src/models/v1_0
```

For a repeatable installation, check out a tested Kokoro-FastAPI release or commit before running `uv sync`; tracking its default branch can introduce unreviewed dependency or API changes. The service template assumes `uv` is installed at `~/.local/bin/uv`.

Install and start the user service:

```bash
mkdir -p ~/.config/systemd/user
cd /path/to/OpenWebUIForBedrock
cp scripts/systemd/kokoro-fastapi.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kokoro-fastapi
```

User services normally stop when the user has no active login session. For a kiosk or unattended host that must start Kokoro at boot, enable lingering for the service account once:

```bash
sudo loginctl enable-linger "$USER"
```

#### Verify Kokoro

Check service status and recent logs:

```bash
systemctl --user status kokoro-fastapi
journalctl --user -u kokoro-fastapi -n 100 --no-pager
```

Verify that `bf_emma` is installed and synthesize a playable sample:

```bash
curl -fsS http://127.0.0.1:8880/v1/audio/voices
curl -fsS http://127.0.0.1:8880/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","voice":"bf_emma","input":"Kokoro is ready.","response_format":"mp3","speed":1}' \
  -o /tmp/kokoro-smoke-test.mp3
ffplay -nodisp -autoexit /tmp/kokoro-smoke-test.mp3
```

The first synthesis can be slower because it loads and warms the model. Compare warm requests only when evaluating conversational latency.

### Audio configuration precedence

The fork applies audio settings in this order:

| Priority | Condition                     | Effective behavior                                                                                                        |
| -------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| 1        | `FORCE_AUDIO_TTS_CONFIG=true` | The backend `AUDIO_TTS_*` values override administrator configuration, user settings, and model-specific voice selection. |
| 2        | `ENABLE_KOKORO_PRELOAD=true`  | The browser uses Kokoro.js with the configured dtype, device, and default voice.                                          |
| 3        | Neither enabled               | Normal Open WebUI administrator, user, and model settings apply.                                                          |

Do not enable browser preload when forcing the local API unless browser Kokoro is deliberately required as a separate option. Environment changes are read when the backend starts; restart the backend and hard-refresh authenticated browser sessions after changing them.

### LAN access and service topology

To serve Open WebUI to multiple authenticated users on a trusted LAN, bind the application to all host interfaces, advertise its LAN URL, and allow new accounts to register pending administrator approval. For a server at `192.168.68.90`:

```env
HOST=0.0.0.0
PORT=8080
WEBUI_URL=http://192.168.68.90:8080

WEBUI_AUTH=true
ENABLE_SIGNUP=true
ENABLE_SIGNUP_PASSWORD_CONFIRMATION=true
DEFAULT_USER_ROLE=pending
BYPASS_MODEL_ACCESS_CONTROL=true

CORS_ALLOW_ORIGIN='http://localhost:5173;http://localhost:8080;http://192.168.68.90:5173;http://192.168.68.90:8080'
```

Users can then open `http://192.168.68.90:8080`, create their own accounts, and wait for an administrator to approve them. Use `DEFAULT_USER_ROLE=user` only when every person who can reach the registration page should receive immediate access. Never use `admin` as the default role.

Provider-discovered Bedrock, Ollama, and OpenAI-compatible models do not automatically have database access-grant records. With the default model access control enabled, an administrator can see these unconfigured provider models but an ordinary user can receive an empty model list. `BYPASS_MODEL_ACCESS_CONTROL=true` intentionally makes every discovered model available to every approved user and is the supported configuration for shared provider discovery in this fork. The upstream Workspace UI was removed, so its **Workspace → Models** flow is not available for creating per-model grants. Do not enable the bypass when approved users must be isolated from one another's configured providers.

On an existing installation, `ENABLE_SIGNUP` and `DEFAULT_USER_ROLE` may already be persisted in the database. Open WebUI automatically changes `ui.enable_signup` to `false` after the first administrator account is created, so adding `ENABLE_SIGNUP=true` to `.env` does not necessarily re-enable registration. From an already authenticated administrator session, open **Admin Panel → Settings → General**, enable **New User Signups**, keep the default role set to **Pending**, and save. New browsers will still be redirected to `/auth?redirect=%2F`; that redirect is normal. Once signup is enabled, the page also offers **Sign up**, and an administrator can approve new accounts under **Admin Panel → Users**.

Open WebUI proxies both Ollama and server-side TTS requests. Remote browsers normally need access only to Open WebUI on port 8080; they do not need direct access to ports 11434 or 8880.

To address Ollama through the host's LAN address, configure:

```env
OLLAMA_BASE_URL=http://192.168.68.90:11434
```

This setting tells Open WebUI where to find Ollama; it does not change Ollama's listener. Ollama must separately listen on that address, commonly by setting `OLLAMA_HOST=0.0.0.0:11434` in the Ollama service environment and restarting it. If Ollama remains loopback-only on the same machine as Open WebUI, use `OLLAMA_BASE_URL=http://127.0.0.1:11434` instead. Do not expose port 11434 merely for remote Open WebUI users—the backend proxy is sufficient and avoids exposing an unauthenticated Ollama API to the LAN.

Keep a host-local Kokoro service configured as:

```env
AUDIO_TTS_OPENAI_API_BASE_URL=http://127.0.0.1:8880/v1
```

The Open WebUI backend calls Kokoro on behalf of every browser. Kokoro should remain bound to `127.0.0.1` unless another host genuinely needs its API; its `not-needed` API key provides no authentication.

After changing these values, restart the Open WebUI backend. Text chat can work over the plain HTTP LAN URL, but remote microphone capture generally cannot: browsers treat `localhost` as a special secure context but require HTTPS for media APIs on addresses such as `192.168.68.90`. Put Open WebUI behind an HTTPS reverse proxy before relying on Voice Mode from another computer.

### Voice Mode and kiosk operation

Voice Mode works in a kiosk, but unattended browser startup has requirements outside the application:

- This fork does not automatically authenticate a user or enter Voice Mode. The kiosk launcher or operator must still reach the call overlay before hands-free turn-taking begins.
- Grant the site persistent microphone permission in the Chrome profile used by the kiosk.
- Allow audio autoplay. Browsers may otherwise require one user gesture before the first response can play.
- Serve remote kiosk clients over HTTPS; browser media APIs are unavailable on an insecure non-localhost origin.
- Keep the same persistent Chrome profile between launches so permissions and the browser Kokoro cache survive restarts.
- If the browser and backend use different origins, explicitly configure the kiosk origin in `CORS_ALLOW_ORIGIN`.
- Prevent the operating system from suspending the kiosk or its audio devices during an active installation.

Chrome enterprise policies are preferable to broad command-line flags for a managed unattended kiosk. At minimum, configure policies for microphone access and autoplay for only the deployed Open WebUI origin. A visible page does not guarantee audio permission: validate one complete speech-to-text, model, and text-to-speech turn after every browser or policy update.

### Voice activation tuning

The current Voice Mode behavior is implemented with these fixed values:

| Setting                |  Value | Purpose                                                                             |
| ---------------------- | -----: | ----------------------------------------------------------------------------------- |
| Listening grace period | 450 ms | Ignores playback tails and device noise immediately after reopening the microphone. |
| Speech confirmation    | 220 ms | Rejects short keyboard clicks and other transients.                                 |
| Silence timeout        | 900 ms | Submits a confirmed utterance after speech stops.                                   |
| Minimum speech RMS     |  0.018 | Establishes a floor below which input is not treated as speech.                     |
| Noise multiplier       |   2.8x | Requires speech to exceed the measured ambient-noise floor.                         |

These values are currently source constants rather than environment settings. Quiet speakers or distant microphones may need a lower RMS floor; noisy rooms may need a higher threshold or multiplier. Test changes with both real speech and representative keyboard noise, and preserve the rolling pre-roll so speech confirmation does not remove the first word.

### Audio troubleshooting

| Symptom                                    | Likely cause and action                                                                                                                                                   |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Invalid data found when processing input` | The recording is malformed or mislabeled. Confirm the current frontend is loaded; this fork preserves Chrome's WebM/Opus container header and uploads its real MIME type. |
| `ffprobe: No such file or directory`       | Install the Ubuntu `ffmpeg` package and restart the backend.                                                                                                              |
| Default system or US voice plays           | Inspect the backend configuration response and confirm `FORCE_AUDIO_TTS_CONFIG`, `AUDIO_TTS_ENGINE=openai`, and `AUDIO_TTS_VOICE=bf_emma` were loaded at backend startup. |
| Kokoro.js remains on “Loading”             | Check browser network errors, storage quota, WebGPU support, and model-download access. Set `KOKORO_DEVICE=wasm` to bypass WebGPU probing.                                |
| Voice Mode remains on waiting dots         | Check the browser console and backend logs for a failed TTS request or stale frontend bundle, then hard-refresh.                                                          |
| Speech playback ends early                 | Look for microphone interruption or overlapping playback. This fork serializes TTS through a per-message FIFO and waits for each audio element to finish.                 |
| Keyboard noise starts a turn               | Check microphone gain and placement. The adaptive detector rejects transients, but repeated or sustained mechanical noise can still resemble speech.                      |
| No microphone input in a remote kiosk      | Use HTTPS and confirm the kiosk profile has permission for the exact deployed origin.                                                                                     |

### Performance and capacity notes

- Whisper `base` with `int8` computation favors response time and modest CPU/RAM use over maximum accuracy.
- Browser Kokoro has a substantial first-load download and initialization cost per browser profile but can use WebGPU when available.
- Kokoro-FastAPI centralizes model loading and gives more predictable warm latency. Its first request after service startup remains a cold request.
- One local CPU TTS worker can become a queue under concurrent use. Measure synthesis time under expected user concurrency before treating this setup as a shared service.
- Voice Mode synthesizes sentence-sized chunks and begins FIFO playback as chunks become ready; total response duration is therefore different from time to first audio.

Record cold-start time, warm synthesis time, transcription time, CPU load, and memory use on the deployment machine. Hardware-specific measurements are more useful than universal targets.

### Production security

The permissive values in `.env.example` are development defaults. For production, restrict them to known proxies and UI origins, for example:

```env
CORS_ALLOW_ORIGIN=https://assistant.example.com
FORWARDED_ALLOW_IPS=127.0.0.1
```

Keep Kokoro bound to `127.0.0.1` unless it must be shared. If exposed beyond the host, place it behind an authenticated TLS reverse proxy; `AUDIO_TTS_OPENAI_API_KEY=not-needed` does not protect the local service. Do not commit AWS credentials or a populated `.env` file.

### Maintaining this fork

This fork currently reports Open WebUI `0.11.0` and modifies provider discovery, backend audio routing, application bootstrap, user audio settings, browser Kokoro workers, response playback, and Voice Mode. Those areas are the most likely merge-conflict points when incorporating upstream changes.

Before releasing an upstream merge, verify at least:

1. Bedrock discovery filters non-chat models and lists usable inference profiles.
2. Bedrock Converse and ConverseStream both complete a chat turn.
3. Keyboard noise does not submit a Voice Mode turn, while ordinary speech retains its first word.
4. Whisper accepts the browser's actual recording format.
5. Forced `bf_emma` synthesis reaches Kokoro-FastAPI and plays every sentence in order.
6. The microphone reopens after successful playback and after a simulated TTS failure.
7. The five-bar waveform appears only during actual TTS playback.
8. `npm` production build and the relevant backend tests pass.

### Start the source application

Start the backend from `backend/`:

```bash
cd backend
./start.sh
```

The backend serves on [http://localhost:8080](http://localhost:8080) by default. To run the Vite frontend during development, use a second terminal:

```bash
npm run dev
```

The development frontend is normally available at [http://localhost:5173](http://localhost:5173). For the simplest source-based test, use the backend-served application at port 8080.

When Bedrock is enabled, refresh the model list after startup. Models requiring on-demand throughput are omitted from direct selection; AWS inference profiles are listed using IDs such as `us.anthropic...` and should be selected for models such as Amazon Nova that require profile-based invocation.

### Verify AWS access before starting the backend

Use the same project interpreter to verify that boto3 can authenticate:

```bash
cd backend
PYTHONPATH=. ./venv/bin/python - <<'PY'
import boto3
import open_webui.env

session = boto3.Session(
    region_name=open_webui.env.AWS_REGION,
    **open_webui.env.AWS_CREDENTIALS,
)
print(session.client('sts').get_caller_identity()['Arn'])
PY
```

If this command succeeds but no Bedrock models appear, check the backend log for IAM permissions, regional model access, or inference-profile availability.

### Offline Mode

If you are running Open WebUI in an offline environment, you can set the `HF_HUB_OFFLINE` environment variable to `1` to prevent attempts to download models from the internet.

```bash
export HF_HUB_OFFLINE=1
```

## What's Next? 🌟

Discover upcoming features on our roadmap in the [Open WebUI Documentation](https://docs.openwebui.com/roadmap/).

## License 📜

This project contains code under multiple licenses. The current codebase includes components licensed under the Open WebUI License with an additional requirement to preserve the "Open WebUI" branding, as well as prior contributions under their respective original licenses. For a detailed record of license changes and the applicable terms for each section of the code, please refer to [LICENSE_HISTORY](./LICENSE_HISTORY). For complete and updated licensing details, please see the [LICENSE](./LICENSE) and [LICENSE_HISTORY](./LICENSE_HISTORY) files.

## Support 💬

If you have any questions, suggestions, or need assistance, please open an issue or join our
[Open WebUI Discord community](https://discord.gg/5rJgQTnV4s) to connect with us! 🤝

## Security 🛡️

If you believe you've found a security vulnerability, or something that shouldn't be disclosed publicly, please [reach out confidentially through our responsible disclosure program on GitHub](https://github.com/open-webui/open-webui/security). We accept reports only through GitHub, not through any other platform. Thank you for helping us keep Open WebUI secure!

## Star History

<a href="https://star-history.com/#open-webui/open-webui&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
  </picture>
</a>

---

Created by [Timothy Jaeryang Baek](https://github.com/tjbck) - Let's make Open WebUI even more amazing together! 💪
