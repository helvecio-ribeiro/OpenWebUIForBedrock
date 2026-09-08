"""Remove native calendar database artifacts."""

from alembic import op
from sqlalchemy import inspect

revision = '7e4c1a9b2d30'
down_revision = 'f0bd01a18a3d'
branch_labels = None
depends_on = None


def upgrade():
    tables = set(inspect(op.get_bind()).get_table_names())
    for table in ('calendar_event_attendee', 'calendar_event', 'calendar'):
        if table in tables:
            op.drop_table(table)


def downgrade():
    pass
