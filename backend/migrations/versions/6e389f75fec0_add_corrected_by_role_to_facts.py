"""add corrected_by_role to facts

Revision ID: 6e389f75fec0
Revises: 6fd63bd91523
Create Date: 2026-08-02 21:49:01.932533

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '6e389f75fec0'
down_revision: Union[str, Sequence[str], None] = '6fd63bd91523'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable: only a corrected fact has this set — every fact that was
    # never corrected (the common case) leaves it NULL, not a placeholder.
    op.add_column(
        'facts',
        sa.Column('corrected_by_role', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('facts', 'corrected_by_role')
