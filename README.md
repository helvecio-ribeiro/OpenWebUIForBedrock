# Open WebUI for AWS Bedrock

This repository is a project-specific fork of [Open WebUI](https://github.com/open-webui/open-webui) with native AWS Bedrock model discovery and chat support.

The upstream project provides the core chat application, frontend, Ollama integration, OpenAI-compatible providers, workspace features, and general documentation. This fork adds a small Bedrock integration that discovers AWS foundation models and inference profiles, exposes them in the model selector, and invokes supported models through the Bedrock Converse APIs.

For upstream features, configuration, and general troubleshooting, see the [Open WebUI repository](https://github.com/open-webui/open-webui) and [Open WebUI documentation](https://docs.openwebui.com/).

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

- 📝 **Notes**: A dedicated workspace for content outside conversations. Draft with a rich editor, use AI to rewrite selected text, and attach notes to any chat for full-context injection.

- 📢 **Channels**: Real-time shared spaces where your team and AI models collaborate in one timeline. Tag models to draft or critique, with threads, reactions, pins, and access control.

- 🧠 **Persistent Memory**: The AI remembers facts about you across conversations, carrying context from one chat to the next.

- ✅ **Live Workflow & Message Flow**: Watch the AI build and work through checklists in real time. Queue messages while the AI is still responding; they send automatically when it's ready.

- 📅 **Calendar & AI Scheduling**: Built-in personal and shared calendars with month/week/day views, recurring events, color coding, attendees, and reminders. Models manage your schedule conversationally through native function calling.

- ⏱️ **Automations**: Schedule prompts to run on recurring schedules, with runs surfaced on your calendar and each completed run linking back to the chat it produced.

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

| Provider | Model ID prefix |
| --- | --- |
| AI21 Labs | `ai21.jamba-` |
| Amazon | `amazon.nova-` |
| Anthropic | `anthropic.claude-` |
| Cohere | `cohere.command-` |
| DeepSeek | `deepseek.` |
| Google | `google.gemma-` |
| Meta | `meta.llama` |
| MiniMax | `minimax.` |
| Mistral AI | `mistral.` |
| Moonshot AI | `moonshot.` |
| NVIDIA | `nvidia.` |
| OpenAI | `openai.` |
| Qwen | `qwen.` |
| Writer | `writer.palmyra-` |
| xAI | `xai.grok-` |
| Z.AI | `zai.glm-` |

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
