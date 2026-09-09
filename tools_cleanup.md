# MCP-Only Tooling Cleanup Plan

## Objective

Make standard MCP integration the only source of model-callable tools in Lambda WebUI. Remove the older database-backed Python Tools, Functions, Filters, Actions, Pipes, Pipelines, OpenAPI tool servers, and Valves while retaining the generic provider tool-call engine required by MCP.

The central architectural issue is that MCP and the legacy systems currently share `/api/v1/tools`, `selectedToolIds`, and the `tool_ids` chat request field. These paths must be separated before legacy code can be deleted safely.

## Required MCP Architecture

The following capabilities must remain:

- Managed MCP discovery, registration, and process supervision.
- Remote MCP connection configuration and authentication.
- MCP tool-schema discovery.
- The generic model tool-call execution loop.
- OpenAI, Ollama, and Bedrock tool-call translation.
- Tool-result insertion into conversation history.
- Frontend MCP selection and persistence across chats.
- Access grants for MCP connections.
- Browser Panel Actions. These are direct chat-completion requests and do not depend on legacy Open WebUI Functions or Actions.

Generic internal and provider-facing code may continue to use the word `tool`, because MCP and model-provider APIs also use that terminology. The obsolete concept being removed is the database-backed Open WebUI Python Tool.

## Implementation Stages

### 1. Create a dedicated MCP catalog

Add an endpoint such as:

```text
GET /api/v1/mcp/servers
```

It should return only enabled MCP connections, including:

- Configured remote MCP servers.
- Managed local MCP servers.
- Authentication status.
- Access-control information.
- Display name and description.

Move the MCP-specific catalog behavior currently mixed into `backend/open_webui/routers/tools.py` into this endpoint.

Verification:

- Local Calendar and Local System Tools appear in the chat integration menu.
- Discover, Add, Remove, Start, and Stop continue working.
- OAuth-enabled remote MCP servers retain their authentication status.

### 2. Give MCP its own request contract

Replace the mixed request representation:

```json
{
  "tool_ids": ["server:mcp:local-calendar"]
}
```

with:

```json
{
  "mcp_server_ids": ["local-calendar"]
}
```

Update frontend state accordingly:

```text
selectedToolIds -> selectedMcpServerIds
settings.tools  -> settings.mcpServerIds
```

Provide a one-time compatibility read that migrates existing `server:mcp:*` selections, then remove the old value.

Verification:

- Selected MCP services persist across chats.
- Enabling a service affects the next message immediately.
- Missing or stopped servers return a bounded, useful error.

### 3. Reduce the backend resolver to MCP

Refactor the server-side resolution path in `backend/open_webui/utils/middleware.py` so it:

1. Reads `mcp_server_ids`.
2. Connects to each authorized MCP server.
3. Discovers its tools.
4. Namespaces tool names.
5. Adds tool schemas to the provider request.
6. Retains clients for execution and guaranteed cleanup.

Remove the database-Python-Tool and OpenAPI branches only after this MCP path has independent tests.

Verification:

- Local Calendar can list, create, update, and safely delete events.
- Local System Tools can access the configured filesystem and current time.
- Nova, Ollama, and other selected models receive valid tool schemas.
- MCP clients close after success, failure, and cancellation.

Steps 1 through 3 form the first delivery checkpoint. Run a manual Calendar and System Tools regression before beginning destructive removal.

### 4. Remove database-backed Python Tools

Remove:

- Python Tool ORM and CRUD operations.
- Tool source upload, import, and export.
- Dynamic Python module loading for Tools.
- `/api/v1/tools/create`, `/id`, `/export`, `/load/url`, Valves, and user-Valves endpoints.
- Python Tool sharing permissions.
- Python Tool administration UI and client API functions.
- The old `/api/v1/tools` catalog after all callers use the MCP catalog.

Primary affected files:

- `backend/open_webui/models/tools.py`
- `backend/open_webui/routers/tools.py`
- `backend/open_webui/utils/plugin.py`
- `src/lib/apis/tools/`

### 5. Remove Functions, Filters, Pipes, Actions, and function Events

Remove:

- Function ORM, router, and administration pages.
- Pipe functions presented as model providers.
- Inlet, outlet, and stream filters.
- Function-based chat Actions.
- Function event subscribers.
- Function import/export and arbitrary dependency installation.
- `filter_ids`, function Action IDs, and related model metadata.
- `ENABLE_PLUGINS`.

Primary affected files:

- `backend/open_webui/models/functions.py`
- `backend/open_webui/routers/functions.py`
- `backend/open_webui/functions.py`
- `backend/open_webui/utils/filter.py`
- `backend/open_webui/utils/actions.py`
- `src/routes/(app)/admin/functions/`
- `src/lib/components/admin/Functions/`

Browser Panel Actions such as Explain Text, Find Bias, Challenge Text, and Summarize Page must remain. They are unrelated direct model requests despite sharing the word `Action`.

### 6. Remove legacy Pipelines

Remove:

- The legacy external Pipelines router.
- Pipeline inlet and outlet processing.
- Pipeline model discovery.
- The Admin Pipelines screen.
- Pipeline client API functions and configuration.
- Pipeline calls made by ordinary chats and background tasks.

Update all callers in `tasks.py`, `utils/chat.py`, and `utils/middleware.py`.

### 7. Remove OpenAPI tool servers

Make server connections MCP-only:

- Remove the `type: openapi` path.
- Remove OpenAPI schema downloading and conversion.
- Remove inline JSON/spec upload.
- Explicitly default connection types to `mcp`; do not interpret a missing type as OpenAPI.
- Simplify Add Connection to MCP transport and authentication fields.
- Preserve the managed runtime's MCP registration path.

After stabilization, rename generic `tool_server.connections` configuration to an MCP-specific name and provide a one-time migration.

### 8. Remove Valves

Once the legacy extension systems have been removed, Valves becomes genuinely dead. Remove:

- Valves UI components and modals.
- Valves client API functions.
- Tool, Function, and Pipeline Valves endpoints.
- Valves permission and environment settings.
- Valves encryption helpers.
- ORM fields and methods.
- Valves event definitions.
- Runtime Valves and UserValves injection.

### 9. Retire native built-in tool injection

A strict MCP-only result also requires removing or replacing `backend/open_webui/tools/builtin.py`. Native built-in tools currently cover capabilities such as:

- Memory.
- Knowledge and file search.
- Web search.
- Code execution.
- Image operations.
- Automations.
- Timers and delegation.

First remove their automatic injection into model requests. Retain ordinary non-tool application APIs until each capability is either intentionally retired or exposed through a local MCP. This prevents models from selecting native tools without forcing an unnecessarily large simultaneous deletion.

Apply the same policy to terminal integration: disable native terminal-tool injection, then remove it or replace it with a local MCP package.

## Database Strategy

After runtime imports and callers are removed, add one explicit destructive migration that drops the legacy `tool` and `function` tables from existing installations.

Do not rewrite historical Alembic revisions during the first pass. Historical migrations are not runtime dead code, and changing them makes existing installations difficult to validate. Once the MCP-only build is stable, migration history may be deliberately squashed as a separate repository-cleanup operation.

## Required Automated Coverage

Add coverage for:

- MCP-only catalog listing and access control.
- Managed and remote MCP connection deduplication.
- MCP selection persistence.
- One-time migration of legacy `server:mcp:*` selections.
- `mcp_server_ids` request validation.
- MCP connection, tool discovery, and execution.
- Tool-name namespacing and collision handling.
- Tool-result round trips through chat history.
- MCP client cleanup after success, failure, and cancellation.
- Stopped, missing, and unavailable MCP servers.
- OpenAI-compatible, Ollama, and Bedrock/Nova tool payload translation.
- Absence of legacy routers and administration navigation.
- Backend startup and import smoke tests.
- Fresh database and upgraded existing-database scenarios.

## Delivery Gates

1. **Independent MCP path:** Complete stages 1-3 and run automated and manual MCP regressions.
2. **Legacy plugin removal:** Complete stages 4-6 and verify normal chats, Browser Actions, audio, and background tasks.
3. **MCP-only connections:** Complete stage 7 and verify local and remote MCP authentication and discovery.
4. **Cross-cutting cleanup:** Complete stage 8 and confirm that no Valves runtime or UI references remain outside immutable migration history.
5. **Native tool retirement:** Complete stage 9 capability by capability, adding MCP replacements where desired.
6. **Persistence cleanup:** Apply the destructive database migration and optionally schedule migration-history squashing as a separate operation.
