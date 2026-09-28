from __future__ import annotations

import asyncio
import contextvars
import hmac
import json
import logging
from uuid import uuid4
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp import types
from mcp.server import Server
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.exceptions import HTTPException as StarletteHTTPException

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

    def request_id(request: Request) -> str:
        return request.headers.get('x-request-id') or uuid4().hex

    def error_detail(
        *, code: str, message: str, request_id_value: str, retryable: bool = False, **context
    ) -> dict:
        return {
            'code': code,
            'message': message,
            'request_id': request_id_value,
            'retryable': retryable,
            **{key: value for key, value in context.items() if value is not None},
        }

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        trace_id = request_id(request)
        message = exc.detail if isinstance(exc.detail, str) else 'Request failed'
        detail = (
            exc.detail
            if isinstance(exc.detail, dict) and exc.detail.get('message')
            else error_detail(
                code='runtime_http_error',
                message=message,
                request_id_value=trace_id,
                retryable=exc.status_code >= 500,
            )
        )
        detail.setdefault('request_id', trace_id)
        log_method = log.error if exc.status_code >= 500 else log.warning
        log_method(
            'Managed MCP HTTP error status=%s method=%s path=%s request_id=%s code=%s message=%s',
            exc.status_code,
            request.method,
            request.url.path,
            trace_id,
            detail.get('code'),
            detail.get('message'),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={'detail': detail},
            headers={'X-Request-Id': trace_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        trace_id = request_id(request)
        detail = error_detail(
            code='invalid_request',
            message='Managed MCP request validation failed',
            request_id_value=trace_id,
            errors=exc.errors(),
        )
        log.warning(
            'Managed MCP validation error method=%s path=%s request_id=%s errors=%s',
            request.method,
            request.url.path,
            trace_id,
            exc.errors(),
        )
        return JSONResponse(
            status_code=422,
            content={'detail': detail},
            headers={'X-Request-Id': trace_id},
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        trace_id = request_id(request)
        log.exception(
            'Managed MCP unhandled error method=%s path=%s request_id=%s',
            request.method,
            request.url.path,
            trace_id,
        )
        return JSONResponse(
            status_code=500,
            content={
                'detail': error_detail(
                    code='runtime_internal_error',
                    message='Managed MCP runtime encountered an internal error',
                    request_id_value=trace_id,
                    retryable=True,
                )
            },
            headers={'X-Request-Id': trace_id},
        )

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
            trace_id = dict(scope.get('headers', [])).get(b'x-request-id', b'').decode() or uuid4().hex

            async def send_error(status_code: int, detail: dict):
                body = json.dumps({'detail': detail}, separators=(',', ':')).encode()
                await send(
                    {
                        'type': 'http.response.start',
                        'status': status_code,
                        'headers': [
                            (b'content-type', b'application/json'),
                            (b'x-request-id', trace_id.encode()),
                        ],
                    }
                )
                await send({'type': 'http.response.body', 'body': body})

            if scope['type'] != 'http':
                detail = error_detail(
                    code='unsupported_scope',
                    message='Managed MCP transport supports HTTP requests only',
                    request_id_value=trace_id,
                )
                log.warning('Managed MCP transport rejected non-HTTP scope request_id=%s', trace_id)
                await send_error(404, detail)
                return
            headers = {key.lower(): value for key, value in scope.get('headers', [])}
            expected = f'Bearer {settings.token}'.encode()
            if not hmac.compare_digest(headers.get(b'authorization', b''), expected):
                detail = error_detail(
                    code='invalid_runtime_token',
                    message='Managed MCP runtime authentication failed',
                    request_id_value=trace_id,
                )
                log.warning(
                    'Managed MCP authentication failure method=%s path=%s request_id=%s',
                    scope.get('method'),
                    scope.get('path'),
                    trace_id,
                )
                await send_error(401, detail)
                return
            path = scope.get('path', '')
            root_path = scope.get('root_path', '')
            if root_path and path.startswith(root_path):
                path = path[len(root_path) :]
            server_id = path.strip('/').split('/', 1)[0]
            actor = supervisor.actors.get(server_id)
            if not actor or actor.state.value != 'ready':
                state = actor.state.value if actor else 'missing'
                reason = actor.last_error if actor else 'server is not registered with the active supervisor'
                detail = error_detail(
                    code='mcp_server_unavailable',
                    message=f"Managed MCP server '{server_id}' is unavailable",
                    request_id_value=trace_id,
                    retryable=state in {'starting', 'failed'},
                    server_id=server_id,
                    state=state,
                    reason=reason,
                )
                log.warning(
                    'MCP request rejected server=%s state=%s request_id=%s error=%s',
                    server_id,
                    state,
                    trace_id,
                    reason,
                )
                await send_error(503, detail)
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
