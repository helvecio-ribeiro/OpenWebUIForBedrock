# Managed MCP Runtime Design Decisions

This document records the architectural decisions for integrating locally managed MCP servers with Open WebUI. It is the durable design reference; implementation tasks and acceptance criteria live in the separate [implementation backlog](managed-mcp-runtime-todo.md).

## Scope

The first release targets a small, single-host installation. An administrator can register locked local Python MCP packages, control their lifecycle and access, and expose their tools through Open WebUI. Existing remote Streamable HTTP MCP connections continue to work unchanged.

Initial non-goals include a public marketplace, remote runtime hosts, Kubernetes orchestration, Git cloning, arbitrary package installation, Node runtimes, and package uploads.

Native Calendar and Notes are outside this architecture. This fork removes both implementations from Open WebUI rather than using feature flags to hide them. Calendar functionality is supplied only by the optional Local Calendar package; Notes currently has no managed-MCP replacement. Historical Alembic revision IDs remain as no-op chain markers, while cleanup migrations remove the old tables and persisted settings.

## Runtime boundary and transport

```text
Browser
  |
Open WebUI backend (authentication, access enforcement, Admin API)
  | authenticated localhost Streamable HTTP
Managed MCP runtime daemon
  | long-lived stdio session
MCP package process
```

The runtime is a separate, long-lived daemon. It owns MCP subprocesses, SDK sessions, queues, health, restarts, and stderr logs. Open WebUI uses one private Streamable HTTP endpoint per managed server, reusing its existing MCP client path.

MCP subprocesses are not launched inside chat requests. Per-request processes would add startup latency, lose useful process state, complicate AnyIO task ownership, and risk leaked children. Each server initially has one persistent subprocess and a serialized call queue so concurrent calls cannot corrupt stdio.

The runtime binds to loopback and authenticates backend calls with a private token stored in a permission-restricted file. A Unix socket may replace loopback TCP later without changing the Admin UI contract.

### Python SDK stdio compatibility

The runtime retains the official MCP `ClientSession` and protocol models but owns subprocess pipe forwarding in `mcp_runtime/transport.py`. During implementation, the SDK's stock stdio client consistently left `session.initialize()` waiting while a live child waited on stdin on this Ubuntu installation. This class of initialization hang is also tracked in [modelcontextprotocol/python-sdk#1452](https://github.com/modelcontextprotocol/python-sdk/issues/1452). The local bridge uses `asyncio` subprocess pipes, a minimal environment, a new process session, explicit EOF handling, and TERM/KILL process-group cleanup.

The bundled Local System Tools example uses a small JSONL stdio loop around FastMCP's registered tool manager for the same reason. It implements only initialize, ping, tool discovery, and tool calls because those are the version-one contract. Third-party MCP packages remain free to use their normal SDK server transport. Both boundaries have real subprocess and protocol tests and can return to the stock SDK transport once the upstream behavior is reliable in this environment.

## JSON registry is intentional

The initial release uses a versioned JSON registry instead of adding an Open WebUI database table. This fits a single-host installation with one runtime writer, infrequent administrative mutations, and a modest server count. It also makes inspection, backup, and recovery straightforward while the contract evolves.

The runtime daemon is the registry's only writer. It serializes mutations with an in-process lock and persists them using a temporary file, `fsync`, and atomic rename. It retains a last-known-good copy and fails safely when the active file is malformed or has an unsupported schema version. Open WebUI accesses the registry only through the authenticated runtime API; Uvicorn workers never edit it directly.

Package manifests remain version-controlled files. The registry records stable server IDs, package paths and digests, desired lifecycle state, configuration references, and user/group access rules. Observed process state, health, and transient errors remain in runtime memory.

This is a bounded design decision rather than omitted persistence work. The registry should move behind the same API abstraction to a database when the system requires multiple runtime hosts or writers, transactional relationships with Open WebUI data, substantial audit history, or query/revision volume unsuitable for one document. That migration must not require Admin UI or chat integration changes.

## Package and configuration contract

The first release accepts only administrator-configured local directories beneath `MANAGED_MCP_PACKAGE_ROOTS`. A Python package must contain `mcp.yaml`, `pyproject.toml`, and `uv.lock`, and runs with `uv run --frozen`. Commands and arguments are stored separately; shell command strings are not accepted.

Ordinary configuration may be recorded in the registry. Secrets are environment-variable or secret-provider references, never plaintext manifest values. Child processes receive a minimal allowlisted environment and must not inherit the Open WebUI environment, AWS credentials, database URL, or session secrets.

Server tools use stable server IDs and the existing Open WebUI MCP namespace. The backend validates user and group identifiers when access rules change and enforces authorization before forwarding a call. MCP packages do not trust browser-supplied identity headers.

## Privilege profiles

Managed packages cannot grant themselves elevated privileges. Privilege is granted only by an administrator-controlled runtime policy and an externally installed service definition. The runtime never stores a sudo password and never passes arbitrary manifest commands through sudo.

The filesystem capability in Local System Tools supports two profiles:

- `confined`: the default unprivileged profile, limited to explicitly configured roots. It must never implicitly select the repository, a home directory, or `/`.
- `system-admin`: an opt-in profile launched through a separately installed privileged systemd service or supervisor. Its configured root may be `/`.

A manifest may request `system-admin`, but that request grants nothing by itself. Startup requires a matching administrator policy containing the server ID, immutable executable path, package digest, and allowed capabilities. Changing the path or digest revokes authorization. The Admin UI displays a persistent high-risk indicator and limits access to explicitly selected principals.

Whole-system access is read-only in the initial release. Root write access is a separate capability, disabled by default, with an independent policy grant and explicit confirmation. The Filesystem package does not expose arbitrary shell execution. Root selection does not disable path/symlink checks, bounded reads, backend authorization, or per-call auditing.

## Failure and administration behavior

A server that fails manifest validation, MCP initialization, or tool discovery remains disabled. The Admin UI reports a clear status and bounded, redacted stderr without exposing raw environment values.

The first Admin UI release covers registration, start, stop, restart, status, discovered tools, configuration references, access rules, and logs. Package upload and revision management are deferred until local-directory registration is stable.

The runtime reconciles desired state after restart, prevents duplicate children, uses bounded restart backoff, and terminates complete process groups on stop. Runtime unavailability does not prevent Open WebUI itself from starting, and existing remote MCP/OpenAPI integrations remain unaffected.

## Example-server boundaries

Local Calendar is a self-contained managed MCP package. It owns one shared SQLite repository and exposes both MCP tools and a loopback REST API over that repository. It has no Open WebUI model, route, permission, feature flag, API-token, or per-user calendar dependency. This keeps the integration boundary explicit and prevents competing native and MCP tool definitions.

Local Calendar intentionally does not import the removed native Calendar database. Its repository is shared by every principal granted access to the MCP, so it must not be used when per-user calendar isolation is required. Removing the package registration stops tool exposure without affecting Open WebUI's chat database; deleting its SQLite file deletes the shared calendar independently of Open WebUI.

The included Local System Tools server provides an explicit local clock for relative date references and demonstrates both confined filesystem operation and the policy boundary needed for future system-wide access. Its initial implementation remains confined; privileged launch and whole-system read-only behavior are runtime milestones, not permissions granted by its example manifest.
