from __future__ import annotations

import asyncio
import os
import signal
from contextlib import asynccontextmanager
from typing import AsyncIterator, TextIO

import anyio
from mcp import StdioServerParameters, types
from mcp.shared.message import SessionMessage


@asynccontextmanager
async def managed_stdio_client(
    server: StdioServerParameters,
    errlog: TextIO,
    termination_timeout: float = 5,
    user: int | None = None,
    group: int | None = None,
) -> AsyncIterator[tuple]:
    """MCP stdio transport with strict env handling and process-group cleanup.

    The SDK's stdio transport has exhibited initialization hangs with the
    AnyIO/process combination used by this project. The protocol session still
    uses the official SDK; only subprocess pipe forwarding is implemented here.
    """
    receive_send, receive_stream = anyio.create_memory_object_stream(0)
    write_stream, write_receive = anyio.create_memory_object_stream(0)
    process = await asyncio.create_subprocess_exec(
        server.command,
        *server.args,
        cwd=server.cwd,
        env=server.env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=errlog.fileno(),
        start_new_session=True,
        **({'user': user} if user is not None else {}),
        **({'group': group} if group is not None else {}),
    )

    async def read_stdout():
        assert process.stdout
        try:
            async with receive_send:
                while line := await process.stdout.readline():
                    try:
                        message = types.JSONRPCMessage.model_validate_json(line)
                        await receive_send.send(SessionMessage(message))
                    except Exception as exc:
                        await receive_send.send(exc)
        except (anyio.ClosedResourceError, asyncio.CancelledError):
            pass

    async def write_stdin():
        assert process.stdin
        try:
            async with write_receive:
                async for session_message in write_receive:
                    payload = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    process.stdin.write((payload + '\n').encode(server.encoding))
                    await process.stdin.drain()
        except (anyio.ClosedResourceError, ConnectionError, asyncio.CancelledError):
            pass

    reader = asyncio.create_task(read_stdout(), name='managed-mcp-stdout')
    writer = asyncio.create_task(write_stdin(), name='managed-mcp-stdin')
    try:
        yield receive_stream, write_stream, process
    finally:
        await write_stream.aclose()
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=termination_timeout)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=termination_timeout)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        for task in (reader, writer):
            task.cancel()
        await asyncio.gather(reader, writer, return_exceptions=True)
        await receive_stream.aclose()
        await receive_send.aclose()
        await write_receive.aclose()
