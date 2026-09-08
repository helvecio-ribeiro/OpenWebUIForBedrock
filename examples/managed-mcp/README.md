# Managed MCP Examples

Local System Tools and Local Calendar implement the `mcp.yaml` contract for the Open WebUI managed MCP runtime. They are functional MCP `stdio` servers and can be registered through the Admin UI or backend management API once the runtime is configured and running.

These packages are the model-facing extension boundary for local services. Open WebUI performs authentication and authorization, the managed runtime supervises long-lived package processes, and each package owns its implementation and any feature-specific repository. The browser communicates only with Open WebUI. It does not connect to MCP stdio, the runtime management port, or the Local Calendar REST port.

The native Open WebUI Calendar and Notes implementations have been removed from this fork. Local Calendar is the sole Calendar tool implementation; Notes has no replacement package. Consequently, installing Local Calendar cannot conflict with a second built-in Calendar tool, and removing it makes Calendar operations unavailable to models.

It is configured for this installation with `/home/helvecio` as both its manifest allowlisted root and its default `MCP_FILESYSTEM_ROOT`. It cannot escape that directory through absolute paths, `..`, or symlinks. Change both values together when deploying under a different account; narrowing the root is recommended when whole-home access is unnecessary.

Before starting the runtime, generate its bearer-token file and point the repository `.env` at it:

```bash
install -d -m 700 ~/.config/open-webui
openssl rand -hex 32 > ~/.config/open-webui/mcp-runtime.token
chmod 600 ~/.config/open-webui/mcp-runtime.token
```

```dotenv
MANAGED_MCP_RUNTIME_URL=http://127.0.0.1:9090
MANAGED_MCP_RUNTIME_TOKEN_FILE=/home/user/.config/open-webui/mcp-runtime.token
MANAGED_MCP_RUNTIME_HOST=127.0.0.1
MANAGED_MCP_RUNTIME_PORT=9090
MANAGED_MCP_PACKAGE_ROOTS=/home/user/open-webui/examples/managed-mcp
MANAGED_MCP_ALLOW_ROOT_RUNTIME=false
```

The token file contains only the random token—not a variable assignment or quoted value. Replace `/home/user` with the real absolute path; shell shortcuts such as `~` are not expanded inside `.env` values.

Prepare or run the package independently with:

```bash
cd examples/managed-mcp/filesystem-tools
uv sync --frozen
uv run --frozen server.py
```

An MCP client must own stdin/stdout when the last command is running. Application diagnostics belong on stderr so they cannot corrupt the MCP protocol.

`get_current_datetime` returns the current local and UTC timestamps, local date, time, weekday, UTC offset, timezone name, and Unix timestamp. Set `MCP_LOCAL_TIMEZONE` to an IANA timezone such as `America/Mexico_City`; the package fails closed when the name is invalid. Models should call it before interpreting relative phrases such as “today,” “tomorrow,” or “next Friday.”

The filesystem operations remain confined to one configured root and deliberately expose no deletion or arbitrary-shell tool. Write operations require `security.read_only: false` in the manifest and also honor the runtime-enforced `OPEN_WEBUI_MCP_READ_ONLY` setting.

## Local Calendar

Local Calendar is a self-contained service. It owns one shared SQLite database and does not call, import, or depend on an Open WebUI calendar model or API. There is deliberately no user or calendar partition: every chat granted this MCP sees the same Shared Calendar.

The default database is `calendar-tools/data/shared-calendar.db`. Set `CALENDAR_DATABASE_PATH` in `calendar-tools/mcp.yaml` to use another location. Its parent directory is created automatically; back up the database plus its `-wal` and `-shm` files together while the service is running, or stop the service before copying the database alone.

Prepare the package:

```bash
cd examples/managed-mcp/calendar-tools
uv sync --frozen
```

Open **Admin Panel → Settings → Integrations**, choose **Discover Services**, add **Local Calendar**, and enable it from the chat integrations menu. The enabled tool selection persists across new chats. No Open WebUI API key is required.

Discovery scans only immediate child directories of `MANAGED_MCP_PACKAGE_ROOTS`. Registration records the package in the runtime registry but does not copy it. Removing the registration stops the process and removes the registry entry while leaving this directory and its SQLite database on disk for later rediscovery.

The MCP exposes current time plus event search, create, update, and single-event delete. Inputs use local ISO datetimes such as `2026-09-08 14:30`; results return exact IDs required by update and delete. Deletion is soft and recorded in `audit_log`; batch deletion is rejected.

The MCP process also starts a small REST API against the same repository by default at `http://127.0.0.1:8091`. It provides `/healthz` and CRUD under `/events`. Configure `CALENDAR_API_ENABLED`, `CALENDAR_API_HOST`, and `CALENDAR_API_PORT` in the manifest. Keep the host on loopback unless an authenticated reverse proxy protects it.

This repository is intentionally independent from old Open WebUI calendar data. The removal migration drops the former native tables; it does not import their contents into Shared Calendar.
