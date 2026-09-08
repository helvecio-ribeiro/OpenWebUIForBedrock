"""Remove persisted native Notes configuration artifacts."""

import sqlalchemy as sa
from alembic import op

revision = 'a2c7e5f91b40'
down_revision = '91b4a2d6c8ef'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if 'config' not in tables:
        return

    config = sa.table(
        'config',
        sa.column('key', sa.Text),
        sa.column('value', sa.JSON),
    )
    connection.execute(sa.delete(config).where(config.c.key == 'notes.enable'))

    permissions = connection.execute(
        sa.select(config.c.value).where(config.c.key == 'user.permissions')
    ).scalar_one_or_none()
    if isinstance(permissions, dict):
        sharing = permissions.get('sharing')
        if isinstance(sharing, dict):
            sharing.pop('notes', None)
            sharing.pop('public_notes', None)
        features = permissions.get('features')
        if isinstance(features, dict):
            features.pop('notes', None)
        connection.execute(
            sa.update(config)
            .where(config.c.key == 'user.permissions')
            .values(value=permissions)
        )


def downgrade():
    pass
