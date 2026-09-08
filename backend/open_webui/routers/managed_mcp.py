from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from open_webui.events import EVENTS, publish_event
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.mcp_runtime.schemas import ManagedServerCreate, ManagedServerUpdate
from open_webui.utils.auth import get_admin_user
from open_webui.utils.mcp.runtime_client import ManagedMCPRuntimeError, managed_mcp_runtime

router = APIRouter()


async def require_runtime():
    if not managed_mcp_runtime.enabled:
        raise HTTPException(status_code=503, detail='managed MCP runtime is not configured')


async def validate_access_grants(data: dict) -> None:
    for grant in data.get('access_grants') or []:
        principal_type = grant.get('principal_type')
        principal_id = grant.get('principal_id')
        if principal_type == 'user' and principal_id != '*' and not await Users.get_user_by_id(principal_id):
            raise HTTPException(status_code=400, detail=f'unknown user: {principal_id}')
        if principal_type == 'group' and not await Groups.get_group_by_id(principal_id):
            raise HTTPException(status_code=400, detail=f'unknown group: {principal_id}')


def runtime_error(exc: ManagedMCPRuntimeError):
    message = str(exc)
    code = 404 if 'not found' in message else 502
    raise HTTPException(status_code=code, detail=message) from exc


@router.get('/', dependencies=[Depends(require_runtime)])
async def list_servers(user=Depends(get_admin_user)):
    try:
        return await managed_mcp_runtime.list_servers()
    except ManagedMCPRuntimeError as exc:
        runtime_error(exc)


@router.get('/discover', dependencies=[Depends(require_runtime)])
async def discover_servers(user=Depends(get_admin_user)):
    try:
        return await managed_mcp_runtime.discover()
    except ManagedMCPRuntimeError as exc:
        runtime_error(exc)


@router.get('/{server_id}', dependencies=[Depends(require_runtime)])
async def get_server(server_id: str, user=Depends(get_admin_user)):
    try:
        return await managed_mcp_runtime.get_server(server_id)
    except ManagedMCPRuntimeError as exc:
        runtime_error(exc)


@router.post('/', dependencies=[Depends(require_runtime)], status_code=201)
async def create_server(request: Request, form_data: ManagedServerCreate, user=Depends(get_admin_user)):
    data = form_data.model_dump(mode='json')
    await validate_access_grants(data)
    try:
        result = await managed_mcp_runtime.create_server(data, user.id)
        await publish_event(
            request,
            EVENTS.MANAGED_MCP_REGISTERED,
            actor=user,
            subject_id=result['id'],
            subject_type='managed_mcp_server',
        )
        return result
    except ManagedMCPRuntimeError as exc:
        runtime_error(exc)


@router.patch('/{server_id}', dependencies=[Depends(require_runtime)])
async def update_server(
    request: Request,
    server_id: str,
    form_data: ManagedServerUpdate,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump(mode='json', exclude_none=True)
    await validate_access_grants(data)
    try:
        result = await managed_mcp_runtime.update_server(server_id, data)
        await publish_event(
            request,
            EVENTS.MANAGED_MCP_UPDATED,
            actor=user,
            subject_id=server_id,
            subject_type='managed_mcp_server',
            data={'changed_fields': sorted(data)},
        )
        return result
    except ManagedMCPRuntimeError as exc:
        runtime_error(exc)


@router.post('/{server_id}/{action}', dependencies=[Depends(require_runtime)])
async def server_action(request: Request, server_id: str, action: str, user=Depends(get_admin_user)):
    if action not in {'start', 'stop', 'restart'}:
        raise HTTPException(status_code=404, detail='unknown managed MCP action')
    try:
        result = await managed_mcp_runtime.action(server_id, action)
        event = {
            'start': EVENTS.MANAGED_MCP_STARTED,
            'stop': EVENTS.MANAGED_MCP_STOPPED,
            'restart': EVENTS.MANAGED_MCP_RESTARTED,
        }[action]
        await publish_event(
            request,
            event,
            actor=user,
            subject_id=server_id,
            subject_type='managed_mcp_server',
        )
        return result
    except ManagedMCPRuntimeError as exc:
        runtime_error(exc)


@router.get('/{server_id}/logs', dependencies=[Depends(require_runtime)])
async def server_logs(server_id: str, limit: int = 200, user=Depends(get_admin_user)):
    try:
        return await managed_mcp_runtime.logs(server_id, limit)
    except ManagedMCPRuntimeError as exc:
        runtime_error(exc)


@router.delete('/{server_id}', dependencies=[Depends(require_runtime)], status_code=204)
async def delete_server(request: Request, server_id: str, user=Depends(get_admin_user)):
    try:
        await managed_mcp_runtime.delete_server(server_id)
        await publish_event(
            request,
            EVENTS.MANAGED_MCP_REMOVED,
            actor=user,
            subject_id=server_id,
            subject_type='managed_mcp_server',
        )
    except ManagedMCPRuntimeError as exc:
        runtime_error(exc)
