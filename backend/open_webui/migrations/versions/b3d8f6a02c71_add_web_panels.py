"""Add per-user web panels."""

import sqlalchemy as sa
from alembic import op

revision = 'b3d8f6a02c71'
down_revision = 'a2c7e5f91b40'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'web_panel',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_web_panel_user_id', 'web_panel', ['user_id'])


def downgrade():
    op.drop_index('ix_web_panel_user_id', table_name='web_panel')
    op.drop_table('web_panel')
