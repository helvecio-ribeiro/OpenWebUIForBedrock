from open_webui.routers.mcp import build_mcp_server_catalog


def test_catalog_merges_remote_and_managed_without_exposing_connections():
    catalog = build_mcp_server_catalog(
        [
            {
                'type': 'mcp',
                'url': 'https://secret.example/mcp',
                'key': 'secret',
                'config': {'enable': True, 'access_grants': []},
                'info': {'id': 'remote', 'name': 'Remote'},
            }
        ],
        [
            {
                'id': 'local',
                'name': 'Local',
                'enabled': True,
                'state': 'ready',
                'access_grants': [],
            }
        ],
        now=123,
    )

    assert [entry['id'] for entry in catalog] == ['local', 'remote']
    assert catalog[0]['selectable'] is True
    assert catalog[0]['access_grants'] == [
        {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
    ]
    assert catalog[1]['access_grants'] == []
    assert all('url' not in entry and 'key' not in entry for entry in catalog)
    assert all('tool_id' not in entry for entry in catalog)


def test_configured_connection_wins_an_id_collision():
    catalog = build_mcp_server_catalog(
        [
            {
                'type': 'mcp',
                'config': {'enable': True},
                'info': {'id': 'same', 'name': 'Configured'},
            }
        ],
        [{'id': 'same', 'name': 'Managed', 'enabled': True, 'state': 'ready'}],
        now=123,
    )

    assert len(catalog) == 1
    assert catalog[0]['name'] == 'Configured'
    assert catalog[0]['source'] == 'remote'


def test_unavailable_servers_remain_visible_but_are_not_selectable():
    catalog = build_mcp_server_catalog(
        [
            {
                'type': 'mcp',
                'config': {'enable': False},
                'info': {'id': 'disabled', 'name': 'Disabled'},
            }
        ],
        [{'id': 'stopped', 'name': 'Stopped', 'enabled': True, 'state': 'stopped'}],
        now=123,
    )

    assert {entry['id']: entry['selectable'] for entry in catalog} == {
        'disabled': False,
        'stopped': False,
    }


def test_catalog_ignores_non_mcp_and_malformed_connections():
    catalog = build_mcp_server_catalog(
        [
            {'type': 'openapi', 'info': {'id': 'old'}},
            {'type': 'mcp', 'info': {}, 'config': {'enable': True}},
        ],
        [],
        now=123,
    )

    assert catalog == []
