# Future Qwen MCP Adapter

## Project boundary

A Qwen MCP adapter is a proposed **separate GitHub project**, not a component of Lambda WebUI. Its purpose is to give Qwen models that do not reliably support native MCP or provider tool calling an MCP-capable inference endpoint without adding Qwen-specific tool orchestration to this repository.

Lambda WebUI should treat the adapter as an ordinary OpenAI-compatible model provider. It sends chat requests and receives model responses; it does not discover, select, invoke, or interpret the adapter's MCP tools.

```text
Lambda WebUI
    |
    | OpenAI-compatible API
    v
Qwen MCP Adapter
    |-- Ollama API --> Qwen
    |-- MCP client --> local MCP servers
    `-- MCP client --> remote MCP servers
```

This design keeps three concerns independent:

- Lambda WebUI owns chat, users, model selection, and presentation.
- The adapter owns Qwen-specific inference and the complete tool-call loop.
- Standard MCP servers own tools, permissions, and feature data.

## Proposed interface

The adapter should expose at least:

- `GET /v1/models`
- `POST /v1/chat/completions`
- OpenAI-compatible streaming responses
- A health endpoint for deployment monitoring

From Lambda WebUI, it should require only a normal OpenAI-compatible connection URL and API key. No Qwen-specific or MCP-specific execution code should be added to Lambda WebUI.

## Adapter responsibilities

For each request, the adapter should:

1. Resolve the configured MCP access profile for the requested model.
2. Connect to the allowed local or remote MCP servers.
3. Discover their tools and JSON Schemas.
4. Convert those schemas into a compact representation suitable for Qwen.
5. Ask Qwen either for a final answer or a structured tool-call envelope.
6. Parse and validate every tool request against the discovered MCP schema.
7. Execute valid calls through MCP and append their results to the model conversation.
8. Repeat within a bounded tool-loop limit until Qwen produces a final answer.
9. Return an OpenAI-compatible response or response stream to Lambda WebUI.

For models without dependable native tool calling, the adapter can constrain the model to an envelope such as:

```json
{
  "type": "tool_call",
  "name": "local-calendar_search_calendar_events",
  "arguments": {
    "start": "2026-09-11T00:00:00-06:00",
    "end": "2026-09-11T23:59:59-06:00"
  }
}
```

This envelope is an internal Qwen compatibility format. It must not replace or modify the standard MCP protocol used between the adapter and MCP servers.

## Configuration direction

Tool availability should be controlled by adapter-side configuration rather than tool definitions submitted by Lambda WebUI. A possible configuration is:

```yaml
models:
  - id: qwen3:4b-mcp
    ollama_model: qwen3:4b
    tool_profile: assistant

ollama:
  url: http://127.0.0.1:11434

tool_profiles:
  assistant:
    max_tool_iterations: 6
    servers:
      - local-calendar
      - local-system-tools

mcp_servers:
  - id: local-calendar
    transport: streamable-http
    url: http://127.0.0.1:9010/mcp
  - id: local-system-tools
    transport: streamable-http
    url: http://127.0.0.1:9020/mcp
```

The eventual project should support standard MCP transports appropriate to its deployment model. Local and public MCP servers use the same tool protocol; their transport, credentials, and trust policies differ.

## Reliability and safety requirements

The adapter must not execute loosely parsed model text. It should provide:

- Strict JSON Schema validation before every MCP call.
- Server and tool allowlists.
- Separate credentials per MCP server, never exposed to Qwen or Lambda WebUI.
- Confirmation or policy denial for destructive operations.
- Maximum tool iterations, timeouts, response-size limits, and duplicate-call detection.
- At most one constrained repair attempt for malformed tool-call output.
- Cancellation propagation when the originating request disconnects.
- A trace ID spanning the incoming request, Qwen calls, and MCP calls.
- Structured audit logs that exclude secrets and sensitive tool results by default.

## User experience trade-off

In the strictly decoupled design, the adapter performs the entire MCP loop internally and Lambda WebUI receives the final answer. Existing Lambda WebUI tool-call cards would therefore not represent those internal calls.

The first implementation should favor final-answer streaming and adapter-side observability. A later version could define optional, standards-compatible progress events, but it should not require proprietary Lambda WebUI behavior for basic operation.

## Suggested implementation phases

1. OpenAI-compatible non-streaming chat proxy to Ollama.
2. MCP configuration, connection lifecycle, and tool discovery.
3. Constrained Qwen tool-call generation and schema validation.
4. Bounded execute-result-infer loop.
5. Streaming final responses, cancellation, and timeouts.
6. Authentication, access profiles, destructive-operation policy, and audit logging.
7. Compatibility tests against representative Qwen releases and MCP servers.
8. Independent packaging, deployment documentation, and release automation.

The project should have its own repository, issue tracker, versioning, tests, and deployment lifecycle. Lambda WebUI documentation should link to it once such a repository exists, but this codebase should remain free of its implementation.
