"""add document_id page source_file to facts provenance

Revision ID: 2add3f2c1418
Revises: 6e389f75fec0
Create Date: 2026-08-03 00:06:08.358331

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '2add3f2c1418'
down_revision: Union[str, Sequence[str], None] = '6e389f75fec0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('facts', sa.Column('provenance_document_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('facts', sa.Column('provenance_page', sa.Integer(), nullable=True))
    op.add_column('facts', sa.Column('provenance_source_file', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('facts', 'provenance_source_file')
    op.drop_column('facts', 'provenance_page')
    op.drop_column('facts', 'provenance_document_id')
