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


async def test_runtime_client_preserves_structured_error_context(monkeypatch):
    monkeypatch.setattr(httpx, 'AsyncClient', FakeAsyncClient)
    FakeAsyncClient.response = httpx.Response(
        503,
        json={
            'detail': {
                'code': 'mcp_server_unavailable',
                'message': "Managed MCP server 'web' is unavailable",
                'request_id': 'trace-123',
                'retryable': True,
                'server_id': 'web',
                'state': 'failed',
                'reason': 'child exited',
            }
        },
        request=httpx.Request('GET', 'http://runtime'),
    )
    client = ManagedMCPRuntimeClient('http://runtime', 'secret')

    with pytest.raises(ManagedMCPRuntimeError) as caught:
        await client.get_server('web')

    error = caught.value
    assert error.status_code == 503
    assert error.code == 'mcp_server_unavailable'
    assert error.request_id == 'trace-123'
    assert error.retryable is True
    assert error.context == {
        'server_id': 'web',
        'state': 'failed',
        'reason': 'child exited',
    }
    assert error.detail()['message'] == "Managed MCP server 'web' is unavailable"


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


def test_mcp_transport_error_surfaces_structured_runtime_context(monkeypatch):
    monkeypatch.setenv('WEBUI_SECRET_KEY', 'test-secret')
    from open_webui.utils.mcp.client import describe_mcp_transport_error

    request = httpx.Request('POST', 'http://runtime/mcp/web')
    response = httpx.Response(
        503,
        json={
            'detail': {
                'code': 'mcp_server_unavailable',
                'message': "Managed MCP server 'web' is unavailable",
                'request_id': 'trace-123',
                'server_id': 'web',
                'state': 'failed',
                'reason': 'child exited',
            }
        },
        request=request,
    )
    status_error = httpx.HTTPStatusError('unavailable', request=request, response=response)

    message = describe_mcp_transport_error(ExceptionGroup('transport failed', [status_error]))

    assert message == (
        "Managed MCP server 'web' is unavailable "
        '(server=web, state=failed, reason=child exited, request_id=trace-123)'
    )
