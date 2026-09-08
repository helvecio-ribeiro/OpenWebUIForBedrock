import asyncio
import json
import sys
from typing import Any


def _result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def _call_tool(server, name: str, arguments: dict) -> dict:
    content, structured = await server._tool_manager.call_tool(name, arguments, convert_result=True)
    return {
        "content": [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in content],
        "structuredContent": structured,
        "isError": False,
    }


def run_stdio(server) -> None:
    """Serve the MCP subset required by the managed runtime over JSONL stdio."""
    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                response = _result(
                    request_id,
                    {
                        "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": server.name, "version": "0.1.0"},
                    },
                )
            elif method == "tools/list":
                tools = []
                for tool in server._tool_manager.list_tools():
                    tools.append(
                        {
                            "name": tool.name,
                            "title": tool.title,
                            "description": tool.description,
                            "inputSchema": tool.parameters,
                            "outputSchema": tool.output_schema,
                            "annotations": (
                                tool.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
                                if tool.annotations
                                else None
                            ),
                        }
                    )
                response = _result(request_id, {"tools": tools})
            elif method == "tools/call":
                response = _result(
                    request_id,
                    asyncio.run(
                        _call_tool(
                            server,
                            params.get("name", ""),
                            params.get("arguments") or {},
                        )
                    ),
                )
            elif method == "ping":
                response = _result(request_id, {})
            elif request_id is None:
                continue
            else:
                response = _error(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            response = _result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
