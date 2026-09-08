import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def test_filesystem_confines_reads_and_writes(tmp_path, monkeypatch):
    sandbox = tmp_path / 'sandbox'
    monkeypatch.setenv('MCP_FILESYSTEM_ROOT', str(sandbox))
    monkeypatch.setenv('OPEN_WEBUI_MCP_READ_ONLY', 'false')
    filesystem = load_module('filesystem_example_write', ROOT / 'examples/managed-mcp/filesystem-tools/server.py')
    filesystem.write_text_file('folder/file.txt', 'hello')
    assert filesystem.read_text_file('folder/file.txt') == 'hello'
    assert filesystem.filesystem_info()['read_only'] is False
    with pytest.raises(ValueError, match='absolute'):
        filesystem.read_text_file('/etc/passwd')
    with pytest.raises(ValueError, match='escapes'):
        filesystem.write_text_file('../escape.txt', 'bad')


def test_filesystem_rejects_symlink_escape(tmp_path, monkeypatch):
    sandbox = tmp_path / 'sandbox'
    outside = tmp_path / 'outside'
    sandbox.mkdir()
    outside.mkdir()
    (outside / 'secret.txt').write_text('secret')
    (sandbox / 'link').symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv('MCP_FILESYSTEM_ROOT', str(sandbox))
    monkeypatch.setenv('OPEN_WEBUI_MCP_READ_ONLY', 'false')
    filesystem = load_module('filesystem_example_symlink', ROOT / 'examples/managed-mcp/filesystem-tools/server.py')
    with pytest.raises(ValueError, match='escapes'):
        filesystem.read_text_file('link/secret.txt')
    with pytest.raises(ValueError, match='escapes'):
        filesystem.write_text_file('link/new.txt', 'bad')


def test_filesystem_read_only_blocks_mutations(tmp_path, monkeypatch):
    monkeypatch.setenv('MCP_FILESYSTEM_ROOT', str(tmp_path / 'sandbox'))
    monkeypatch.setenv('OPEN_WEBUI_MCP_READ_ONLY', 'true')
    filesystem = load_module('filesystem_example_readonly', ROOT / 'examples/managed-mcp/filesystem-tools/server.py')
    with pytest.raises(PermissionError, match='read-only'):
        filesystem.write_text_file('file.txt', 'blocked')
    with pytest.raises(PermissionError, match='read-only'):
        filesystem.create_directory('blocked')


def test_filesystem_enforces_read_limit_and_explicit_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv('MCP_FILESYSTEM_ROOT', str(tmp_path / 'sandbox'))
    monkeypatch.setenv('MCP_FILESYSTEM_MAX_READ_BYTES', '3')
    monkeypatch.setenv('OPEN_WEBUI_MCP_READ_ONLY', 'false')
    filesystem = load_module('filesystem_example_limits', ROOT / 'examples/managed-mcp/filesystem-tools/server.py')
    filesystem.write_text_file('file.txt', 'four')
    with pytest.raises(ValueError, match='read limit'):
        filesystem.read_text_file('file.txt')
    with pytest.raises(ValueError, match='overwrite=true'):
        filesystem.write_text_file('file.txt', 'new')
    filesystem.write_text_file('file.txt', 'new', overwrite=True)
    assert filesystem.read_text_file('file.txt') == 'new'


def test_local_system_time_has_explicit_timezone_and_reference_fields(tmp_path, monkeypatch):
    monkeypatch.setenv('MCP_FILESYSTEM_ROOT', str(tmp_path / 'sandbox'))
    monkeypatch.setenv('MCP_LOCAL_TIMEZONE', 'America/Mexico_City')
    filesystem = load_module('local_system_time', ROOT / 'examples/managed-mcp/filesystem-tools/server.py')

    result = filesystem.get_current_datetime()

    assert result['timezone'] == 'America/Mexico_City'
    assert result['weekday'] in {
        'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'
    }
    assert datetime.fromisoformat(result['local_datetime']).tzinfo is not None
    assert datetime.fromisoformat(result['utc_datetime']).utcoffset().total_seconds() == 0
    assert result['local_datetime'].startswith(result['date'])
    tools = {tool.name for tool in filesystem.mcp._tool_manager.list_tools()}
    assert 'get_current_datetime' in tools
    assert {'list_directory', 'read_text_file', 'write_text_file', 'create_directory'} <= tools


def test_local_system_time_rejects_unknown_timezone(tmp_path, monkeypatch):
    monkeypatch.setenv('MCP_FILESYSTEM_ROOT', str(tmp_path / 'sandbox'))
    monkeypatch.setenv('MCP_LOCAL_TIMEZONE', 'Not/A_Real_Zone')
    with pytest.raises(RuntimeError, match='Unknown MCP_LOCAL_TIMEZONE'):
        load_module('local_system_bad_timezone', ROOT / 'examples/managed-mcp/filesystem-tools/server.py')


def load_calendar_module(tmp_path, monkeypatch, name='calendar_example'):
    monkeypatch.setenv('CALENDAR_DATABASE_PATH', str(tmp_path / 'shared-calendar.db'))
    monkeypatch.setenv('MCP_LOCAL_TIMEZONE', 'America/Mexico_City')
    sys.modules.pop('repository', None)
    return load_module(name, ROOT / 'examples/managed-mcp/calendar-tools/server.py')


def test_calendar_create_uses_its_shared_sqlite_repository(tmp_path, monkeypatch):
    calendar = load_calendar_module(tmp_path, monkeypatch, 'calendar_example_create')
    result = calendar.create_calendar_event(
        'Dentist', '2026-09-08 12:00', location='Clinic'
    )

    assert result['status'] == 'created'
    assert result['event']['title'] == 'Dentist'
    assert result['event']['location'] == 'Clinic'
    assert result['event']['end'] == '2026-09-08T13:00:00-06:00'
    assert (tmp_path / 'shared-calendar.db').is_file()


def test_calendar_search_update_and_soft_delete_use_exact_event_ids(tmp_path, monkeypatch):
    calendar = load_calendar_module(tmp_path, monkeypatch, 'calendar_example_mutations')
    created = calendar.create_calendar_event('Dentist', '2026-09-08 12:00')['event']

    search = calendar.search_calendar_events(query='Dentist')
    updated = calendar.update_calendar_event(created['id'], title='Dental appointment')
    batch_deleted = calendar.delete_calendar_events([created['id'], created['id'], 'event-2'])
    deleted = calendar.delete_calendar_event(created['id'])

    assert search['events'][0]['id'] == created['id']
    assert updated['event']['title'] == 'Dental appointment'
    assert deleted['status'] == 'deleted'
    assert deleted['event']['deleted_at']
    assert calendar.search_calendar_events(query='Dental')['total'] == 0
    assert calendar.repository.get(created['id'], include_deleted=True)['title'] == 'Dental appointment'
    assert batch_deleted == {
        'status': 'rejected',
        'error': 'Bulk calendar deletion is disabled. Delete one event at a time by exact ID.',
        'requested_total': 2,
    }

    tools = {tool.name for tool in calendar.mcp._tool_manager.list_tools()}
    assert tools == {
        'get_current_datetime',
        'search_calendar_events',
        'create_calendar_event',
        'update_calendar_event',
        'delete_calendar_event',
        'delete_calendar_events',
    }


def test_calendar_tools_publish_descriptions_for_every_parameter(tmp_path, monkeypatch):
    calendar = load_calendar_module(tmp_path, monkeypatch, 'calendar_example_schema')
    tools = {tool.name: tool for tool in calendar.mcp._tool_manager.list_tools()}

    for name in (
        'search_calendar_events',
        'create_calendar_event',
        'update_calendar_event',
        'delete_calendar_event',
        'delete_calendar_events',
    ):
        tool = tools[name]
        assert tool.description
        assert all(
            schema.get('description')
            for schema in tool.parameters.get('properties', {}).values()
        )

    assert tools['create_calendar_event'].parameters['required'] == ['title', 'start']
    assert tools['update_calendar_event'].parameters['required'] == ['event_id']
    assert tools['delete_calendar_event'].parameters['required'] == ['event_id']


def test_shared_calendar_schema_has_no_open_webui_user_or_calendar_partition(tmp_path, monkeypatch):
    import sqlite3

    calendar = load_calendar_module(tmp_path, monkeypatch, 'calendar_example_schema_ownership')
    with sqlite3.connect(calendar.repository.path) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(events)')}

    assert 'user_id' not in columns
    assert 'calendar_id' not in columns


def test_calendar_rest_api_and_mcp_share_the_same_repository(tmp_path, monkeypatch):
    calendar = load_calendar_module(tmp_path, monkeypatch, 'calendar_example_shared_api')
    api = load_module('calendar_example_api', ROOT / 'examples/managed-mcp/calendar-tools/api.py')

    created = calendar.create_calendar_event('Lunch', '2026-09-09 12:00')['event']
    api_result = api.search_events(query='Lunch')
    updated = api.update_event(created['id'], {'location': 'Cafe'})

    assert api_result['events'][0]['id'] == created['id']
    assert updated['location'] == 'Cafe'
    assert calendar.search_calendar_events(query='Lunch')['events'][0]['location'] == 'Cafe'
