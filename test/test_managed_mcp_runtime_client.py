import json

import httpx
import pytest

from open_webui.utils.mcp.runtime_client import ManagedMCPRuntimeClient, ManagedMCPRuntimeError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return 'asyncio'


class FakeAsyncClient:
    response = httpx.Response(200, json=[])
    request_data = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def request(self, method, url, **kwargs):
        type(self).request_data = (method, url, kwargs)
        return type(self).response


async def test_runtime_client_authenticates_and_disables_proxy_environment(monkeypatch):
    monkeypatch.setattr(httpx, 'AsyncClient', FakeAsyncClient)
    client = ManagedMCPRuntimeClient('http://127.0.0.1:9090/', 'secret')
    assert await client.list_servers() == []
    method, url, kwargs = FakeAsyncClient.request_data
    assert (method, url) == ('GET', 'http://127.0.0.1:9090/api/servers')
    assert kwargs['headers']['Authorization'] == 'Bearer secret'


async def test_runtime_client_uses_discovery_endpoint(monkeypatch):
    monkeypatch.setattr(httpx, 'AsyncClient', FakeAsyncClient)
    FakeAsyncClient.response = httpx.Response(200, json={'roots': [], 'services': [], 'errors': []})
    client = ManagedMCPRuntimeClient('http://127.0.0.1:9090', 'secret')
    result = await client.discover()
    assert result['services'] == []
    method, url, _ = FakeAsyncClient.request_data
    assert (method, url) == ('GET', 'http://127.0.0.1:9090/api/discovery')


async def test_runtime_client_surfaces_runtime_errors(monkeypatch):
    monkeypatch.setattr(httpx, 'AsyncClient', FakeAsyncClient)
    FakeAsyncClient.response = httpx.Response(
        400,
        content=json.dumps({'detail': 'invalid manifest'}),
        request=httpx.Request('POST', 'http://runtime'),
    )
    client = ManagedMCPRuntimeClient('http://runtime', 'secret')
    with pytest.raises(ManagedMCPRuntimeError, match='invalid manifest'):
        await client.create_server({}, 'admin')


def test_runtime_connection_uses_existing_mcp_shape():
    client = ManagedMCPRuntimeClient('http://runtime', 'secret')
    connection = client.connection(
        {
            'id': 'calendar',
            'name': 'Calendar',
            'description': 'Events',
            'enabled': True,
            'state': 'ready',
            'access_grants': [{'principal_type': 'user', 'principal_id': 'u1', 'permission': 'read'}],
        }
    )
    assert connection['url'] == 'http://runtime/mcp/calendar'
    assert connection['auth_type'] == 'bearer'
    assert connection['config']['enable'] is True
    assert connection['info']['managed'] is True


def test_runtime_connection_shares_managed_server_when_grants_are_unspecified():
    client = ManagedMCPRuntimeClient('http://runtime', 'secret')
    connection = client.connection(
        {
            'id': 'calendar',
            'enabled': True,
            'state': 'ready',
            'access_grants': [],
        }
    )

    assert connection['config']['access_grants'] == [
        {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
    ]


def test_runtime_token_file_is_supported(tmp_path, monkeypatch):
    token_file = tmp_path / 'token'
    token_file.write_text('file-secret\n')
    monkeypatch.delenv('MANAGED_MCP_RUNTIME_TOKEN', raising=False)
    monkeypatch.setenv('MANAGED_MCP_RUNTIME_TOKEN_FILE', str(token_file))
    client = ManagedMCPRuntimeClient('http://runtime')
    assert client.token == 'file-secret'
