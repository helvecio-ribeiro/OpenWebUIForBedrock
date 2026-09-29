from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


class ManagedMCPRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = 'managed_mcp_runtime_error',
        request_id: str | None = None,
        retryable: bool = False,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.retryable = retryable
        self.context = context or {}

    def detail(self) -> dict:
        return {
            'code': self.code,
            'message': str(self),
            'request_id': self.request_id,
            'retryable': self.retryable,
            **self.context,
        }


class ManagedMCPRuntimeClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 30):
        self.base_url = (base_url if base_url is not None else os.getenv('MANAGED_MCP_RUNTIME_URL', '')).rstrip('/')
        if token is None:
            token = os.getenv('MANAGED_MCP_RUNTIME_TOKEN', '')
            token_file = os.getenv('MANAGED_MCP_RUNTIME_TOKEN_FILE', '')
            if not token and token_file:
                try:
                    token = Path(token_file).read_text(encoding='utf-8').strip()
                except OSError:
                    token = ''
        self.token = token
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.token)

    async def request(
        self,
        method: str,
        path: str,
        *,
        actor_id: str | None = None,
        json=None,
        timeout: float | None = None,
    ):
        if not self.enabled:
            raise ManagedMCPRuntimeError('managed MCP runtime is not configured')
        headers = {'Authorization': f'Bearer {self.token}'}
        if actor_id:
            headers['X-Actor-Id'] = actor_id
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout if timeout is None else timeout,
                trust_env=False,
            ) as client:
                response = await client.request(method, f'{self.base_url}{path}', headers=headers, json=json)
        except httpx.HTTPError as exc:
            raise ManagedMCPRuntimeError(
                f'managed MCP runtime unavailable: {exc}',
                code='runtime_connection_failed',
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail', response.text)
            except Exception:
                detail = response.text
            if isinstance(detail, dict):
                message = detail.get('message') or f'managed MCP runtime returned HTTP {response.status_code}'
                context = {
                    key: value
                    for key, value in detail.items()
                    if key not in {'message', 'code', 'request_id', 'retryable'}
                }
                raise ManagedMCPRuntimeError(
                    str(message),
                    status_code=response.status_code,
                    code=detail.get('code', 'runtime_http_error'),
                    request_id=detail.get('request_id') or response.headers.get('x-request-id'),
                    retryable=bool(detail.get('retryable', response.status_code >= 500)),
                    context=context,
                )
            raise ManagedMCPRuntimeError(
                str(detail) or f'managed MCP runtime returned HTTP {response.status_code}',
                status_code=response.status_code,
                code='runtime_http_error',
                request_id=response.headers.get('x-request-id'),
                retryable=response.status_code >= 500,
            )
        if response.status_code == 204:
            return None
        return response.json()

    async def health_diagnostic(self, timeout: float = 1.0) -> dict[str, Any]:
        """Return a bounded, secret-free runtime diagnostic that never raises."""
        if not self.enabled:
            return {
                'configured': False,
                'available': None,
                'status': 'disabled',
            }
        try:
            result = await self.request('GET', '/readyz', timeout=timeout)
            runtime_status = result.get('status', 'unknown') if isinstance(result, dict) else 'unknown'
            failed_servers = result.get('failed_servers', []) if isinstance(result, dict) else []
            return {
                'configured': True,
                'available': True,
                'status': runtime_status if runtime_status in {'ready', 'degraded'} else 'unknown',
                'failed_server_count': len(failed_servers) if isinstance(failed_servers, list) else 0,
            }
        except ManagedMCPRuntimeError as exc:
            return {
                'configured': True,
                'available': False,
                'status': 'unavailable',
                'error_code': exc.code,
            }
        except Exception:
            return {
                'configured': True,
                'available': False,
                'status': 'unavailable',
                'error_code': 'runtime_diagnostic_failed',
            }

    async def list_servers(self) -> list[dict[str, Any]]:
        return await self.request('GET', '/api/servers')

    async def discover(self) -> dict[str, Any]:
        return await self.request('GET', '/api/discovery')

    async def get_server(self, server_id: str) -> dict[str, Any]:
        return await self.request('GET', f'/api/servers/{server_id}')

    async def create_server(self, data: dict, actor_id: str) -> dict[str, Any]:
        return await self.request('POST', '/api/servers', actor_id=actor_id, json=data)

    async def update_server(self, server_id: str, data: dict) -> dict[str, Any]:
        return await self.request('PATCH', f'/api/servers/{server_id}', json=data)

    async def action(self, server_id: str, action: str) -> dict[str, Any]:
        return await self.request('POST', f'/api/servers/{server_id}/{action}')

    async def logs(self, server_id: str, limit: int = 200) -> dict[str, Any]:
        return await self.request('GET', f'/api/servers/{server_id}/logs?limit={limit}')

    async def delete_server(self, server_id: str) -> None:
        await self.request('DELETE', f'/api/servers/{server_id}')

    def connection(self, server: dict[str, Any]) -> dict[str, Any]:
        # Managed local services are installed once for this Open WebUI
        # instance and are shared with its authenticated users by default.
        access_grants = server.get('access_grants') or [
            {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
        ]
        return {
            'type': 'mcp',
            'url': f'{self.base_url}/mcp/{server["id"]}',
            'auth_type': 'bearer',
            'key': self.token,
            'config': {
                'enable': server.get('enabled', False) and server.get('state') == 'ready',
                'access_grants': access_grants,
            },
            'info': {
                'id': server['id'],
                'name': server.get('name') or server['id'],
                'description': server.get('description') or '',
                'managed': True,
            },
        }

    async def connections(self, include_unavailable: bool = False) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        servers = await self.list_servers()
        connections = [self.connection(server) for server in servers]
        return connections if include_unavailable else [c for c in connections if c['config']['enable']]


managed_mcp_runtime = ManagedMCPRuntimeClient()
