from __future__ import annotations

import asyncio
import contextvars
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from mcp import types
from mcp.server import Server
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .registry import JSONRegistry, RegistryDocument, RegistryError
from .schemas import MCPManifest, ManagedServerCreate, ManagedServerUpdate, ServerState
from .settings import RuntimeSettings
from .supervisor import Supervisor, SupervisorError

log = logging.getLogger(__name__)
current_server_id: contextvars.ContextVar[str | None] = contextvars.ContextVar('managed_mcp_server_id', default=None)


def create_app(settings: RuntimeSettings) -> FastAPI:
    registry = JSONRegistry(settings.registry_path, settings.package_roots)
    supervisor = Supervisor(
        settings.load_privilege_policy(),
        allow_root_runtime=settings.allow_root_runtime,
        unprivileged_uid=settings.unprivileged_uid,
        unprivileged_gid=settings.unprivileged_gid,
    )
    protocol_server = Server('open-webui-managed-mcp-runtime')

    @protocol_server.list_tools()
    async def list_tools() -> list[types.Tool]:
        server_id = current_server_id.get()
        if not server_id:
            raise SupervisorError('missing managed server context')
        specs = await supervisor.list_tools(server_id)
        return [
            types.Tool(
                name=spec['name'],
                description=spec.get('description', ''),
                inputSchema=spec.get('parameters') or {'type': 'object', 'properties': {}},
                outputSchema=spec.get('outputSchema'),
            )
            for spec in specs
        ]

    @protocol_server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict):
        server_id = current_server_id.get()
        if not server_id:
            raise SupervisorError('missing managed server context')
        result = await supervisor.call_tool(server_id, name, arguments)
        return types.CallToolResult.model_validate(result)

    session_manager = StreamableHTTPSessionManager(
        app=protocol_server,
        json_response=True,
        stateless=True,
    )
    protocol_app = StreamableHTTPASGIApp(session_manager)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        document = await registry.load()
        async with session_manager.run():
            starts = [supervisor.start(server) for server in document.servers.values() if server.enabled]
            if starts:
                results = await asyncio.gather(*starts, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        log.error('Failed to restore managed MCP server: %s', result)
            yield
        await supervisor.shutdown()

    app = FastAPI(title='Open WebUI Managed MCP Runtime', lifespan=lifespan)
    app.state.registry = registry
    app.state.supervisor = supervisor

    async def authenticate(authorization: str | None = Header(default=None)):
        expected = f'Bearer {settings.token}'
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid runtime token')

    def error(exc: Exception):
        code = status.HTTP_404_NOT_FOUND if 'not found' in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    @app.get('/healthz')
    async def health():
        return {'status': 'ok'}

    @app.get('/readyz', dependencies=[Depends(authenticate)])
    async def ready():
        document = await registry.snapshot()
        failed = [
            server.id
            for server in document.servers.values()
            if server.enabled
            and (server.id not in supervisor.actors or supervisor.actors[server.id].state.value != 'ready')
        ]
        return {'status': 'ready' if not failed else 'degraded', 'failed_servers': failed}

    @app.get('/api/servers', dependencies=[Depends(authenticate)])
    async def get_servers():
        document = await registry.snapshot()
        return [supervisor.view(server) for server in document.servers.values()]

    @app.get('/api/discovery', dependencies=[Depends(authenticate)])
    async def discover_servers():
        result = await registry.discover()
        for service in result['services']:
            if service['discovery_state'] == 'registered':
                actor = supervisor.actors.get(service['id'])
                service['runtime_state'] = actor.state.value if actor else ServerState.stopped.value
        return result

    @app.get('/api/schema/manifest', dependencies=[Depends(authenticate)])
    async def manifest_schema():
        return MCPManifest.model_json_schema()

    @app.get('/api/schema/registry', dependencies=[Depends(authenticate)])
    async def registry_schema():
        return RegistryDocument.model_json_schema()

    @app.get('/api/servers/{server_id}', dependencies=[Depends(authenticate)])
    async def get_server(server_id: str):
        document = await registry.snapshot()
        server = document.servers.get(server_id)
        if not server:
            raise HTTPException(status_code=404, detail='server not found')
        return supervisor.view(server)

    @app.post('/api/servers', dependencies=[Depends(authenticate)], status_code=201)
    async def create_server(form: ManagedServerCreate, x_actor_id: str = Header(default='system')):
        try:
            server = await registry.create(form, x_actor_id)
            if server.enabled:
                await supervisor.start(server)
            return supervisor.view(server)
        except (RegistryError, SupervisorError, ValueError) as exc:
            error(exc)

    @app.patch('/api/servers/{server_id}', dependencies=[Depends(authenticate)])
    async def update_server(server_id: str, form: ManagedServerUpdate):
        try:
            before = (await registry.snapshot()).servers.get(server_id)
            if not before:
                raise RegistryError(f'server {server_id} not found')
            server = await registry.update(server_id, form)
            if server.enabled:
                if before.enabled and (form.environment is not None or form.secret_environment is not None):
                    await supervisor.stop(server_id)
                await supervisor.start(server)
            elif before.enabled:
                await supervisor.stop(server_id)
            return supervisor.view(server)
        except (RegistryError, SupervisorError, ValueError) as exc:
            error(exc)

    @app.post('/api/servers/{server_id}/start', dependencies=[Depends(authenticate)])
    async def start_server(server_id: str):
        try:
            server = await registry.update(server_id, ManagedServerUpdate(enabled=True))
            await supervisor.start(server)
            return supervisor.view(server)
        except (RegistryError, SupervisorError, ValueError) as exc:
            error(exc)

    @app.post('/api/servers/{server_id}/stop', dependencies=[Depends(authenticate)])
    async def stop_server(server_id: str):
        try:
            server = await registry.update(server_id, ManagedServerUpdate(enabled=False))
            await supervisor.stop(server_id)
            return supervisor.view(server)
        except (RegistryError, SupervisorError, ValueError) as exc:
            error(exc)

    @app.post('/api/servers/{server_id}/restart', dependencies=[Depends(authenticate)])
    async def restart_server(server_id: str):
        try:
            document = await registry.snapshot()
            server = document.servers.get(server_id)
            if not server:
                raise RegistryError(f'server {server_id} not found')
            await supervisor.stop(server_id)
            await supervisor.start(server)
            return supervisor.view(server)
        except (RegistryError, SupervisorError, ValueError) as exc:
            error(exc)

    @app.delete('/api/servers/{server_id}', dependencies=[Depends(authenticate)], status_code=204)
    async def delete_server(server_id: str):
        try:
            await supervisor.remove(server_id)
            await registry.delete(server_id)
        except RegistryError as exc:
            error(exc)

    @app.get('/api/servers/{server_id}/tools', dependencies=[Depends(authenticate)])
    async def get_server_tools(server_id: str):
        try:
            return await supervisor.list_tools(server_id)
        except SupervisorError as exc:
            error(exc)

    @app.get('/api/servers/{server_id}/logs', dependencies=[Depends(authenticate)])
    async def get_server_logs(server_id: str, limit: int = 200):
        try:
            return {'lines': supervisor.logs(server_id, limit)}
        except SupervisorError as exc:
            error(exc)

    class MCPDispatcher:
        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                await send({'type': 'http.response.start', 'status': 404, 'headers': []})
                await send({'type': 'http.response.body', 'body': b''})
                return
            headers = {key.lower(): value for key, value in scope.get('headers', [])}
            expected = f'Bearer {settings.token}'.encode()
            if not hmac.compare_digest(headers.get(b'authorization', b''), expected):
                await send({'type': 'http.response.start', 'status': 401, 'headers': []})
                await send({'type': 'http.response.body', 'body': b'Unauthorized'})
                return
            path = scope.get('path', '')
            root_path = scope.get('root_path', '')
            if root_path and path.startswith(root_path):
                path = path[len(root_path) :]
            server_id = path.strip('/').split('/', 1)[0]
            actor = supervisor.actors.get(server_id)
            if not actor or actor.state.value != 'ready':
                await send({'type': 'http.response.start', 'status': 503, 'headers': []})
                await send({'type': 'http.response.body', 'body': b'MCP server unavailable'})
                return
            token = current_server_id.set(server_id)
            try:
                await protocol_app(scope, receive, send)
            finally:
                current_server_id.reset(token)

    app.mount('/mcp', MCPDispatcher())
    return app


def main():
    import uvicorn

    settings = RuntimeSettings.from_environment()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == '__main__':
    main()
