"""add resolution to the price_history key

Revision ID: a1c3e5f7b9d2
Revises: e6f69dfe670d
Create Date: 2026-10-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3e5f7b9d2"
down_revision: str | Sequence[str] | None = "e6f69dfe670d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "price_history",
        sa.Column("resolution", sa.Text(), nullable=False, server_default="day"),
    )
    op.alter_column("price_history", "resolution", server_default=None)
    op.drop_constraint("pk_price_history", "price_history", type_="primary")
    op.create_primary_key("pk_price_history", "price_history", ["item_id", "resolution", "ts"])


def downgrade() -> None:
    op.drop_constraint("pk_price_history", "price_history", type_="primary")
    op.execute("DELETE FROM price_history WHERE resolution = 'hour' AND ts::time = '00:00'")
    op.create_primary_key("pk_price_history", "price_history", ["item_id", "ts"])
    op.drop_column("price_history", "resolution")
