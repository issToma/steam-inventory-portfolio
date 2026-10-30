"""chart excluded: positions left out of the portfolio's value chart and period stats

Revision ID: 0bc26ed90d94
Revises: 0a8fb6a52488
Create Date: 2026-10-30 16:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0bc26ed90d94"
down_revision: str | Sequence[str] | None = "0a8fb6a52488"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "holdings",
        sa.Column("chart_excluded", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("holdings", "chart_excluded")
