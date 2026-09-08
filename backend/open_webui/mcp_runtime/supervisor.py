from __future__ import annotations

import asyncio
import io
import os
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters

from .registry import package_digest
from .schemas import ManagedServer, ManagedServerView, ServerState
from .transport import managed_stdio_client


class SupervisorError(RuntimeError):
    pass


@dataclass
class ActorRequest:
    operation: str
    name: str | None
    arguments: dict[str, Any]
    future: asyncio.Future


class BoundedLog(io.TextIOBase):
    def __init__(self, max_lines: int = 500):
        self._lines = deque(maxlen=max_lines)
        self._partial = ''
        self._lock = threading.Lock()
        self._redactions: set[str] = set()
        self._read_fd, self._write_fd = os.pipe()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def fileno(self) -> int:
        return self._write_fd

    def _drain(self) -> None:
        while True:
            try:
                chunk = os.read(self._read_fd, 8192)
            except OSError:
                return
            if not chunk:
                return
            self._append(chunk.decode('utf-8', errors='replace'))

    def write(self, value: str) -> int:
        self._append(value)
        return len(value)

    def _append(self, value: str) -> None:
        with self._lock:
            value = self._partial + value
            self._partial = ''
            for secret in self._redactions:
                value = value.replace(secret, '[REDACTED]')
            parts = value.splitlines(keepends=True)
            for part in parts:
                if part.endswith(('\n', '\r')):
                    self._lines.append(part.rstrip('\r\n'))
                else:
                    self._partial = part

    def lines(self, limit: int = 200) -> list[str]:
        with self._lock:
            values = list(self._lines)
            if self._partial:
                values.append(self._partial)
        return values[-max(0, min(limit, 500)) :]

    def set_redactions(self, values) -> None:
        with self._lock:
            self._redactions = {value for value in values if value}

    def close(self) -> None:
        if not self.closed:
            for descriptor in (self._write_fd, self._read_fd):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        super().close()


class ServerActor:
    def __init__(
        self,
        server: ManagedServer,
        base_environment: dict[str, str],
        launch_uid: int | None = None,
        launch_gid: int | None = None,
    ):
        self.server = server
        self.base_environment = base_environment
        self.launch_uid = launch_uid
        self.launch_gid = launch_gid
        self.state = ServerState.stopped
        self.last_error: str | None = None
        self.tools: list[dict] = []
        self.logs = BoundedLog()
        self._queue: asyncio.Queue[ActorRequest] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            await self.wait_ready()
            return
        if package_digest(Path(self.server.package_path)) != self.server.package_digest:
            raise SupervisorError('package digest changed after registration')
        self.state = ServerState.starting
        self.last_error = None
        self._ready.clear()
        self._task = asyncio.create_task(self._run(), name=f'mcp:{self.server.id}')
        await self.wait_ready()

    async def wait_ready(self) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.server.manifest.limits.startup_timeout_seconds)
        except TimeoutError as exc:
            await self.stop()
            raise SupervisorError(f'server {self.server.id} startup timed out') from exc
        if self.state != ServerState.ready:
            raise SupervisorError(self.last_error or f'server {self.server.id} failed to start')

    def _environment(self) -> dict[str, str]:
        values = dict(self.base_environment)
        values['OPEN_WEBUI_MCP_READ_ONLY'] = 'true' if self.server.manifest.security.read_only else 'false'
        for name, definition in self.server.manifest.environment.items():
            if definition.default is not None:
                values[name] = definition.default
        values.update(self.server.environment)
        for name, reference in self.server.secret_environment.items():
            value = os.environ.get(reference)
            if value is None:
                raise SupervisorError(f'secret environment reference {reference} for {name} is unset')
            values[name] = value
        self.logs.set_redactions(values[name] for name in self.server.secret_environment if name in values)
        return values

    def _working_directory(self) -> Path:
        package = Path(self.server.package_path).resolve()
        working = (package / self.server.manifest.runtime.working_directory).resolve()
        if working != package and not working.is_relative_to(package):
            raise SupervisorError('runtime working directory escapes package directory')
        return working

    async def _run(self) -> None:
        attempts = 0
        while True:
            try:
                await self._run_session()
                raise SupervisorError(f'server {self.server.id} exited unexpectedly')
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self.last_error = str(exc)
                self.state = ServerState.failed
                self._fail_pending(exc)
                limits = self.server.manifest.limits
                if limits.restart == 'on-failure' and attempts < limits.max_restarts:
                    attempts += 1
                    self.state = ServerState.starting
                    await asyncio.sleep(min(2 ** (attempts - 1), 30))
                    continue
                self._ready.set()
                return

    async def _run_session(self) -> None:
        try:
            parameters = StdioServerParameters(
                command=self.server.manifest.runtime.command,
                args=self.server.manifest.runtime.args,
                cwd=self._working_directory(),
                env=self._environment(),
            )
            async with managed_stdio_client(
                parameters,
                errlog=self.logs,
                user=self.launch_uid,
                group=self.launch_gid,
            ) as (read_stream, write_stream, process):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.tools = [
                        {
                            'name': tool.name,
                            'description': tool.description or '',
                            'parameters': tool.inputSchema,
                            **(
                                {'outputSchema': tool.outputSchema}
                                if getattr(tool, 'outputSchema', None) is not None
                                else {}
                            ),
                        }
                        for tool in listed.tools
                    ]
                    self.state = ServerState.ready
                    self._ready.set()
                    while True:
                        queued = asyncio.create_task(self._queue.get())
                        exited = asyncio.create_task(process.wait())
                        done, pending = await asyncio.wait({queued, exited}, return_when=asyncio.FIRST_COMPLETED)
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        if exited in done:
                            raise SupervisorError(f'server {self.server.id} exited with status {exited.result()}')
                        request = queued.result()
                        try:
                            if request.operation == 'list_tools':
                                result = self.tools
                            elif request.operation == 'call_tool' and request.name:
                                with anyio.fail_after(self.server.manifest.limits.call_timeout_seconds):
                                    response = await session.call_tool(request.name, request.arguments)
                                result = response.model_dump(mode='json')
                            else:
                                raise SupervisorError(f'unsupported actor operation: {request.operation}')
                            if not request.future.done():
                                request.future.set_result(result)
                        except BaseException as exc:
                            if not request.future.done():
                                request.future.set_exception(exc)
                            raise
                        finally:
                            self._queue.task_done()
        finally:
            if self.state not in (ServerState.failed, ServerState.stopping):
                self.state = ServerState.stopped

    def _fail_pending(self, exc: BaseException) -> None:
        while not self._queue.empty():
            request = self._queue.get_nowait()
            if not request.future.done():
                request.future.set_exception(exc)
            self._queue.task_done()

    async def request(self, operation: str, name: str | None = None, arguments=None):
        if self.state != ServerState.ready:
            raise SupervisorError(f'server {self.server.id} is not ready')
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(ActorRequest(operation, name, arguments or {}, future))
        return await future

    async def stop(self) -> None:
        if not self._task:
            self.state = ServerState.stopped
            return
        self.state = ServerState.stopping
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self.state = ServerState.stopped
        self._fail_pending(SupervisorError(f'server {self.server.id} stopped'))


class Supervisor:
    SAFE_ENVIRONMENT_NAMES = ('PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'UV_CACHE_DIR')

    def __init__(
        self,
        privilege_policy: dict | None = None,
        allow_root_runtime: bool = False,
        unprivileged_uid: int | None = None,
        unprivileged_gid: int | None = None,
    ):
        self.actors: dict[str, ServerActor] = {}
        self.privilege_policy = privilege_policy or {'servers': {}}
        self.allow_root_runtime = allow_root_runtime
        self.unprivileged_uid = unprivileged_uid
        self.unprivileged_gid = unprivileged_gid
        self.base_environment = {name: os.environ[name] for name in self.SAFE_ENVIRONMENT_NAMES if name in os.environ}

    def authorize(self, server: ManagedServer) -> None:
        security = server.manifest.security
        if security.profile != 'system-admin':
            return
        policy = (self.privilege_policy.get('servers') or {}).get(server.id) or {}
        if policy.get('package_path') != server.package_path:
            raise SupervisorError('system-admin package path is not authorized')
        if policy.get('package_digest') != server.package_digest:
            raise SupervisorError('system-admin package digest is not authorized')
        capabilities = set(policy.get('capabilities') or [])
        required = {'system-read'}
        if security.root_write:
            required.add('system-write')
        if not required.issubset(capabilities):
            raise SupervisorError('system-admin capabilities are not authorized')
        if not self.allow_root_runtime or os.geteuid() != 0:
            raise SupervisorError('system-admin requires an explicitly enabled root runtime service')

    async def start(self, server: ManagedServer) -> ServerActor:
        self.authorize(server)
        actor = self.actors.get(server.id)
        if actor and actor.server.package_digest != server.package_digest:
            await actor.stop()
            actor = None
        if actor is None:
            launch_uid = None
            launch_gid = None
            if os.geteuid() == 0 and server.manifest.security.profile == 'confined':
                if self.unprivileged_uid is None or self.unprivileged_gid is None:
                    raise SupervisorError('root runtime requires an unprivileged UID and GID for confined servers')
                launch_uid = self.unprivileged_uid
                launch_gid = self.unprivileged_gid
            actor = ServerActor(
                server,
                self.base_environment,
                launch_uid=launch_uid,
                launch_gid=launch_gid,
            )
            self.actors[server.id] = actor
        else:
            actor.server = server
        await actor.start()
        return actor

    async def stop(self, server_id: str) -> None:
        actor = self.actors.get(server_id)
        if actor:
            await actor.stop()

    async def remove(self, server_id: str) -> None:
        await self.stop(server_id)
        actor = self.actors.pop(server_id, None)
        if actor:
            actor.logs.close()

    async def shutdown(self) -> None:
        await asyncio.gather(*(actor.stop() for actor in self.actors.values()), return_exceptions=True)
        for actor in self.actors.values():
            actor.logs.close()

    async def list_tools(self, server_id: str) -> list[dict]:
        actor = self.actors.get(server_id)
        if not actor:
            raise SupervisorError(f'server {server_id} is not running')
        return await actor.request('list_tools')

    async def call_tool(self, server_id: str, name: str, arguments: dict) -> dict:
        actor = self.actors.get(server_id)
        if not actor:
            raise SupervisorError(f'server {server_id} is not running')
        return await actor.request('call_tool', name, arguments)

    def logs(self, server_id: str, limit: int = 200) -> list[str]:
        actor = self.actors.get(server_id)
        if not actor:
            raise SupervisorError(f'server {server_id} is not running')
        return actor.logs.lines(limit)

    def view(self, server: ManagedServer) -> ManagedServerView:
        actor = self.actors.get(server.id)
        return ManagedServerView(
            id=server.id,
            name=server.manifest.name,
            description=server.manifest.description,
            version=server.manifest.version,
            package_path=server.package_path,
            package_digest=server.package_digest,
            enabled=server.enabled,
            state=actor.state if actor else ServerState.stopped,
            last_error=actor.last_error if actor else None,
            tools=actor.tools if actor else [],
            access_grants=server.access_grants,
            security=server.manifest.security,
            created_by=server.created_by,
            created_at=server.created_at,
            updated_at=server.updated_at,
        )
