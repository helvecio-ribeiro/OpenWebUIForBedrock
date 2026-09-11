from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.internal.db import get_async_session
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.utils.access_control import has_access
from open_webui.utils.auth import get_verified_user
from open_webui.utils.mcp.runtime_client import ManagedMCPRuntimeError, managed_mcp_runtime

log = logging.getLogger(__name__)
router = APIRouter()


class MCPServerCatalogEntry(BaseModel):
    id: str
    name: str
    description: str = ''
    source: Literal['managed', 'remote']
    transport: Literal['streamable_http'] = 'streamable_http'
    status: str
    enabled: bool
    selectable: bool
    authenticated: bool | None = None
    access_grants: list[dict] = Field(default_factory=list)
    created_at: int
    updated_at: int


def build_mcp_server_catalog(
    configured_connections: list[dict], managed_servers: list[dict], now: int | None = None
) -> list[dict]:
    """Merge MCP sources into a secret-free catalog. Configured entries win ID collisions."""
    timestamp = int(time.time()) if now is None else now
    entries: dict[str, dict] = {}

    for connection in configured_connections:
        if connection.get('type') != 'mcp':
            continue
        info = connection.get('info') or {}
        server_id = info.get('id')
        if not server_id:
            log.warning('Skipping configured MCP connection without info.id')
            continue
        config = connection.get('config') or {}
        enabled = bool(config.get('enable'))
        auth_type = connection.get('auth_type', 'none')
        entries[server_id] = {
            'id': server_id,
            'name': info.get('name') or info.get('title') or server_id,
            'description': info.get('description') or '',
            'source': 'remote',
            'transport': 'streamable_http',
            'status': 'available' if enabled else 'disabled',
            'enabled': enabled,
            'selectable': enabled,
            'authenticated': None if auth_type not in ('oauth_2.1', 'oauth_2.1_static') else False,
            'auth_type': auth_type,
            'access_grants': config.get('access_grants') or [],
            'created_at': timestamp,
            'updated_at': timestamp,
        }

    for server in managed_servers:
        server_id = server.get('id')
        if not server_id or server_id in entries:
            continue
        enabled = bool(server.get('enabled'))
        state = server.get('state', 'stopped')
        entries[server_id] = {
            'id': server_id,
            'name': server.get('name') or server_id,
            'description': server.get('description') or '',
            'source': 'managed',
            'transport': 'streamable_http',
            'status': state,
            'enabled': enabled,
            'selectable': enabled and state == 'ready',
            'authenticated': True,
            'auth_type': 'bearer',
            'access_grants': server.get('access_grants') or [],
            'created_at': server.get('created_at', timestamp),
            'updated_at': server.get('updated_at', timestamp),
        }

    return sorted(entries.values(), key=lambda item: (item['name'].lower(), item['id']))


@router.get('/servers', response_model=list[MCPServerCatalogEntry])
async def get_mcp_servers(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    configured = list(await Config.get('tool_server.connections', []) or [])
    managed = []
    if managed_mcp_runtime.enabled:
        try:
            managed = await managed_mcp_runtime.list_servers()
        except ManagedMCPRuntimeError as exc:
            log.warning('Managed MCP runtime unavailable while building catalog: %s', exc)

    catalog = build_mcp_server_catalog(configured, managed)
    group_ids: set[str] = set()
    if not (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL):
        group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, db=db)}

    visible = []
    for entry in catalog:
        if not (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL) and not await has_access(
            user.id, 'read', entry['access_grants'], group_ids, db=db
        ):
            continue

        if entry.pop('auth_type') in ('oauth_2.1', 'oauth_2.1_static'):
            oauth_server_id = entry['id'].split(':')[-1]
            token = await request.app.state.oauth_client_manager.get_oauth_token(
                user.id, f'mcp:{oauth_server_id}'
            )
            entry['authenticated'] = token is not None
        visible.append(entry)

    return visible
