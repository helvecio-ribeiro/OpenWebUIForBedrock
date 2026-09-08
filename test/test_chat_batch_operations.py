import json
import zipfile
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from open_webui.routers import chats as chats_router
from open_webui.routers.chats import (
    BatchChatsForm,
    _get_owned_batch_chats,
    _unique_chat_ids,
    _write_chat_archive,
    archive_selected_chats,
)


def test_unique_chat_ids_preserves_order_and_removes_empty_values():
    assert _unique_chat_ids(['one', '', 'two', 'one']) == ['one', 'two']


def test_write_chat_archive_contains_manifest_and_complete_chat_json(tmp_path):
    destination = tmp_path / 'user-1' / 'archive.zip'
    chats = [
        {
            'id': 'chat-1',
            'user_id': 'user-1',
            'title': 'First chat',
            'chat': {'messages': [{'role': 'user', 'content': 'Hello'}]},
            'created_at': 1,
            'updated_at': 2,
            'archived': False,
        },
        {
            'id': 'chat-2',
            'user_id': 'user-1',
            'title': '../unsafe title',
            'chat': {'messages': [{'role': 'assistant', 'content': 'Hi'}]},
            'created_at': 3,
            'updated_at': 4,
            'archived': False,
        },
    ]

    _write_chat_archive(destination, chats)

    assert destination.exists()
    assert destination.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            'manifest.json',
            'chats/chat-1.json',
            'chats/chat-2.json',
        }
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['format'] == 'open-webui-chat-archive'
        assert manifest['chat_count'] == 2
        assert manifest['chats'][1]['file'] == 'chats/chat-2.json'
        assert json.loads(archive.read('chats/chat-1.json'))['chat']['messages'][0]['content'] == 'Hello'


def test_write_chat_archive_removes_temporary_file_on_failure(tmp_path, monkeypatch):
    destination = tmp_path / 'archive.zip'

    def fail(*args, **kwargs):
        raise RuntimeError('zip failed')

    monkeypatch.setattr(zipfile.ZipFile, 'writestr', fail)

    try:
        _write_chat_archive(destination, [{'id': 'chat-1', 'title': 'Chat'}])
    except RuntimeError:
        pass
    else:
        raise AssertionError('expected archive creation to fail')

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_batch_lookup_rejects_empty_and_foreign_chat(monkeypatch):
    with pytest.raises(HTTPException) as empty_error:
        asyncio.run(_get_owned_batch_chats([], 'user-1', object()))
    assert empty_error.value.status_code == 422

    async def missing(*args, **kwargs):
        return None

    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id_and_user_id', missing)
    with pytest.raises(HTTPException) as foreign_error:
        asyncio.run(_get_owned_batch_chats(['foreign-chat'], 'user-1', object()))
    assert foreign_error.value.status_code == 404


def test_archive_endpoint_writes_zip_before_marking_chats_archived(tmp_path, monkeypatch):
    chat = SimpleNamespace(
        id='chat-1',
        user_id='user-1',
        title='Archived chat',
        chat={'messages': [{'role': 'user', 'content': 'Keep this'}]},
        updated_at=2,
        created_at=1,
        share_id=None,
        archived=False,
        pinned=False,
        meta={},
        variables={},
        folder_id=None,
        summary=None,
        current_message_id=None,
    )
    sequence = []

    async def selected(*args, **kwargs):
        return [chat]

    async def stop(*args, **kwargs):
        return None

    async def archive(ids, user_id, db=None):
        archive_files = list((tmp_path / 'archives' / user_id).glob('*.zip'))
        assert len(archive_files) == 1
        sequence.append('archive')
        return True

    async def no_op(*args, **kwargs):
        return None

    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(chats_router, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(chats_router, '_get_owned_batch_chats', selected)
    monkeypatch.setattr(chats_router, 'stop_item_tasks', stop)
    monkeypatch.setattr(chats_router.Chats, 'archive_chats_by_ids_and_user_id', archive)
    monkeypatch.setattr(chats_router.Chats, 'delete_orphan_tags_for_user', no_op)
    monkeypatch.setattr(chats_router, 'publish_event', no_op)
    monkeypatch.setattr(chats_router.asyncio, 'to_thread', run_inline)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
    result = asyncio.run(
        archive_selected_chats(
            request,
            BatchChatsForm(chat_ids=['chat-1']),
            user=SimpleNamespace(id='user-1'),
            db=object(),
        )
    )

    assert result.archived_count == 1
    assert sequence == ['archive']
    with zipfile.ZipFile(tmp_path / 'archives' / 'user-1' / result.archive_name) as archive_file:
        assert json.loads(archive_file.read('chats/chat-1.json'))['chat']['messages'][0]['content'] == 'Keep this'
