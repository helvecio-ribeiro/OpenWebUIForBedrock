# Managed MCP Runtime Implementation Backlog

This document is the restart handoff and implementation checklist for adding locally managed MCP servers to Open WebUI. The rationale and settled architecture are recorded separately in [Managed MCP Runtime Design Decisions](managed-mcp-runtime-design.md).

The core runtime, authenticated Open WebUI control plane, local discovery/Add/Remove administration slice, chat-tool integration, Local System Tools example, standalone Local Calendar example, and initial Local Web Research service are implemented. The phases below retain the original acceptance criteria and identify hardening or administration work that remains; they should not be read as evidence that the native Calendar or Notes features still exist. Both native implementations have been removed, and Calendar is now provided only by Local Calendar MCP. A future MCP Builder and validation workbench is tracked as a separate phase; generated candidates must remain isolated from installed services until an administrator explicitly publishes them.

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
- `backend/open_webui/routers/mcp.py`: expose the authorized, secret-free local and remote MCP catalog.
- `backend/open_webui/utils/middleware.py`: resolve managed server IDs to private runtime MCP URLs.
- `backend/open_webui/utils/mcp/client.py`: retain current HTTP behavior; optionally add Unix-socket HTTP support.
- `backend/open_webui/models/users.py` and `groups.py`: validate access-rule subjects without creating new persistence relationships.
- `backend/open_webui/events.py`: lifecycle and configuration events.

Acceptance criteria:

- Existing remote Streamable HTTP MCP servers continue to work.
- A managed server appears under its raw stable ID and uses MCP tool namespacing internally.
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
- [x] Ensure ordinary users see only selectable tools, never management controls. Management UI placement and every `/api/v1/managed-mcp` operation are administrator-only; the user catalog exposes only authorized, secret-free selections.

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

## Phase 7: MCP Builder and validation workbench

Build an administrator-governed authoring workflow that can use a selected model to create or revise local Python MCP packages without granting generated code access to the active runtime. The builder may help write and test a candidate, but it must not install, enable, or publish one. Publishing remains a distinct administrator action implemented through the revision workflow in Phase 5.

Required lifecycle:

`Draft -> Static validation -> Isolated execution -> MCP protocol validation -> Behavioral evaluation -> Administrator review -> Install`

### Candidate workspace and authoring

- [ ] Define a dedicated candidate root outside active package roots, the runtime registry, application data, and production secret locations.
- [ ] Add a locked `local-mcp-builder` service or equivalent narrowly scoped workbench API for creating, reading, editing, listing, and discarding files only inside one candidate workspace.
- [ ] Provide maintained templates for a Python `stdio` MCP package, including `mcp.yaml`, `pyproject.toml`, `uv.lock`, server entry point, README, and tests.
- [ ] Add a requirements interview that captures intended tools, input/output schemas, owned data, external services, network destinations, filesystem needs, secrets, resource limits, and expected failure behavior before generation starts.
- [ ] Generate tool descriptions and JSON Schemas together with implementation code so the model-facing contract is reviewable independently of the Python source.
- [ ] Track candidate identity, parent revision, author, timestamps, model/provider used, source changes, declared capabilities, and immutable content digest.
- [ ] Keep model-generated source, test output, and fetched reference material explicitly untrusted throughout the workflow.
- [ ] Prevent builder tools from modifying their own implementation, Open WebUI source, installed MCP packages, registry files, service units, or privilege policy.

### Static and dependency validation

- [ ] Parse and validate the manifest before dependency resolution or code execution.
- [ ] Reject path escapes, symlinks outside the candidate, shell command strings, undeclared executables, unsafe archive entries, and unsupported runtimes or transports.
- [ ] Require deterministic dependency locks and report lockfile drift, package origin, hashes, licenses, known vulnerabilities, and platform compatibility.
- [ ] Add linting, formatting checks, type checks, import checks, secret scanning, and prohibited-API checks with machine-readable findings.
- [ ] Extend the manifest contract with explicit outbound-network and other capability declarations before generated packages can request them.
- [ ] Produce an SBOM and candidate digest before any behavioral review.

### Isolated execution and protocol validation

- [ ] Select and document the candidate sandbox boundary; prefer an ephemeral rootless container or equivalently isolated systemd unit over execution in the Open WebUI backend or active MCP runtime.
- [ ] Run candidates without production credentials, user files, application data, runtime tokens, cloud metadata access, or unrestricted network access.
- [ ] Mount only the candidate and disposable fixtures, use a temporary writable data directory, and destroy the environment after each run.
- [ ] Enforce CPU, memory, process, file-descriptor, output-size, and wall-clock limits for dependency installation, tests, initialization, and tool calls.
- [ ] Verify clean MCP `initialize`, `tools/list`, schema serialization, representative `tools/call`, error responses, cancellation, timeout, and shutdown behavior through the official MCP SDK.
- [ ] Fail validation when protocol output is written incorrectly, child processes survive shutdown, schemas are invalid, output exceeds limits, or observed capabilities exceed the manifest.

### Behavioral and security evaluation

- [ ] Generate unit tests from the approved tool contract while keeping administrator-authored acceptance tests separate from model-generated tests.
- [ ] Run deterministic happy-path, malformed-input, boundary, timeout, partial-failure, and concurrency cases against disposable fixtures.
- [ ] Add adversarial checks for filesystem escape, SSRF, credential access, command execution, prompt-injected tool input, oversized results, and persistence outside declared data paths.
- [ ] Record coverage and clearly distinguish tested behavior from untested claims; a candidate cannot approve itself by generating only passing tests.
- [ ] Support administrator-maintained policy suites that every candidate must pass regardless of its generated tests.
- [ ] Make every finding reproducible with the exact digest, fixture version, test command, sanitized logs, and environment description.

### Preview, review, and publishing

- [ ] Add an administrator-only workbench showing source diffs, manifest and schemas, dependency/SBOM data, declared privileges, test results, sanitized logs, and unresolved findings.
- [ ] Add an optional **Test in Chat** session that exposes only the candidate tools to an isolated evaluation conversation and disposable fixture data; candidates must never appear in ordinary users' tool catalogs.
- [ ] Label all candidate tool calls and outputs as non-production and retain a bounded evaluation transcript linked to the candidate digest.
- [ ] Generate a signed or integrity-protected validation report bound to the exact candidate digest; any source, manifest, lockfile, or policy change invalidates prior approval.
- [ ] Require explicit administrator acknowledgement for network access, host filesystem access, persistent data, secrets, and elevated privilege profiles.
- [ ] Publish only through Phase 5's side-by-side revision mechanism, rerun initialization and `tools/list`, then atomically activate the approved digest.
- [ ] Retain the previous known-good revision and expose immediate rollback when post-install health checks fail.
- [ ] Audit candidate creation, validation, preview, approval, rejection, installation, activation, rollback, and deletion without recording secrets or sensitive fixture contents.

Acceptance criteria:

- A model can create a complete candidate MCP from a maintained template without writing anywhere outside its candidate workspace.
- Validation runs generated code only inside an ephemeral, resource-limited environment containing no production secrets or user data.
- The report proves which source digest, dependencies, policies, and tests were evaluated and becomes stale after any candidate change.
- An administrator can exercise the candidate with disposable data before installation, while ordinary users cannot discover or invoke it.
- No model-facing tool or builder endpoint can install, activate, grant access to, or elevate a candidate.
- Publishing creates a reviewable revision, activates only after a final protocol probe succeeds, and preserves a known-good rollback target.

## Next managed service: Local Web Research

Add `examples/managed-mcp/web-research-tools` as the third locally managed MCP package. Its purpose is to give models without native web access a controlled way to read one public page or traverse a small, bounded set of related pages. Implement both behaviors in one service so URL validation, fetching, extraction, caching, limits, and security policy have a single implementation.

### Service contract

- [x] Add a schema-version-1 `mcp.yaml` with stable ID `local-web-research`, confined privileges, and conservative time and memory limits. Schema version 1 has no network-capability field; the service enforces its outbound policy internally.
- [x] Add a locked Python package using the same `uv run --frozen server.py` and MCP `stdio` conventions as the existing examples.
- [x] Expose `fetch_web_page` for a single URL. Inputs include `url`, output format, maximum returned characters, and an optional content selector.
- [x] Return the final URL, title, description, detected publication/modification dates, cleaned main content, discovered links, content type, and any truncation or extraction warnings.
- [x] Expose `crawl_website` by reusing the single-page pipeline. Inputs include `start_url`, `max_depth`, `max_pages`, same-origin policy, include/exclude patterns, and output format.
- [x] Default crawls to the starting origin, depth `1`, a small page limit, and server-enforced hard ceilings regardless of model input.
- [x] Return a crawl manifest containing visited, rejected, and failed URLs plus page content, titles, link relationships, and truncation state. Redirect destinations are recorded as each page's final URL.
- [x] Keep search-engine integration outside the initial release. A future `search_web` tool must use an explicitly configured provider and feed selected results through `fetch_web_page`.

### Fetching and extraction pipeline

- [x] Permit only `http` and `https` URLs and validate the destination before every request and redirect.
- [x] Fetch with an identifiable user agent, bounded redirects, request timeouts, response-size limits, and identity encoding. Unexpected compression is rejected.
- [x] Accept only supported textual content types in the first release. Report unsupported documents instead of returning binary data.
- [x] Decode HTML safely, remove scripts, styles, navigation, and common page chrome, then extract the primary readable content.
- [x] Normalize extracted content to Markdown by default, with plain text and metadata-only output options.
- [x] Preserve source URLs and relevant link targets so model answers can identify their evidence.
- [x] Treat all fetched text as untrusted source material and clearly delimit it from MCP/tool instructions in tool descriptions and results.
- [x] Add a bounded short-lived in-memory cache keyed by normalized URL. The service accepts no authentication headers, cookies, or personalized sessions.

### Network and execution security

- [x] Block loopback, private, link-local, multicast, unspecified, reserved, and cloud-instance metadata destinations, including IPv4 and IPv6 representations.
- [x] Resolve and validate every hostname immediately before connecting, validate every resolved address, repeat validation after redirects, and pin the connection to the validated address to mitigate SSRF and DNS rebinding.
- [x] Do not accept caller-provided headers, credentials, cookies, proxy settings, request bodies, or non-GET methods in the initial release.
- [x] Do not submit forms, download files, authenticate to sites, or mutate remote state.
- [ ] Add configurable domain allow/deny policies, per-domain rate limits, global concurrency limits, and crawl-delay behavior.
- [x] Define and document the service policy for `robots.txt`; crawling respects it by default and bypass requires explicit administrator configuration.
- [x] Bound page bytes, extracted characters, crawl depth, total pages, combined characters, request time, and redirects. Links-per-page and total execution-time ceilings remain hardening work.
- [x] Keep page bodies and response credentials out of application logs. Query-string redaction remains hardening work before adding request logging.

### Large results and artifacts

- [x] Return small page and crawl results inline within a strict response budget. Per-page and combined-crawl character ceilings are enforced and covered by truncation tests.
- [x] Define an isolated, expiring research-artifact store for results that exceed the inline budget.
- [x] Add `read_web_artifact` with bounded ranges and `search_web_artifact` with bounded matches before enabling crawls large enough to require stored artifacts.
- [x] Return artifact IDs, source indexes, titles, URLs, excerpts, creation time, expiration time, and truncation state; never expose host filesystem paths.
- [x] Add quota and cleanup behavior for cached responses and artifacts. Response caching remains bounded in memory; artifacts enforce TTL, per-artifact, count, and total-byte ceilings with oldest-first eviction.

### Optional JavaScript rendering

- [x] Do not require Chromium for the initial HTTP-fetch release.
- [x] Detect likely application-shell responses and return a warning when meaningful content could not be extracted.
- [ ] After the HTTP implementation is stable, add an optional `render_web_page` capability backed by an isolated headless browser.
- [ ] Make browser availability discoverable in tool metadata and keep HTTP fetching as the default path.
- [ ] Execute page JavaScript only in a sandboxed process with strict CPU, memory, navigation, download, popup, request, and wall-clock limits.
- [ ] Apply the same destination validation to every browser subresource and navigation; block access to local services, private networks, metadata endpoints, downloads, permissions, and persistent browser storage.
- [ ] Capture the rendered DOM and pass it through the same main-content extraction and normalization pipeline rather than returning an uncontrolled raw page.

### Tests and acceptance criteria

- [x] Add unit tests for URL normalization, redirect validation, IP classification, DNS results, content-type handling, extraction, Markdown conversion, output ceilings, and truncation metadata. Socket-level byte-limit tests remain.
- [x] Add SSRF regression tests for local/private/metadata addresses, IPv4-mapped IPv6, localhost aliases, mixed-answer rebinding simulations, and redirect pivots. Additional exotic textual IP forms remain hardening work.
- [x] Add crawler tests for cycles, duplicate/canonical URLs, fragments, cross-origin links, and depth ceilings. Include/exclude, cancellation, rate limiting, and partial-failure cases remain.
- [ ] Use a controlled local HTTP fixture for deterministic integration tests; tests must not depend on public websites.
- [ ] Verify calls through an MCP SDK `ClientSession` over managed stdio. Initialize and `tools/list` have direct stdio smoke coverage.
- [ ] Verify the package can be discovered, registered, enabled per user, invoked from chat, stopped, and recovered after runtime restart.
- [ ] Verify a model can fetch one page without receiving crawler complexity, and can request a bounded crawl without receiving unbounded content in its context.
- [x] Document installation, configuration, resource requirements, network policy, operational limits, and Raspberry Pi considerations in `examples/managed-mcp/README.md`.

Initial release acceptance criteria:

- `fetch_web_page` reliably returns clean, source-attributed Markdown from supported public HTML pages without executing JavaScript.
- `crawl_website` traverses only URLs permitted by its origin and policy constraints and cannot exceed server-enforced budgets.
- Requests cannot reach the host, private networks, local MCP/runtime ports, or cloud metadata services.
- Oversized and unsupported responses fail predictably without exhausting service or model context resources.
- Chromium and search-provider credentials are not required for the initial release.

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
