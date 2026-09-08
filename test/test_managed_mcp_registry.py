import asyncio
import json
from pathlib import Path

import pytest

from open_webui.mcp_runtime.registry import JSONRegistry, RegistryError
from open_webui.mcp_runtime.schemas import ManagedServerCreate, ManagedServerUpdate

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def write_package(path: Path, server_id: str = 'example', extra_manifest: str = '') -> Path:
    path.mkdir(parents=True)
    (path / 'mcp.yaml').write_text(
        f'''schema_version: 1
id: {server_id}
name: Example
version: 1.0.0
runtime:
  type: python
  command: uv
  args: [run, --frozen, server.py]
transport:
  type: stdio
environment:
  VALUE:
    default: safe
  TOKEN:
    secret: true
{extra_manifest}''',
        encoding='utf-8',
    )
    (path / 'pyproject.toml').write_text('[project]\nname="example"\nversion="1.0.0"\n', encoding='utf-8')
    (path / 'uv.lock').write_text('version = 1\n', encoding='utf-8')
    (path / 'server.py').write_text('', encoding='utf-8')
    return path


async def test_registry_create_update_delete_and_reload(tmp_path):
    package = write_package(tmp_path / 'packages' / 'one')
    registry = JSONRegistry(tmp_path / 'registry.json', [tmp_path / 'packages'])
    await registry.load()
    server = await registry.create(
        ManagedServerCreate(
            package_path=str(package),
            environment={'VALUE': 'configured'},
            secret_environment={'TOKEN': 'TEST_TOKEN'},
        ),
        'admin-1',
    )
    assert server.id == 'example'
    assert 'TEST_TOKEN' in (tmp_path / 'registry.json').read_text()

    updated = await registry.update('example', ManagedServerUpdate(enabled=True))
    assert updated.enabled is True
    assert (await JSONRegistry(tmp_path / 'registry.json', [tmp_path / 'packages']).load()).revision == 2

    await registry.delete('example')
    assert (await registry.snapshot()).servers == {}


async def test_registry_discovers_available_registered_and_invalid_packages(tmp_path):
    root = tmp_path / 'packages'
    write_package(root / 'available', server_id='available')
    registered = write_package(root / 'registered', server_id='registered')
    invalid = root / 'invalid'
    invalid.mkdir(parents=True)
    (invalid / 'mcp.yaml').write_text('not: a-valid-manifest\n', encoding='utf-8')

    registry = JSONRegistry(tmp_path / 'registry.json', [root])
    await registry.load()
    await registry.create(ManagedServerCreate(package_path=str(registered)), 'admin')

    result = await registry.discover()
    states = {service['id']: service['discovery_state'] for service in result['services']}
    assert states == {'available': 'available', 'registered': 'registered'}
    assert result['roots'] == [str(root.resolve())]
    assert result['errors'][0]['package_path'] == str(invalid)
    available = next(service for service in result['services'] if service['id'] == 'available')
    assert available['package_digest']
    assert 'TOKEN' in available['environment']


async def test_registry_discovery_reports_manifest_id_conflicts(tmp_path):
    root = tmp_path / 'packages'
    first = write_package(root / 'first', server_id='duplicate')
    write_package(root / 'second', server_id='duplicate')
    registry = JSONRegistry(tmp_path / 'registry.json', [root])
    await registry.load()
    await registry.create(ManagedServerCreate(package_path=str(first)), 'admin')

    result = await registry.discover()
    assert sorted(service['discovery_state'] for service in result['services']) == [
        'conflict',
        'registered',
    ]


async def test_registry_rejects_path_escape_and_symlink_escape(tmp_path):
    root = tmp_path / 'allowed'
    root.mkdir()
    outside = write_package(tmp_path / 'outside')
    registry = JSONRegistry(tmp_path / 'registry.json', [root])
    await registry.load()

    with pytest.raises(RegistryError, match='outside'):
        await registry.create(ManagedServerCreate(package_path=str(outside)), 'admin')

    link = root / 'link'
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RegistryError, match='outside'):
        await registry.create(ManagedServerCreate(package_path=str(link)), 'admin')


async def test_registry_rejects_undeclared_and_misclassified_environment(tmp_path):
    package = write_package(tmp_path / 'packages' / 'one')
    registry = JSONRegistry(tmp_path / 'registry.json', [tmp_path / 'packages'])
    await registry.load()
    with pytest.raises(RegistryError, match='undeclared'):
        await registry.create(
            ManagedServerCreate(package_path=str(package), environment={'SURPRISE': 'value'}), 'admin'
        )
    with pytest.raises(RegistryError, match='secret environment'):
        await registry.create(
            ManagedServerCreate(package_path=str(package), environment={'TOKEN': 'plaintext'}), 'admin'
        )


async def test_registry_enforces_manifest_filesystem_roots(tmp_path):
    package = write_package(
        tmp_path / 'packages' / 'one',
        extra_manifest='''security:
  profile: confined
  filesystem_roots: [./sandbox]
  read_only: false
''',
    )
    manifest = package / 'mcp.yaml'
    manifest.write_text(
        manifest.read_text().replace(
            '  TOKEN:\n    secret: true\n',
            '  TOKEN:\n    secret: true\n  MCP_FILESYSTEM_ROOT:\n    default: ./sandbox\n',
        )
    )
    registry = JSONRegistry(tmp_path / 'registry.json', [tmp_path / 'packages'])
    await registry.load()
    with pytest.raises(RegistryError, match='outside manifest'):
        await registry.create(
            ManagedServerCreate(package_path=str(package), environment={'MCP_FILESYSTEM_ROOT': '/'}),
            'admin',
        )


async def test_registry_serializes_concurrent_updates(tmp_path):
    package = write_package(tmp_path / 'packages' / 'one')
    registry = JSONRegistry(tmp_path / 'registry.json', [tmp_path / 'packages'])
    await registry.load()
    await registry.create(ManagedServerCreate(package_path=str(package)), 'admin')

    await asyncio.gather(
        *(registry.update('example', ManagedServerUpdate(environment={'VALUE': str(i)})) for i in range(20))
    )
    document = await registry.snapshot()
    assert document.revision == 21
    assert document.servers['example'].environment['VALUE'] in {str(i) for i in range(20)}
    json.loads((tmp_path / 'registry.json').read_text())


async def test_registry_recovers_last_known_good_copy(tmp_path):
    package = write_package(tmp_path / 'packages' / 'one')
    path = tmp_path / 'registry.json'
    registry = JSONRegistry(path, [tmp_path / 'packages'])
    await registry.load()
    await registry.create(ManagedServerCreate(package_path=str(package)), 'admin')
    await registry.update('example', ManagedServerUpdate(enabled=True))
    path.write_text('{broken', encoding='utf-8')

    recovered = await JSONRegistry(path, [tmp_path / 'packages']).load()
    assert recovered.servers['example'].enabled is False


def test_manifest_rejects_shell_command(tmp_path):
    package = write_package(tmp_path / 'package')
    manifest = package / 'mcp.yaml'
    manifest.write_text(manifest.read_text().replace('command: uv', 'command: "uv run"'))
    registry = JSONRegistry(tmp_path / 'registry.json', [tmp_path])

    async def create():
        await registry.load()
        await registry.create(ManagedServerCreate(package_path=str(package)), 'admin')

    with pytest.raises(ValueError, match='one executable'):
        asyncio.run(create())
