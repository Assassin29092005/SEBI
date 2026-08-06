"""add audit_events table

Moves the audit log off the flat encrypted file (``app.audit.AuditLog``,
now removed) and into Postgres. The old file did a full read-modify-write
per request — O(n) on a log that only ever grows. An append-only table with
an index on ``at`` is exactly the thing a database exists for.

No data migration: the previous log lived under ``data/audit/`` encrypted
with ``ENCRYPTION_KEY`` and was only ever populated by dev/demo runs. Any
existing file is left on disk untouched rather than imported.

Revision ID: 9c41d7b02f5a
Revises: 2add3f2c1418
Create Date: 2026-08-06 22:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9c41d7b02f5a'
down_revision: Union[str, Sequence[str], None] = '2add3f2c1418'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'audit_events',
        sa.Column('event_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('actor_user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('actor_email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('actor_role', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('method', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('path', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('resource_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('resource_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('outcome', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint('event_id'),
    )
    # Every filter GET /api/audit exposes, plus the ORDER BY that backs it.
    op.create_index('ix_audit_actor_email', 'audit_events', ['actor_email'])
    op.create_index('ix_audit_action', 'audit_events', ['action'])
    op.create_index('ix_audit_at', 'audit_events', ['at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_audit_at', table_name='audit_events')
    op.drop_index('ix_audit_action', table_name='audit_events')
    op.drop_index('ix_audit_actor_email', table_name='audit_events')
    op.drop_table('audit_events')
