"""Remove the legacy database-backed Python Tool subsystem.

Revision ID: c4a71e5d9b20
Revises: b3d8f6a02c71
"""

from alembic import op


revision = 'c4a71e5d9b20'
down_revision = 'b3d8f6a02c71'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Grants have no foreign key to the polymorphic resource they protect.
    op.execute("DELETE FROM access_grant WHERE resource_type = 'tool'")
    op.drop_table('tool')


def downgrade() -> None:
    # This fork intentionally removes executable Python source stored in the
    # database. Restoring it would be unsafe and the deleted rows are not
    # recoverable, so this migration is deliberately irreversible.
    raise RuntimeError('The legacy Python Tool removal cannot be downgraded')
