from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from open_webui.mcp_runtime.app import create_app
from open_webui.mcp_runtime.schemas import ServerState
from open_webui.mcp_runtime.settings import RuntimeSettings
from open_webui.mcp_runtime.supervisor import BoundedLog

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return 'asyncio'


class FakeActor:
    state = ServerState.ready
    last_error = None
    tools = [
        {
            'name': 'echo',
            'description': 'Echo a value.',
            'parameters': {
                'type': 'object',
                'properties': {'value': {'type': 'string'}},
                'required': ['value'],
            },
        }
    ]
    logs = BoundedLog()

    async def request(self, operation, name=None, arguments=None):
        if operation == 'list_tools':
            return self.tools
        return {
            'content': [{'type': 'text', 'text': arguments['value']}],
            'isError': False,
        }

    async def stop(self):
        self.state = ServerState.stopped


@pytest.fixture
def settings(tmp_path):
    packages = tmp_path / 'packages'
    packages.mkdir()
    return RuntimeSettings(
        registry_path=tmp_path / 'registry.json',
        package_roots=[packages],
        token='test-token',
    )


async def test_management_api_requires_runtime_token(settings):
    app = create_app(settings)
    await app.state.registry.load()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://runtime') as client:
        assert (await client.get('/healthz')).status_code == 200
        assert (await client.get('/api/servers')).status_code == 401
        response = await client.get('/api/servers', headers={'Authorization': 'Bearer test-token'})
        assert response.status_code == 200
        assert response.json() == []
        schema = await client.get('/api/schema/manifest', headers={'Authorization': 'Bearer test-token'})
        assert schema.status_code == 200
        assert schema.json()['properties']['schema_version']['const'] == 1


async def test_discovery_api_lists_registers_and_removes_local_packages(settings):
    package = settings.package_roots[0] / 'demo'
    package.mkdir()
    (package / 'mcp.yaml').write_text(
        '''schema_version: 1
id: demo
name: Demo Service
description: A locally discovered service.
version: 1.0.0
runtime:
  command: uv
  args: [run, --frozen, server.py]
''',
        encoding='utf-8',
    )
    (package / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1.0.0"\n')
    (package / 'uv.lock').write_text('version = 1\n')
    (package / 'server.py').write_text('')

    app = create_app(settings)
    await app.state.registry.load()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://runtime') as client:
        response = await client.get('/api/discovery', headers={'Authorization': 'Bearer test-token'})
    assert response.status_code == 200
    result = response.json()
    assert result['services'][0]['id'] == 'demo'
    assert result['services'][0]['discovery_state'] == 'available'

    async with httpx.AsyncClient(transport=transport, base_url='http://runtime') as client:
        headers = {'Authorization': 'Bearer test-token'}
        created = await client.post(
            '/api/servers',
            headers=headers,
            json={'package_path': str(package), 'enabled': False},
        )
        assert created.status_code == 201
        registered = await client.get('/api/discovery', headers=headers)
        assert registered.json()['services'][0]['discovery_state'] == 'registered'

        removed = await client.delete('/api/servers/demo', headers=headers)
        assert removed.status_code == 204
        available = await client.get('/api/discovery', headers=headers)
        assert available.json()['services'][0]['discovery_state'] == 'available'


async def test_mcp_endpoint_dispatches_tools_and_calls(settings):
    app = create_app(settings)
    app.state.supervisor.actors['demo'] = FakeActor()

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url='http://runtime',
            headers={'Authorization': 'Bearer test-token'},
        ) as http_client:
            async with streamable_http_client('http://runtime/mcp/demo', http_client=http_client) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert [tool.name for tool in tools.tools] == ['echo']
                    result = await session.call_tool('echo', {'value': 'hello'})
                    assert result.content[0].text == 'hello'


async def test_mcp_endpoint_rejects_bad_token_and_unknown_server(settings):
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://runtime') as client:
        bad = await client.post('/mcp/demo', headers={'Authorization': 'Bearer wrong'})
        assert bad.status_code == 401
        missing = await client.post('/mcp/missing', headers={'Authorization': 'Bearer test-token'})
        assert missing.status_code == 503
