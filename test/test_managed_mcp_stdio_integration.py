import sys
from pathlib import Path

import pytest

from open_webui.mcp_runtime.registry import JSONRegistry
from open_webui.mcp_runtime.schemas import ManagedServerCreate
from open_webui.mcp_runtime.supervisor import Supervisor

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def write_protocol_fixture(package: Path):
    package.mkdir()
    (package / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\n')
    (package / 'uv.lock').write_text('version = 1\n')
    (package / 'mcp.yaml').write_text(f'''schema_version: 1
id: protocol-fixture
name: Protocol Fixture
version: "1"
runtime:
  command: {sys.executable}
  args: [server.py]
limits:
  startup_timeout_seconds: 5
  call_timeout_seconds: 5
  restart: never
''')
    (package / 'server.py').write_text('''import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if request.get("id") is None:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fixture", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{
            "name": "echo",
            "description": "Echo text",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        }]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": request["params"]["arguments"]["text"]}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
''')


async def test_supervisor_owns_real_stdio_session_and_calls_tool(tmp_path):
    root = tmp_path / 'packages'
    root.mkdir()
    package = root / 'fixture'
    write_protocol_fixture(package)
    registry = JSONRegistry(tmp_path / 'registry.json', [root])
    await registry.load()
    server = await registry.create(ManagedServerCreate(package_path=str(package)), 'test')
    supervisor = Supervisor()
    try:
        actor = await supervisor.start(server)
        assert actor.state.value == 'ready'
        assert [tool['name'] for tool in await supervisor.list_tools(server.id)] == ['echo']
        result = await supervisor.call_tool(server.id, 'echo', {'text': 'real subprocess'})
        assert result['content'][0]['text'] == 'real subprocess'
    finally:
        await supervisor.shutdown()
    assert actor.state.value == 'stopped'
