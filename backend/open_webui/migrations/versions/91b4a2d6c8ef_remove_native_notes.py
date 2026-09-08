"""Remove native Notes database artifacts."""

from alembic import op
from sqlalchemy import inspect, text

revision = '91b4a2d6c8ef'
down_revision = '7e4c1a9b2d30'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    tables = set(inspect(op.get_bind()).get_table_names())
    if 'access_grant' in tables:
        connection.execute(text("DELETE FROM access_grant WHERE resource_type = 'note'"))
    for table in ('pinned_note', 'note'):
        if table in tables:
            op.drop_table(table)


def downgrade():
    pass
