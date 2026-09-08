# Managed MCP Runtime Implementation Backlog

This document is the restart handoff and implementation checklist for adding locally managed MCP servers to Open WebUI. The rationale and settled architecture are recorded separately in [Managed MCP Runtime Design Decisions](managed-mcp-runtime-design.md).

The core runtime, authenticated Open WebUI control plane, local discovery/Add/Remove administration slice, chat-tool integration, Local System Tools example, and standalone Local Calendar example are implemented. The phases below retain the original acceptance criteria and identify hardening or administration work that remains; they should not be read as evidence that the native Calendar or Notes features still exist. Both native implementations have been removed, and Calendar is now provided only by Local Calendar MCP.

## Objective

An administrator should be able to register, configure, start, stop, inspect, and grant access to a local MCP package from Open WebUI. A user should see its allowed tools in the existing chat tool selector without knowing how the process is deployed.

The initial target is a locked Python package that communicates over `stdio`. Existing remote Streamable HTTP MCP connections must continue to work unchanged.

The `examples/managed-mcp/filesystem-tools` Local System Tools package defines the initial contract. The self-contained Local Calendar package uses the same runtime contract while owning its shared SQLite repository and REST API.

## Non-goals for the first release

- Public package marketplace.
- Installing arbitrary `uvx` or `npx` packages by name.
- Git credentials or private repository cloning.
- Multiple runtime hosts.
- Kubernetes orchestration.
- Node, Bun, or compiled executable packages.
- Per-tool user approvals beyond existing function-calling behavior.
- OpenAI Secure MCP Tunnel management. It can be added later as another consumer of the same MCP server.

## Phase 0: validate the contract

- [x] Add a versioned `mcp.yaml` example manifest.
- [x] Add a Local System Tools MCP example with current date/time and root-confined filesystem operations.
- [x] Pin `mcp==1.27.2` and generate `uv.lock` for both examples.
- [x] Validate tool registration and direct SDK calls for both examples.
- [x] Run both examples through an MCP SDK `ClientSession` over managed stdio.
- [x] Record representative initialize, `tools/list`, and tool-call behavior in integration fixtures.
- [x] Use snake_case for manifest schema version 1.
- [x] Publish JSON Schemas for manifest and registry through the authenticated runtime API.

Acceptance criteria:

- The Filesystem server starts with `uv run --frozen server.py` after locking dependencies.
- No protocol data is written to stdout outside the MCP transport.
- Filesystem paths cannot escape the configured root through `..`, absolute paths, or symlinks.

## Phase 1: runtime daemon prototype

Create `backend/open_webui/mcp_runtime/` as a separately runnable service, not an in-process FastAPI background task.

- [x] Add a minimal private FastAPI application.
- [x] Bind to `127.0.0.1` initially; prefer a Unix socket once the MCP HTTP client supports one.
- [x] Read a backend-to-runtime bearer token directly or from a permission-restricted file.
- [x] Implement a runtime registry keyed by manifest `id`.
- [x] Implement one actor task per server. The actor exclusively owns the MCP `ClientSession` and subprocess transport.
- [x] Serialize calls through an `asyncio.Queue` in the first version.
- [x] Implement MCP initialize, `tools/list`, `tools/call`, crash detection, and clean shutdown.
- [x] Implement process-group termination so child processes do not survive a stop.
- [x] Implement startup timeout, call timeout, bounded exponential restart, and maximum restart count.
- [x] Capture stdout only as MCP protocol. Capture stderr as bounded, secret-redacted application logs.
- [x] Add `/healthz`, `/readyz`, and per-server status endpoints.
- [x] Add a Streamable HTTP MCP endpoint for each ready managed server.
- [x] Provide a development/runtime command without Admin UI integration.

Acceptance criteria:

- The Filesystem process stays alive across multiple chat-equivalent calls.
- Two concurrent callers cannot corrupt the stdio stream.
- Killing the child changes observed state and follows the restart policy.
- Stopping the runtime terminates the entire child process group.
- Runtime logs never contain configured secret values.

## Phase 2: JSON persistence and backend control plane

Add a versioned runtime-owned JSON registry. Do not put managed server state in `tool_server.connections`, and do not add an Alembic migration for the initial release.

Suggested registry entry fields:

- `id`, `name`, `description`.
- `package_version`, `package_digest`, `manifest`.
- `install_path`, `active_revision`.
- `desired_state`; observed state and errors are intentionally runtime-only.
- `created_by`, `created_at`, `updated_at`.
- `access`: user IDs, group IDs, or an explicit everyone policy.

Tasks:

- [x] Define a versioned JSON Schema for the registry document.
- [x] Add a runtime registry repository with one-writer locking and atomic durable replacement.
- [x] Keep a last-known-good copy and fail safely on malformed or unsupported registry versions.
- [x] Add runtime management endpoints for registry reads and mutations.
- [x] Validate referenced user/group IDs through the Open WebUI backend before writing access rules.
- [x] Add admin router at `/api/v1/managed-mcp`.
- [x] Add list, inspect, register, start, stop, restart, logs, tools, and access endpoints.
- [x] Treat registry desired state as authoritative and runtime state as observed/ephemeral.
- [x] Reconcile enabled servers at backend/runtime startup.
- [x] Publish audit events for register, start, stop, restart, configuration, access, and removal.
- [x] Never expose install paths, environment, or logs to ordinary users.
- [ ] Add runtime availability to backend health diagnostics without making remote MCP availability block Open WebUI startup.

Backend integration points:

- `backend/open_webui/main.py`: lifespan startup, runtime client, readiness, shutdown.
- `backend/open_webui/routers/tools.py`: include accessible managed servers in the tool list.
- `backend/open_webui/utils/middleware.py`: resolve managed server IDs to private runtime MCP URLs.
- `backend/open_webui/utils/mcp/client.py`: retain current HTTP behavior; optionally add Unix-socket HTTP support.
- `backend/open_webui/models/users.py` and `groups.py`: validate access-rule subjects without creating new persistence relationships.
- `backend/open_webui/events.py`: lifecycle and configuration events.

Acceptance criteria:

- Existing remote MCP and OpenAPI tool servers behave exactly as before.
- A managed server appears as `server:mcp:<id>` and uses existing tool namespacing.
- A user without a read grant cannot see or invoke it.
- Backend restart reconciles desired state without duplicating processes.
- Multiple Uvicorn workers do not create multiple MCP children.
- Concurrent Admin UI mutations cannot lose updates or produce invalid JSON.
- Copying the registry and package directories is sufficient to restore server definitions on the same host.

## Phase 3: manifest and local-directory registration

- [x] Add Pydantic models for manifest schema version 1.
- [x] Permit only known runtime and transport values.
- [x] Store command and arguments separately; never accept a shell command string.
- [x] Canonicalize and validate working directories beneath configured package roots.
- [x] Validate environment names and scalar values.
- [x] Support environment references for secrets; do not store secret values in manifests.
- [x] Require `pyproject.toml` and `uv.lock` for Python packages.
- [x] Execute examples with `uv run --frozen` and an allowlisted child environment.
- [x] Calculate and store a digest of package source and locked contents.
- [x] Probe initialize and `tools/list` before marking a package usable.
- [x] Add `MANAGED_MCP_PACKAGE_ROOTS` and `MANAGED_MCP_RUNTIME_URL` environment settings.
- [x] Add safe defaults to `.env.example` and operational guidance to README.
- [x] Discover manifest packages from the immediate children of configured package roots.
- [x] Expose authenticated runtime and admin-proxy discovery endpoints.

Acceptance criteria:

- An administrator can register the example directory without manually editing the registry JSON.
- A package outside an allowed root is rejected.
- A missing/outdated lockfile fails closed.
- Failed validation cannot replace a working registered revision.

## Phase 4: administration UI

Extend **Admin Panel -> Settings -> Integrations** with a distinct **Managed MCP Servers** section.

The first discovery slice is implemented: **Local MCP Services -> Discover Services** lists valid packages, validation errors, registration conflicts, and observed state, and permits one-click registration using manifest defaults. Full lifecycle/configuration administration remains below.

- [ ] Add runtime status card: version, uptime, health, active calls, managed process count.
- [ ] Add server table: name, version, runtime, status, tool count, access, actions.
- [ ] Add local-directory registration dialog with parsed manifest review.
- [x] Add local-service discovery and one-click registration using manifest defaults.
- [ ] Add overview, configuration, tools, access, logs, and revisions tabs.
- [ ] Add start, stop, restart, diagnose, and remove actions with confirmation.
- [ ] Show the active privilege profile and a persistent high-risk indicator for system-admin servers.
- [ ] Require separate confirmations for whole-system read access and root write capability.
- [ ] Use existing `AccessControlModal` for server-level grants.
- [ ] Stream status/log updates through existing application events or WebSockets.
- [ ] Show bounded, redacted stderr logs; do not show raw environment.
- [ ] Show discovered tools and descriptions after initialization.
- [ ] Ensure ordinary users see only selectable tools, never management controls.

Likely frontend files:

- `src/lib/components/admin/Settings/Integrations.svelte`.
- New `src/lib/components/admin/Settings/ManagedMCP/` components.
- New `src/lib/apis/managed-mcp/` API wrapper.
- `src/lib/stores/index.ts` for runtime/server state types if globally needed.

Acceptance criteria:

- An administrator can register, start, inspect, grant, and stop Filesystem without a shell.
- Status changes propagate without a full page reload.
- Filesystem displays its configured root without exposing unrelated host paths.
- Regular users cannot call management APIs directly.

## Phase 5: package upload and revisions

Do not begin this phase until local-directory registration is stable.

- [ ] Accept size-limited archives into a temporary directory.
- [ ] Reject absolute paths, traversal, devices, FIFOs, unsafe permissions, and escaping symlinks.
- [ ] Validate before dependency installation.
- [ ] Install each revision alongside the current revision.
- [ ] Atomically activate only after initialize and `tools/list` succeed.
- [ ] Retain at least one known-good revision for rollback.
- [ ] Add asynchronous installation jobs whose progress survives dialog closure.
- [ ] Add quotas and cleanup for packages, environments, caches, and logs.
- [ ] Add import/export of manifests without secrets.

## Phase 6: hardening

- [ ] Run the runtime as a dedicated OS user.
- [x] Add administrator-owned privilege policy mapping approved server IDs, executable paths, package digests, and capabilities.
- [x] Add a fixed systemd service path for system-admin servers; never accept sudo credentials or manifest-defined privileged commands.
- [x] Prevent inheritance of Open WebUI `.env`, AWS credentials, database URLs, and session secrets.
- [ ] Add CPU, memory, process, file-descriptor, and execution-time limits.
- [ ] Add filesystem and network policies per package.
- [ ] Evaluate systemd transient units versus rootless containers for package isolation.
- [ ] Add encrypted write-only secret storage with a key distinct from normal session signing.
- [x] Redact configured secret values from logs. Common credential-pattern redaction remains future hardening.
- [x] Record administrator lifecycle/configuration events without logging sensitive configuration. Per-tool invocation audit remains future hardening.
- [ ] Add per-tool enable/disable and approval policies.
- [ ] Add SBOM/digest display and optional signature verification.
- [ ] Threat-model malicious packages, compromised dependencies, confused-deputy calls, and prompt-injected tool use.

## Test matrix

- [x] Manifest parsing and forward-incompatible schema rejection.
- [x] Directory confinement and symlink escape tests.
- [ ] Process start, ready, stop, forced stop, crash, and restart. Start/ready/stop/restart limits are covered; explicit crash/forced-kill cases remain.
- [ ] Server that hangs during initialize.
- [ ] Server that hangs during a tool call.
- [ ] Concurrent calls and queue cancellation. Serialization is covered; cancellation races remain.
- [ ] Backend restart while runtime stays alive.
- [ ] Runtime restart and desired-state reconciliation.
- [ ] Multiple Uvicorn workers.
- [ ] User, group, anyone, and administrator grants.
- [x] Secret references, runtime API authentication, and configured-value log redaction.
- [ ] Failed install and failed upgrade rollback.
- [ ] Existing remote MCP regression tests.
- [x] Filesystem traversal, absolute path, symlink, oversized read, and overwrite tests.
- [x] Privilege-profile request without an external grant is rejected.
- [x] Package digest/path changes revoke system-admin startup authorization.
- [x] Runtime-enforced read-only mode rejects every mutating Filesystem tool.
- [x] Root write capability requires its independent policy grant.

## Deployment artifacts

- [x] `scripts/systemd/open-webui-mcp-runtime.service` plus an explicit privileged variant.
- [x] Runtime environment example with loopback binding and token-file paths.
- [ ] Docker Compose service with a private network and explicit package mounts.
- [x] Health-check and log-diagnostic endpoints in README.
- [x] Backup guidance for the registry and packages. Encrypted secret storage is not part of version one.

## Open decisions

- [x] Use authenticated loopback TCP initially; retain Unix sockets as a future option.
- [x] Ship the runtime in the same Python distribution.
- [x] Use one persistent MCP process per server.
- [x] Share one serialized stdio session per server.
- [x] Enforce identity in Open WebUI and do not trust or forward browser-supplied identity to managed packages.
- [ ] Whether package network access defaults to allowed or denied.
- [ ] Retention limits for logs and revisions.
- [ ] Secret backend: encrypted database, systemd credentials, or external secret manager.

## Definition of initial release

The initial release is complete when an administrator can register the included Filesystem example from an allowed local directory, start it, view its status and tools, grant it to a user/group, invoke it from chat, inspect redacted logs, restart it, and recover it after an Open WebUI restart—without manually creating a URL-facing MCP service.
