from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


class ManagedMCPRuntimeError(RuntimeError):
    pass


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

    async def request(self, method: str, path: str, *, actor_id: str | None = None, json=None):
        if not self.enabled:
            raise ManagedMCPRuntimeError('managed MCP runtime is not configured')
        headers = {'Authorization': f'Bearer {self.token}'}
        if actor_id:
            headers['X-Actor-Id'] = actor_id
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                response = await client.request(method, f'{self.base_url}{path}', headers=headers, json=json)
        except httpx.HTTPError as exc:
            raise ManagedMCPRuntimeError(f'managed MCP runtime unavailable: {exc}') from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail', response.text)
            except Exception:
                detail = response.text
            raise ManagedMCPRuntimeError(str(detail))
        if response.status_code == 204:
            return None
        return response.json()

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
        return {
            'type': 'mcp',
            'url': f'{self.base_url}/mcp/{server["id"]}',
            'auth_type': 'bearer',
            'key': self.token,
            'config': {
                'enable': server.get('enabled', False) and server.get('state') == 'ready',
                'access_grants': server.get('access_grants') or [],
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
