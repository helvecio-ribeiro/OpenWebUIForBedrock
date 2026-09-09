"""Regression coverage for the removal of legacy Workspace permissions."""

import pytest

from open_webui.config import DEFAULT_USER_PERMISSIONS
from open_webui.routers import knowledge, models, prompts, skills, tools, users
from open_webui.utils.auth import get_admin_user


def _route_dependency(router, path: str, method: str):
    route = next(
        route
        for route in router.routes
        if route.path == path and method.upper() in route.methods
    )
    return {dependency.call for dependency in route.dependant.dependencies}


@pytest.mark.parametrize(
    ('router', 'path', 'method'),
    [
        (models.router, '/create', 'POST'),
        (models.router, '/export', 'GET'),
        (models.router, '/import', 'POST'),
        (prompts.router, '/create', 'POST'),
        (knowledge.router, '/create', 'POST'),
        (tools.router, '/export', 'GET'),
        (tools.router, '/create', 'POST'),
        (skills.router, '/export', 'GET'),
        (skills.router, '/create', 'POST'),
    ],
)
def test_removed_workspace_mutations_are_admin_only(router, path, method):
    assert get_admin_user in _route_dependency(router, path, method)


def test_workspace_permissions_are_not_part_of_the_active_configuration():
    assert 'workspace' not in DEFAULT_USER_PERMISSIONS
    assert 'workspace' not in users.UserPermissions.model_fields
