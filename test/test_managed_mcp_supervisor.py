import asyncio
import os
from pathlib import Path

import pytest

from open_webui.mcp_runtime.registry import package_digest
from open_webui.mcp_runtime.schemas import MCPManifest, ManagedServer
from open_webui.mcp_runtime.supervisor import BoundedLog, ServerActor, Supervisor, SupervisorError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def server_record(tmp_path: Path, *, profile='confined', root_write=False, read_only=True):
    for name in ('mcp.yaml', 'pyproject.toml', 'uv.lock', 'server.py'):
        (tmp_path / name).write_text(name)
    manifest = MCPManifest.model_validate(
        {
            'schema_version': 1,
            'id': 'filesystem-tools',
            'name': 'Filesystem',
            'version': '1',
            'runtime': {'command': 'uv'},
            'environment': {'SECRET': {'secret': True}},
            'security': {
                'profile': profile,
                'read_only': read_only,
                'root_write': root_write,
            },
        }
    )
    return ManagedServer(
        id=manifest.id,
        package_path=str(tmp_path),
        package_digest=package_digest(tmp_path),
        manifest=manifest,
        secret_environment={'SECRET': 'SECRET_REFERENCE'},
        created_by='admin',
        created_at=1,
        updated_at=1,
    )


def test_actor_environment_is_allowlisted_and_resolves_secret_reference(tmp_path, monkeypatch):
    server = server_record(tmp_path)
    monkeypatch.setenv('SECRET_REFERENCE', 'resolved')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'must-not-leak')
    actor = ServerActor(server, {'PATH': '/bin'})
    environment = actor._environment()
    assert environment['SECRET'] == 'resolved'
    assert 'AWS_SECRET_ACCESS_KEY' not in environment


def test_actor_rejects_missing_secret_reference(tmp_path, monkeypatch):
    server = server_record(tmp_path)
    monkeypatch.delenv('SECRET_REFERENCE', raising=False)
    with pytest.raises(SupervisorError, match='unset'):
        ServerActor(server, {})._environment()


def test_supervisor_rejects_unapproved_system_admin(tmp_path):
    server = server_record(tmp_path, profile='system-admin')
    with pytest.raises(SupervisorError, match='path is not authorized'):
        Supervisor().authorize(server)


def test_supervisor_rejects_changed_digest_for_system_admin(tmp_path):
    server = server_record(tmp_path, profile='system-admin')
    policy = {
        'servers': {
            server.id: {
                'package_path': server.package_path,
                'package_digest': 'wrong',
                'capabilities': ['system-read'],
            }
        }
    }
    with pytest.raises(SupervisorError, match='digest'):
        Supervisor(policy).authorize(server)


def test_root_write_requires_independent_capability(tmp_path):
    server = server_record(tmp_path, profile='system-admin', read_only=False, root_write=True)
    policy = {
        'servers': {
            server.id: {
                'package_path': server.package_path,
                'package_digest': server.package_digest,
                'capabilities': ['system-read'],
            }
        }
    }
    with pytest.raises(SupervisorError, match='capabilities'):
        Supervisor(policy).authorize(server)


def test_approved_read_only_system_admin_requires_explicit_root_runtime(tmp_path, monkeypatch):
    server = server_record(tmp_path, profile='system-admin')
    policy = {
        'servers': {
            server.id: {
                'package_path': server.package_path,
                'package_digest': server.package_digest,
                'capabilities': ['system-read'],
            }
        }
    }
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    with pytest.raises(SupervisorError, match='explicitly enabled'):
        Supervisor(policy).authorize(server)
    Supervisor(policy, allow_root_runtime=True).authorize(server)


def test_bounded_log_keeps_only_recent_complete_lines():
    log = BoundedLog(max_lines=2)
    log.write('one\ntwo\npart')
    log.write('ial\nthree\n')
    assert log.lines() == ['partial', 'three']


def test_bounded_log_redacts_configured_secrets():
    log = BoundedLog()
    log.set_redactions(['very-secret'])
    log.write('token=very-')
    log.write('secret\n')
    assert log.lines() == ['token=[REDACTED]']
    log.close()


async def test_actor_queue_serializes_requests(tmp_path):
    server = server_record(tmp_path)
    actor = ServerActor(server, {})
    actor.state = actor.state.ready

    async def worker():
        request = await actor._queue.get()
        request.future.set_result(request.arguments['sequence'])
        actor._queue.task_done()

    tasks = [asyncio.create_task(actor.request('call_tool', 'x', {'sequence': i})) for i in range(3)]
    for _ in range(3):
        await worker()
    assert await asyncio.gather(*tasks) == [0, 1, 2]


async def test_actor_restarts_with_bounded_attempts(tmp_path, monkeypatch):
    server = server_record(tmp_path)
    server.manifest.limits.max_restarts = 2
    actor = ServerActor(server, {})
    calls = 0

    async def fail_session():
        nonlocal calls
        calls += 1
        raise RuntimeError('boom')

    async def no_delay(_):
        return None

    monkeypatch.setattr(actor, '_run_session', fail_session)
    monkeypatch.setattr(asyncio, 'sleep', no_delay)
    await actor._run()
    assert calls == 3
    assert actor.state.value == 'failed'
    assert actor.last_error == 'boom'
