"""publisher fee: a game's own share of the market fee, when Steam reports one

Revision ID: 7d2b9e4c1f60
Revises: 1a45ffafa0ff
Create Date: 2026-10-28 16:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d2b9e4c1f60"
down_revision: str | Sequence[str] | None = "1a45ffafa0ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("items", sa.Column("publisher_fee", sa.Numeric(5, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "publisher_fee")
