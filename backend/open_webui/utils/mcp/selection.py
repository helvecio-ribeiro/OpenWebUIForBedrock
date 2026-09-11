from fastapi import HTTPException


def normalize_mcp_server_ids(value) -> list[str] | None:
    """Validate, trim, and de-duplicate the public MCP request contract."""
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise HTTPException(status_code=400, detail='mcp_server_ids must be a list of non-empty strings')
    return list(dict.fromkeys(item.strip() for item in value))


def resolve_mcp_server_ids(form_data: dict, metadata: dict) -> list[str] | None:
    """Resolve MCP selection without losing the pre-inlet metadata snapshot.

    Pipeline/filter inlets may rebuild the request body and omit extension fields.
    The chat entry point captures the original selection in metadata before those
    inlets run, so use it whenever the post-inlet body no longer contains the key.
    An explicit empty list remains meaningful and disables MCP servers.
    """
    if 'mcp_server_ids' in form_data:
        value = form_data.pop('mcp_server_ids')
    else:
        value = metadata.get('mcp_server_ids')
    return normalize_mcp_server_ids(value)


def get_user_mcp_server_ids(user) -> list[str] | None:
    """Read the persisted UI-wide MCP selection from an authenticated user."""
    settings = getattr(user, 'settings', None)
    if settings is None:
        return None
    if hasattr(settings, 'model_dump'):
        settings = settings.model_dump()
    if not isinstance(settings, dict):
        return None

    ui_settings = settings.get('ui') or {}
    if not isinstance(ui_settings, dict) or 'mcpServerIds' not in ui_settings:
        return None
    return normalize_mcp_server_ids(ui_settings.get('mcpServerIds'))
