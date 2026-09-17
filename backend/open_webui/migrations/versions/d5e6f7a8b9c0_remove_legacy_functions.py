"""Remove the legacy database-backed Function subsystem.

Revision ID: d5e6f7a8b9c0
Revises: c4a71e5d9b20
"""

import json

import sqlalchemy as sa
from alembic import op


revision = 'd5e6f7a8b9c0'
down_revision = 'c4a71e5d9b20'
branch_labels = None
depends_on = None


LEGACY_MODEL_META_KEYS = ('actionIds', 'filterIds', 'defaultFilterIds')


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'access_grant' in tables:
        bind.execute(sa.text("DELETE FROM access_grant WHERE resource_type = 'function'"))

    if 'model' in tables:
        model_table = sa.Table('model', sa.MetaData(), autoload_with=bind)
        rows = bind.execute(sa.select(model_table.c.id, model_table.c.meta)).fetchall()
        for model_id, raw_meta in rows:
            meta = raw_meta
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (TypeError, ValueError):
                    continue
            if not isinstance(meta, dict):
                continue

            changed = False
            for key in LEGACY_MODEL_META_KEYS:
                if key in meta:
                    meta.pop(key)
                    changed = True
            if changed:
                bind.execute(model_table.update().where(model_table.c.id == model_id).values(meta=meta))

    if 'function' in tables:
        op.drop_table('function')


def downgrade() -> None:
    raise RuntimeError('The legacy Function removal cannot be downgraded')
