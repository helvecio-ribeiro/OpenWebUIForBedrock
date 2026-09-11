import pytest
from fastapi import HTTPException

from open_webui.utils.mcp.selection import (
    get_user_mcp_server_ids,
    normalize_mcp_server_ids,
    resolve_mcp_server_ids,
)


def test_missing_selection_disables_mcp_without_error():
    assert normalize_mcp_server_ids(None) is None
    assert normalize_mcp_server_ids([]) == []


def test_selection_is_trimmed_and_deduplicated():
    assert normalize_mcp_server_ids([' local-calendar ', 'local-system-tools', 'local-calendar']) == [
        'local-calendar',
        'local-system-tools',
    ]


@pytest.mark.parametrize('value', ['local-calendar', [None], [''], ['   '], [7]])
def test_invalid_selection_is_rejected(value):
    with pytest.raises(HTTPException) as exc:
        normalize_mcp_server_ids(value)
    assert exc.value.status_code == 400


def test_selection_survives_an_inlet_that_strips_the_payload_field():
    metadata = {'mcp_server_ids': ['local-calendar', 'local-system-tools']}

    assert resolve_mcp_server_ids({}, metadata) == [
        'local-calendar',
        'local-system-tools',
    ]


def test_explicit_empty_selection_overrides_pre_inlet_metadata():
    form_data = {'mcp_server_ids': []}
    metadata = {'mcp_server_ids': ['local-calendar']}

    assert resolve_mcp_server_ids(form_data, metadata) == []
    assert 'mcp_server_ids' not in form_data


def test_persisted_user_selection_is_available_as_ui_request_fallback():
    class Settings:
        def model_dump(self):
            return {
                'ui': {
                    'mcpServerIds': [
                        'local-calendar',
                        'local-system-tools',
                    ]
                }
            }

    class User:
        settings = Settings()

    assert get_user_mcp_server_ids(User()) == [
        'local-calendar',
        'local-system-tools',
    ]


def test_persisted_empty_selection_disables_mcp_servers():
    class User:
        settings = {'ui': {'mcpServerIds': []}}

    assert get_user_mcp_server_ids(User()) == []
